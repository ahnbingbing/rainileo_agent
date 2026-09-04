# Session handoff — 2026-09-04 (RF 실패의 진짜 근본들 + 슬롯 진실원 + stop)

**스파인:** RF가 "풀이 작다 / 자꾸 실패한다"는 건 **콘텐츠 문제가 아니라 메타데이터·구조·판정 진실원의
문제**였다 — ①NULL duration이 긴 클립을 선택기 시야에서 지웠고(메타데이터 맹점), ②same-clip collapse가
사전게이트의 과대추정을 뚫고 gutted stub을 만들었고(구조), ③카드 DB가 YouTube와 desync해 슬롯을 가짜로
"찼다/비었다" 판정했다(진실원). 그리고 **aggregate 로그 grep은 근본을 오귀속한다**(face-leak 오진) —
구체적 케이스를 끝까지 추적하고, PD의 도메인 지식(프레임이 어떻게 동작하는지)·프레임·YouTube를 진실원으로
검증하라. "풀이 작다/빈 슬롯"은 자원·메타데이터·판정 상태가 콘텐츠 실패로 위장한 것일 수 있다(전제부터 검증).

## VM authoritative · push=deploy
- **VM HEAD c785776** 이후 (오늘 다수 durable 배포·pull 검증). `git push origin main` → deploy.timer pull.
- **crontab.vm 변경 시 VM 재설치 필요**: `crontab -u rianileo deploy/crontab.vm` (오늘 backfill 항목 설치 완료).
- SSH가 IAP 터널로 매번 ~15-20s → 조회를 한 명령에 묶어라. **prod 렌더 kill 불가**(권한) → 이제
  `render_control.request_stop()` 파일플래그로 렌더를 세운다(D_stopflag).

## SHIPPED — durable (오늘, 커밋순)
1. **RF 풀 NULL-duration 맹점** (C_nulldur): 인입 ffprobe(`icloud/sync.py`)+`scripts/backfill_durations.py`
   (post-Leo NULL 0, **VM 풀 dur≥12 ~98→654**)+`producer._backfill_pool_durations`+`ingest_register` COALESCE.
   +**야간 백필 cron**(crontab.vm 01:00 `--limit 400`, 잔존 ~1975 pre-Leo 소진).
2. **연속사진→video 자산** (C_photoseq): `scripts/build_photo_sequences.py` 175개, 기존 RF video 경로 소비.
   ★단 ken-burns 저모션이라 첫 라이브 Giri 4/10 → **quality 0.65<floor로 게이트**(PD 리뷰 후 유입 결정).
3. **OpenAI best-of** (D_openaicost): `REGEN_BEST_OF` 2→1. 죽은 config(REGEN_MODEL) 명시. (텍스트 캐스케이드
   OpenAI-primary는 PD 결정 대기 — 엔진 화질 트레이드오프.)
4. **slot-truth** (D_slottruth): `slot_topup.slot_occupancy()` — YouTube(public+예약)를 슬롯 점유의 단일
   진실원으로. 빈슬롯감지·collision가드(YouTube우선·카드fallback)·slot_topup 통일. 오늘 18:00 헛렌더의 근본.
5. **clip-reuse 비완화 하드플로어**: 선택단이 검수단 kill-set(`_live_published_rf_assets` 7일 live-published,
   공유함수) 완화불가 제외 — Giri 실패 52% 근본(소프트쿨다운이 얇은풀서 완화하며 재사용 재admit).
6. **collapse-aware 사전게이트** (C_toolshort): ★face-leak은 오진(PD 맞음, 공간crop이라 시간 못깎음). 진짜
   too-short #1 원인=same-clip collapse. 사전게이트가 컷을 asset별 그룹핑→클립 실제길이로 cap(collapse 시뮬).
7. **RF 3회→AV 폴백** (C_avfallback): `RF_FOOTAGE_MAX_TRIES=3` 재시도(공짜)후 소재부족이면 `RF_AV_FALLBACK=1`로
   AV 전환(footage-first: 스토리 없으면 AV, PD 컨펌). AV 실패시 마지막 RF로 graceful.
8. **stop 플래그** (D_stopflag): `agents/render_control.py` 협조적 STOP(6h TTL)+렌더루프 3곳 경계 체크+board
   "stop"/"go" 즉시 처리. "stop"이 실제로 먹힌다.

## 조사만 (수정 대기, PD 결정/후속)
- **인입율 하락**(`notes/ingestion_rate_drop_2026-09-04.md`): 8/24부터 "Ryani&Leo" 앨범 신규 0(라이브러리엔
  141개, 업스트림/앨범 태깅 문제). ★PD 액션=앨범 확인. 부차=petlabel `df -BG` macOS 파싱버그(exit3, 소진잡).
- **알고리즘 노출**: 파이프라인이 **impressions/CTR 미수집**(Studio서만) + 로컬 DB stale. 썸네일=텍스트없는
  프레임그랩(CTR 병목 후보)·12:30 약함·memory-lane retention. 미착수.

## gotcha (오늘 값비싸게 배운 것)
- **aggregate 로그 grep ≠ 근본**: face-leak 오진(서브에이전트 grep이 "dropping face-leaking (0)"을 원인처럼).
  구체적 케이스 추적 필수. PD 도메인지식이 이긴다.
- **`list_scheduled_videos`는 미래-private만** 반환(이미-public 제외) → 슬롯 공석 판정에 쓰면 false gap.
  공석/충돌은 `slot_occupancy`(publishAt|publishedAt) 사용.
- **카드 `youtube_publish_at`은 desync한다** — 슬롯 판정에 믿지 마라(YouTube가 진실).
- Giri-통과 렌더도 slot-collision(오늘은 가짜)에 막히면 orphan mp4 → prune에 삭제될 수 있음(복구 불가).
- **prod 렌더 kill 막힘**(classifier+소유권) → `render_control.request_stop()`. IAP 터널 누수 `pkill start-iap-tunnel`.

## ★NEXT
- **다음 03:00 배치 = 오늘 RF수정 첫 실전**: collapse-aware 사전게이트·AV 폴백·clip-reuse 하드플로어·
  slot-truth·stop 플래그. 스팟체크=빈슬롯 감소 / 과-AV폴백(RF가 너무 쉽게 AV로 가는지) / 과-재제안 / board
  stop·go 동작 / backfill cron 로그(`cron.backfill_dur.log`).
- **9/5·9/6 빈슬롯 채우기** 실행함(launch_selfheal --date, slot-truth로 빈슬롯만) — 결과 확인 필요.
- **PD 결정 대기**: 연속사진 시퀀스 라이브 유입(quality≥0.7), OpenAI 텍스트엔진 Gemini 전환, 인입 앨범 복구.
