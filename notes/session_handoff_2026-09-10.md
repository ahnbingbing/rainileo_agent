# Session handoff — 2026-09-10 (TikTok 확장 + impact-edit 3-arm + RF-heavy 슬롯 전환)

**스파인:** 이 세션 = 두 개의 전략 확장(TikTok 이중발행 · impact-edit 3-arm 편집문법)과
하나의 라이브 프로덕션 변경(RF-heavy 슬롯 믹스). PD가 3 편집문법 룩을 승인("굿")했고,
슬롯 믹스는 **킬스위치와 함께 라이브**, Phase B(문법을 프로덕션 RF에 배선)는 **시작됨** —
엔진 일반화(B1) 완료, 프로덕션 배선(B2~B5)은 코드 스케치까지 준비하되 **라이브 배선 안 함**
(자동생성 출력을 PD가 검증하기 전엔 RF 렌더 경로를 반쯤 자동으로 못 건드림).

> PD가 졸려서 컨펌 없이 진행 요청 → **안전 원칙: 자는 동안 라이브 채널 깰 수 있는 건 안 건드림.**
> 그래서 오늘은 B1(standalone, 프로덕션 무영향)만 완성 + 나머지는 이 핸드오프에 정확히.

## VM authoritative · push=deploy
- **VM HEAD**: `e940f2e` + 이 세션 마지막 커밋(B1). `git push origin main` → deploy.timer 2분 폴.
- 하루 스케줄(KST): 03:00 launch_selfheal / 09:10 slot_topup / 09:40 pd_reviewer (변경 없음).

## SHIPPED — 라이브 (커밋순)
1. **`02e4741`** — 일일 슬롯 믹스 **2av+2rf → 3rf+1av**(RF가 도달에서 우세). `day_assignments`
   재작성: AV 1개가 타임슬롯 4일주기 순환, RF가 나머지. **★롤백 킬스위치 `LAUNCH_LANE_MIX`**
   (기본 `3rf1av` → `2av2rf` 세팅 시 옛 라틴스퀘어+BANDIT_STEER 즉시 복귀, 재배포無). 옛 경로
   `_assign_latin_2av2rf`로 보존. change-impact 확인: 호출처 5곳(launch_pipeline·launch 765·
   slot_topup·launch_selfheal·pin_episode) 전부 반환 리스트 동적 순회 → 자동 상속(2av 가정 없음).
   렌더 계약(AV/RF·Writer→Director→cameraman→burn→Giri) 무변경. 회고 §4.5 **D_lanemix**.
2. **`e940f2e`** — impact_edit Phase-0 프루프 엔진 + 계획문서 + 회고 D_lanemix.
3. **(B1 커밋)** — impact_edit 엔진 일반화(아래).

## 결정 잠금 (재논의 불필요)
- **TikTok**: Phase 1 **인박스(드래프트) 모드**(완성 mp4+캡션 PD 드래프트함 자동 push→원탭 발행,
  심사 불필요) · 캡션 **둘 다**(packaging 틱톡 arm + 번인 세이프존) · **4편 전부**. **주말 구현.**
  ★PD 숙제=developers.tiktok.com 앱 scope `video.upload` + Client Key/Secret → .env. 전체
  설계 `notes/tiktok_expansion_plan.md`.
- **전략 분기**: 틱톡=임팩트 숏츠 / **유튜브=롱폼**(story 페이오프-선공개가 씨앗).
- **슬롯 믹스**: 3RF+1AV **런칭-월 고정** → 이후 edit_grammar 밴딧.
- **edit_grammar 매핑**: velocity/meme/story를 RF 3슬롯에 **한 달 고정** → 밴딧.

## impact-edit 3-arm (PD 승인 "굿", v5) — `scripts/impact_edit.py`
- **velocity** 15.8s: 고속 hue 연속순환(색변환 frequency 빠르게)+**원본색↔클럽색 씬 교대**, 드랍 라이저.
- **meme** 16.1s: 16컷 점프컷+줌펀치4+프리즈2+휘익, **KO/EN 이중언어**(상단 마진 확보, 한자 제거
  =甲은 ffmpeg 두부렌더).
- **story** 33.4s: 페이오프-선공개(클라이맥스 콜드오픈→되감기→인과 빌드→복귀) + **OpenAI nova 뉴럴
  TTS 내레이션**(씬 window에 atempo 맞춤=겹침 방지) + 인과서사(랴니 밖에 나가 축구).
- CLI: `.venv/bin/python scripts/impact_edit.py --grammar velocity|meme|story|all`.
- 신규 프리미티브: colorbalance/hue-cycle 클럽색·`render_freeze`·`_gen_sfx`(boom/ding/riser/whoosh)·
  `_tts`(OpenAI→macOS say 폴백)·bilingual `_draw`·`assemble`(seq/caps/music/sfx/voices 일반).

## Phase B 진행 (edit_grammar → 프로덕션 RF)
### ✅ B1 완료 — 엔진 데이터-드리븐화
- `build_velocity/meme/story(music, out, clips=None)` + `render_grammar(grammar, out, clips=, music=)`
  추가. `clips`=역할→asset_id **또는 파일경로**(`_resolve_clip`이 둘 다 처리). 기본=프루프 footage.
- **역할 키 = 에너지/기능 슬롯**(콘텐츠 리터럴 아님): soccer=클라이맥스/고모션 페이오프,
  play1/play2=중에너지 상호작용, swim=고모션, belly=느린 "뷰티"/차분 앵커.
- 검증: 회귀(CLI 기본=15.8s 동일) + 주입 스모크(역할 치환 clips=15.8s 정상). 프로덕션 무영향.

### ⏳ B2~B5 남음 (라이브 배선 X — PD가 자동출력 검증 후에)
- **B2 스키마+배정**: RF 컨셉에 `edit_grammar` 필드. `launch.py`가 RF 3슬롯에 velocity/meme/story
  **고정 로테이션** 배정(EDIT_GRAMMAR 플래그, 기본 OFF). producer가 `draft["edit_grammar"]` 저장
  (packaging_arm 미러). Seam: `producer.py`~3206(RF 컨셉 파싱)·`producer.py:4690`(draft 저장)·
  `launch.py` day_assignments/_slot_pipeline.
- **B3 cameraman seam**: 문법 RF는 엔진이 trim→burn→assemble **꼬리를 대체**(문법은 "컷1=클립1+캡션"
  모델과 다름 — 적은 소스로 다수 비트컷/색/SFX). Seam `cameraman.py`~6143(캡션 그라운딩 후·번인 전).
  RF 컨셉의 선택 클립 → `render_grammar` clips dict로 매핑(모션량으로 energetic/beauty 분류). 게이트=
  `concept.get("edit_grammar")` AND EDIT_GRAMMAR 플래그. OFF면 기존 경로 그대로(무영향).
- **B4 Writer (★진짜 일 = 품질 게이트)**: 지금 엔진은 **프루프 TEXT 하드코딩**. 에피소드마다 문법별
  카피 생성 필요 — velocity 짧은 훅 3캡션·meme 펀치라인+EN·story **인과 내레이션 대본**+조밀 캡션.
  프롬프트 `agents/prompts/writer_realfootage.md`·`caption_agent.md`. 반복+PD 리뷰 필요.
- **B5 검증→ON→밴딧**: 문법별 실footage 검증 → EDIT_GRAMMAR ON → (한 달 후) edit_grammar를
  4번째 밴딧 arm으로(packaging_arm 패턴 미러: `bandit.py` 컬럼+analyze+choose / producer 저장 / collect).

### ★ 최대 열린 리스크
**자동생성 카피(Writer)+동적선택 footage가 프루프 퀄을 버티나?** B1은 **엔진**이 일반화됨을 증명
(아무 클립에서나 돌아감). 품질은 **B4**에 있음. **권장 다음 스텝: 실제 RF 컨셉의 클립을
`render_grammar`에 넣고 Writer 생성 캡션으로 한 편 돌려 눈으로 확인 → 그 다음에야 launch/cameraman
배선.** 순서 = de-risk(실footage 드라이런) → B2/B3(플래그 뒤 배선) → B5(플립).

## 롤백/안전
- 슬롯 믹스: `LAUNCH_LANE_MIX=2av2rf`(즉시, 다음 배치부터). self-heal/slot_topup은 day_assignments
  단일 진실원 상속 → 같이 롤백(검증됨). ★VM에서 세 cron이 같은 env 소싱하는 곳(run_job.sh)에 세팅.
- Phase B는 **inert**(엔진 import 가능하나 프로덕션서 아무도 호출 안 함).

## ★ NEXT (우선순위)
1. **다음 03:00(KST) 첫 3RF+1AV 배치 스팟체크** — 슬랙 써머리 `RF/RF/RF/AV`, RF 풀이 하루 3편 감당,
   AV 슬롯 순환. 이상하면 `LAUNCH_LANE_MIX=2av2rf` 롤백.
2. **Phase B**: B4 Writer 카피가 크럭스 → 실footage 드라이런(de-risk) 먼저 → B2/B3 플래그 배선 → B5 플립.
3. **TikTok 주말** — PD dev-app 크레덴셜 나오면.

관련: `notes/impact_edit_plan.md`(Phase 1 상세)·`notes/tiktok_expansion_plan.md`·회고 §4.5 D_lanemix.
