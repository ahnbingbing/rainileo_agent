# Session handoff — 2026-11-11 PM (RF footage-aware 재설계 착수 + era-mix 게이트)

**스파인:** "era-mix 게이트 + 캡션 타이밍 + 렌더 9분" 3과제로 시작했으나, 캡션 문제를 파다가 PD가
근본을 재정의 → **RF 편집의 근본 재설계**(footage-aware Director/Writer/brainstorm/Title) 대화로
확장. 설계 문서를 쓰고 enabler(Track A)+brainstorm 그라운딩(B0)까지 **OFF로 구현·배포**. 다음 세션은
**A2 샘플 백필 → B0.5 스모크**부터.

## ✅ 오늘 배포 (전부 가역·main, 순서대로)
| 커밋 | 내용 |
|---|---|
| `e6edeea` | **era-mix 시점마커 결정론 게이트(생성기 쪽)**. `agents/era_mix.py` 공유모듈 추출(span/token) → reviewer `_temporal_grounding_gate` 거울-리팩터(바이트동등) + producer `_rf_structure_issues` 호출부에 `[시대혼합]` 재작성 피드백(`RF_ERA_GATE=0` 킬스위치). 11-21 아기레오 케이스 유닛검증. **checker는 이미 있었고(PD 6/23) 생성기가 빠져 리롤 미수렴이던 걸 메움** |
| `3909593` | 회고 C_eramixgen(§4.4): 검수기만 막으면 리롤 수렴 안함, 생성기가 같은 규칙으로 렌더 전에 막아야 |
| `cfd0f2b`·`299ab1b` | **설계 문서** `notes/rf_footage_aware_direction_design.md`(아래 요약) |
| `2d4864c` | **Track A 인에이블러(OFF)**: `video_actions.clip_knowledge()`(네이티브 영상+오디오→CK_SCHEMA, pd_notes 영상교차검증) + `clip_actions` `clip_knowledge` 컬럼/store/`knowledge_for`/`format_knowledge`, `USE_CLIP_KNOWLEDGE=0` |
| `1d5121b` | **B0 footage-grounded brainstorm(OFF)**: `_clip_knowledge_material`(그라운디드 클립카드)+그라운딩계약 principle-first 통합+`clips` 스키마+`_grounded_violations` 결정론 게이트(지어낸 컨셉 드롭)+`knowledge_or_synth` swap point. `RF_CONCEPT_BRAINSTORM=0` 유지, AV 무변경 |

## ★설계 요약 (notes/rf_footage_aware_direction_design.md — 다음 세션 필독)
**근본:** "RF 편집이 안 됨" = 캡션(story)과 화면(footage)이 **따로 결정돼 합의 레벨이 없음**. 렌더-끝
그라운딩 패치는 이길 수 없는 싸움(캡션↔화면 한쪽 맞추면 다른쪽 깨짐). 증거=11-21 cut2 전구간 랴니만인데
캡션 "레오는 호기심"(/tmp/capcheck 프레임으로 실측).

**4단계 파이프라인(전부 기존 자산 진화 — 새 에이전트 아님, §1.5 표):**
1. **clip_knowledge RAG**(enabler): Gemini 네이티브 영상+오디오 → 축약 summary + 소재 beats(material_type) +
   audio_events(레오 냐옹 off-frame) + **frame-truth(subjects_in_video) vs pd_notes(pd_verified.conflicts) 분리**.
   = `clip_actions`/`video_actions`/`asset_embed` 확장.
2. **brainstorm(그라운디드 ideation+관객)** → **Director(아크/시퀀스/컷역할, 기존 AV Director `writer_director.py`를
   RF 편집연출로 확장)** → **Writer(컷별 그라운딩+창의 캡션)** → **Title/Packaging(트렌디 최종제목, `channel_manager`
   승격, Director⇄Giri 중재)**.
3. **Director⇄Writer 양방향 bounded 루프**: 컨셉·제목 mutable, 의도=북극성, footage 합치까지 iterate(레오만
   사고뭉치면 제목도 바꿈).
4. **트림 coherence**: Writer 트림 → Cameraman authoritative(에디터 재트림 금지).

**역할 핵심(PD 교정들):** ①RF는 "있는 것" 그라운딩+그 한마리를 **창의적 해석**(flat 금지, 없는대상 금지)
②큰그림=Director/그라운딩=Writer 안 싸움(대조는 시퀀스로) ③제목=별도 **트렌디 Title 에이전트**가 소유
(Director는 이야기·연출, 올드OK) ④지표·트렌드·밈=Title 입력(`trend_feed`/`trends`/`active_trends` 재사용)
⑤brainstorm은 OFF로 **피하지 말고 뜯어고친다**(억지컨셉=ungrounded ideation, clip_knowledge가 치료).

**가드레일:** 선택 재구축 금지 · 9/20 "랴니 지워짐" 재발 금지(보임 vs 있었음 구분+양쪽회귀) · **빌드 OFF→
스모크→플립**(§5.7, v2 롤백 교훈) · Giri lockstep · 쓰기 멱등보존(`_VM_OWNED`).

## 🔴 다음 세션 — 바로 할 일 (우선순위)
1. **A2 샘플 백필 → B0.5 스모크** (PD가 "a" 선택 = **샘플 백필** 직전 핸드오프 요청). 최근 RF 풀 ~20–30클립만
   `clip_knowledge` 백필(소액 Gemini 비디오) → `USE_CLIP_KNOWLEDGE=1 RF_CONCEPT_BRAINSTORM=1`로 **B0.5 스모크**:
   ①출력 품질 ②★**forced-concept 치료 회귀**(과거 "산으로 간"=매복의달인/침묵의엄마 brief 재현 → 이제 안 나오나)
   ③후보 beats ⊆ 실재. 통과+PD 스팟체크 후에만 플립. **백필은 비용이라 전체 백필/플립은 PD 승인 후.**
2. **B1 Director RF 확장**: `run_rf_director` 사이드카(writer_director.py, 기존 run_director는 AV유지) →
   `_propose_realfootage_singlepass`에 `RF_DIRECTOR_PASS` 프리패스. 계획=이 핸드오프 세션의 백그라운드 에이전트
   산출(상세: director_brief={arc,working_title,edit_grammar,sequence[role],notes_to_writer}, placeholder메타→
   clip_knowledge swap). **프롬프트 `director_realfootage.md` 신설 시 prompt-authoring 스킬**.
3. **B2 Director⇄Writer 루프** · **B3 Title/Packaging 승격** · **I1 트림 coherence** · **I2 Giri lockstep+E2E**.

## ⚠️ 미해결 (원래 3과제 중)
- **era-mix**: ✅ 완료(배포).
- **캡션 "타이밍 늦음"**: 타이밍-메커니즘 문제 아님(내가 디졸브로 오진 → PD "디졸브 상관없어"). 근본=캡션↔
  footage 불일치 → **위 재설계가 진짜 수정**. (별도 캡션 패치 아님.)
- **렌더 9분**: ❌ 미착수. render_card 내 LLM 3개(에디터·VLM검수·캡션재작성)+컷별 4K 재인코딩. 여전히 열림.

## 운영 메모
- ★**또 저해상도 타일로 캡션 읽어 오독**(216px 그리드 → "카페의 딸따니는 누 세상" 날조, 실제 "카페마다 펼쳐지는
  두 세계!"). 메모리 [[caption_readoff_lowres_misread]] 박음. **캡션=풀해상도 프레임/JSON만.** 타일=타이밍·경계 구조확인 전용.
- clip_knowledge 검증 샘플 = `/tmp/ck_sample.json`(산책 클립, pd_notes "둘 다"→subjects_in_video=[leo]+conflict 플래그).
- 스크래치 프로브 `scripts/_ck_probe.py`는 `video_actions.clip_knowledge`로 승격하며 삭제.
- 킬스위치 현황: `RF_ERA_GATE=1`(활성·배포), `USE_CLIP_KNOWLEDGE=0`·`RF_CONCEPT_BRAINSTORM=0`(OFF, 스모크 후 플립).

## 참조
- 설계: `notes/rf_footage_aware_direction_design.md`(§1.5 기존자산 매핑·§2.0 M0 스키마·§3.5 Title에이전트·§6 2트랙).
- 회고: §4.4 C_eramixgen·C_multicutlayers(트림 authority)·C_photozerosec.
- 메모리: [[rf_editing_direction_memories]](별도빌더 금지)·[[mv_pipeline_and_action_layer]](Gemini 액션레이어)·
  [[grounding_pd_notes_gpt4omini]](pd_notes 그라운딩)·[[caption_readoff_lowres_misread]](캡션 풀해상도로 읽기)·
  [[title_hook_coherence_generator]](제목 리롤 수렴).
- 근본파일: era_mix.py·clip_actions.py·scripts/video_actions.py·concept_brainstorm.py·producer.py
  (`_propose_realfootage_singlepass` 3162·RF brainstorm 게이트 3655)·writer_director.py(`run_director` 753, AV전용).
