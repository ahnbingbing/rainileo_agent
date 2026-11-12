# Session handoff — 2026-11-12 (RF 캡션 품질 전면 수정 + 경로 통일 + BGM 라이브러리 + B0.5/B1)

**스파인:** PD가 실렌더를 연속으로 리뷰하며 캡션 결함을 하나씩 짚었고, 각각 타깃 수정하다
**진짜 근본**에 도달 — **RF 캡션 생성 경로가 2개였고(action-grounding vs 레거시 narrator),
재렌더가 카드에서 manifests를 재구성하며 `author` 태그를 잃어 레거시 "덮어쓰기" 경로로 샜다.
그래서 내가 넣은 캡션 수정들이 "계속 안 먹히는" 것처럼 보였다.** PD 지시로 **한 경로로 통일**(de7a046).
그 외 BGM 라이브러리(배포·플립 대기), A2 clip_knowledge 백필, B0.5 재평가, B1 Director 사이드카(OFF),
캡션 VLM 병렬화까지. HEAD `8519c76`.

---

## ✅ 오늘 배포 (main, 시간순 — 전부 가역/킬스위치)

| 커밋 | 내용 | 상태 |
|---|---|---|
| `f743139` | **A2**: clip_knowledge 백필툴(`scripts/backfill_knowledge.py`) + `video_actions.clip_knowledge`가 M0 메타(schema_version/captured_iso/duration_sec) 생산지점 스탬프 | OFF (`USE_CLIP_KNOWLEDGE=0`) |
| `86f9d45` | 회고 C_clipknowledgebackfill | — |
| `96dfe71` | **RF 캡션 체인 수정**: era 마커 lockstep(`_enforce_memorylane_anchors`를 `era_mix.era_span_fire`에·asset_id 매칭·kitten 백스톱) + punchup 날조 가드(코툭) | 라이브 |
| `488f0b3` | 회고 C_captionrewritechain | — |
| `ab78f48`·`d464460`·`12cba8b` | **컨셉기준 Suno BGM 라이브러리**(라이브러리-매치): `gen_song --mood`·`scripts/gen_bgm_library.py`·`_pick_bgm_track` 생성풀 선택. 17무드×2=34트랙 시드·git추적(VM 배포됨) | OFF (`USE_GENERATED_BGM=0`) **플립 대기** |
| `0a222cc` | **B1 Director 사이드카**: `writer_director.run_rf_director` + `agents/prompts/director_realfootage.md`(아크/시퀀스/페이오프 그라운딩) | OFF (호출부 미배선) |
| `2c57948` | 캡션 자연스러움(번역투 한자어 금지, 분리식사→따로/각자) | 라이브 |
| `bf30b37` | 프롭 보존(Writer 원캡션 소품명 VLM에 참고, 터그→천 격하 방지) | 라이브 |
| `6463480` | **VLM⇄Writer 캡션 합의**(`_rf_caption_reconcile`): VLM=동작/타이밍 권한, Writer=사물/이름/맥락 권한. 일방 덮어쓰기 금지 | OFF (`RF_CAPTION_RECONCILE=0`) **플립 대기** |
| `8af7cc2`→`4051747` | 캡션 방향: 가족→함미하비(주인)·리드줄 정신(묶였어도 마음껏)·짧고 전환 많게(beat÷5→÷3.5·한줄≤4s)·물매니아 "오늘도 풍덩"(처음인 양 금지)·파도는 바다만(냇물=물결/물살) | 라이브 |
| `de7a046` | ★**경로 통일**: RF는 **항상 action-grounding 경로**(모든 규칙 보유). 레거시 narrator rewrite는 `RF_FORCE_VLM_REWRITE=1` 디버그만. author 분기 제거 → 재렌더도 안 샘. +캐릭터 사실(물매니아) 주입 | 라이브 |
| `8519c76` | **캡션 VLM 병렬화**: action-grounding per-cut를 ThreadPool(`RF_CAPTION_CONCURRENCY=4`) | 라이브 |

## ★핵심 교훈 (다음 세션 필독)
- **PD 피드백이 "계속 안 먹히던" 진짜 원인 = 경로 이중화.** 캡션 규칙은 action-grounding 경로에 넣었는데
  재렌더/일부 경로가 레거시 narrator(`_vlm_post_render_caption_rewrite`)로 빠져 규칙이 안 탐. **"둘을
  똑같이 만들 거면 한 조건으로 태워라"**(PD) → 통일이 정답. 비슷한 수정을 두 곳에 포팅하지 말 것.
- **렌더타임 캡션 6겹**(그라운딩·동작그라운딩·그라운딩게이트·합의·punchup·날씨위브)이 각자 어휘/프레이밍을
  건드려 Writer의 맞는 캡션을 격하/날조 — 코툭·분리식사·파란천·가족·질질·뜬금·파도·"아예 풍덩". 타깃 패치 +
  **VLM⇄Writer 합의**가 완화, 근본은 **B 재설계**(캡션을 상류에서 footage와 함께 결정).
- **검증은 소비처(번인 프레임)로.** payload 초안 캡션을 읽고 "그라운딩 됨" 오보한 실수 반복 — 캡션은
  풀해상도 번인 프레임/캡션 JSON으로만 읽어라([[caption_readoff_lowres_misread]]).
- **B0.5 재평가 결론**: 캡션 수정이 footage-aware brainstorm을 **언블록**(Giri 캡션 8·캐릭터 9, era/날조
  미검출). 남은 Giri 블록은 **구조**(약속 페이오프 미착지 + 컨셉↔클립 불일치=수영장+냇물 섞임) = **B1 Director**
  영역. B0 단독 플립보다 B1 먼저.

## 🔴 다음 세션 — 남은 일 (우선순위)

### 1. BGM 라이브러리 플립 (거의 끝)
- 34트랙 시드·배포 완료, PD 샘플 청취 OK("아주 괜츈" 아님 — BGM 자체는 OK 받음). **플립 = VM `deploy.env`에
  `USE_GENERATED_BGM=1` 추가.** CLI Bash는 `.env` 경로를 크레덴셜훅이 차단 → **PD가 `!`로 실행**:
  `! gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap --command="printf 'USE_GENERATED_BGM=1\n' | sudo tee -a /etc/rianileo/deploy.env"`
  롤백=같은 파일에 `=0` 추가. 미시드 무드는 라이선스 폴백(안전). comedy 1무드만 2트랙(나머지 동일).

### 2. VLM⇄Writer 합의 플립 (`RF_CAPTION_RECONCILE=1`)
- 스모크·E2E 둘 다 통과(파란천→파란터그 복원, 실렌더 4줄 조정). **단 렌더타임 LLM 1패스 추가 = 속도 트레이드오프**
  (#4와 함께 판단). 플립은 VM deploy.env 또는 코드 기본값. PD 판단 대기.

### 3. B1 Director RF 확장 — **배선** (사이드카·프롬프트는 배포됨 OFF)
- 남은 것: `producer._propose_realfootage_singlepass`(3162~)에 `RF_DIRECTOR_PASS` 프리패스로
  `writer_director.run_rf_director(concept, grounded_cards)` 주입 → director_brief의 `sequence[].must_keep`를
  **에디터/트림에 먹여 페이오프 컷 드롭 방지**(B0.5 Giri 실패 근본). 스모크 통과한 사이드카가 실제로 아크/
  페이오프를 보장. 그다음 B2 Director⇄Writer 루프·B3 Title·I1 트림 coherence·I2 Giri lockstep E2E.

### 4. #3 렌더 속도 — **프로파일링** (1차 병렬화만 배포)
- action-grounding VLM만 병렬화함(멀티컷서 효과). 총 ~10분(612s)은 **여러 스테이지 분산**. **추측 그만,
  스테이지 타이밍 계측 넣고 측정 렌더 1회** → 최대 주범 타깃. 의심: ①Giri 리트라이(실패=전체 재렌더)
  ②4K 소스 디코드(every ffmpeg pass가 4K 디코드) ③`_rf_grounded_truth`·`grounding_gate` 직렬 per-cut(동일
  패턴, 미병렬) ④`clip_actions.extract` 네이티브비디오 per-cut. 미병렬 루프 2개 병렬화는 action-grounding과
  같은 패턴(저위험).

### 5. B0.5 footage-aware 플립 (B1 후)
- clip_knowledge 로컬 30클립 샘플백필만 됨. 플립=VM 전체백필(유료)+`USE_CLIP_KNOWLEDGE=1 RF_CONCEPT_BRAINSTORM=1`.
  B1(페이오프 coherence) 먼저 깔고 묶어서. PD 스팟체크 필수.

### 잔챙이
- EN 로마자 "Liani"→"Ryani" 오타(캡션 EN). canon/프롬프트에 로마자 고정 넣을 것.
- PD #1 "풍덩 후 바로 컷"(트림/페이싱, 캡션 아님) — 미해결.
- 에피 coherence(수영장+냇물 다른장소 섞임) = B1 Director가 해결.

## 운영 메모
- 킬스위치 현황: 라이브=캡션 규칙·경로통일·병렬화·era게이트. OFF=`USE_GENERATED_BGM`·`RF_CAPTION_RECONCILE`·
  `RF_DIRECTOR_PASS`(미배선)·`USE_CLIP_KNOWLEDGE`·`RF_CONCEPT_BRAINSTORM`.
- 스크래치: `scripts/_b05_e2e.py`·`_b05_smoke.py`·`_rerender_card.py`(재렌더 드라이버, 유용).
- 캡션 검증은 **번인 프레임**으로(저해상도 타일 오독 금지). 재렌더 드라이버=`RF_CAPTION_RECONCILE=1 .venv/bin/python scripts/_rerender_card.py <card_prefix>`.

## 참조
- 회고: §4.4 C_captionrewritechain·C_clipknowledgebackfill·C_eramixgen.
- 설계: `notes/rf_footage_aware_direction_design.md`.
- 메모리: [[rf_editing_direction_memories]]·[[caption_readoff_lowres_misread]]·[[rf_footage_aware_design]]·
  [[grammar_form_fit_and_lyria_music]](BGM 생성 선례).
- 근본파일: `agents/cameraman.py`(캡션 체인 6730~·`_rf_action_grounded_captions`·`_rf_caption_reconcile`·
  `_enforce_memorylane_anchors`·`_RF_ACTION_SYS` 2069)·`agents/writer_director.py`(`run_rf_director`)·
  `agents/era_mix.py`·`scripts/gen_bgm_library.py`·`agents/prompts/director_realfootage.md`.
