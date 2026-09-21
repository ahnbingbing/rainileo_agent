# Session handoff — 2026-09-21 (velocity/meme/story 품질·음악 전면개편 + Lyria 음악생성 파이프라인 / 그리고 9/23 빈배치 인시던트)

**스파인:** PD "요즘 클럽(velocity)·meme 버전 허접"에서 시작해, 프레임으로 파보니 근본이 전부 **형태(form)** 문제였다 —
편집 문법은 footage에 형태를 강요하고, footage가 그 에너지를 못 받치면 형태가 싸운다. 세션 후반 PD 요청으로 **문법별 음악을
Vertex Lyria로 생성**하는 파이프라인을 깔고 velocity/meme/story 3문법 전부 라이브에 붙였다. 관통 교훈: **라이브러리 공백은 새
의존성이 아니라 이미 깔린 인프라로 메운다(Lyria=Veo용 Vertex 재활용)**, 그리고 **타이밍이 종속인 두 스트림은 가변적인 쪽에
고정적인 쪽을 맞춰라(그림을 말에 맞춤 — story 음성 버그).**

## VM authoritative · push=deploy
- **VM HEAD `9b59dc5`** (deploy.timer 2분 폴 정상, 세션 중 전 커밋 pull 확인). 하루 스케줄(KST, crontab):
  03:00 launch_selfheal / 09:10 slot_topup / 09:40 pd_reviewer(APPLY=1) / */30 ytcache. LAUNCH_LEAD_DAYS=2.
- 로그: `data/logs/cron.*.log`. VM에 **sqlite3 CLI 없음** → python으로 DB 조회. repo=`/home/rianileo/rianileo-agent`(유저 rianileo,
  `sudo -u rianileo` + `git -c safe.directory` 필요).

## SHIPPED (velocity/meme/story 품질·음악, 전부 VM 검증)
근본은 회고 §4.4 **C_grammarfit**(①~⑤)에 principle-first로 통합. 커밋 순서:
1. **velocity 클럽 색**(`888ffdc`) — 죽어있던 `CLUB[]` colorbalance 테이블 배선. hue 전체회전(개·고양이가 통째로 초록/마젠타로
   뒤집힘, PD 9/8-9 거부)을 폐기, 컷별 `club_cast` 네온 라이트캐스트로 피사체 가독 유지·컷마다 색 교체 스트로브.
2. **footage-fit 게이트**(`53faf06`) — `clip_motion_peak` 스칼라(달리기 23-30·냄새 8-14·낮잠 6)로 velocity=고모션 climax≥16 +
   build≥2클립≥11, meme=반응≥11 요구. 못 넘으면 그 문법 **스킵→표준RF 폴백**(빈슬롯 없음, 3 캐스트경로 전부). story 무게이트.
   D_b4copy "form은 footage가 받쳐야 강함"을 모션 게이트로 일반화.
3. **meme arc 그라운딩**(`29f216c`) — 정적 "다른 펫 등장/부인" 지시가 없는 레오를 날조하게 강제하던 근본 → 그라운딩된 캐스트
   subject-union으로 blame/solo arc 분기. 엔진(build_meme 7슬롯) 무변경.
4. **meme SFX**(`cdc9323`) — 테스트톤 → 제대로 된 DSP(하강 피치 처프 붐·비화성 벨·상승 스윕, **스펙트로그램 검증**) + 실샘플
   오버라이드 `assets/sfx/<kind>.*`. 줌펀치는 이미 모션정렬.
5. **meme 캡션 variation**(`f7ebe15`) — `_MEME_BEATS`가 스톡 문구(?!?!·현행범 포착·또?!)를 예시로 못박아 매 에피 동일하던 근본 →
   비트를 **기능(function)**으로 재서술 + Writer에 밈 레지스터 메뉴(자막예능/짤방/다큐패러디/채팅체/과장감탄) + "매 에피 새로·스톡
   금지·클립 그라운딩"(edit_grammar_copy.md §4 통합, prompt-authoring 경유). dry-run 2회 = 완전히 다른 그라운딩 캡션·스톡 0.
6. **story 음성 3버그 + 잘림**(`cbe1248`) — PD "스토리↔자막 안 맞음·말 갑자기 빨라짐·말 겹침". 근본=assemble이 비디오 타이밍을
   **먼저 고정**하고 내레이션을 욱여넣음(긴 라인 1.7x speed-up·오버런→다음 라인 밀림 desync·probe실패 dur오추정 겹침). Fix=
   **그림을 말에 맞춤**: `_prerender_and_size`가 내레이션 먼저 TTS·측정→씬 target을 dur+0.7로 키움. speed-up 캡 1.7→1.15. +별도
   버그: **Lyria 트랙 ~32.8s인데 story body 44s+** → atrim+`-shortest`가 비디오를 음악 길이로 잘랐다(32.7s payoff 끊김) → BGM
   `-stream_loop -1`로 전체 커버(velocity/meme<32s 무해).

## SHIPPED — Lyria 음악 생성 파이프라인 (`scripts/gen_music.py`)
- 라이브러리 93트랙에 클럽 뱅어 0 + CC0 소싱은 청취불가·CDN 로그인게이트(Pixabay/Mixkit 403). → **Vertex Lyria(`lyria-002`)로 생성**:
  Veo와 같은 GCP 프로젝트(새 키·의존성 불요), 32.8s 48kHz stereo/콜. 문법별 브리프(velocity=explosive big-room festival hype /
  meme=bouncy trap 808 hype / story=warm cinematic cozy). `--count N` variant, `--promote`.
- **컨벤션 오버라이드**(`impact_edit._grammar_music`): `assets/bgm/<grammar>_music.mp3` 있으면 그 문법이 코드·env 없이 그걸 씀.
  3문법 다 promote(velocity 129BPM 클럽·meme 밈비트·story 시네마틱), VM에서 각 `*_music.mp3` resolve 확인.
- **영구저장**(PD "만든 음악은 계속 저장"): 모든 생성물이 `assets/bgm/generated/` + `manifest.jsonl`(프롬프트/시드/날짜)에 아카이브.
  **git-추적**(`.gitignore`: `assets/bgm/*` 무시 + 컨벤션·`generated/**` 재포함) → git push가 영구저장이자 VM 배포(기존 93트랙은
  여전히 무시, bgm 첫 바이너리 커밋). `assets/sfx/`는 아직 gitignore(실샘플 드롭시 별도 전달).
- ★후속(PD): 음악 "좀 더 흥"은 브리프 더 밀어 재생성→`--promote`(archive에서 스왑). 프롬프트 정교화 여지.

## ★ 인시던트 — 9/23 배치 0/4 전멸 (미해결, 다음 세션 1순위)
PD "9/23 아무것도 생성 안됐잖아". 확인: 카드 09-21=7·09-22=5·**09-23=1**. 9/21 03:00 launch_selfheal(LEAD_DAYS=2 → 9/23 생산)이
**성공 0/4**: 08:00 RF `no_concept`·12:30 RF `no_concept`·18:00 AV `giri_fail`·21:00 RF `no_concept`. 배치 5400s 벽시계 캡 도달(라운드2 미시작).
로그 `data/logs/cron.launch.log`(04:31, 3.7MB), 아티팩트 `realfootage_2026-09-23_20260921_043135.json`(0 cuts).

- **★내 이번 세션 배포는 근본 아님(확인됨):** 배치 트레이스백 전수 grep에 `impact_edit|grammar_slot|gen_music|edit_grammar_writer|
  _grammar_music|clip_motion_peak|_prerender|stream_loop|footage-fit` **0건**. 실패는 표준 RF(no_concept·0 cuts)·AV Seedance(giri_fail)로
  내가 안 건드린 경로. grammar/음악 변경과 무관.
- **징후:** "신선 클립 부족(5<8) 쿨다운 자동완화(75일 31개 재-admit)" 후에도 단일-패스 0 cuts / Anthropic truncated→max_tokens=24000 재시도 /
  트레이스백 40+개(내용 미확인 — RF no_concept 근본 규명 필요). 이건 반복되던 **RF 얇은 신선풀 + writer 0 cuts + AV Giri fail** 패턴으로
  보이나 0/4 전멸+타임아웃은 이례적 → **다음 세션에서 트레이스백 실내용 확인**(159129 등)해 근본 규명.
- **복구:** 9/23은 9/21 배치만 시도(9/22 배치는 9/24 생산) → 정규 배치로 재시도 안 됨. self-heal 6R 소진. **수동/self-heal 재실행 필요**
  (`launch_selfheal --slot` 개별, 또는 재배치). slot_topup(09:10)이 9/23을 채웠는지 미확인.
- **고아 1건:** `2026-09-21 21:00 KST BqRCRAHFkxA`(여름 산책 모음) 유튜브 예약됐는데 카드 없음 → `python -m agents.reconcile --veto` 정리 대상.

## ★ NEXT (다음 세션)
1. **9/23 빈 4슬롯 채우기** — self-heal/수동 재실행. 먼저 RF no_concept 근본 규명(트레이스백 내용 + 신선풀 상태 + writer 0 cuts).
2. **9/23 배치 0/4 전멸 근본** — 왜 4슬롯 다 실패+5400s 타임아웃인지(반복 RF 얇은풀인지, 새 회귀인지). 내 코드 무관은 확인됨.
3. **PD 스팟체크** — velocity/meme/story 첫 라이브 grammar(롤링윈도우)에 생성 hype/어울림 음악 + 색·캡션·음성 수정 반영분. 나쁘면
   `EDIT_GRAMMAR_MODE=0` 롤백(문법)·archive에서 음악 스왑.
4. 고아 `BqRCRAHFkxA` 정리(reconcile --veto).

cf. 회고 §4.4 **C_grammarfit**(①색 ②footage-fit ③meme arc ④SFX ⑤story 음성 + Lyria) · 메모리 [[grammar_form_fit_and_lyria_music]].
