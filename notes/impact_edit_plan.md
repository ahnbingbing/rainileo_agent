# Impact-Edit 방향 — 편집 문법 3-arm MAB (계획서)

> 상태: **Phase 0 (룩 검증 중)** · 시작 2026-08-07 · 최종수정 2026-08-09
> 관련 코드: `scripts/impact_edit.py` (진짜 재편집 엔진), `scripts/edit_style.py` (폐기된 후처리 필터 — 반면교사)
> 관련 밴딧: `agents/bandit.py` (edit_style를 4번째 arm으로 추가 예정)

## 왜 (데이터 근거 진단)

업로드 121편 성과 분석 + 2026 유튜브 알고리즘 조사 결과, 우리 병목은 **리텐션이 아니라 도달**이다.

- **리텐션은 건강함** — RF 63.5% / AV 74.5% 평균. 보기 시작하면 끝까지 본다.
- **조회수는 정체** — 6월 중앙값 184 → 7월 159 (48h). 돌파(breakout)가 안 난다.
- **알고리즘이 실제로 보상하는 것**: 초반 리텐션 > 완주 > **재시청(rewatch)** > 공유. 재생률 15%↑ = "더 넓게 밀어라" 트리거.
- 🔥 **길이 25~40초 = 죽음의 구간**. 15~20초(단일 컨셉) 또는 45~58초(스토리)가 먹힌다. **우리 영상 ~25초 = 정확히 최악 구간.**
- **첫 시청 85% 무음 · 조회 74% 비구독자** → 1프레임 비주얼+텍스트 훅이 전부.
- **컬러 그레이드는 곁다리** — "모바일용 대비·채도 살짝"이 전부. 임팩트는 컬러가 아니라 **구조·편집 문법·길이**에서 온다.

## 핵심 교훈 (PD 피드백으로 확정)

1. **후처리 필터 = 실패.** 완성된(잔잔한 펫 다큐로 조립·자막 구워진) 영상 위에 컬러+줌을 덮는 건 "편집"이 아니다. 음악부터 잔잔하고 컷 리듬도 스토리용이라 뭘 덮든 필터티만 난다. → `edit_style.py`는 폐기, 반면교사로만 보존.
2. **임팩트 편집은 원본 컷부터 재편집해야 한다** — 자체 음악·컷 리듬·길이·자막.
3. **한 처리를 처음부터 끝까지 균일하게 = 틀림.** 편집은 구간마다 달라야 한다(빌드→드랍).
4. **클럽 = 컷마다 색이 바뀐다**(클럽 조명처럼). 등속 비트를 처음부터 끝까지 = 클럽도 코미디도 아님.
5. **"이게 숏츠에서 먹히냐"는 아무도 모른다** → 그래서 **MAB로 실측**한다. 내가 한 방향을 답인 양 들이밀지 않는다.

## 무엇을 만드나 — 편집 문법 3종을 MAB arm으로

세 가지를 각각 **원본 클립에서 진짜로 재편집**하고, `edit_style` 밴딧 arm(4번째 차원)으로 붙여 실제 조회수·재생률로 승자를 뽑는다.

| arm | 길이 | 편집 문법 | 차별 축 |
|---|---|---|---|
| **A. velocity (=클럽)** | 15~20s | 비트에 컷, 드랍에 속도램프(슬로우→스냅), **컷마다 컬러 체인지**, 플래시 트랜지션 | 고에너지·재시청 |
| **B. meme / reaction** | 15~20s | 점프컷, 큰 키네틱 자막(말풍선·펀치라인), 효과음 스팅, **줌은 웃긴 순간에만** | 코미디 타이밍 |
| **C. story / immersive** | 45~58s | 느린 빌드, ASMR/satisfying, 심리스 루프 | 긴 포맷 실험 |

### 공통 뼈대 (전 arm 강제)
1. **1프레임 비주얼+텍스트 훅** (무음·비구독자 대응)
2. **1.5~3초마다 변화** (컷/줌/자막/효과음 — 4초 정적 금지)
3. **페이오프 −3~5s + 심리스 루프** (재생률 견인)
4. **키네틱 자막** (1~4단어, 세이프존: 하단 15%·상단 10% 회피)
5. **사운드 디자인** (휘익/라이저/베이스히트, 음악 −8~−12dB 덕킹)

## 어디서 도나 (운영 컨텍스트)

- **프로덕션은 GCP VM `rianileo-brain`** (전 cron·봇·DB). 배포=`git push origin main`. 운영 진실=VM/라이브,
  로컬 `data/agent.db`는 stale. 상세: [gcp_migration_plan.md](gcp_migration_plan.md).
- **Phase 0(현재)만 Mac 로컬** — 룩 프루프 빠른 반복(PD 승인). 이건 개발이지 운영 아님.
- **Phase 1**(밴딧 배선)은 **VM 파이프라인에 태운다** — `assemble_episode.py`/`launch.py`/`bandit.py` 수정은
  push=배포로 VM에 반영, 로컬 렌더 아님.

## 엔진 — `scripts/impact_edit.py`

Phase 0은 손-구동(Mac 로컬 빠른 반복, PD 승인). 구현/구현예정:
- **모션 윈도 자동선별** — 각 클립 프레임차로 가장 다이내믹한 구간만 채택(블라인드 추측 X). *velocity 구현됨.*
- **비트 그리드** — `edit_style.estimate_tempo_phase` 재사용(scipy 온셋/템포, librosa 불필요).
- **컷마다 색 변화** — hue 로테이션. *구현됨(강도 튜닝 필요 — 현재 다소 셈).*
- **속도 램프** — 슬로우 비트(3비트)→드랍 버스트(1비트). *구현됨.*
- **키네틱 자막** — Pretendard-Black, 세이프존. *구현됨(팝 애니메이션은 ffmpeg 세그폴트 회피로 일단 고정크기 — 복구 예정).*
- **사운드 디자인** — 현재 베이스드랍 1개. *컷 휘익/라이저 추가 예정.*
- ⚠️ **ffmpeg 세그폴트 gotcha**: `concat -c copy`의 불규칙 타임베이스 + 다중 drawtext 체인 = SIGSEGV(evermeet 빌드). → concat을 **클린 CFR 재인코딩**(`fps=30,setsar=1,format=yuv420p`)하고 자막은 **패스 분리**로 회피.

### 음악 제약
우리 BGM 93곡은 대부분 잔잔한 펫뮤직. 비트 후보: ibiza chill-house(117bpm)·eat-me(123)·whistle-dance(144). **진짜 클럽/EDM 트랙은 소싱 필요할 수 있음.**

## Phase 1 — 밴딧 배선 (룩 승인 후)

`packaging_arm`과 동일 패턴(초기 라운드로빈 → Thompson):
- `agents/bandit.py`: `analyze()`에 `("edit_style","edit_style")` level 추가 + `choose_edit_style()` + `edit_style` 컬럼 마이그레이션 + `collect()`에서 `payload_json.draft.edit_style` 추출.
- `agents/launch.py`: 배치가 매 에피소드에 edit_style 배정.
- `agents/cameraman.py` → `scripts/assemble_episode.py --edit-style`: choke point에서 문법 적용(RF·AV 공용).
- `agents/producer.py`: `payload_json.draft.edit_style` 저장.
- **보상**: 기존(조회수·리텐션)에 **재생률(replay-rate)** 가중 추가 검토 — 루프 설계의 실제 효과 포착.
- **안전장치**: 데이터 희소 시 아무것도 stabilize 안 됨 → 라운드로빈 유지(빈 슬롯 안 생김).

## 현재 상태 / 열린 결정

- ✅ velocity 프루프 1편 렌더·전달 (`data/output/style_demo/velocity_proof.mp4`, 15.8s).
- ⏳ PD 판단 대기: 방향 OK? hue 강도·컷 속도·자막·음악 조정?
- ⏳ meme·story 그램마 미착수(같은 엔진).
- ⏳ Phase 1 밴딧 배선 미착수.

## 출처
- taletok(데이터): https://taletok.io/blog/how-to-make-viral-youtube-shorts/
- vortexxcel(편집가이드): https://vortexxcel.com/youtube-shorts-editing-guide/
- CapCut velocity 트렌드: https://www.capcut.com/explore/latest-velocity-trend-2026
