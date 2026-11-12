# RF Footage-Aware Direction — 설계 문서

> **상태:** 설계 확정 중 (PD 2026-11-11 대화에서 수렴). 구현 전 스펙.
> **한 줄:** RF "편집이 안 되는" 근본 = *하고 싶은 캡션(story)* 과 *화면(footage)* 이 따로 정해져
> 안 맞는 것. 해법 = ①클립이 뭘 보여주는지 사전에 풍부히 알아 RAG에 넣고, ②Director⇄Writer가
> 그 위에서 **주고받으며** 컨셉·제목을 footage에 합치될 때까지 developing, ③Writer가 그라운딩한
> 트림을 Cameraman이 그대로 써서 캡션↔화면이 **구조적으로** 어긋나지 않게.

---

## 0. 왜 이 문서가 필요한가 (근본)

렌더-끝 그라운딩 탐지기로 캡션을 화면에 맞추려던 접근은 **이길 수 없는 싸움**이다:

- 캡션을 story에 맞추면(예: "두 세계") → 그 컷 화면(랴니만)과 어긋남.
- 캡션을 화면에 맞추면(랴니만 말함) → 하고 싶던 story/제목이 깨짐.

실측 증거(11-21 교차편집): cut1~cut2 전 구간(1.0–7.6s)에 **랴니만** 나오는데 캡션은 "랴니는 앞만
보고, **레오는 호기심 뿜뿜**" — 화면에 없는 레오를 말한다. 코드상 근본은 "그라운딩이 레오를
오검출"이 아니라, **캡션 내용과 footage가 서로 다른 단계에서 따로 결정돼 합의 레벨이 없다**는 것.

RF에는 **큰 그림(아크·테마)을 들면서 동시에 footage에 그라운딩하는 레벨이 없었다.** single-pass
Writer(`producer._propose_realfootage_singlepass`)가 풀 메타데이터(이벤트 레벨 "둘 다 카페")로
아크를 *추측*하고, footage가 못 받치는 캡션을 쓴 뒤, 렌더-끝 패치로 수습하려 했다. 그래서 못 고쳐짐.

### PD가 세운 원칙 3개 (북극성)

1. **RF 캡션 = "있는 것"에 그라운딩(날조 금지) + 그 한 마리를 창의적으로 해석(flat 금지).**
   창의성의 소스가 *없는 대상 끌어오기*가 아니라 *있는 대상을 캐릭터로 깊이 해석하기*로 바뀐다.
   - ❌ "레오는 호기심 뿜뿜"(없는 레오) ❌ "랴니가 앞을 본다"(flat)
   - ✅ "이 카페 터줏대감, 세상 다 가진 옆모습 😤 랴니" (보이는 랴니를 캐릭터로)
2. **큰 그림은 Director, 그라운딩은 Writer — 둘이 안 싸운다.** "두 세계" 대조는 한 캡션에 욱여넣지
   말고 **시퀀스를 가로질러**(cut1=선언 → 랴니 컷=랴니 라인 → 레오 컷=레오 라인) 나오게.
3. **Director⇄Writer는 단방향이 아니라 양방향 iteration으로 developing.** 컨셉·제목은 입력 상수가
   아니라 iteration의 **산출물**. footage가 안 받치면 컨셉을 바꾸고 제목도 바꾼다. 최초 의도를
   북극성 삼되 footage가 강제하는 만큼만 구부려 **진실하게 실현**한다.
   - 예: Director "랴니·레오 카페 사고뭉치들" → Writer가 써보니 **레오만** 사고침 → 같이 논의해
     "레오의 카페 사고, 랴니는 말리는 반장"으로 방향·제목 교체 → 재그라운딩 → … 수렴할 때까지.

---

## 1. 아키텍처 — 3 레이어

```
[Layer 1] 클립 지식 RAG  (사전계산·캐시·offline)  ← enabler, 먼저 구축
     Gemini 비디오 언더스탠딩(영상+오디오) → 축약 summary + 소재 beats(타임스탬프)
     + 함미하비 pd_notes를 "영상 내에서 검증"한 뒤 저장 → 임베딩/RAG
          │  (Director/Writer가 의도로 검색 → 실제 그 소재가 있는 클립·구간이 떠오름)
          ▼
[Layer 2] Director ⇄ Writer  (컨셉 단계, bounded 양방향 루프)
     Director(footage-aware 큰 그림: 테마·아크·시퀀스·컷역할, 정직한 working 제목 — 좀 올드해도 됨)
        ⇅  주고받으며 developing, 컨셉 mutable, 의도=북극성
     Writer(컷별 그라운딩+창의 캡션, 특정 beat/트림에 앵커)
          │  (산출: 합치된 컨셉 + per-cut 캡션 + per-cut 트림창)
          ▼
[Layer 2.5] Title/Packaging(트렌디: 밈·훅·지표) ⇄ Director ⇄ Giri  (끝단 3자 루프, §3.5)
     Director 컨셉을 받아 최종 제목·훅 패키징 → Director(진실?)·Giri(통과?·더 고칠것?) 수렴
          │  (산출: 최종 제목 + 패키징, Giri-통과·컨셉-진실)
          ▼
[Layer 3] 트림 coherence  (Writer → Cameraman)
     Writer가 그라운딩한 트림창이 Cameraman에 authoritative → 에디터가 조용히 재트림 금지
     → 캡션↔화면이 구조적으로 안 어긋남
```

핵심: **무거운 Gemini-video 패스는 Layer 1에서 한 번 뽑아 캐시**하므로, Layer 2의 iteration은 RAG
텍스트 검색 + 경량 LLM 논의뿐 → 라운드를 돌려도 비용이 안 터진다(런어웨이 트라우마 해소).

---

## 1.5 기존 자산에 매핑 — 새 에이전트가 아니라 진화·RF 확장 (PD 2026-11-11)

**이 설계의 역할 대부분은 이미 코드에 있다 — 단 AV 전용이거나 파편화·footage-blind일 뿐.** 새로
짓지 말고 **기존을 RF로 확장 + footage-aware + 양방향으로 진화**시킨다(별도 빌더 금지 교훈).

| 설계 역할 | 이미 있는 것 | 상태 / 해야 할 일 |
|---|---|---|
| **Director(큰 그림·아크·연출)** | `agents/writer_director.py` **Director** (`director_shots.md`) — shot_size·camera_move·lighting·seedance/veo·regen/motion 프롬프트 조립 | ⚠️ **AV 전용**(코드 주석 `Director assembles prompts (AV only)`, writer_director.py:65 — 생성 footage용 cinematography). **RF로 확장**: shot *생성* 대신 **실footage 편집·아크 연출**(시퀀스·cut-role·edit-grammar) + **footage-aware(클립 RAG)** |
| **Writer(스토리·캡션)** | `agents/writer_director.py` **Writer** 3-pass(draft→critique→revise, `writer_realfootage.md`) | ✅ RF 이미 씀. **footage-aware(클립 RAG)** + Director와 **양방향 그라운딩 루프** 추가 |
| **아이디어·관객 게이트(ideation)** | `agents/concept_brainstorm.py` — 다중 후보(title·logline·beats·hook) + **audience-gate**(reviewer §1 렌즈) + **timely-hook**(222, 트렌드) + 실제 material 결합(42) | ⚠️ **AV 전용**(`best("ai_vtuber"…)` producer:960), RF는 **OFF**(`RF_CONCEPT_BRAINSTORM=0` — "억지 컨셉 산으로" 교훈). ★**우회 말고 뜯어고친다**: 억지컨셉의 근본=**ungrounded ideation**(이미 material 결합을 시도하나 이벤트-레벨 메타라 거침) → **clip_knowledge(beats·소재·frame-truth)로 ideation을 그라운딩**하면 실재하는 소재에서만 컨셉이 나와 치료됨 → **RF 재활성**. Director **상류**(ideation=무슨 이야기, Director=어떻게 연출) |
| **트렌드/밈 소스** | `scripts/trend_feed.py`(calendar + live discovery, `meme`, add/refresh) → `trends` 테이블 → `agents/writer.py:active_trends()` | ✅ 재사용. 지금 RF는 `producer._rf_trends_hint`(3238) **얇은 힌트 한 줄**만 → Title/Packaging이 제대로 소비 |
| **Title/Packaging(트렌디 finalizer)** | `agents/channel_manager.py:make_packaging` — 훅 제목 + SEO + A/B arm | 단발 재포장 → **Director⇄Giri 대화형 트렌디 finalizer로 승격**(§3.5) |
| **audience/품질 렌즈** | `agents/reviewer.py` §1 관객 렌즈 + title 게이트들 | ✅ 재사용. Giri가 3자 루프의 품질 백스톱 |
| **캐릭터·시즌 directive** | `agents/arc.py`(CHARACTER_FACTS, season/holiday/trend directive) | ✅ 재사용. Director 입력 |

**핵심 갭 2개**: (1) **RF엔 Director가 없다** — Director의 연출 작업이 AV cinematography 전용이라 RF는
Writer 3-pass만 돌고 cameraman 직행(편집·아크를 드는 레벨 부재). (2) **RF엔 upstream 아이디어/관객/
트렌드 게이트가 없다** — concept_brainstorm이 AV 전용. 이 설계의 본질 = **기존 Director·brainstorm을
RF로 확장하되 footage-aware·양방향으로** 만드는 것(새 에이전트 아님).

---

## 2. Layer 1 — 클립 지식 RAG (enabler)

### 2.0 M0 — `clip_knowledge` 스키마 (두 트랙의 공유 계약, 먼저 못 박을 것)

버그를 **구조적으로** 막는 핵심 = 세 가지 "존재"를 **분리**해 저장: ①화면에 **보이는**(frame-truth)
②pd_notes가 **주장**하는 ③오디오로만 **들리는**(레오 냐옹 off-frame). 이 분리가 "없는 펫 호명"과
"있는 펫 지움"을 동시에 막는다.

```json
{
  "asset_id": "med_2026_11_21_...",
  "schema_version": 1,
  "captured_iso": "2026-11-21",
  "duration_sec": 12.4,
  "summary": "카페 테라스, 랴니가 의자에서 앞을 응시하다 코 핥고 졸음. 중반 주인 손이 간식을 내민다.",
  "subjects_in_video": ["ryani"],          // ★ 화면에 실제 보이는 펫 (frame-truth) — 캡션 그라운딩의 상한
  "location": {"indoor_outdoor": "outdoor", "specific": "cafe_terrace"},
  "beats": [                                 // 소재 beats — Writer가 캡션을 여기 앵커 → 트림창도 결정
    {"t0": 0.0, "t1": 3.2, "who": ["ryani"], "action": "앞을 응시, 도도한 표정",
     "material_type": "funny_face", "note": "세상 다 가진 옆모습"},
    {"t0": 3.2, "t1": 6.0, "who": ["ryani"], "action": "코 핥고 눈 감았다 뜸", "material_type": "quirk"},
    {"t0": 6.0, "t1": 9.0, "who": ["ryani"], "action": "주인 손 간식에 반응", "material_type": "interaction"}
  ],
  "audio_events": [                          // ★ 들리지만 안 보일 수 있음 — 시각 존재와 분리
    {"t0": 4.1, "t1": 4.6, "who": "leo", "sound": "냐옹", "type": "vocalization", "on_screen": false}
  ],
  "pd_verified": {                           // 함미하비 설명을 영상으로 교차검증
    "subjects_claimed": ["ryani", "leo"],    // pd_notes가 말한 것 (이벤트 레벨)
    "conflicts": ["pd_notes '둘 다'지만 프레임엔 랴니만 (레오는 4.1s 화면밖 냐옹만)"],
    "context_kept": "카페 나들이 — 둘 다 참석한 이벤트 (감정·사연은 유효)"
  },
  "model": "gemini-...", "extracted_at": "..."
}
```

- `material_type` ∈ {mischief, interaction, funny_face, emotion, vocalization, quirk, hook}.
- **그라운딩 규칙(소비처가 지킬 계약)**: 캡션의 *시각적* 주체·동작은 `subjects_in_video`/`beats`를
  넘지 못한다(없는 레오를 "화면에" 두지 마라). `audio_events`는 *소리*로만 인용 가능("어디선가 레오
  냐옹" OK, "레오가 호기심" ❌). `pd_verified.context_kept`는 감정·사연엔 쓰되 off-screen 물리존재
  주장엔 안 씀.
- Track A는 이 JSON을 **생산**, Track B는 이 JSON을 **소비** — 필드가 계약. 버전(`schema_version`)으로 진화.

### 2.1 무엇을 뽑나 (Gemini 비디오 언더스탠딩, 네이티브 — 프레임 샘플 VLM 아님)

클립마다 1회, **영상+오디오 전체**를 보고:

- **동작 beats** (타임스탬프): 이미 있는 `action_moments`의 확장. `{t0, t1, who, action}`.
- **오디오 이벤트**: 레오 냐옹/하악, 랴니 멍/낑낑/코골이 등 **소리**도 beat로. (Gemini 네이티브는
  오디오를 듣는다 — `video_actions.align_audio`가 이미 오디오 타임스탬프를 뽑는 선례.)
- **특이/캐릭터 행동**: "랴니가 특이한 행동", "레오가 사고치는 순간" 같은 **소재로 쓰일 것** —
  `{t0, t1, who, what, 소재_type, why_interesting}`. 소재_type ∈ {mischief, interaction,
  funny_face, emotion, vocalization, quirk, hook}.
- **씬/장소/시점**: indoor/outdoor, location_specific, 촬영일 기반 시점(기존 그라운딩 재사용).
- **축약 summary**: 원 분석은 방대하니 **컷 단위로 쓸 수 있는 짧은 요약**으로 압축 저장(Director가
  훑기용). 원본 beats는 별도 보존.

### 2.2 함미하비 pd_notes = "영상 내에서 검증 후" 저장 (중요)

pd_notes(주인 설명)는 **맹신 금지**. 영상 언더스탠딩으로 **교차검증**한 뒤 저장한다:

- pd_notes가 맥락/감정/사연(화면에 안 보이는 진실)을 주면 → **context로 보존**(여전히
  authoritative for 감정·사연).
- pd_notes가 **물리적 사실**(누가 나오나/어디)을 주장하면 → 영상과 대조. 예: pd_notes "둘 다 카페"
  인데 이 클립(또는 트림 세그먼트)이 랴니만 보여주면 → **"이벤트엔 둘 다였으나 이 클립엔 랴니만"**
  으로 분리 기록. 이게 "둘 다" 바이어스가 캡션에 absent 펫을 호명하던 근본을 끊는다.
- 저장 형태: `{pd_notes_raw, pd_verified: {subjects_in_clip, location, conflicts:[...]}}`.

### 2.3 저장 + RAG

- **캐시 컬럼**: 기존 `assets.action_moments`(JSON)를 **`clip_knowledge`**(JSON: summary + beats +
  오디오 + 소재 + pd_verified)로 확장. idempotent ALTER(기존 idiom, `clip_actions.ensure_column`).
  ⚠️ **쓰기 멱등·보존**: C_ingestclobber 교훈 — `ON CONFLICT DO UPDATE` 업서트, `_VM_OWNED`에 포함
  해 Mac export가 NULL로 못 덮게.
- **RAG 인덱스**: `agents/asset_embed.py`(기존 임베딩/25,916벡) 확장 — clip_knowledge의 summary +
  소재 텍스트를 임베딩해, 의도("카페 사고뭉치")로 검색하면 **그 소재 beat가 실제 있는 클립**이 뜸.
- **언제 돈다**: 인입(ingest) 시 incremental + 백로그 배치 백필(기존 backfill 패턴). **렌더마다 아님**
  — offline amortize. 킬스위치 `USE_CLIP_KNOWLEDGE`(현 `USE_CLIP_ACTIONS` 승계).

### 2.4 코드 앵커
- 엔진: `scripts/video_actions.py:actions()` / `align_audio()` (Gemini 네이티브, thinkingBudget 주의).
- 캐시/영속: `agents/clip_actions.py` (`ensure_column`/`store`/`actions_for` → knowledge로 확장).
- RAG: `agents/asset_embed.py` (find_similar/임베딩).
- pd_notes 검증: `agents/openai_vision.py:ground_video` + `_rf_grounded_truth`의 pd_notes 주입 로직 재활용.

---

## 3. Layer 2 — Director ⇄ Writer (bounded 양방향 루프)

### 3.1 역할 분담
- **Director (큰 그림, footage-aware)**: RAG에서 의도에 맞는 클립·소재를 retrieve → **테마·아크·
  시퀀스·컷별 역할·제목**을 짠다. "두 세계"·"사고뭉치" 같은 throughline은 여기 산다. AV Director와
  달리 **거꾸로**: footage를 *생성*하는 게 아니라 **있는 footage가 뭘 주는지 읽고 최선의 아크를 짠다.**
- **Writer (컷 레벨)**: Director가 준 역할을 **그 컷에 실제 보이는 것**(clip_knowledge의 beats)에
  그라운딩해 창의적으로 쓴다(날조 금지, flat 금지). 캡션을 **특정 beat(타임스탬프)**에 앵커 →
  트림창이 정해짐.

### 3.2 양방향 developing (핵심 — 단방향 아님)
1. Director 초안(컨셉·아크·역할·제목).
2. Writer가 realize 시도, **footage 충돌을 보고**("랴니 사고 beat 없음 — '둘 다 사고뭉치' 안 나옴").
3. Director가 컨셉·**제목**을 수정(의도를 footage가 강제하는 만큼 구부림).
4. 재그라운딩 → 2~3 반복 → **story-intent ≡ footage-reality** 수렴.
- **수렴 판정**: 모든 컷 캡션이 그 컷 footage로 뒷받침 + 아크 throughline 유지 + 제목이 합치된 컨셉 반영.
- **bounded**: `RF_DEVLOOP_MAX_ROUNDS`(기본 2~3) + 킬스위치. 무한 왕복 금지(self-heal 런어웨이 트라우마).
  수렴 안 되면 **마지막 합치 버전으로 확정**(빈 슬롯보다 낫게), Giri가 백스톱.

### 3.3 구현 방침 — 선택 재구축 금지
- 기존 `producer._propose_realfootage_singlepass`를 **Director 패스 + Writer 패스 + 루프**로 분리.
  **클립 선택 로직은 재구축하지 않는다**(지난 교훈 [[rf_editing_direction_memories]]: 별도 빌더 =
  그라운딩/캐릭터/캡션 계약 상실 → "쪼르르 달려감" 날조). 이미 뽑힌 footage 위에 **연출·아크를
  얹는** 패스로만.
- 프롬프트: `agents/prompts/writer_realfootage.md`(Writer 그라운딩+창의 원칙) + **기존 Director
  (`writer_director.py`/`director_shots.md`)를 RF 편집·아크 연출로 확장**(신규 에이전트 아님 — AV
  cinematography 전용이던 걸 RF 편집연출 프롬프트로). **편집 시 prompt-authoring 스킬 사용**.

### 3.4 코드 앵커
- `agents/producer.py:_propose_realfootage_singlepass` (분리 지점), `_rf_grounded_truth`(그라운딩 소스).
- `agents/writer_director.py` (AV의 Writer/Director 분리 패턴 참고 — 재사용, 재구축 아님).
- `agents/prompts/writer_realfootage.md`, (신설) `agents/prompts/director_realfootage.md`.

### 3.5 역할 분화 — Director(크래프트) · Title/Packaging(트렌디) · Giri(검수) 3자 (PD 2026-11-11)

제목은 Director가 소유하지 **않는다.** 별도의 **트렌디한 Title/Packaging 에이전트**가 최종 제목을
관리하고, 그 친구가 **Director와 Giri 양쪽과 대화**하며 "더 고칠 건 없나"를 수렴시킨다. 관심사 분리:

- **Director = 이야기·연출·그라운딩 (좀 올드해도 됨).** footage-aware 테마·아크·시퀀스·컷역할·
  진실성. **정직한 working 제목**(컨셉을 그대로 반영)을 제안하되 최종 확정은 안 함. 북극성 = 진실+스토리.
- **Title/Packaging 에이전트 = 트렌디(밈·요즘 훅·도달).** Director의 합치된 컨셉을 받아 **최종
  제목+훅 패키징**을 만든다 — **지표·트렌드·밈 지능이 여기 산다**(Director는 크래프트, 이 친구는
  포장). 두 쪽과 대화:
  - **↔ Director**: 트렌디한 제목이 컨셉에 **진실한지**(오버셀·왜곡 없게) 확인 — Director가 "그건
    footage에 없다"면 되돌림.
  - **↔ Giri**: title 게이트 통과 + **남은 수정사항 더 없나** 점검(checker를 생성 단계로 당겨 수렴).
- **Giri = 검수/진실 백스톱.** title 게이트 + 내용 진실. 3자의 품질 게이트.

**끝단 = 3자 루프**: Director(컨셉+정직한 제목) → Title/Packaging(트렌디하게) → Director(진실?)·
Giri(통과?·더 고칠 것?) 확인 → 수렴할 때까지 iterate → **최종 제목**. (bounded + 킬스위치, Layer 2 루프와 동일 규율.)

**(a) 지금 파편화 → Title 에이전트로 통합**
- 현황: Writer가 컨셉 title 생성 → 업로드 시 `channel_manager.make_packaging`가 훅 제목을 **다시**
  만들고 A/B arm 부여 → Giri가 게이트. **셋이 따로** 돌아 어긋남.
- 지향: `channel_manager`를 이 **Title/Packaging 에이전트로 승격** — 단발 재포장이 아니라 Director·
  Giri와 대화하는 트렌디 finalizer. A/B 훅 변주(`packaging_arm`)는 이 친구의 실험 메커니즘으로 유지.

**(b) 지표·트렌드·밈 = Title 에이전트의 입력** (Director 아님)
- `video_performance`·[[view_data_concrete_hook_title]](이기는 훅 shape)·breakout 아키타입·
  `hook_search`·bandit 신호 + 요즘 밈/트렌드. **advisory**(진실성·footage 그라운딩이 상위 — 없는
  걸 트렌디하게 지어내지 말 것; Director가 거부권).

**(c) Giri title-lockstep**
- Title 에이전트가 Giri title bar를 **생성 단계에서 미리 만족**해야 리롤 수렴([[title_hook_coherence_generator]]
  교훈). bar: 한글-script(한자/kana 금지)·**제목↔cut1 훅 일관**·날조동작 금지·구체 훅(시적/모호 금지)·
  연도↔존재(레오 2025-09)·cast-subject 일치. Giri 게이트는 그대로 백스톱 → giri-update+review-learning 한 쌍.

**코드 앵커**: `agents/channel_manager.py:make_packaging`(→ Title/Packaging 에이전트로 승격),
`agents/bandit.py`·`youtube/analytics.py`·`video_performance`(지표), `agents/reviewer.py` title
게이트들(110·111·295·`_title_script_gate`), producer `_title_cast_subject_gate`(1246).

### 3.6 PD 관찰 결함 = B1 수용/회귀 케이스 (2026-11-12 수영 에피 리뷰)

PD가 실렌더(card `7caa60ec`, 여름 수영장 + 냇물 2컷)를 보고 캡션 결함 둘을 짚었다. 둘 다
**개별 캡션을 패치할 문제가 아니라** Director 부재의 증상 — **B1 배선 때 같이 고치고, 이 두
케이스를 회귀 테스트로 건다**(PD "근본원인 파악 후 수정", "다음 B1 배선 때 같이").

**결함 A — 캡션에 장면 전환 "설명(이음)"이 없다.** cut1(수영장) → cut2(냇물)로 넘어갈 때
*다른 장소/다른 때*인데 캡션이 그걸 **설명하지 않고** 그냥 점프한다(cut1 "늦가을에 여름 랴니가
생각나네요"=회상 → cut3 "오늘도 물 좋아서"=현재, 시점 whiplash에 이음 캡션 無). PD가 말한
"이음"은 **비디오 크로스디졸브가 아니다**(0.4s 디졸브는 이미 걸려 있음) — **캡션의 연결 서사**다.
근본=캡션 `action` 필드가 **의도적으로 per-cut**("Describes ONLY what's visible in THIS cut,
NOT the whole concept's narrative", `writer_realfootage.md`)이고, 컷 사이 다리인 `transition_in`은
i2v 모션/연속성에만 쓰이고 **캡션 텍스트로 표면화되지 않는다**. 렌더타임 action-grounding VLM도
per-cut이라 **컷 간 연결을 만들 권한이 없다**. → **시퀀스 레벨 권한(Director `director_brief`의
arc + sequence[role, what_it_shows] + notes_to_writer)**이 장면/시점 전환을 캡션에 명시하도록 해야
풀린다(§3.1 Director 소유).

**결함 B — "마음은 이미 물속 한복판"이 이미 헤엄치는 footage에 붙었다.** 이 캡션은 *아직 안 들어간*
기대 시점(리드줄에 묶여 물 쪽으로 몸이 쏠림)을 묘사하는데, footage는 **이미 물속에서 헤엄 중**.
근본=Writer 플로리시가 per-cut 그라운딩만 통과(물 클립과 "물속"이 느슨하게 일치) → VLM이 **시간적
상태**(이미-in vs ~하려-함)를 느슨하게 봐 생존. 게다가 cut1이 이미 "헤엄 월드클래스"를 세웠는데
cut3가 그걸 모르고 "마음은 이미"로 되돌아감 = **컷 간 상태 인지 부재**.

**공통 근본 = 캡션이 per-cut으로 고립 저작·그라운딩된다. 장면/시점 전환을 설명으로 잇고(A) 컷 간
시간·서사 일관성을 강제할(B) 시퀀스-레벨 authority가 없다** — 그게 정확히 B1 Director `director_brief`
(이미 빌드됨·OFF·미배선, progress_log 2026-11-12 13:16)의 일. B1 배선 시 **이 두 케이스를 스모크/회귀로
걸어** must_keep 시퀀스 + notes_to_writer가 전환 설명과 시간 그라운딩을 실제로 만들어내는지 검증한다.
(수정은 B1, 지금은 기록만.) cf [[rf_footage_aware_design]]·[[caption_readoff_lowres_misread]]·§4 coherence.

---

## 4. Layer 3 — 트림 coherence (Writer → Cameraman)

- Writer가 캡션을 특정 beat에 그라운딩하면 **트림창(start/dur)**이 정해진다. 이 트림이
  **Cameraman에 authoritative**로 전달돼 정확히 그 구간을 자른다.
- 지금 문제(C_multicutlayers): 트림/길이 authority가 `sources.json` · producer cap ·
  **에디터 LLM(`cameraman._run_editor`)** 사이에 엉켜 에디터가 Writer 트림을 **덮어씀**. →
  에디터는 Writer가 **그라운딩한 창을 조용히 재트림하지 못하게**(reorder/length 조정은 되되,
  그라운딩된 subject 창은 보존). 이게 "캡션이 안 보이는 걸 말함"을 **구조적으로** 막는 다리.
- 코드 앵커: `agents/cameraman.py:_run_editor`(4190)·`_apply_edit_plan`(4256)·sources.json 소비처,
  `producer._rf_cap_cuts`(길이 cap은 shrink-not-drop 유지).

---

## 5. 가드레일 / 재사용 (전부 지킬 것)

1. **선택 재구축 금지** — 이미 뽑힌 footage 위에 연출을 얹는다(별도 빌더 금지).
2. **9/20 "랴니 지워짐" 버그 재발 금지** — "이 트림된 컷에 **보이는**" vs "이벤트에 **있었던**"을
   구분. 단일-주체 컷은 프레임 진실을 authoritative로 쓰되, 둘 다 보이는 컷은 작은 펫 지우지 말 것.
   **양쪽 회귀**(랴니-지워짐 금지 + absent-펫 호명 금지) 필수.
3. **bounded iteration + 레이어별 킬스위치** (`USE_CLIP_KNOWLEDGE`, `RF_DEVLOOP_MAX_ROUNDS`,
   `RF_DIRECTOR_PASS`, `RF_TRIM_AUTHORITATIVE`).
4. **Giri lockstep** — 검수기가 같은 clip_knowledge/그라운딩 truth를 읽어 생성기와 같은 기준.
   (pipeline-change-impact: 생성기 고치면 검수기도.)
5. **쓰기 멱등·보존** — clip_knowledge 컬럼은 upsert + `_VM_OWNED`(C_ingestclobber).
6. **pd_notes는 감정·사연엔 여전히 authoritative**, 단 off-screen 물리적 존재 주장엔 영상 검증을 우선.
7. **빌드는 기본 OFF, 스모크 통과 후에만 플립.** 재작업/새 패스(brainstorm·Director·Title)는 전부
   플래그 뒤 OFF로 착지 → 스모크(출력 품질 + 과거 실패 케이스 회귀) + PD 스팟체크 통과 후에만 기본 ON.
   "dry-run 통과 ≠ 라이브 OK", "기본 ON이 채널을 죽인다"(v2 롤백). 플립은 빌드와 **별도 단계**.

---

## 6. 빌드 순서 — 2 병행 트랙 (PD 2026-11-11)

**M0 (먼저, 공유 계약):** `clip_knowledge` **스키마/인터페이스 확정** — RAG가 노출할 필드
(summary·beats·오디오·소재_type·pd_verified·트림 후보). 두 트랙이 이 계약에 대고 각자 빌드 →
초반 유일 의존성. 못 박으면 **병행**.

**Track A — 데이터 enabler (백엔드)**
- **A1 — 추출**: `video_actions`를 영상+오디오(냐옹/멍)+소재+특이행동까지 뽑게 확장, pd_notes
  영상-검증 병합. 소수 클립으로 출력 품질 확인(축약이 Director가 쓸 만한가).
- **A2 — 영속 + RAG**: `clip_actions` 컬럼 확장(멱등 업서트, `_VM_OWNED`), `asset_embed`에
  summary·소재 임베딩, 인입 incremental + 백로그 백필. 의도 질의 → 올바른 소재 클립 검색 검증.

**Track B — 에이전트 구조 (프롬프트/로직)** — A 완성 전엔 **기존 메타데이터를 placeholder 입력**으로
스캐폴딩, A 착지 시 clip RAG 입력으로 **swap**. 파이프라인 = **brainstorm(무슨 이야기, 그라운디드
ideation+관객) → Director(어떻게, 아크/시퀀스/컷역할) → Writer(컷별 캡션)**.
- **B0 — brainstorm 뜯어고치기(footage-aware)**: 억지컨셉 근본=ungrounded ideation →
  `concept_brainstorm`이 **clip_knowledge(beats·소재·frame-truth)에서만** 후보를 뽑게(실재 소재 결합),
  audience-gate 유지. **빌드는 기본 OFF**(`RF_CONCEPT_BRAINSTORM=0` 유지).
- **B0.5 — 스모크 테스트 → 그다음에 플립**: ON 하기 전 반드시 스모크. ①출력 품질(후보가 그라운디드·
  관객성 있나) ②★**forced-concept 치료 회귀**(과거 "산으로 간" 케이스 재현 → 이제 clip_knowledge
  그라운딩으로 억지 컨셉이 안 나오는지) ③후보 beats가 실재 소재 ⊆ 인지. **통과 + PD 스팟체크 후에만**
  `RF_CONCEPT_BRAINSTORM=1`로 플립(빌드와 별개 단계, v2 롤백 교훈 — "기본 ON 금지, 검증 후 플립").
- **B1 — Director RF 확장**: 기존 Director(writer_director.py, AV 전용) 사이드카 `run_rf_director` —
  brainstorm 승자 위에 RF 편집·아크 연출(아크/시퀀스/컷역할) + 정직한 working 제목. `RF_DIRECTOR_PASS`.
- **B2 — Director⇄Writer 루프**: 양방향 developing, 컨셉 mutable(필요시 brainstorm 재-ideate),
  bounded, 수렴 판정. `_propose_realfootage_singlepass` 분리.
- **B3 — Title/Packaging 에이전트**: `channel_manager.make_packaging` 승격 — Director 컨셉+지표·
  트렌드·밈 → 최종 제목/훅, **Director⇄Giri 3자 루프**, Giri title-bar 생성기-측 반영(§3.5).

**통합 (두 트랙 합류)**
- **I1 — 트림 coherence**: Writer(RAG beat) 트림 → Cameraman authoritative, 에디터 재트림 가드.
- **I2 — Giri lockstep + E2E 회귀**: 검수기 동기화(그라운딩 + title), 양쪽 회귀(erase/absent +
  제목↔cut1/날조), 실렌더 **끝까지** 확인(통계 통과 ≠ 소비처; "끝까지 돌려 결과 보라").

각 단계 가역(킬스위치)·독립 배포. A·B는 M0 계약 뒤 **병행**, I에서 합류.

---

## 7. 열린 질문 / 리스크

- **비용**: M1 Gemini-video 패스 × 전 클립 백필 비용(1회). 캐시라 이후는 인입분만. 예산 확인 필요.
- **축약 손실**: summary가 너무 줄면 Director가 쓸 소재가 사라짐 / 너무 길면 토큰·검색 노이즈.
  M1에서 "Director가 실제로 쓰는가"로 길이 튜닝.
- **수렴 실패 처리**: bounded 라운드 내 미수렴 시 — 마지막 합치본 확정 + Giri 백스톱(빈 슬롯 금지).
- **AV 레인**: 이 설계는 RF 전용(아카이브 실footage). AV는 Director가 footage를 생성하므로 무관 —
  단 clip_knowledge RAG는 AV의 포즈·장면 레퍼런스 선택에도 쓸 수 있음(후속).
- **에디터 vs 트림 authority**: 에디터 LLM의 가치(reorder/pacing)는 살리되 그라운딩 창 보존 —
  경계 설계 필요(M5).

## 참조
- 근본 대화: PD 2026-11-11. 증거: /tmp/rf_crosscut_11_21 (cut2 랴니-only + "레오" 캡션).
- 메모리: [[rf_editing_direction_memories]](별도 빌더 금지)·[[mv_pipeline_and_action_layer]](Gemini
  액션레이어 선택 근거)·[[grounding_pd_notes_gpt4omini]](pd_notes 그라운딩·렌더시점)·
  [[caption_readoff_lowres_misread]](캡션은 풀해상도/JSON로 읽기).
- 회고: §4.4 C_multicutlayers(트림 authority 엉킴)·C_photozerosec·§4.5 C_ingestclobber(쓰기 보존).
- 코드: clip_actions.py·video_actions.py·asset_embed.py·openai_vision.py·producer._propose_realfootage_singlepass·cameraman._run_editor.
