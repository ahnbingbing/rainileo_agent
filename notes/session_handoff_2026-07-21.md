# Session handoff — 2026-07-21 (7/20밤→7/21 대형 세션)

**스파인:** 이번 세션 내내 반복된 주제는 **"증상의 근본은 대개 한 겹 아래에 있다 — 그리고 PD 직감이
그 아래를 짚는다."** PD가 던진 모든 증상(7/21 RF 빈 슬롯 / AV 하네스·가방 드리프트 / 7월에 크리스마스 /
챌린지 반복 / Seedance 비용)의 근본은 눈에 보이는 게이트·모델이 아니라 **한 겹 아래의 자동복구·선택·프레이밍
로직**이었다. 그리고 **유료 렌더($50 AV)는 디버그 반복 대상이 아니다** — 오프라인으로 먼저 확정하고 1회 렌더.

## VM authoritative. push=deploy. 렌더 env.
- `git push origin main` → deploy 타이머(2분 폴)가 pull→smoke→봇 재기동. **VM HEAD = 이 세션 끝 기준 최신**(3e2dbfc + 그 뒤 handoff 커밋).
- VM 렌더: `sudo -u rianileo bash -c 'set -a; source /etc/rianileo/env; set +a; PATH=/home/rianileo/.local/bin:$PATH PYTHONPATH=/home/rianileo/rianileo-agent .venv/bin/python -m agents.launch_selfheal --date … --slot … --rounds N'`.
- **일배치 = 03:00 cron `agents.launch_selfheal`**. ★이 세션서 `LAUNCH_LEAD_DAYS=2`로 바꿈 → 크론은 이제 **이틀 뒤** 배치를 만든다(7/22 03:00 → 7/24). crontab.vm 재설치 완료.
- **YouTube 상태는 반드시 API로 확인**(DB stale 가능). Mac↔VM SSH IAP 간헐 255(재시도로 붙음). **인라인 python one-liner 금지**(이스케이프 깨짐) — 작은 .py를 scp해서 실행. **nohup 백그라운드는 스크립트 전체를 `sudo -u rianileo bash script.sh`로** 돌려야 자식이 살아남음(중첩 sudo bash -c 안에 nohup은 SSH 종료시 죽음).
- ★★**standalone 재렌더 스크립트는 logging 미설정이라 `log.info`가 억제된다** — 렌더 중 진단은 반드시 `progress_cb`로 흘려라(이거 몰라 AV 재렌더 여러 번 낭비).

## 손수정 (예약 교체/채움, 손실 0)
- **7/21 RF ×2 빈 슬롯 채움**: self-heal이 fresh 컨셉 렌더 → 12:30 `3lGFT2pHK1o`(랴니 소파낮잠)·21:00 `iUAKpk_1zQE`(랴니 카페). salvage-upload 버그로 자동예약 실패 → 수동 `_auto_upload_episode`.
- **7/21 18:00 AV 하네스/가방 재렌더**: 옛 `X5Le` reupload 교체 → `16Ei9JpWPIY`. cut5서 랴니 빨간 하네스 유지+레오 회색 가방 일정 검증.
- **7/22 백필**: 18:00 RF `MQ6J62US1dU`(여름·자동예약)·21:00 AV `tRWYEjtSvzs`(낮잠꿈, Giri미통과지만 캐릭터정상·PD 그대로예약결정). 크리스마스 08:00은 PD 지시로 유지.
- **7/23 테스트 배치**(PD가 launch 1회 요청) — **최종 4/4 완성**: 08:00 AV `9fsjASDaiP0`(폴라로이드 평행세계)·12:30 RF `MKi5jmKeW7I`(겨울농구)·18:00 AV `r02-GgxIG_0`(에어컨바람사수전)·21:00 RF `VSfZqNLrg8k`(수영장). 첫 테스트서 AV 두 슬롯 다 실패(순간이동-취약 "평행/비교" 컨셉)→PD 지시 C로 두 AV 재렌더→**포맷 픽스(챌린지+평행 회피)가 순간이동 없는 컨셉 만들어 둘 다 R1/R2 성공**(각 ~$25-50). 이게 포맷-로테이션이 실제로 AV 품질을 올린 라이브 증거. (7/23은 크론 안 채움 — LAUNCH_LEAD_DAYS=2로 크론은 7/24부터.)

## Durable 픽스 (전부 배포)
1. **content_gutted 재롤**(a939708) — 결정론 게이트(coherence/face/stub-too-short)가 에피소드를 생존선 아래로 gutting하면 self-heal이 같은 컨셉 재렌더가 아니라 **fresh 컨셉 재롤**로. 7/21 RF 두 슬롯 전멸의 근본. [[empty_slot_multiroot_and_prop_anchor]]
2. **salvage output_video_path 재포인트**(e6f74e8) — 캡션-salvage가 새 타임스탬프 파일 쓰며 카드 포인터 안 갱신→auto_upload "no card"→렌더+통과한 에피소드가 조용히 예약 실패(빈 슬롯의 두 번째 원인). salvage가 반환 직전 카드 갱신.
3. **AV 소품 prop-ref 앵커**(173d428 + 182628c) — ref 모드는 캐릭터만 앵커·소품 무앵커→하네스/가방 드리프트. canonical 크롭을 object_refs 등록+버전관리. ★함정: prop 감지가 asset-정렬 `cc`의 한글 description을 읽는데 그게 스트립됨→**DB 카드 payload 직접 읽기**(dict(card) Row정규화). 4번 재렌더 헤맨 근본=log.info 억제로 진단 안 보임. 회고 A18.
4. **iCloud DB export 배선**(f043643) — launchd icloud-sync가 파일만 mirror하고 `ingest_register --export`(DB row 스냅샷)를 안 불러 VM 풀이 7/5부터 stale. icloud.sync 끝에 export 배선.
5. **★recency-first 재료 샘플링**(62d9295) — concept_brainstorm이 writer에 주는 영상재료가 `RANDOM()`(전연도 3078개서 6%)→fresh grandmompapa 여름소재 묻히고 겨울 memory-lane이 머릿수로 이김("7월 크리스마스"). recent-first+random tail 블렌드. **PD 직감("iCloud보다 grandmompapa 안 쓰는 게 문제")이 근본**. 회고 D26.
6. **날씨-캡션 위브**(05e2ae1) — off-season footage를 버리지말고 오늘 실제 날씨로 프레이밍(계절 불일치→훅). `trend_feed.weather_context`(google_search 서울날씨)+RF 오프너 위브(대비있을때만·자연스러우면만·RF-only). 회고 D28.
7. **컨셉 포맷-로테이션**(a24f00b+d0b2b65) — dedup이 토픽만 보고 **포맷/프레임**은 안 봐서 챌린지 21일간 9회. 최근 과다반복 포맷(챌린지/년생/뱃살) 감지→"다른 형식으로, 챌린지면 야외/독특하게" 주입. (버그: DB_PATH 미정의·wall-clock 윈도우 →고쳐서 실제 걸림 확인.)
8. **위글 de-default**(1b5f0f9) — director_shots가 위글을 기본 추천액션으로 박음. canon(랴니 기쁨=위글) 유지하되 아껴 쓰고 다양하게(플레이바우·홉·귀쫑긋).
9. **LAUNCH_LEAD_DAYS=2**(3e2dbfc) — 크론 배치를 이틀 뒤로(스팟체크 하루 더).

## ★비용 (D27) — 이 세션 최대 교훈
어제 Seedance $63.9 = 대부분 내가 AV prop-주입 버그 잡느라 X5Le를 ~8번 죽였다살렸다(깨끗한 1회면 ~$50). **원칙: $50 렌더는 디버그 반복 대상 아님 — 매칭/경로/데이터 로직은 오프라인(verify 스크립트/dry-run)으로 확정 후 1회.** AV 재롤도 매번 $50이니 신중(SELFHEAL_REROLL=0). **크론 재렌더도 공짜 아님**(Seedance=API콜 숫자 기반, PD 지적).

## ★AV는 "소재 시점"이 없다 (PD 지적)
AV는 사진을 포즈·장면 ref 이미지로만 씀→촬영날짜=에피소드 시점 아님(계절은 컨셉이 정함). **off-season/철 문제는 RF 전용**. 날씨-위브도 RF-only.

## ★★ NEXT
1. **7/24 03:00 크론 배치**(첫 LAUNCH_LEAD_DAYS=2 + 대부분 durable의 첫 실전) 스팟체크: recency로 fresh 여름소재 쓰는지 / **챌린지·평행·위글 줄었는지**(포맷 로테이션+위글 de-default) / **날씨-위브 실전 작동**(off-season 오프너가 오늘 날씨로 프레이밍) / content_gutted·salvage로 빈 슬롯 없는지 / prop-ref 소품 앵커 유지.
2. 미완/판단대기: **12:30 겨울농구**(7/23)에 날씨-위브 데모적용(recaption+reupload)은 PD 판단 대기 — 이 겨울 클립이 "장마에 그리운 겨울 농구"로 살아나는 걸 보고 싶으면. 21:00 AV(7/22) 낮잠꿈 Giri미통과본 유지 중.
3. 관찰: AV "평행/비교" 구조가 순간이동에 취약 — 포맷 마커로 회피 시작했으나, 근본(멀티공간 AV still collapse)은 [[av_multispace_still_collapse]] 계열 상시 과제.

## 교훈
- **증상의 근본은 한 겹 아래** — 빈 슬롯=게이트 아니라 자동복구가 복구 못함 / 크리스마스=풀 아니라 RANDOM 샘플링 / 챌린지=dedup이 토픽만 봄 / 소품드리프트=cc가 description 스트립. PD 직감이 반복해서 그 아래를 짚었다.
- **availability ≠ usage** — 풀에 있어도(fresh footage) 선택 로직이 선호 안 하면 안 쓰인다.
- **소재의 '문제'가 콘텐츠 각도가 될 수 있다** — off-season을 필터로 버리기 전에 프레이밍(오늘 날씨)으로 살려라.
- **유료 렌더 전 오프라인 검증** — $50은 디버그 반복 대상이 아니다. 진단은 억제 안 되는 채널(progress_cb)로.
- 회고 A18/C15/D26/D27/D28 + 메모리 [[empty_slot_multiroot_and_prop_anchor]]·[[fresh_footage_recency_and_render_cost]].
