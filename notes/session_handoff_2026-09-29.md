# Session handoff — 2026-09-29 (v2 6슬롯: 실배선→라이브→9회 반복 견고화, 이월일까지 라이브 검증)

**스파인:** 9/27~29 사이 v2를 **dry-run 스캐폴딩에서 라이브 프로덕션으로** 밀어붙였고, PD의 매 신호가
각각 다른 근본을 드러냈다 — 죽은 플래그(실배선) → 품질 게이트(AV 리롤·역할스왑) → 전환기(date-gating)
→ 런어웨이(벽시계 캡) → 조용한 orphan(salvage-card desync) → 측정 오염(bandit grid) → 같은 footage
반복(인터리브) → 리뷰 UX(mp4 스레드). 관통 교훈 셋: **①플래그는 진입점이 읽고 렌더/예약까지 배선돼야
"플립 준비"다**(dry-run 통과 아님). **②슬롯-세트/모델 변경의 블래스트는 전환기·측정·리뷰까지 뻗는다 —
per-date-grid로 일관하고 "언제부터"를 명시하라.** **③"다양성"은 한 축이 아니다 — 소스 間 클립오버랩과
소스 內 편집반복(같은 footage)을 따로 막아야 한다.**

## VM authoritative · push=deploy
- **VM HEAD `23ad37e`** (전부 배포·검증). cron 유휴, 봇 active.
- **v2 라이브**: `/etc/rianileo/deploy.env` `LAUNCH_MODEL=v2`. **`V2_START_DATE` 기본 2026-09-30**(코드 기본값 —
  플립 前 4슬롯 날 제외). 롤백 = deploy.env에서 `LAUNCH_MODEL` 줄 제거 → 다음 배치 4슬롯.
- 하루 스케줄(KST): 03:00 launch_selfheal(v2 위임) / 09:10 slot_topup / 09:40 pd_reviewer / */5 board_esc / */30 ytcache.

## v2 모델 (6슬롯 · 2일 사이클)
- 슬롯: 08:00/09:00/13:00/18:00/**20:00(AV)**/21:00. 생산일=5RF+1AV(+4RF 익일 이월), 이월일=핀4RF+2AV(08·20).
- 생산일 RF: senior_director→3소스(A 함미하비노트·B VLM·C arc, footage-다양성<60% 게이트)→각 소스 3 grammar.

## SHIPPED (9/27→9/29, 시간순 · 전부 배포)
1. **실배선+라이브 플립**(`02eadd5`): `run_v2_batch` 오케스트레이터 · `effective_assignments` 슬롯 단일진실원 ·
   6슬롯 occupancy/collision 스냅 · launch_selfheal/topup v2 분기 · grammar rolling-window v2 가드 · 소스별 tag.
   플래그 OFF=4슬롯 바이트동일. 9/30 수동 첫 실전 → deploy.env로 플립.
2. **멱등 생산일 가드**(`6adb18d`): 이미 찬 생산일 재타겟 시 RF 렌더/핀 스킵.
3. **AV 리롤 + 역할스왑 게이트**(`75eac28`): `V2_AV_REROLL`(기본1, Giri 실패 시 새 컨셉 1회) · `_AV_ROLESWAP_RX`에
   리듬/생활패턴/수동 "바뀐" 보강(9/30 AV giri_fail 근본; 어휘 게이트=pre-filter, 백스톱=Giri+리롤).
4. **date-gating**(`6e73d99`): `active_for(date)`=enabled()+date≥V2_START_DATE. slot_occupancy `slots=` per-day
   grid(find_gaps/충돌가드/skip-filled). 플립 다음날 topup이 전환일 phantom 갭(09:00/20:00)에 유료 AV 렌더하던 런어웨이 근본.
5. **벽시계 캡 + salvage-orphan + bandit grid**(`3bbefcf`): `V2_BATCH_MAX_SECONDS`(3h) · `_finish`가 out_to_card로
   우승 카드 repoint(RF `[ORPHAN-SKIP] no-card-for-output` 근본 = salvage가 카드를 _salvaged로 옮겼는데 루프가 다른
   attempt를 best로 골라 desync) · `_timeslot_of` 발행일별 grid.
6. **인터리브 + day1_winners 배선**(`a3c5c83`): `_interleave` grammar-major(Day-1 ABCAB, 같은 footage ≤2×/일·3연속無 —
   9/30 코 3연속 근본) · day1_winners를 **"가장 최근 실측 발행일"**로 재정의(PD 승인) → carry AV가 `PD_RERENDER_DIRECTIVE`로
   최근 인기편 판타지 재해석(dominant 1편2앵글/spread top2).
7. **PD mp4-in-thread 리뷰**(`23ad37e`): 써머리를 스레드 부모로 + 각 mp4 답글 + `record_batch_video`로 `veto <파일명>`
   작동(4슬롯과 같은 테이블, 봇 무변경). v2 RF 생산분(launch_pipeline 우회)의 유일 리뷰면.

회고: §4.5 **D_v2wiring**(여러 addendum). 회귀 `_launch_v2_regress`(day_plan/active_for/interleave/role-swap 등 다수).

## 라이브 스케줄 현황 (2026-09-29 15:53 KST)
- **9/30**(생산일): 08:00 RF `i7PKxSlSX4s`(코) · 09:00 RF `nT3y4Gjt1mU`(topup refill·다른 footage) · 18:00 RF
  `Vt7u7m5YEKU`(정원) · 20:00 AV `1VZ5SpXG5XI`. = **4/6**. ★13:00·21:00 아직 빔(veto 후 topup 미완 — 다음 topup이
  시도하되 pool/publish-time에 따라 빌 수 있음). 코 3연속은 해소됨.
- **10/1**(이월일): **6/6 완성** — 08:00 AV `6wkc_hRB8uw` · 09:00 RF `3jgvInrvsl4` · 13:00 RF `PfELZmZ0P2I` ·
  18:00 RF `1LbJHPN0dB8` · 20:00 AV `w0Kpp42OQ1k` · 21:00 RF `PWpkuDo6gWI`. **v2 이월일 흐름 라이브 검증 성공**
  (핀 4 RF 예약 + AV 2편). 9/29 03:00 배치작.

## ★ NEXT (스팟체크)
1. **10/1 AV 2편이 day1_winners 판타지였나** — a3c5c83 배포 후 첫 이월일이라 판타지 재해석/PD_RERENDER_DIRECTIVE의
   첫 라이브. 08:00 `6wkc_hRB8uw`·20:00 `w0Kpp42OQ1k` 품질/컨셉 확인(최근 인기편 상상판 맞는지, 아니면 데이터 없어 일반 timely 폴백).
2. **9/30 03:00 → 10/2 (생산일 cron 첫 full)** — 9RF 인터리브(같은 footage 3연속 없나)·5예약+4이월·AV·비용·벽시계.
3. **9/30 13:00·21:00 빈 슬롯** — 채워지나/비나. 안 채워지면 pool 얇음 점검.
4. PD mp4 리뷰 스레드가 실제로 mp4+veto버튼으로 뜨는지(첫 라이브 배치 써머리에서).

## 남은 후속 (단 하나)
- **bandit v2 arm 운용(B5)** — timeslot 버킷은 grid 고침 완료. v2 발행분 48h 실측이 며칠 쌓이면 marginal로
  다음 달 arm(문법/슬롯) 선택 시작. 지금은 자동 수집만.

## Gotchas (이번 세션 누적)
- **`.env` 읽기 차단**(auto-mode 분류기): deploy.env/env **읽기** 금지 → 값 grep/cat 금지, append는 무출력 `tee -a`,
  검증은 python 불리언(`active_for`/`enabled`). 세션 밖 프로덕션 프로세스 kill은 **명시 승인 필요**(dangerouslyDisableSandbox).
- **SSH 인자 파싱**: 멀티라인/콤마 python `-c`가 gcloud --command에서 깨짐 → **base64로 스크립트 심고 실행**이 안전.
- **Monitor 55분 타임아웃**: 긴 렌더(9RF~90분·AV~40분)는 재장착 or 저널 직접 tail.
- **VM SSH=ahnbingbing 로그인**: 레포/로그/cron은 `rianileo` 소유 → `sudo -u rianileo`+절대경로. 긴 렌더는 systemd-run transient.
- 수동 v2 실행=`sudo -u rianileo env LAUNCH_MODEL=v2 bash $R/deploy/run_job.sh -m agents.launch_selfheal --date <D>`.

메모리: [[production_model_v2_senior_director]](라이브·9회 견고화) · [[streaming_guard_masked_dead_primary]].
