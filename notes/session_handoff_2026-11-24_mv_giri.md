# Session handoff — 2026-11-24 · MV 드리프트 근본해결 (MV Giri 빌드) + mv03 공개

**한 줄:** MV E2E 3편째(mv03 「첫눈 오는 날」, 셔플 캐롤)를 완주·**공개**(`WIITFUkf8pY`)했고, 그 과정에서 PD가
핵심 통찰을 줬다 — *"단건 case-by-case 교정이 일반화 안 돼 매번 드리프트. 에이전트가 일반적으로 잘 만들게 근본을
바꿔야 한다."* → **MV 레인에 체커(Giri)가 없던 게 근본**이라 보고, 일상 레인의 generator+checker lockstep을 MV에
이식했다: `agents/mv_reviewer.py`(MV Giri, 결정론 게이트) + `agents/mv_principles.py`(공용 원칙 1소스). **전 MV
파이프라인을 처음으로 커밋+배포**(#3 결정남). 이전 핸드오프 = `notes/session_handoff_2026-11-23_mv_pipeline.md`.

---

## 지금 상태 (전부 커밋+푸시 완료 — 로컬 미커밋 없음)

- **커밋 2건 main 푸시 완료** (VM deploy.timer가 pull→smoke→봇 재기동):
  - `2954319` feat(mv): 전 MV 파이프라인 첫 커밋 + 세션 품질수정
  - `6a2703a` feat(mv): MV Giri (결정론 리뷰 게이트 + 공용 원칙)
- **mv03 「첫눈 오는 날」 공개**: `WIITFUkf8pY` (public, 즉시공개). 제목 "폴짝! 랴니는 눈밭을, 레오는 전나무를
  타다?! ⛄️" · 크레딧 작곡 라레수·작사 함미하비. 수동 업로드라 VM DB card 없는 orphan(측정/반딧 비추적).
  업로드 중 SSL write timeout 떴지만 서버엔 정상 수신됨(먼저 `videos.list`로 확인 후 중복 안 냄).
  `data/tmp/mv/mv03/manifest.json` = 진실(stage=qc, youtube_video_id 기록).

---

## ★다음 세션 메인 할 일 (PD 지시, 이번에 인터럽트된 것)

**MV 한 편 더 E2E로 돌려서 리뷰어 자동검수가 실제로 작동하는지 확인.** 목적 = 새 slug를 **손 안 대고** 돌렸을 때
`mv_reviewer`가 still_qc/final_qc에서 결함을 선-검수해 PD가 반복 결함을 안 잡아도 되는지 증명(= 이번 세션 빌드의 E2E).

```
.venv/bin/python -m agents.mv_producer --slug mv04              # discovery까지 → concept_pick 게이트
# PD pick 또는 새 theme: --pick-concept N --reason "..." / --no-resume --theme "..."
```
- **확인 포인트:** still_qc 게이트에서 PD 확인 메시지에 `:rotating_light: [MV리뷰] …` 라인이 뜨는지(결함 있으면).
  결함 없으면 안 뜸(정상). 일부러 결함 만들어보고 싶으면 manifest 손봐서 리뷰어가 캡하는지 봐도 됨.
- **MV_GATES 기본=all pd**라 게이트는 여전히 PD 확인에서 멈춤 — 리뷰어는 그 PD 게이트에 **결함을 미리 붙여** 보여준다
  (PD가 찾는 게 아니라 확인만). 완전 자율 테스트는 `MV_GATES=all_auto`(그럼 리뷰어가 통과/반려를 자동 판정).

---

## ★이번 세션이 지은 것 (근본해결의 전모 — 회고 §4.11 참조)

### 1. MV Giri = `agents/mv_reviewer.py` (신규)
`review(m, past_themes=) → {점수, 판정, issues[], 최종_결정}` (일상 reviewer와 같은 verdict 어휘). 결정론 게이트:
- **클립 재탕** — 한 실 클립이 ≥2컷에 쓰이면 cap≤5
- **RF 설정 불일치** — 가사=눈/실외인데 매칭 클립 scene=실내-only("흰 담요를 눈이라") cap≤5
- **캡션 커버리지 싱크** — 각 컷이 제 가사줄 불리는 동안 화면에 있나(start==start 아님, 커버리지) cap≤6
- **솔로 컷 딴 펫** — subject=leo/ryani인데 프롬프트가 개+고양이 둘 다 지칭 cap≤5
- **컨셉 신선도** — 과거 MV 테마와 토큰 중첩 ≥50% cap≤6
- killsw `MV_REVIEW_{DIVERSITY,SETTING,SYNC,SOLOPET,FRESHNESS}=0`. VLM층(`MV_REVIEW_VLM=1`)=미배선 스텁(다음 과제).
- 회귀검증: shipped mv03 = 업로드 10/10 · 주입결함 = 수정필요 5/10.

### 2. 공용 원칙 = `agents/mv_principles.py` (신규)
한 원칙이 **두 얼굴**: `MV_PRINCIPLES`(프로즈, 생성기 주입) + `check_*`(결정론, 리뷰어 집행) → 생성기-수정과
체커-수정이 한 파일이라 다시 안 갈라짐(드리프트 구조적 원인 제거). 7개 원칙(재료-진실 그라운딩·다양성·피사체 스코프·
캐릭터 락·싱크·지정값 존중·디자인 1급). discovery/songwriter/director가 이걸 주입(`+ MV_PRINCIPLES`).

### 3. `_auto_judge` 배선 (`agents/mv_producer.py`)
`_REVIEW_GATES={still_qc, final_qc}`에서만 리뷰어 실행(나머지 concept/song/title은 PD 취향픽이라 패스).
auto 모드=`mv_reviewer.passed()` 판정 / PD 모드=리뷰어 선-실행해 결함을 게이트 메시지에 `:rotating_light:`로 표면화.

### 4. 생성기 근본수정 (이번 세션 case-by-case 교정 → 일반화)
- **mv_discovery**: 시즌 시드 날짜기반 `_season_hint`(할로윈 하드코딩 제거)·`past_mv_themes` 실제 populate
  (`mv_producer._past_mv_themes`가 sibling manifest 스캔) → 컨셉이 매번 비슷/비시즌 안 됨.
- **mv_songwriter**: concept의 `music_genre` 존중(무시하던 버그)·후렴=기억에 남는 훅 원칙.
- **mv_still**: 랴니 아이덴티티에 **슬렌더 얼굴**(가는 볼·주둥이, 스탠다드 프불 볼 거부) 강화·scarf `art_direction`
  **피사체 중립**("강아지와 고양이 둘 다"가 솔로컷에 개 호출 → "각 펫만, 딴 동물 추가 금지").
- **mv_title_card**: `make_snow_card`(눈 쌓인 글자·빨간모자 눈사람)·`make_snow_title_anim`(눈송이 낙하 알파 mov)·
  `auto_card`가 겨울/캐롤 스타일을 눈 카드로 라우팅. **make_mv**가 영상 타이틀(알파 .mov) 오버레이 지원.
- **_fal_v2v**: 프롬프트 테마 선택(`snow`/`halloween`, env `FAL_V2V_THEME` 또는 argv3)·피사체보존 유지.

### 5. 결정론 버그 2건 (리뷰어가 앞으로 잡음)
- **캡션 싱크** = `set_cut_durations`가 곡 인트로(보컬 전 ~3s)를 첫 컷에 안 넣어 컷 시퀀스 전체가 캡션보다 3초 앞섬.
  Fix = `starts[0]=0`(첫 컷이 인트로 흡수) → 이후 컷이 karaoke 라인에 telescope 정렬.
- **RF 다양성** = rf_match에 다양성 제약 없어 최고점 클립 재탕 → `exclude_assets`로 클립당 1컷.

---

## ★MV 리뷰어 작동 모델 (다음 세션이 알아야)

- **지금은 결정론 manifest 체크만**(VLM 없음, 0비용). 캐릭터 fidelity(랴니 무꼬리/슬렌더)·솔로컷 동물수의 **시각**
  검증은 `MV_REVIEW_VLM=1` 스텁(`_vlm_still_checks`, `agents.openai_vision`로 배선하면 됨) — **다음 선택과제**.
- 결정론 체크가 생성-시점 근본(프롬프트가 둘 다 지칭 등)을 잡으니 대부분 커버. 모델이 깨끗한 프롬프트에도 드리프트하면
  VLM층이 필요.

---

## ★하이브리드 RF/AV 교훈 (mv03에서 확립 — 다음 MV에도 적용)

- **로컬 인덱스에 '양펫+특정 세팅(눈/창가)' 실 footage가 없으면**(레오 2025-11-15 구조라 양펫 눈 영상 자체가 없음;
  VLM이 2016 양펫 후보 내놔도 환각) **억지 RF 금지 → AV 플립**. mv03은 Leo-창가·양펫-창가 6컷을 AV로 플립(v2v 가짜눈
  폐기), 진짜 매칭되는 2016/2017 랴니 솔로 눈산책 4컷만 RF 유지. 결과 RF4/AV10, 전 컷이 가사와 일치.
- **fal.ai v2v 가짜눈은 드리프트 심함**(PD 반려) — 창밖 눈은 실제 눈 footage나 AV 생성으로. v2v는 MV 시즌필터용이지
  "없는 눈 만들기"엔 부적합.

---

## 운영 주의 (변동 없음)
- 키는 로컬 `.env`, `load_dotenv(ROOT/".env")` 명시경로. 실행 = `.venv/bin/python`, `python -m scripts.xxx`.
- 공개 업로드 = 되돌리기 어려움 → PD 명시 승인 있을 때만(mv03은 "바로 올리자" 명시).
- **board(자율 executor)가 main에 동시 커밋 중** — 이번 세션에도 board가 HLG 밝기 npl 근본수정(`277f94b`·`00cb8ce`·
  `174780e`)을 커밋했다(PD "후반부 RF 밝기" 와 수렴). 커밋/푸시 전 `git fetch` + `rev-list --count main...origin/main`로
  동기 확인. make_mv의 npl=600 하이라이트 압축은 board 작품 — 건드리지 말 것.
- 살아있는 메모리 = `mv_pipeline_build`(갱신됨: 커밋+Giri), `giri_pd_mirror_build`, `cli_quality_drift_root`,
  `mv_character_consistency_lock`. 회고 = §4.11(MV Giri). 피드백 원장 = `notes/mv_agent_feedback_log.md`.

## 다음 세션 할 일 (우선순위)
1. **mv04 E2E로 리뷰어 자동검수 증명**(위 커맨드) — 손 안 대고 돌려 리뷰어가 선-검수하는지.
2. (옵션) `mv_reviewer` VLM층 배선(`MV_REVIEW_VLM`, 솔로 동물수·랴니 무꼬리 프레임검증).
3. (옵션) `MV_GATES=all_auto`로 완전 자율 MV 한 편(VM cron 목표의 리허설).
