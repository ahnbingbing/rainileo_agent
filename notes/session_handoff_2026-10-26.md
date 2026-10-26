# Session handoff — 2026-10-26 (인입 재부진 재진단 — 롤백으로도 회복 안 됨 → RF-only 회복 모드)

**스파인:** PD "아직도 인입률이 너무 낮아 — 확인하고 해결 플랜". 10/22에 케이던스 2슬롯 컷 + 107편
숨김을 했는데도 여전히 낮다. **채널 레벨 Analytics 직접 조회**로 진짜 그림이 드러났고, 이전 핸드오프들의
짐작보다 심각하다: **일일 채널 조회 9월 3,000~6,500 → 10/23 ~200(약 95% 붕괴)**, 붕괴 시작점 정확히
**v2 플립(10/1)**, 그리고 **10/12 롤백 후에도 회복 안 됨**(10/15~17 반짝 2,306 뒤 다시 ~200 지속 하락).

## 핵심 통찰 (왜 롤백이 안 먹혔나)
- v2의 12일 저engagement 홍수가 Shorts 알고리즘에 "이 채널 신규 업로드는 시청자 불만족"을 학습시켜
  **채널 전체 분배를 조이는 자기강화 스파이럴**을 만들었다. **코드 롤백은 그 *알고리즘 평판*을 되돌리지
  못한다.** → 케이던스 컷·숨김·롤백은 전부 **브레이크**(음 신호를 멈출 뿐)지 **가속 페달이 아니다.**
- 트래픽 **95.6%가 Shorts 피드**(검색·추천·외부 ≈0) = 알고리즘 밀어주기에 100% 의존(구조적 취약).
- 리텐션은 멀쩡(50~97%, 리플레이>100%) = 문제는 콘텐츠가 아니라 **순수 분배(노출)**. 보이면 본다.
- 구독 순증 ≈0(104명) = 콜드스타트 띄울 기반 시청자 없음.
- **AV는 반복적으로 5~16 조회**(RF의 1/10~1/50) = 매 routine AV 업로드가 채널 평균을 끌어내리는 음 신호.

## VM authoritative · push=deploy
- **VM HEAD `317b462`** (3 push: 6089577 레인믹스 · 9fe5d24 retro · 317b462 intake_report). deploy.timer 적용 확인.
- 운영 진실 = VM DB / 라이브 Analytics(로컬 DB stale). ★하드 훅이 deploy.env 경로 포함 명령 전부 차단(크레덴셜 보호).
- ★classifier가 VM 레포 working-tree 쓰기+실행 차단(git-push 가드레일 우회). VM 검증은 `/tmp` 단독 실행 or git push 후.

## SHIPPED
### 1. RF-only 회복 레인 믹스 (`6089577`, PD 결정 = 브레이크아웃 집중 + AV 특별일만)
- `agents/launch.py` `day_assignments` 기본 `3rf1av` → **`rfonly`**: 전 슬롯 RF, AV는 **특별일 18:00 한 슬롯만**
  (`_SPECIAL_AV_DAYS`=할로윈/크리스마스/신정/어린이날 + `LAUNCH_AV_DATES` env로 음력명절 등 ad-hoc). 21:00은
  특별일에도 RF(도달 엔진) 유지. 신규 `_assign_rfonly`/`_is_special_av_day`.
- 블래스트(pipeline-change-impact): `day_assignments`=SSOT, effective_assignments/selfheal/slot_topup/pin 상속.
  `timely_av_slot`=routine day엔 None(graceful), 특별일 AV가 timely-hook 상속. grammar_slot dormant(EDIT_GRAMMAR=0).
  RF 풀 압력 ↓(2 RF/day < 런칭월 3). Writer/Director/Cameraman/Giri 무변경(레인 라우팅만). v2 회귀 갱신 ALL PASS.
- 가역: `LAUNCH_LANE_MIX=3rf1av`(매일 AV 복원) 또는 `2av2rf`(레거시), 다음 배치, 재배포 불요.
- 10/28 할로윈 박쥐 AV는 이미 예약(무영향), 10/31 할로윈은 캘린더가 자동 AV 배정.

### 2. intake_report Phase 3 트래커 (`317b462`)
- `scripts/intake_report.py` — 채널 레벨 진짜 인입을 Analytics API 직접 조회(일일 채널 조회 바차트 + 트래픽
  믹스 + 발행일별 per-video 48h). packaging_report(per-arm 프록시)와 달리 "인입이 회복 중인가"를 한눈에.
  `python -m scripts.intake_report [--days N] [--slack]`. VM 검증=10/17 peak 2306→10/23 272, 94.7% Shorts.

## 플랜 (PD 승인 "이 플랜대로 가보자")
- **Phase 0 (PD 숙제, 최우선·저는 Studio 불가):** 커뮤니티 가이드 스트라이크 / Shorts 노출·수익화 플래그 확인.
  플래그 있으면 콘텐츠 수정 무의미 — 그걸 푸는 게 전부.
- **Phase 1 ✅ 배포:** RF-only(음 신호 차단). 낙제 슬롯은 이미 빈 슬롯(dud 발행 안 함).
- **Phase 2 (대부분 라이브, Bet 3 10/12):** 브레이크아웃 아키타입 6종·사건주도 hook-score·reach-우선 패키징·
  트렌드 피드. 회복기엔 기본 바로 승격. ⚠️공백=AV 접으며 *강제 트렌드-라이딩*(AV 전용) 소실, RF는 footage-fit만.
- **Phase 3 (2~3주):** 매주 `intake_report`로 채널 조회 추세. 성공=4자리 등반 or 브레이크아웃>1k.
  **트립와이어=~3주 진짜 RF에도 ~200 평탄 → Phase 4(harder reset) 포크.** 10/22 2슬롯 컷 효과는 아직
  데이터 無(Analytics 10/23까지, 첫 진짜 2슬롯 발행 10/24~) — 판단 이르니 성급히 안 바꿈.
- **Phase 4 (중기, 트립와이어 시):** 95.6% Shorts 의존 다변화(롱폼/검색-SEO) 또는 신규 채널/포맷 피벗(큰 PD 결정).

## NEXT
- PD: Phase 0 Studio 확인(결과에 따라 플랜 분기).
- CLI/board: 매주 `python -m scripts.intake_report --days 21 --slack`로 추세 체크(월요일 bandit 리포트에 합치는 것도 후보).
- 10/31 할로윈 AV 첫 특별일 배정 스팟체크. ~3주 후 트립와이어 판정.

[[production_model_v2_senior_director]] [[view_data_concrete_hook_title]] [[growth_program_2026-10]]
