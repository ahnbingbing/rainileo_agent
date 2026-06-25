# 봇·배치 잡 클라우드 이전 설계 (PD 2026-06-25: "봇·배치 다 GCP에서. 아니면 깃 액션")

## 결론 먼저
이건 plist 한 줄 바꾸는 일이 아니라 **재아키텍처**다. 지금 시스템은 세 가지로
이 Mac에 물려 있다 — 이 셋을 풀지 않으면 어떤 잡도 안전하게 못 떠난다.

1. **에셋 인입이 macOS 전용.** `icloud/sync.py` 는 osxphotos 로 macOS Photos.app
   라이브러리를 직접 읽는다(Apple ID·PhotoKit·로컬 라이브러리 필요). GCP/GitHub
   Actions 어디서도 못 돈다. 새 사진/클립의 *원천*이 이 Mac이다. → **이 Mac은
   인입 노드로 남아야 한다.** (대안: 사진 소스를 공유앨범 export/iCloud API로 바꾸기 = 별도 큰 작업.)
2. **단일 로컬 SQLite 가 진실원천.** 약 25개 모듈이 `sqlite3.connect(data/agent.db)`
   (57MB). SQLite는 머신 간 동시 read-write 공유가 안 된다. 잡을 Mac 밖으로 빼려면
   DB가 먼저 원격(Cloud SQL/Postgres)으로 가거나, **단일 GCE VM이 DB+잡을 같이** 들어야 한다.
3. **비용·비가역.** GCE VM / Cloud SQL / GCS egress 모두 과금. 프로비저닝 자체가
   이 작업에서 금지된 유료·비가역 영역. (GCS 버킷 `rianileo-assets`는 이미 과금 중 — Phase 1.)

## GitHub Actions 는 여기 안 맞다 (부분적으로만)
- 러너가 휘발성 — 로컬 SQLite(57MB)·에셋(3.3GB)·Photos 라이브러리 없음.
- **Slack 봇은 cron이 아니라 Socket Mode 상시 websocket** (`slack/app.py`) → Actions에서
  애초에 못 산다. VM/Cloud Run 필요.
- Actions가 돌릴 수 있는 건 순수 DB+API cron 잡뿐이고, 그것도 **DB가 원격이 된 뒤에만**.

## 현재 15개 launchd 잡 분류
| 잡 | 성격 | 이전 가능성 |
|---|---|---|
| `icloud-sync` (01:30) | osxphotos 인입 | ❌ Mac 고정 (원천) |
| `petlabel-backlog`(07:00) / `revlm-pre2025`(07:10) | 에셋 VLM 태깅 | △ 에셋이 GCS면 VM 가능 (지금 로컬파일) |
| `launch`(03:00, launch_selfheal) | 렌더(ffmpeg+Seedance/Veo/OpenAI 유료) | △ VM 가능하나 유료키+에셋 필요 |
| `slack`(상시) | Socket Mode 봇 | ✅ VM/Cloud Run (Actions ❌) |
| `slack-sync`(15m)·`ytcache`(30m)·`board-escalations`(5m)·`bandit-collect`(6:30)·`bandit-report`·`daily-metrics`·`api-cost`·`gmp-morning/evening`·`giri-weekly`·`producer`·`writer`·`remind-photos` | 순수 DB+외부 API | ✅ DB 원격화 후 VM/Actions |

## 권장 타깃 (실용 최소안)
이미 옳은 첫발(GCS 에셋 미러, `icloud/gcs.py`)은 디뎠다. 그 위에:

**A. 작은 always-on GCE VM (e2-small) 하나** —
- SQLite DB 보유(추후 Cloud SQL 승격), Slack 봇(systemd) + 모든 DB/API cron(systemd timer로
  plist 1:1 포팅), 에셋은 GCS에서 on-demand fetch.
- 렌더(`launch`)도 여기서: 유료키는 VM 시크릿(Secret Manager).

**B. Mac은 iCloud 인입 노드로만 잔류** — osxphotos sync → 새 에셋 GCS 업로드 + 원격 DB에 행 기록.

이 분리가 불가피한 최소 경계다. 작업 = (1) DB→원격, (2) 15개 plist→systemd, (3) 시크릿→Secret
Manager, (4) Mac 인입 잔류. 전부 유료 VM/DB에 게이트됨.

## 핵심 재구성 (PD 2026-06-25: "그럼 맥미니 사는 게 낫냐")
iCloud 인입이 Mac에 못 박힌 순간, "클라우드로 탈출"이라는 전제가 깨진다. 어느 옵션을
골라도 **Mac 1대는 무조건 상시 켜져 있어야** 한다. 그러면 진짜 질문은 "클라우드 vs 로컬"이
아니라 **"이미 있어야 하는 Mac 위에 전부 얹을까(단일 노드), 아니면 Mac + 별도 VM으로 쪼갤까
(2-노드)"** 다.

그리고 지금 운영의 실제 고통은 "Mac이라서"가 아니라 **PD 개인/작업용 Mac을 공유**해서다 —
병렬 Claude 세션과 osxphotos 락이 충돌(밤샘 stuck 프로세스로 03:00 배치 전멸한 그 사건,
[[osxphotos_lock_tmpdir_bug]]). 전용 헤드리스 Mac은 바로 이걸 없앤다.

또 하나 — 렌더 비용(Seedance/Veo/OpenAI)은 **오케스트레이터가 어디서 돌든 동일한 클라우드
API 과금**이다. VM을 산다고 렌더가 싸지지 않는다. VM이 추가하는 건 순수 인프라 비용뿐.

## 결정 필요 (PD)
- **옵션 0 (신규·현실적 권장): 전용 Mac mini 1대로 전부 단일 노드.** iCloud 인입(osxphotos
  네이티브)·DB·Slack 봇·15개 cron·렌더 모두 한 대에서. **재아키텍처 작업이 0이다** — DB→원격
  (50+ connect 사이트)·plist→systemd·Secret Manager 전부 불필요, 지금 코드 그대로 이사.
  비용: M4 mini 베이스 ~$599 일회성(≈ e2-small VM 2~3년치). 공유-Mac 락 충돌도 해소
  (sleep 영구 off + 아무도 Photos 안 건드림). 제어는 이미 있는 Slack 봇이 원격 콘솔.
  - 트레이드오프(정직히): 단일 장애점 → 백업 필요(에셋은 GCS 미러 이미 있음, DB는 GCS 덤프/
    Time Machine 추가). 그리고 mini에 같은 Apple ID 로그인 + iCloud "원본 다운로드"로 사진
    라이브러리가 실제로 내려와 있어야 함(이 한 가지가 유일한 셋업 손맛).
- **옵션 1: GCE VM 1대 + Mac 인입 잔류(2-노드).** 봇·cron·렌더 VM, 월 ~$15-30(e2-small).
  하지만 **Mac도 여전히 켜둬야 함** → 두 대 운영 + 마이그레이션 작업 부담은 그대로. iCloud가
  못 떠나는 한 옵션 0 대비 이점이 약하다.
- **옵션 2: DB→Cloud SQL + cron은 GitHub Actions, 봇은 Cloud Run.** 가장 "클라우드 네이티브"
  지만 DB 드라이버 전면 교체(50+ connect) + 봇 별도 호스팅 + Mac 인입 잔류 = 작업량 최대.
- 어느 쪽이든 **iCloud 인입은 Mac 잔류**(또는 사진 소스를 공유앨범/iCloud API로 교체 = 별도 큰 프로젝트).

요약: iCloud가 Mac을 못 떠나게 하는 이상, **옵션 0(전용 Mac mini 단일 노드)이 비용·작업량·
안정성 셋 다 우위**다. 클라우드 분할은 "Mac을 없앨 수 있을 때"만 의미가 있는데 지금은 아니다.

다음 단계(Mac mini 구매 또는 VM/DB 프로비저닝)는 유료라 PD 승인 전까지 진행 불가.
