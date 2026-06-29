# 클라우드 이관 — CANONICAL 플랜 (봇·배치 → GCP VM, Mac = 새벽 백필만)

> **상태:** APPROVED 방향 (2026-06-28). 프로비저닝(VM/과금)은 PD 승인·결제 게이트.
> **이 문서가 `notes/cloud_migration_plan.md`(board executor, 이슈 #12)와 이전
> `gcp_migration_plan.md`를 통합·대체한다.** 두 분석이 엇갈렸던 지점은 §1에서 해소.
>
> **진행현황 (2026-06-28):** Mac-탈피 선행작업 §7 ①② **DONE**(GCS 커버리지 100% · 렌더
> GCS-only 가드), ③ 선택 잔여. 코드 변경 **미커밋**(브랜치 세션충돌 주의). **다음 = VM
> 프로비저닝(P1, 결제 게이트)** — 그 전까진 로컬에서 더 할 $0 작업 없음(③ 선택 제외).

---

## 1. 결정을 가른 단 하나의 변수

추천이 세 번 흔들렸는데, 변덕이 아니라 **load-bearing 변수 하나**가 바뀐 결과였다:

> **"osxphotos가 *지속적* 인입 경로로 얼마나 중요한가?"**

- 처음(하이브리드): osxphotos = 인입이라 가정, Mac은 "얇게" 잔류.
- board 문서(맥미니): osxphotos가 *모든 신규 콘텐츠의 원천* → Mac 24/7 필수 →
  "클라우드 탈출" 전제 붕괴 → 전용 맥미니 단일노드가 우위.
- **PD 정정(확정): Slack 봇 `_ingest_file`이 앞으로의 신규 콘텐츠 원천이다.**
  osxphotos는 **새벽 1회 백필**로 강등. → **Mac은 더 이상 always-on이 아니다** →
  클라우드 전제 부활, 그리고 *Mac을 끝내 없애는 경로*까지 열린다.

**검증 완료:** `slack/app.py:_ingest_file`은 Slack 업로드를 받아
`INSERT OR IGNORE INTO assets(... duration_sec, width, height, phash, subjects_csv)`로
**완전한 asset 행**을 쓴다. `file_shared` + `message/file_share` 이벤트에 물려
**봇 채널에 파일만 올리면 자동 인입.** osxphotos 미사용(PIL+ffprobe만) → **Mac 독립, 어디서든 실행.**

## 2. 타깃 아키텍처

```
Cloud VM (e2-medium @ asia-northeast3 서울, tz Asia/Seoul, 상시 두뇌):
  • Slack 봇 + 파일 인입(_ingest_file)  ← 주 인입 경로, BrokenPipe 근본 해결
  • DB (sqlite 57MB, VM 영구 디스크 단일 거주)
  • 렌더 오케스트레이션(Seedance/Veo/OpenAI = API, 위치 무관)
  • 전 cron (launch/producer/board/bandit/channel_manager/…)

Mac (PD 기존 개인 Mac, 새벽 1회 비크리티컬):
  • osxphotos 백필 스윕 → GCS 업로드 + 매니페스트 → VM이 행 등록
  • 꺼져 있어도 봇·콘텐츠 무관. Slack-only 정착 시 은퇴 가능 = 순수 클라우드 엔드게임
```

## 3. 검증된 사실

| 사실 | 값 | 출처/함의 |
|---|---|---|
| DB | 단일 `data/agent.db` **57MB**, `sqlite3.connect` **65호출 / 53파일** | sqlite는 VM 단일거주. DB를 Mac 밖 *원격*으로 빼는 건(Cloud SQL) 53파일 수술 = **안 한다** |
| 에셋 로컬 | **3.3GB** | GCS 미러(`icloud/gcs.py`, `gs://rianileo-assets`) 이미 가동 |
| **GCS 커버리지** | **100% (16,602/16,602)** | 백필 후 실측(2026-06-28). 렌더 GCS-pull로 충분, 로컬 56개뿐 |
| Slack 인입 | `_ingest_file` 완전동작·dedup·메타추출 | Mac 독립. **GCS 푸시 추가 완료(작업1a)** — 인입 즉시 미러 |
| 렌더 비용 | 오케스트레이터 위치 무관 | VM 산다고 렌더 안 싸짐. VM은 순수 인프라 비용만 |
| 진짜 운영 고통 | 공유 개인 Mac의 osxphotos 락 충돌(03:00 배치 전멸) | [[osxphotos_lock_tmpdir_bug]]. 렌더/launch가 VM로 빠지면 거의 증발 |
| 유일 macOS 종속 | osxphotos | 이것만 Mac. `sips`는 레거시·Lane2뿐(핵심경로 X), `pillow-heif` 이미 deps |

## 4. board 문서(맥미니) 두 반론 — 왜 무너지나

1. **"Mac 24/7 필요"** → 해소. Slack이 주 인입이면 Mac은 새벽 1회 스윕. 전용 맥미니
   구매 불필요(기존 Mac). 무거운 배치가 VM로 빠져 락 충돌도 증발.
2. **"DB 53파일 수술"** → 이 타깃엔 **해당 없음.** 그건 board 옵션 2(Cloud SQL) 얘기.
   여기선 sqlite가 VM 단일거주 + 행 쓰는 봇이 같은 VM → 원격 DB 쓰기 0.

> board 문서의 분석 자체는 옳았다(맥 종속 3축·렌더비 위치무관·락 충돌 진단). 결론만
> 바뀐 건 PD가 인입 경로를 Slack으로 재정의했기 때문. 그 doc은 provenance로 보존.

## 5. launchd 잡 분리 (이제 1개 빼고 전부 VM)

**Mac 잔류 (1, 비크리티컬 새벽):** `icloud-sync`(01:30) — pull + GCS + 매니페스트(DB 안 건드림)

**VM 이동 (systemd service/timer로 포팅, tz Asia/Seoul 필수):**
- 상시: `slack`(KeepAlive→service)
- 타이머: `slack-sync`(900s)·`board-escalations`(300s)·`ytcache`(1800s)
- cron: `launch`(03:00)·`producer`(18:00)·`writer`(18:00)·`bandit-collect`(06:30)·
  `bandit-report`(월10:00)·`api-cost`(08:10)·`daily-metrics`(09:00)·`bgm-claim-sync`(07:00)·
  `gmp-morning`(09:00)·`gmp-evening`(19:00)·`giri-weekly`(3d)·`petlabel-backlog`(07:00)·
  `revlm-pre2025`(07:10)·`remind-photos`(06/12/18)

## 6. 단계 (봇 우선 검증 → shadow → 원자 컷오버 → Mac 강등)

> 봇은 board 명령이 라이브 DB·렌더를 발화 → 단독 라이브 불가. **연결 검증은 1순위,
> 라이브 소유권 이전은 전체와 함께 원자적으로.**

- **P1 — GCP 기반**: 빌러블 프로젝트(gen-lang-client 금지, 고차#10) · e2-medium@서울,
  tz Asia/Seoul, 영구디스크 · Secret Manager(.env) · ffmpeg/폰트/deps.
- **P2a — 봇 우선 검증(즉시)**: VM에서 봇 socket-mode를 **별도 dev 워크스페이스**에 띄워
  **BrokenPipe 소멸 즉시 증명**. 운영 봇은 Mac 유지(이중 응답 0).
- **P2b — shadow 풀 기동**: systemd 유닛 · DB 스냅샷 복원 · **dry**(`YOUTUBE_AUTO_UPLOAD=0`,
  산출물 `gs://…/shadow/`).
- **P3 — 패리티(1주)**: 같은 GCS 에셋으로 동등 에피소드 산출 비교 · launch 드라이런 ·
  봇 명령 dev 채널 응답 · cron 발화 확인.
- **P4 — 전체 컷오버(원자)**: Mac launchd 18개 `unload` ↔ 같은 순간 VM 라이브
  (`YOUTUBE_AUTO_UPLOAD=1`, 봇 운영 워크스페이스 재바인딩) · 최종 DB 동기화로 VM 권위본.
- **P5 — Mac 강등**: `icloud-sync` 새벽 스윕만 잔류. (Slack-only 정착 시 P6=osxphotos 은퇴 → 순수 클라우드)

## 7. Mac-탈피 선행 작업 (프로비저닝 게이트와 무관, $0) — 진행현황

PD 단순화(GCS 95%→실측) 후 원래 4모듈 분리는 **과설계로 판명.** 실작업은 셋으로 축소:

1. ✅ **GCS 미러 완성** — `_ingest_file` GCS 푸시(작업1a) + 누락 769 백필(735 osxphotos→GCS +
   34 Slack 직접) → **커버리지 100% (16,602/16,602)**. 일회성 launchd로 osxphotos TCC 권한
   확보(셸 nohup은 인증 실패). (2026-06-28)
2. ✅ **VM 렌더 = GCS-only** — `icloud/sync._osxphotos_available()`(darwin+CLI 게이트) +
   cameraman `_ensure_local`/`_prefetch` 폴백 가드. **Mac=동작 0변경**, Linux VM=osxphotos 스킵
   →GCS-only(누락은 per-cut 게이트). py_compile + Mac=True/Linux=False 검증. (2026-06-28)
3. ⬜ **인입 시 GCS 보장(선택)** — Slack=작업1a로 완료. 새벽 osxphotos 델타에도 GCS 푸시 보장
   + (선택)태깅 시 ensure-in-GCS 세이프티 1줄. 미러 100%라 belt-and-suspenders.

> **`sync.py` 분리는 "델타 전용 축소판"으로 격하** — 벌크/렌더는 GCS-pull(분리 불요). 남은 건
> 새벽 osxphotos가 *Slack 안 거친 신규*를 GCS+얇은 매니페스트로 넘기는 작은 경로뿐.
> 상세·격하근거: [sync_split_design.md](sync_split_design.md). systemd 유닛/VM 부트스트랩은
> 프로비저닝(P1) 직전 코드화.

## 8. PD 결정 — LOCKED + 잔여

- ✅ VM: e2-medium @ 서울, tz Asia/Seoul · ✅ shadow 봇: 별도 dev 워크스페이스 ·
  ✅ DB: sqlite 단일거주 · ✅ 컷오버: 봇우선검증→1주shadow→전체원자 · ✅ 인입: **Slack-primary**, osxphotos=새벽백필→은퇴경로
- ⬜ 월 비용 캡(api_calls 원장 합산) — 착수 비차단 · ⬜ VM/Secret 프로비저닝 = 결제 승인 시점
