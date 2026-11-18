# 모델별 프롬프팅 가이드 (공식 문서 기반)

> **목적.** 우리가 쓰는 모든 생성·추론 모델의 **공식 프롬프팅 베스트프랙티스**를 한곳에 영속한다.
> CLI/board가 프롬프트를 쓰거나 고칠 때 **추측·평판이 아니라 이 문서**를 기준으로 삼는다. 각 생성기
> docstring은 이 문서를 가리킨다(가드 배선).
>
> **왜 생겼나.** "모델마다 최적 프롬프팅이 다른데 공식 문서 확인 없이 범용 스타일로 질렀다"가 MV
> 품질저하(카툰·결함)의 근본. nano-banana 3원칙조차 PD가 알려줘서 안 거지 CLI가 문서에서 뽑은 게
> 아니었다. (출처: `notes/session_handoff_2026-11-18.md` §8.)
>
> **하드룰.** 이 문서의 모든 항목은 **1차 공식 출처**(벤더 docs·model card·공식 cookbook/blog)에서
> 나왔고 URL이 붙어 있다. 커뮤니티 lore는 `(community, unverified)`로 분리 표기. 공식이 비어 있으면
> "문서에 없음"이라고 명시한다 — 지어내지 않는다.
>
> **리서치:** 2026-11-18, 8개 모델 패밀리 병렬 리서치. 갱신 시 해당 섹션 출처 URL 재확인.

---

## 0. 전 모델 관통 원칙 (가장 중요 — 먼저 읽어라)

개별 모델 섹션으로 내려가기 전에, 8개 리서치에서 **반복 확인된 축**들. 우리 파이프라인의 실제
안티패턴 대부분이 여기서 잡힌다.

### 0.1 ★부정(negation) 처리 — 모델이 두 진영으로 갈린다 (최대 레버)

우리의 "no tail / NO dog / do not alter / NO on-screen text" 같은 **부정 남발**이 모델에 따라
무해하거나(LLM계), **역효과**(diffusion/비디오계)다. 모델별로 반드시 구분:

| 모델 | 전용 negative 필드 | main 프롬프트 "no/don't X" | 올바른 방법 |
|---|---|---|---|
| **nano-banana** (Gemini image) | 없음 | 비권장 | 원하는 **상태를 긍정 묘사**. `"no tail"` → `"smooth rounded tailless rear"` |
| **gpt-image** (OpenAI) | 없음(LLM 인코더) | **✅ 지원·권장** | `"state exclusions explicitly"` — `"no text, no watermark"` OK. edit은 `"change only X, keep everything else the same"` |
| **FLUX** (BFL) | **없음** (fal 엔드포인트에도 없음) | ✗ **backfire** | 긍정 반대묘사 + Kontext edit은 **보존절** `"keep her white markings exact"` |
| **Seedance** (BytePlus) | 없음 | 제약을 **프롬프트 문장**으로 | 끝에 제약 슬롯: `"keep it subtitle-free, do not generate any text or watermark"` (공식 문구) |
| **Veo** (Google) | **✅ `negativePrompt`** (명사 나열) | ✗ **금지** (`"no"/"don't"` 쓰지 말 것 — 공식 명시) | 제외는 `negativePrompt`에 명사: `"wall, frame, text, watermark"` |
| **Lyria** (`lyria-002`) | **✅ `negative_prompt`** | — | `"vocals, fast tempo"` 등을 negative_prompt로 |
| **Claude** (text) | — | ✅ 가능하나, **"왜"를 주면** 더 잘 일반화. `"하지 마"`보다 긍정 지시(`"매끄러운 산문으로"`) | 이유 설명 + 긍정형 + 모순되는 옛 규칙은 **supersede**(쌓지 말 것) |

**우리 파이프라인 함의:** `regen_end_frame.py`(nano에 `"NO TAIL, do not alter, Do NOT paint over"`),
Seedance `_NO_TEXT_GUARD`(대문자 NO 9연발), `mv_prompt_guards.SOLO/NO_HUMANS`(nano에 부정형) —
전부 부정-싫어하는 모델에 부정을 쏘고 있다. nano/FLUX/Seedance는 긍정 재작성, Veo는 negativePrompt로.

### 0.2 "한 번에 다" 금지 — 단계적/반복 편집 (이미지 전반)

nano-banana·gpt-image·FLUX **모두** 공식적으로 "복잡한 요구는 한 샷에 몰지 말고 배경→주체→요소를
단계적으로, 편집은 한 번에 하나씩, 매 패스 보존 리스트 재명시"를 권장. → 두 펫+세트+의상+소품을 한
프롬프트에 몰면 품질저하(브릴리언 보드 카툰화 증상). 승인 스틸을 베이스로 요소를 하나씩.

### 0.3 i2v는 **모션만** — 스틸이 보여주는 걸 다시 묘사하지 마라 (Seedance·Veo 공통)

두 비디오 모델 공식 지침이 일치: **입력 스틸이 이미 장면/피사체/조명을 고정**하므로 프롬프트는
**모션+카메라만**. 정적 재묘사는 낭비이고 프레임과 충돌. + **카메라 무빙은 샷당 1개**(Seedance 명시,
Veo 권장). → 우리 "primary motion + secondary + 1 camera move" 구조는 공식과 일치. 유지.

### 0.4 자연어 산문 > 태그 나열 (이미지 전반)

nano-banana·gpt-image·FLUX 모두 **완전한 서술형 문장**을 권장, **booru/콤마 태그 나열을 반대**.
`"black frenchie, orange cat, cute, 8k, masterpiece"` 금지. 품질 스팸어("highly detailed/4k/
masterpiece")는 효과 없음 — 구체적 가시 디테일이 이김.

### 0.5 각 모델 한 줄 TL;DR

| 모델 | 한 줄 |
|---|---|
| nano-banana | 산문(Style·Subject·Setting·Action·Composition+카메라/조명), **부정→긍정**, 길이 제한 없으나 복잡하면 단계적, 레퍼런스는 이름 붙여 캡 내에서 |
| gpt-image | 계층 순서(scene→subject→details→constraints), **부정 1급 지원**, edit은 "change only X + keep …" 매 패스 보존 재명시 |
| FLUX | 구체적 산문, **negative/strength 없음**, Kontext는 target→change→**보존절** |
| Seedance | 모션 degree를 **명시**(스틸서 못 읽음), 카메라 1개, 제약문장으로 텍스트 억제 |
| Veo | (cinematography)→subject→action→scene→camera→style, i2v는 모션만, 제외는 **negativePrompt 명사** |
| Suno/Lyria | Suno: Style(장르+악기+보컬타입)+Lyrics(구조태그), 아티스트명 금지 / Lyria-002: **instrumental-only**, negative_prompt |
| Claude | 역할+안정 레퍼런스=캐시된 system, 변동=user, JSON은 **Structured Outputs**(prefill 금지 4.6+) |
| VLM | 고해상 프레임 + 스키마 강제 JSON + **중립 질문**(유도 금지) + 빠른 액션엔 FPS↑ |

---

## nano-banana (Gemini image: gemini-2.5-flash-image / gemini-3-pro-image-preview)

### Official sources (URLs + dates)
- **Gemini API — Nano Banana image generation docs** (primary; `ai.google.dev/.../image-generation` now 301→AI Studio): https://aistudio.google.com/docs/image-generation.md.txt — API revision **2026-05-20**. 프롬프트 구조·레퍼런스 캡·종횡비/해상도·멀티턴 편집·모델표·SynthID.
- **Google DeepMind — Nano Banana prompt guide**: https://deepmind.google/models/gemini-image/prompt-guide/ — 5-core-elements + 반복 편집 + 멀티레퍼런스 네이밍.
- **Google blog — Nano Banana Pro prompt tips** (2025-11-20): https://blog.google/products-and-platforms/products/gemini/prompting-tips-nano-banana-pro/
- **Google blog — Nano Banana editing tips** (2025-10-02): https://blog.google/products/gemini/nano-banana-tips/
- **Vertex AI — Gemini image generation best practices** (유일하게 negation 명시): https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/gemini-image-generation-best-practices

모델 ID 주의: 공식 docs는 `gemini-2.5-flash-image`를 **legacy "Nano Banana"**로 표기, 현행은 Gemini 3 Pro Image("Nano Banana Pro", `gemini-3-pro-image-preview`).

### Core principles (priority order)
1. **키워드 태그가 아니라 풍부한 자연어 장면 묘사.** 캐노니컬 템플릿도 문장: *"A photorealistic [shot] of a [subject] in a [setting]."*
2. **원하지 않는 것(부정)이 아니라 원하는 것을 묘사.** 1차 문서의 유일한 negation 규칙: *"Instead of 'no cars', say 'an empty, deserted street with no signs of traffic.'"* → 랴니 꼬리는 `"no tail"`보다 올바른 실루엣을 긍정 묘사.
3. **극도로 구체적으로.** 디테일이 많을수록 통제력↑. 랴니 흰 턱/가슴/발 마킹, 레오 chartreuse 눈을 매번 명시.
4. **디테일은 반복적으로; 편집은 한 번에 하나씩.** 모델이 대화 내 이전 편집을 기억.
5. **멀티레퍼런스는 각 캐릭터/오브젝트에 이름.**
6. **맥락/의도 제공.** 7. **카메라를 사진/영화 용어로 통제**(샷/앵글/렌즈/조명).

### Recommended prompt structure/format
**DeepMind 5-core:** Style → Subject → Setting → Action → Composition.
**Nano Banana Pro:** Subject·Composition·Action·Location·Style → 그다음 camera/lens, lighting, 따옴표 텍스트, 사실 제약으로 **refine**.
캐노니컬 문장: *"A photorealistic [shot] of a [subject] in a [setting], [lighting], [camera/lens], [style]."*

### Anti-patterns + thresholds
- **부정 리스트("no X") = 안티패턴.** 전용 negative 필드 없음 → 긍정/시맨틱-네거티브로 재작성.
- **복잡 장면을 한 프롬프트에 과밀** → 단계적 지시 or 멀티턴 편집으로 분해. *"short prompts also generate high-quality images; add detail bit-by-bit for control."*
- **프롬프트 길이:** **공식 최대 길이·"길면 나쁨" 지침 없음.** 짧아도 됨; 디테일=통제; 복잡함은 단계로 분해. 토큰/문자 제한 명시 없음.
- **레퍼런스 캡(하드 넘버, API docs 2026-05-20):** Flash = **총 14**(오브젝트 10 + 캐릭터 4). **Pro = 오브젝트 6 + 캐릭터 5 + 스타일 3**. (Gemini 앱 UX는 3장.) 캐릭터 캡 초과=일관성 붕괴.
- **해상도:** 1K/2K/4K. **종횡비:** 1:1·16:9·**9:16**·4:3·3:4·5:4 등.

### Model-specific footguns
- **Negation:** 금지가 아니라 **묘사된 상태**에 반응. negative 파라미터 없음.
- **`gemini-2.5-flash-image`는 공식 "legacy"** — 신형 대비 기능 격차(스타일 레퍼런스·넓은 종횡비는 신형 문서). frozen으로 취급.
- **Nano Banana Pro 차이:** Gemini 3 Pro 기반, **thinking 기본 on**(`thinking_level` 조절), interleaved text+image, **스타일 레퍼런스(최대 3)**, text-in-image/다이어그램 사실성↑. 같은 구조적 자연어를 쓰되 더 명시적 지시(정확한 텍스트·사실 제약·정밀 카메라)를 받아줌. Flash는 스타일 레퍼런스 없음, 캐릭터 캡 4.
- **캐릭터 일관성:** 같은 레퍼런스 재사용 + 주체 **네이밍** 시 유지. 캡 초과가 붕괴점 — 랴니+레오를 캡 내에 두고 둘 다 이름.
- **★마킹 충실도 = 레퍼런스가 운반, 프롬프트 아님 (PD 2026-11-18 샘플 확인).** 랴니의 특정 마킹(얇은 흰
  턱/가슴/발, **등·목덜미는 솔리드 블랙**)은 긍정/부정 어느 산문으로도 안정적으로 안 들어간다 — 드리프트한다.
  정답은 **ref-lock**: 그 마킹을 또렷이 담은 네이밍된 레퍼런스 이미지를 입력으로 주고, 프롬프트는 "유지"만
  지시. 긍정-재작성(§0.1)과 ref-lock은 별개·보완이다(긍정 프롬프트 + 강한 레퍼런스 = 정답). 메모리
  `mv_character_consistency_lock`과 동일 결론. 뒷모습 컷이면 등은 원래 검정이라 흰색이 거의 안 보이는 게 정상.
- **Moderation:** 이미지 프롬프트 모더레이션 스펙 **공식 미공개**. 우리의 "고유명사/warp 동사 모더레이션"은 **Veo 교훈**이지 nano에 적용된다는 1차 확인 **없음**.
- **SynthID:** 모든 출력에 비가시 워터마크.
- **텍스트-대신-나오는 함정:** 모호하면 텍스트로 답할 수 있음 → `"create an image of…"`로 강제.

### Good vs bad example
- **Bad(태그+부정):** `black french bulldog, orange cat, no tail, no leash, cute, studio, 4k`
- **Good(자연어+긍정+네이밍+카메라):** `A photorealistic medium close-up of two pets on a sunlit wooden floor: a small black French bulldog with a smooth rounded tailless rear and white markings on her chin, chest and toes, next to a young orange tabby cat with bright chartreuse eyes. Soft golden-hour window light, shallow depth of field, eye level. 9:16 vertical.`

### Prompt-adjacent params
- **aspect_ratio/image_size**(`9:16`, `2K`/`4K`) — 프롬프트 텍스트에도 명시 권장.
- **reference images** — generateContent 이미지 파트로; 캡 준수 + 텍스트에 **네이밍**. Pro는 object/character/style 별도 예산.
- **`thinking_level`**(Pro/신형): minimal/medium/high. **temperature 공식 지침 없음**(이미지는 thinking_level·레퍼런스·구조가 레버). **출력:** png/jpeg + SynthID.

### TL;DR
리치 자연어 문장(Style·Subject·Setting·Action·Composition+카메라/조명), **원하지 않는 것이 아니라 원하는 것 묘사**(negative 필드 없음 — "no tail"을 tailless 실루엣 긍정 묘사로), 구체적으로. **공식 길이 제한 없음**이나 복잡하면 단계적/한번에-하나 편집. 캐릭터 일관성=각 주체 네이밍+캡 준수(Flash 14/캐릭터4, Pro 6/5/3). `gemini-2.5-flash-image`=legacy, `gemini-3-pro-image-preview`=thinking기본+스타일레퍼런스+사실성.

---

## gpt-image (OpenAI: gpt-image-1 / gpt-image-2)

> **⚠ 마이그레이션:** `gpt-image-1`은 ~2026-10-23 종료. 현행 = `gpt-image-2.5`/`2.6`(각 **sunburst**=편집정밀/디테일, **flare**=빠른생성). "gpt-image-2"라는 이름의 모델은 없음 — "2" 패밀리=2.5/2.6. `input_fidelity`는 **gpt-image-1 에서만**; 2.x는 **생략**(항상 고충실). §부록 참조.

### Official sources (URLs + dates)
- Image prompting guide (2.6 Flare/Sunburst 참조): https://developers.openai.com/api/docs/guides/image-prompting
- Image generation guide (2.5/2.6, `complexity`, SVG/GIF): https://developers.openai.com/api/docs/guides/image-generation
- Cookbook: image-gen models prompting guide: https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide
- Cookbook: gpt-image-1.5 prompting guide: https://developers.openai.com/cookbook/examples/multimodal/image-gen-1.5-prompting_guide
- Cookbook(archived): high input fidelity (`gpt-image-1`): https://developers.openai.com/cookbook/examples/generate_images_with_high_input_fidelity
- API ref: Create image edit: https://developers.openai.com/api/reference/resources/images/methods/edit

주의: "gpt-image가 diffusion보다 negation을 잘 따른다"는 **명시 문장은 없음**(아래 community). 단, docs가 "state exclusions explicitly"를 **적극 권장**하는 posture는 diffusion 가이드와 정반대.

### Core principles (priority order)
1. **무엇을 바꾸고 무엇을 보존할지 따로 명시.** edit: *"say 'change only X' and list details to preserve (identity, geometry, layout, lighting, labels)."*
2. **길이보다 계층.** *"main subject first, essential composition/action next, then a smaller number of style/lighting details."*
3. **일관된 순서·라벨 섹션.** `background/scene → subject → key details → constraints`, 긴 문단 대신 짧은 라벨 세그먼트/줄바꿈.
4. **장황이 아니라 구체.** 재질·모양·질감·매체를 구체적으로.
5. **한 번에 하나 편집; 매 패스 보존 리스트 반복.**
6. **뉘앙스 아트디렉션은 영어로.** (우리 KO 캡션이어도 **이미지 프롬프트는 영어**로.)

### Recommended prompt structure/format
`BACKGROUND/SCENE → SUBJECT → KEY DETAILS → CONSTRAINTS`. 특수 문법 불필요(문단/JSON-like/태그 아무거나 일관되게). **용도 명시**("ad/UI mock/infographic"). **멀티레퍼런스 edit은 입력마다 번호+역할** 부여(subject/style/clothing/background) — 우리 "캐릭터 레퍼+씬 레퍼" edit에 직접 적용.

### Anti-patterns + thresholds
- **과복잡/자기모순 금지.** 복잡해도 모순만 없으면 OK.
- **픽셀 정확 보존은 프롬프트에만 의존 금지** → 편집 후 원본 영역 **합성**.
- **사이즈 임계(숫자):** 양 변 16의 배수, 종횡비 1:3~3:1. 커스텀: 각 변 ≤3,840px, 총 픽셀 655,360~8,294,400. (9:16은 범위 내.)
- **프롬프트 최대 길이 명시 없음** — 질적 지침(계층+구체).

### Model-specific footguns
- **Negation:** **공식 권장** — *"State exclusions and invariants explicitly ('no watermark','no extra text','no logos')."* edit은 `"change only X" + "keep everything else the same"`. (community, unverified: LLM 인코더라 "no X"를 CLIP계보다 훨씬 잘 따름 — docs가 명시 비교는 안 함.)
- **`input_fidelity`:** gpt-image-1=`"high"`로 얼굴/로고/마킹 보존(입력 토큰↑, 멀티입력 시 **첫 이미지가 최고 디테일 보존**). **2.x=생략**(기본 고충실) — 마이그레이션 시 kwarg 제거.
- **반복 편집 드리프트:** 보존 제약 매번 재명시 — 랴니 마킹/레오 크기 드리프트와 동일. `"black Frenchie, no tail, white chin/chest/paw markings"` 매 패스.
- **텍스트 렌더:** 필요 단어는 따옴표+위치/타이포. 우리는 ffmpeg 번인이므로 **"no text in the image"**로 잡스러운 글자 방지.
- **Moderation:** 2.x는 `safety_identifier`(≤128자). gpt-image-1의 `moderation` 파라미터는 현행 생성 가이드에 없음.

### Good vs bad example
- **Bad:** `A cute black dog and an orange cat in a cozy cafe, no tail, highly detailed, 4k, masterpiece` (모순/품질스팸/보존리스트·역할 없음)
- **Good(씬에서 펫 제거 edit):** `SCENE: the exact input room photo. CHANGE ONLY: remove both animals; fill vacated areas with matching floor, rug, sofa texture. KEEP THE SAME: furniture layout, wall color, window light direction, camera framing. No added objects, no text, no watermark.`

### Prompt-adjacent params
- `size`(16배수, 1:3~3:1) · `quality`(auto/low/medium/high/xhigh/max) · `complexity`(2.6; low/medium/high) · `background`(auto/opaque/transparent) · `input_fidelity`(**gpt-image-1 only**; 2.x 생략) · `n`(변형수) · `safety_identifier`.

### TL;DR
계층 순서(scene→subject→details→constraints) + 구체 명사, 길이/품질스팸 금지. **negation은 1급 문서화 패턴**("state exclusions explicitly"). edit은 **"change only X, keep everything else the same"** + 매 패스 보존 재명시, gpt-image-1은 `input_fidelity="high"`로 얼굴/마킹 락(토큰↑) — **2.5/2.6은 생략**. 픽셀 정확 영역은 합성.

---

## FLUX (Black Forest Labs: FLUX.1 Kontext [pro/max], FLUX.1 [pro] v1.1 — via fal)

> 참고: BFL은 신규는 FLUX.2 권장(Kontext [pro]는 "previous-generation")하나, 우리 엔드포인트는 Kontext/v1.1. 로드맵 메모.

### Official sources (URLs + dates) — accessed 2026-11-18
- Prompting Summary: https://docs.bfl.ai/guides/prompting_summary
- Prompting Basics: https://docs.bfl.ai/guides/prompting_unified_basics
- Building a Good Prompt: https://docs.bfl.ai/guides/prompting_unified_building
- Working Without Negative Prompts: https://docs.bfl.ai/guides/prompting_guide_t2i_negative
- Single-Reference Editing(Kontext): https://docs.bfl.ai/guides/prompting_editing_single_reference
- Kontext Image Editing: https://docs.bfl.ai/kontext/kontext_image_editing · Kontext T2I: https://docs.bfl.ai/kontext/kontext_text_to_image
- fal: flux-pro/kontext/max · flux-pro/kontext · flux-pro/v1.1 (fal.ai/models/…)

### Core principles (priority order)
1. **자연어 산문 > 태그 soup.** *"Use sentences to explain relationships: who holds what, what stands behind what, where light comes from."* booru 태그 금지.
2. **구체적이되 꼭 길진 않게.** *"Strong prompts are specific, not necessarily long."* 품질어보다 가시 디테일.
3. **어순 — 중요한 걸 먼저.** medium→subject+action→setting→light→framing, 단 *"order is a habit, not a rule."*
4. **반복; 원샷 금지.** 간단히 시작→확인→한 번에 하나 수정.
5. **긍정 묘사, 부정 금지.** (아래 footgun)
6. **편집(Kontext): target 지명 → change 명시 → preserve 명시.**

### Recommended prompt structure/format
**t2i(v1.1):** 산문 한 문단. 슬롯: `[medium] → [subject+action] → [setting] → [light] → [framing/detail]`. 색은 hex, 텍스트는 따옴표.
**Kontext edit:** **변하는 것만** 쓴다(입력 이미지가 나머지 맥락 공급). 3부:
1) **Target 정밀 지명**("only one thing matches" — 크기/색/위치로 disambiguate) 2) **Change**(현재→변경, 텍스트는 `Replace 'joy' with 'BFL'`) 3) **Preserve 명시**(`"while maintaining the same facial features"`, `"keep proportions exactly"`, `"Keep the background unchanged"`). 멀티체인지도 preserve를 **전부** 열거. 그래도 **한 번에 하나** 반복 편집 권장.

### Anti-patterns + thresholds
- 태그 나열, 부정("no X/without X"), 모호 지시("make it better" → 구체화), 과장 길이(단어수 공식 없음; 복잡도만큼만 — "medium 30–80 words"는 **FLUX.2** 가이드 수치, Kontext 아님), under-specified edit target, 한 콜에 여러 변경(반복이 정석).

### Model-specific footguns
- **negative_prompt 없음 — 양측 확인.** fal 세 엔드포인트 모두 미노출. "person without glasses"가 안경을 그림. **Fix:** 긍정 반대묘사("no people"→"empty/deserted"). 우리 꼬리 환각은 "no tail"로 못 잡음 → 긍정 + preservation절.
- **Identity 보존은 프롬프트 책임, 자동 아님.** preserve절 안 쓰면 전체 redraw 드리프트. 랴니 턱/가슴/발 흰마킹·레오 크기/눈색을 **"keep …"**로 인코딩.
- **텍스트 edit은 정확한 따옴표 문자열+위치.** 대소문자 존중.
- **Annotation 박스**(입력에 그린 밝은 색 박스)로 국소 편집 지정 — 모델이 인지+출력서 제거(공식 기능).

### Good vs bad example
- **t2i bad:** `black frenchie, orange cat, cute, 8k, masterpiece, no blurry, no tail`
- **t2i good:** `A photorealistic photo of a small black French bulldog with white markings on its chin, chest, and paws, sitting beside an orange tabby cat on a sunlit wooden floor. Soft late-afternoon window light from the left, shallow depth of field, warm tones.`
- **Kontext bad:** `make the dog look better, no tail, nicer background`
- **Kontext good:** `Change the background to a blurred green garden at golden hour. Keep the black French bulldog exactly the same — same face, same white chin, chest, and paw markings, same body and rear silhouette.`

### Prompt-adjacent params
- **guidance_scale**(프롬프트 준수 강도; 공식 기본값 미공개 — 경험적 튜닝). **strength 미노출**(Kontext는 instruction-edit, denoise-strength 아님 — 변경량은 **프롬프트 스코프+preservation**으로 통제). **aspect_ratio**(Kontext는 입력 치수 자동, override 가능; v1.1은 width/height). **num_inference_steps**(기본 미공개). **seed**(재현성 — 승인 스틸 반복 시 핀). **prompt_upsampling**(off가 안전). **safety_tolerance**(0–6).

### TL;DR
구체적 **자연어 산문**(태그 금지), 중요한 것 먼저, 한 번에 하나 반복. **negative 없음·negation 역효과** — 긍정 반대묘사. **Kontext**는 target→change→**preserve 명시**(identity 보존=프롬프트 의무), 텍스트는 따옴표, 레버는 프롬프트 스코프+`guidance_scale`/`seed`(strength·negative 없음).

---

## Seedance (ByteDance/BytePlus i2v: dreamina-seedance-2-0)

### Official sources (URLs + dates)
- Dreamina Seedance 2.0 prompt guide (BytePlus ModelArk, **2026-11-15**): https://docs.byteplus.com/en/docs/ModelArk/2222480 (볼캔진 CN: https://www.volcengine.com/docs/82379/2222480)
- Seedance 1.0-lite Prompt Guide (motion-degree 규칙 원출처): https://docs.byteplus.com/en/docs/modelark/1587797
- Create a video generation task (API): https://ai.byteplus.com/ark/region:ap-southeast-1/docs/create-video-generation-task-api
- Seedance 2.0 technical report: https://arxiv.org/pdf/2604.14148

주의: 공식 prompt guide는 **i2v 전용이 아니라 2.0 t2v/i2v/ref 일반**. i2v 지침은 그 일반식 + 1.0-lite motion-degree 규칙의 부분집합. 아래 i2v-특화는 표시.

### Core principles (priority order)
1. **★모션을 명시 — 모델은 스틸에서 강도를 못 읽는다.** 1.0-lite 원문: *"cannot infer the degree of motion from the input reference image, so the text prompt must explicitly specify it"* (`"car quickly passing by"`). 가장 load-bearing한 i2v 규칙.
2. **i2v는 모션+카메라만 — 이미지가 보여주는 걸 재묘사 말 것.**
3. **샷당 카메라 1개.** *"Try to specify only 1 type of camera movement in a single shot."*
4. **작고 느리고 연속적인 모션 선호.** 바디파트+범위+속도+힘.
5. **간단·직접 언어 — 모델이 확장.** 6. **추상→물리 디테일**("very sad"→"lowering head, shoulders trembling, eyes reddening").

### Recommended prompt structure/format (i2v 공식)
공식 고급식(2.0 guide 순서): **정밀 subject + action details + scene/environment + lighting & color tone + camera movement + visual style + image quality + constraints**.
우리 i2v 적용(스틸이 고정하는 subject/scene/lighting/style는 드롭, 모션 요소만):
> **[subject, breed/color] + [primary action, 바디파트+속도] + [secondary action] + [1 camera move] + [constraints: no text/watermark]**

### Anti-patterns + thresholds
- 샷당 카메라 >1개, 고강도/대형 액션, 정적 이미지 재묘사, 물리 externalize 없는 추상/감정어, 중복/의미충돌, 구별특징 없는 멀티서브젝트. duration 지원 `[4,15]`s(또는 `-1` auto) — 우리 5s=기본.

### Model-specific footguns
- **전용 negative 필드 없음.** 제약은 **긍정 프롬프트에 문장으로.** 공식 문구: `"keep it subtitle-free"`, `"avoid generating any text or subtitles"`, `"do not generate a logo/watermark."` → formula의 trailing constraint 슬롯에.
- **온스크린 텍스트 실제 위험** — 모델이 자막을 렌더(공식 예시도 내레이션을 하단 자막으로). 위 문구로 적극 억제.
- **watermark는 별도 API 파라미터**(`--wm` 기본 false) + 모델이 텍스트/로고 환각 → 둘 다 억제.
- **모션 진폭은 degree 부사+바디파트 구체성**으로(숫자 노브 없음). `--cf`(camera_fixed)는 카메라만 고정.
- **고유명사/moderation: 공식 지침 없음**(thin). 우리 CLAUDE.md의 "Leo/Ryani 회피, breed/color, 랴니 no tail"은 **우리 테스트 lore**이지 BytePlus 공식 아님. (community, unverified: warp/morph/name 토큰 간헐 트립 — 우리 로그와 일치하나 1차 페이지엔 없음.)

### Good vs bad example
- **Bad(재묘사·카메라2·추상·무제약):** `A cute orange cat and a small black dog sitting in a sunny room looking happy, camera zooms in and pans around them.`
- **Good(모션만·primary+secondary+카메라1·degree·제약):** `The orange tabby cat slowly swishes its tail. At the same time the small black tailless dog gently tilts its head. Camera slowly pushes in. Keep it subtitle-free, do not generate any text or watermark.`

### Prompt-adjacent params (JSON 필드 또는 trailing `--` 플래그)
- **duration**(`--dur`): `[4,15]`s 또는 `-1`, **기본 5s**. **resolution**(`--rs`): 480p/720p/1080p/4k, **기본 720p**. **ratio**(`--rt`): 16:9/4:3/1:1/3:4/**9:16**/21:9/adaptive(기본 adaptive — 쇼츠는 9:16 설정). **camera_fixed**(`--cf`). **watermark**(`--wm`, 기본 false). **seed**(`--seed`, 기본 -1; 같은 seed≈유사, 완전 일치 보장 X).
- **Content-item roles(i2v):** `first_frame`=단일 init(표준 i2v), `last_frame`=첫+끝 보간용 2번째, `reference_image`=1–9장 omni. **`first_frame`/`last_frame`와 `reference_image` 혼용 불가**(우리 알던 규칙과 일치).

### TL;DR
공식은 단일 **일반식**(subject+action+scene+lighting+camera+style+quality+constraints) + 1.0-lite 하드 규칙: **모델은 스틸에서 모션 강도를 못 읽으니 degree 부사+바디파트/속도로 명시, 카메라는 샷당 1개, 모션만 묘사**(정적 재묘사 금지). **negative 필드 없음** — 텍스트/워터마크/로고는 제약문장으로. 고유명사 moderation은 우리 lore(docs thin).

---

## Veo (Google: veo-3.0-generate-001, Gemini API + Vertex)

### Official sources (URLs + dates)
- Vertex AI — Video generation prompt guide(캐노니컬 element 리스트+negativePrompt+audio): https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/video-gen-prompt-guide
- Gemini API — Generate videos with Veo(`veo-3.0-generate-001`/`-fast`, i2v, audio, params; **Stable, 2025-07**): https://ai.google.dev/gemini-api/docs/veo (→ aistudio.google.com/docs/veo)
- Vertex — Best practices(i2v "focus on motion"): https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice
- Google Cloud Blog — Ultimate prompting guide for Veo 3.1(**2025-10-16**; 5-part formula, audio, negation): https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1
- Vertex — first+last frames / reference images 문서(i2v 보간·레퍼런스)

버전 주의: 우리 모델=**Veo 3**(stable 2025-07). 가장 풍부한 가이드는 **Veo 3.1 blog** — formula/audio/negation은 전이되나, 3.1-전용 기능(첫+끝 프레임+audio, reference stacking)은 **3.0에 다 없음**(우리 CLAUDE.md의 "Veo lastFrame 사용 불가" gotcha와 일치).

### Core principles (priority order)
1. **구체적·서술적; 아이디어를 요소로 분해.** 2. **cinematography를 먼저.** 3.1 formula: `[Cinematography] + [Subject] + [Action] + [Context] + [Style & Ambiance]`. 3. **i2v는 MOTION 묘사, 이미지 재묘사 금지.** 4. **제외는 positive하게 negativePrompt에.** 5. **audio는 별도 문장으로 명시.** 6. **반복.**

### Recommended prompt structure/format (full element list + order)
캐노니컬 순서(Vertex guide; 전부 필수는 아님): 1) **Subject** 2) **Action** 3) **Scene/Context** 4) **Camera angle** 5) **Camera movement** 6) **Lens & optical** 7) **Visual style**(Lighting·Tone/Mood·Artistic style·Ambiance) 8) **Temporal**(slow-mo/time-lapse) 9) **Audio** 10) **Cinematic terms**(match cut 등).
Gemini API 단축 5요소: Subject·Action·Style·Camera·Composition/Ambiance. 쇼츠 i2v엔 10-요소 리스트 참조 + 5-part formula를 퀵 템플릿으로.

### Anti-patterns + thresholds
- **Negation 하드룰:** main 프롬프트에 `"no"/"don't"` **금지**(공식: *"avoid prompts such as 'no walls' or 'don't show walls'"*). 제외는 **`negativePrompt`에 명사 리스트**(`"wall, frame"`). main에 absence가 꼭 필요하면 긍정 묘사("a desolate landscape with no buildings").
- **i2v 과묘사** — 스틸 재묘사 금지, 모션에 집중(~1–3문장).
- **모호 subject** → 제네릭. **하드 글자수 제한 없음**(질적: 목적있게 서술).

### Model-specific footguns
- **i2v: 입력 이미지=첫 프레임.** 텍스트가 **모션+카메라** 구동, 프레임 repaint 안 함.
- **Audio(Veo 3 네이티브):** Dialogue 따옴표(`A woman says, "…"`), SFX 평문(`SFX: thunder cracks`), Ambient(`Ambient noise: …`). 우리는 외부 BGM+오디오 미제거 — 생성/억제 시 이 문법.
- **personGeneration 모드별 제한:** t2v/extend→`allow_all`; **i2v/보간/reference→`allow_adult`만**(EU/UK/CH/MENA도 `allow_adult`). 우리 i2v=`allow_adult` 캡.
- **고유명사:** 공식 금지 명시는 없으나 **모든 공식 예시가 breed/role/color 서술자** 사용(개인명 0). 우리 "Leo/Ryani 모더레이션 트립" 교훈과 corroborate.
- **`seed`:** 결정성 보장 안 함("slightly improves"). **SynthID** 워터마크. (community, unverified: i2v ~1–3문장·모션만이 프레임 유지 유리.)

### Good vs bad example
- **Bad(i2v):** `An orange cat named Leo and a black dog named Ryani in a sunny room, no tail on the dog, don't show any other pets, the cat looks happy.` (고유명사+부정+재묘사)
- **Good(i2v):** main=`Static shot. The orange tabby cat slowly swishes its tail while the small black French bulldog tilts its head toward it. Camera gently pushes in. Soft morning window light.` + `negativePrompt: "tail on the dog, extra animals, text, watermark"`

### Prompt-adjacent params
- **aspectRatio:** `16:9`(기본)·`9:16` 지원. **negativePrompt:** 콤마 명사(`"wall, frame, text, watermark"`), "no/don't" 금지. **seed:** 약한 결정성. **resolution/duration:** 720p/1080p, 4/6/8s. **image/first+last frame:** 입력=첫 프레임; **첫+끝 보간은 Veo 3.1 기능**(3.0 불가 — 우리 gotcha 일치). **reference("Ingredients to Video"):** 3.1 전용.

### TL;DR
`(cinematography)→subject→action→scene→camera→lens→style/lighting/ambiance→audio`, 구체적으로, i2v는 **모션/카메라만**(스틸 재묘사 금지). main에 `"no/don't X"` 금지 — 제외는 `negativePrompt` 명사. Veo 3는 9:16+약한 seed+네이티브 오디오(따옴표 dialogue/SFX/ambient), i2v personGeneration=`allow_adult`, breed/role/color 서술자 선호. lastFrame/reference=3.1 전용(3.0 아님).

---

## Suno (song) + Lyria (lyria-002 BGM)

> **⚠ 최대 footgun = Lyria 버전 split:** Vertex "Lyria prompt guide"는 vocals/lyrics/`[mm:ss]`를 문서화하지만 그건 **Lyria 3/3 Pro**(노래함). 우리 **`lyria-002`는 instrumental-only** — API ref는 `prompt`/`negative_prompt`/`seed`/`sample_count`만 노출. 가이드의 보컬 섹션을 lyria-002에 복사 금지.

### Official sources (URLs + dates)
**Suno**(Help Center): Custom Mode https://help.suno.com/en/articles/3726721 · V4.5 Lyrics https://help.suno.com/en/articles/5782977 · Music Glossary https://help.suno.com/en/articles/9010177 · Moderation(아티스트명 차단) https://help.suno.com/en/articles/3198209 · Community Guidelines https://about.suno.com/community-guidelines
**Lyria**(Vertex): lyria-002 API ref https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/lyria-music-generation · prompt guide https://docs.cloud.google.com/vertex-ai/generative-ai/docs/music/music-gen-prompt-guide · model card(**GA, 2025-10-27**) https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/lyria/lyria-002

### Core principles (priority order)
**Suno:** 1) 필드 분리(Style=장르/무드/악기/보컬, Lyrics=가사+구조태그, Title=이름) 2) 보컬 명시(안 하면 drift) 3) 장르뿐 아니라 feel+악기 서술 4) Lyrics에 구조태그 5) **아티스트/저작권 명칭 금지**(모더레이션 하드).
**Lyria-002:** 1) 상세·구체 2) 순서 `[Genre & style] + [Mood] + [Instrumentation] + [Tempo & rhythm]` 3) `negative_prompt`로 제외 4) 반복 5) US English(`en-us`).

### Recommended prompt structure/format
**Suno Style:** 디스크립터 문자열(tempo→장르/서브→리듬→악기→무드→보컬타입), 문장보다 짧은 구. 예: `warm lo-fi hip-hop, ~85 BPM, brushed drums, mellow Rhodes, soft female lead vocal`.
**Suno Lyrics:** 구조 메타태그를 해당 텍스트 **앞**에. 공식 태그: `[Intro]/[Start]`·`[Verse]`·`[Pre-Chorus]`·`[Chorus]`·`[Bridge]`·`[Break]`·`[Drop]`·`[Outro]/[End]`; 보컬/딜리버리 `[Male Vocals]/[Female Vocals]`·`[Whisper]`·`[Spoken Word]`·`[Narrator]`; 결합형 `[Intro: Spoken Word]`.
**Lyria:** 단일 프롬프트 `[Genre & style] + [Mood] + [Instrumentation] + [Tempo & rhythm]`(+ 선택 soundscape/production). `negative_prompt`에 제외.

### Anti-patterns + thresholds
**Suno:** 아티스트명/저작권/실존인물명·음성·likeness 금지(생성 실패 가능). 모호 보컬. 과장 긴 Style 문자열(짧은 구 선호; 공식 Help엔 하드 글자수 없음). 장르정보를 Lyrics에 주거주 금지(Style용).
**Lyria:** lyria-002에 vocals/lyrics 요청 금지(stray 보컬은 `negative_prompt: "vocals"`). **en-us만.** **`seed`와 `sample_count` 동시 사용 불가.** 과밀 금지.

### Model-specific footguns
**Suno:** 필드 채널 분리(태그=Lyrics, 디스크립터=Style). **Instrumental 토글**이 무가사 정석(빈 가사로 강제 금지). 우리는 **sunoapi.org 릴레이**(프롬프팅 의미는 공식 Custom Mode 미러, 배포권은 별도 — 개인청취만).
**Lyria-002:** **버전 트랩**(위 경고). model card "Instrumental mode: Not supported"=별도 토글 없음(출력이 이미 instrumental)이지 보컬 생성 아님. **출력 고정 ~30s(≤32.8s), 48kHz WAV base64** — 프롬프트로 더 길게 못 만듦. `seed`=best-effort.

### Good vs bad example
- **Suno Style bad:** `a song like [Artist Name], epic, best song ever, very emotional and long…` (아티스트명 트립+filler)
- **Suno good:** Style `uplifting acoustic folk-pop, ~100 BPM, fingerpicked guitar and claps, warm, bright female lead vocal` + Lyrics `[Intro]/[Verse]/[Chorus]/[Bridge]/[Outro]`.
- **Lyria bad:** `nice song with singing like a famous pop star, any tempo` (보컬 요청+아티스트+under-spec)
- **Lyria good:** `warm lo-fi instrumental hip-hop, cozy and nostalgic, mellow Rhodes piano, soft brushed drums, vinyl crackle, relaxed 82 BPM` + `negative_prompt: "vocals, harsh distortion, fast tempo"`.

### Prompt-adjacent params
**Suno:** model version(신형=구조 준수↑) · Instrumental(bool) · 보컬 gender/type(Style 디스크립터/Lyrics 태그로; 릴레이 `vocalGender`가 이에 매핑) · Title(오디오 영향 0).
**Lyria-002:** `negative_prompt`(주 레버) · `seed`(sample_count와 배타) · `sample_count`(기본 1, seed와 배타) · `prompt`(필수, en-us).

### TL;DR
**Suno:** 짧은 Style(tempo+장르+악기+무드+보컬타입)+Lyrics(구조태그), 무가사는 Instrumental 토글, 아티스트명 금지. **Lyria-002:** en-us 단일 프롬프트 `genre+style, mood, instrumentation, tempo`, **instrumental-only**(가이드 보컬 섹션 무시), `negative_prompt`로 steer, 출력 ~30s WAV 고정.

---

## Claude (text LLM: Writer/Director, claude-opus-4-x)

### Official sources (URLs + dates) — fetched 2026-11-18
- Prompting best practices(통합): https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- Prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Structured outputs(JSON schema): https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Prompting Claude Opus 4.8: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-4-8

(구 per-topic 페이지는 platform.claude.com로 302. `anthropic-version: 2026-10-21`, Opus 4.6/4.7/4.8 명시.)

### Core principles (priority order) — "가장 널리 효과적인 것부터"
1. **명확·직접.** 컨텍스트 0인 신입처럼 대하라; 포맷·제약 구체화; 순서 있으면 번호 리스트.
2. **맥락/동기 추가.** 규칙의 **왜**를 설명("TTS로 읽히니 줄임표 금지") — 일반화 잘함. 맨 "NEVER X"보다 낫다.
3. **예시(multishot).** 출력 포맷/톤/구조 스티어링의 가장 신뢰 가는 수단. **3–5개**, 관련·다양(엣지케이스), `<example>` 태그.
4. **XML 태그로 구조화.** 콘텐츠 타입마다 태그(`<instructions>`,`<context>`,`<input>`) — 지시·데이터 혼동 방지.
5. **system으로 역할 부여.** 6. **생각하게(CoT)·프롬프트 체이닝.**

### Recommended prompt structure/format
- **system = 역할 + 안정 지시 + 정적 레퍼런스**(캐릭터시트·샷 문법·exemplar). → **캐시 대상.**
- **user = 에피소드별 변동 태스크**(날짜·선택 클립·구체 ask). 안정 자료 **뒤**에.
- **XML 어디나.** 긴 레퍼런스는 `<documents><document index><source>…</source><document_content>…</document_content></document></documents>`.
- **긴 컨텍스트 순서(20k+ 토큰):** 긴 문서를 **맨 위**(쿼리/지시/예시보다 위). "쿼리를 끝에 두면 품질 최대 ~30%↑." 긴 문서는 관련 부분을 **먼저 인용**(`<quotes>`)시키고 답.
- **JSON 출력 = Structured Outputs 기능**(prefill 아님). `output_config.format`에 JSON Schema(또는 SDK `messages.parse()`) → "항상 유효 JSON, 재시도 불필요." 미사용 시: 스키마 명시+XML/tool로 유도+parse 실패 재시도.

### Anti-patterns + thresholds
- 모호("대시보드 만들어" → "가능한 많은 관련 기능… 기본 넘어서"). "하지 마"형 금지(긍정형 + XML 지시 태그). **ALL-CAPS "CRITICAL/You MUST" 과용 금지** — Opus 4.5/4.6은 system에 **과반응**("If in doubt use X" 같은 blanket default 오버트리거). 임계: 예시 3–5개, 긴컨텍스트 20k+, 쿼리-끝 ~30%.

### Model-specific footguns
- **⚠ prefill은 Opus 4.6+(4.7·4.8)에서 미지원** — 마지막 assistant 턴 prefill은 **400**. Writer/Director가 `{`/`"Here is the JSON:"` prefill하면 **깨짐**. Structured Outputs 또는 "preamble 없이 바로" system 지시로.
- **⚠ `budget_tokens`는 4.7+에서 400.** `thinking:{type:"adaptive"}`+`effort`로.
- **thinking-on-by-default drift:** 일부 모델은 thinking 생략 시 on → 응답이 thinking 블록으로 시작 가능 → **블록은 position 아니라 `type`으로 선택**. (Opus 4.6–4.8은 생략 시 off.)
- **모순 지시:** 옛 모델용 규칙(anti-laziness)이 역효과(오버트리거). 새 규칙 추가 시 **옛 것 supersede**(쌓지 말 것) — 우리 prompt-authoring 규칙과 일치.
- **캐싱 함정:** breakpoint는 **요청마다 동일한 prefix의 마지막 블록**에. 변동(날짜·클립)을 cache_control 앞에 두면 **0% 히트**. **최소 캐시 토큰: Opus 4.7=2,048, 4.8=1,024, 4.6/4.5=4,096**(미만이면 무시). 최대 4 breakpoint, lookback 20블록. tools/tool_choice/output_config.format 변경은 캐시 무효. 이전 메시지 편집은 이후 thinking 블록 무효화 — 히스토리는 append-only.

### Good vs bad example
- **Bad:** `system: "펫 비디오 컨셉 써. 나쁜 JSON 쓰지 마. 오늘 2026-11-18." / assistant(prefill): "{"`
- **Good:** `system(cached, 안정): <role>…</role><character_sheets>…</character_sheets><examples>…3–5…</examples>` / `user(변동): <task>…</task><date>2026-11-18</date><clips>…</clips>` / `output_config.format = {json_schema}` (유효 JSON 보장, prefill 없음).

### Prompt-adjacent params / features
- **`cache_control:{"type":"ephemeral"}`** 기본 **5분 TTL**(히트마다 갱신); `{"ttl":"1h"}` 1시간(쓰기비용↑). 순서: **tools → system → examples → messages → current user**. 5분마다 워밍 or 시작 시 프리웜.
- **prefill:** 4.6+ 제거. **thinking:** `{type:"adaptive"}`+`effort`(low/med/high), `budget_tokens` 없음. **temperature/stop:** 공식 특별 지침 없음(구조 JSON엔 낮은 temperature가 결정적 — 질적 플래그, 지어내지 않음).

### TL;DR
캐시된 Opus 4.7/4.8 Writer/Director: 역할+캐릭터시트+3–5 태그 예시를 **안정·XML system에 넣고 캐시**(breakpoint=마지막 불변 블록), 에피소드 변동은 user에. JSON은 **Structured Outputs**(prefill은 4.6+에서 400). 3 footgun: `budget_tokens` 없음(→`effort`), 마지막턴 prefill 없음, 캐시 최소(4.7=2,048·4.8=1,024).

---

## VLM grounding/understanding (OpenAI gpt-4o-mini vision + Gemini image/video understanding)

### Official sources (URLs + dates) — fetched 2026-11-18
- OpenAI Images/vision(detail, 32px patch, 멀티이미지): https://developers.openai.com/api/docs/guides/images
- OpenAI Structured outputs(`json_schema` strict): https://developers.openai.com/api/docs/guides/structured-outputs
- Gemini Video understanding(FPS, Files API, MM:SS, 클리핑): https://ai.google.dev/gemini-api/docs/video-understanding
- Gemini Image understanding(258-token tiling, 프롬프트 배치, bbox): https://ai.google.dev/gemini-api/docs/image-understanding
- Gemini Structured output(`responseMimeType`+`responseSchema`): https://ai.google.dev/gemini-api/docs/structured-output
- Gemini thinking(`thinkingBudget`): https://ai.google.dev/gemini-api/docs/generate-content/thinking — **NOTE(dated):** `thinkingBudget`은 Gemini 3+ deprecated(4에서 400); 우리 핀 ≤2.5는 유효.

### Core principles (priority order)
1. **질문당 그라운딩 팩트 1개가 omnibus보다 신뢰.** 한 콜에 과밀(펫+실내외+장소+액션+무드)=필드별 정확도↓. 분리하거나 스키마로 독립 필드 열거.
2. **작은 피사체엔 해상도가 구속조건.** OpenAI: 소물체/OCR은 `detail:"original"`(또는 `high`), `low` 아님. Gemini: 비디오 기본 **1 FPS**, low-res ≈66 token/frame — 검은 프렌치/먼 고양이가 가독 바닥 아래로. **프롬프트 전에 해상도/FPS를 먼저 고쳐라.**
3. **JSON은 스키마로 강제, 산문으로 애원 X.** OpenAI Structured Outputs(`strict:true`)·Gemini `responseSchema`가 디코딩을 문법에 구속. "always use Structured Outputs instead of JSON mode."
4. **중립 질문; 답 선확정 금지.** 유도/예-아니오("둘 다 있지, 맞지?")는 동의 편향. 개방 귀속("보이는 펫 나열, 없으면 'none'").
5. **비디오는 모션을 줘라 — 샘플로 날리지 마라.** 빠른 액션(1–2s)이 1 FPS 실패 모드 — 해당 윈도우만 FPS↑.

### Recommended prompt structure/format
**OpenAI(gpt-4o-mini, 멀티프레임):** content에 짧은 system → 프레임마다 라벨("Frame 1 of 3 …")로 귀속. 닫힌 열거 출력(`pets_present:["leo"|"ryani"]`(빈 배열 허용), `setting:"indoor"|"outdoor"|"unknown"`, `location_guess`, `confidence`) + "unknown/none" 명시 허용. `response_format:{type:"json_schema",json_schema:{strict:true,…}}`.
**Gemini image(2–3 프레임):** **텍스트 프롬프트를 이미지 앞**에. bbox는 `[ymin,xmin,ymax,xmax]` 정규화 **0–1000**. `responseMimeType:"application/json"`+`responseSchema`, evidence→verdict 순.
**Gemini native video(액션+타임스탬프):** **텍스트 프롬프트를 비디오 뒤**에(이미지와 반대!). 타임스탬프 **MM:SS**. Files API 업로드→`ACTIVE` 폴링→`uri`+`mime_type`. `videoMetadata.start_offset`/`end_offset`로 윈도우 한정(싸고 정확).

### Anti-patterns + thresholds
- **저해상/저detail 오독(우리 반복 함정).** `detail:"low"`·216/240px 썸네일 타일로 그라운딩 금지. 우리 메모리 `caption_readoff_lowres_misread`와 동일 — 캡션/피사체는 **풀해상 프레임만**.
- **유도/예-아니오** 금지(귀속 질문+unknown 탈출구).
- **한 콜 과밀** 금지(팩트클래스 1개/콜 또는 독립 필드 스키마).
- **1 FPS vs 빠른 액션:** <~2s면 윈도우 FPS 2–4로(우리 "꿀잠 vs 간식 몰래" 오독 근본).
- **프롬프트-only JSON** 금지(스키마 강제).

### Model-specific footguns
**OpenAI:** `detail` 기본 **`auto`**. `low`가 항상 싸진 않음("low does not always use fewer tokens than high") — 정확/비용 **둘 다** 레버(32×32 patch). 멀티이미지 ≤1,500장/512MB.
**Gemini:** **`thinkingBudget:0`은 2.5 Flash에서 thinking off**(범위 0–24,576; `-1`=dynamic). **2.5 Pro는 0 불가**(min 128). 0이 JSON 신뢰↑하는 이유: thinking on이면 reasoning 토큰이 출력 예산 먹어 `MAX_TOKENS`로 JSON 중간절단 → Flash에서 0+`responseSchema`가 결정적·저렴. **deprecation(2026):** 3+에서 deprecated/4에서 400, `thinkingLevel`과 배타 — 우리 2.5 핀은 OK, 모델 bump 시 `thinkingLevel`로. **비디오 샘플:** 기본 1 FPS; 액션 윈도우만 FPS↑. **이미지 타일:** ≤384px면 258토큰, 크면 768×768 타일 — 큰 프레임의 작은 피사체는 타일 내 다운샘플 → 그라운딩 시 피사체로 **크롭**.

### Good vs bad example
- **Bad(유도·예/아니오·거짓확인 유도):** "This clip shows both the cat and the black dog indoors at a cafe, right? Confirm."
- **Good(중립·귀속·탈출구·스키마):** "List which pets are visible — choose from ['leo','ryani'] or return [] if none. Separately state setting as 'indoor'/'outdoor'/'unknown' and a location_guess string. Judge only from the frames; use 'unknown' when unsure." (+ `responseSchema`/`strict`).

### Prompt-adjacent params
- OpenAI: `detail`=low|high|original|auto(기본 auto; 소피사체=high/original). 멀티이미지 반복 `image_url`. `response_format` json_schema strict.
- Gemini: `responseMimeType:"application/json"`+`responseSchema`(필드 순서=evidence→verdict, 스키마 작게). `thinkingConfig.thinkingBudget:0`(**Flash만**). 비디오 `videoMetadata.fps`(기본 1), `start_offset`/`end_offset`.

### TL;DR
**고해상 프레임 + 스키마 강제 JSON + 중립·귀속 질문(unknown 탈출구).** OpenAI는 소/검은 피사체에 `detail:"high"/"original"`+strict json_schema; Gemini는 `responseSchema`+`thinkingBudget:0`(Flash만, JSON 절단 방지), **프롬프트는 비디오 뒤**, MM:SS, 빠른 액션 윈도우는 **FPS를 1 위로**.
**우리 핀 확인:** `thinkingBudget:0`은 `gemini-2.5-flash`에서만 유효(Pro는 min 128); gpt-4o-mini 그라운딩은 `detail` 생략=auto이나 소/검은 피사체엔 **명시 `high`**. Gemini **이미지는 프롬프트 앞 / 비디오는 프롬프트 뒤**(두 사이트 혼동 주의).

---

## 우리 사이트 → 모델 교차표 + 감사 우선순위 (Phase 2 입력)

정찰(2026-11-18) 결과. ✗=안티패턴 확인, ⚠=점검 필요, ✓=이미 양호.

| 사이트 | 모델 | 상태 | Phase 2 액션 |
|---|---|---|---|
| `regen_end_frame.py` | nano-banana | ✗ 부정 남발(NO TAIL/do not alter) | 긍정 재작성 + docstring |
| `regen_vtuber_style.py` | nano-banana | ⚠ JSON 매니페스트 base/preserve/cut | 매니페스트 점검, docstring(부정→긍정) |
| `gen_still_multiref.py` | nano-banana | ⚠ caller 전적 의존 | docstring 가이드 + 호출부 점검 |
| `_gen_overlay_gemini.py` | nano-banana(3-pro) | ✗ ~400단어 과장황 | 축약(Pro는 더 강인하나 과밀 분해) |
| `generate_character_scene.py`(gemini 폴백) | nano-banana | ⚠ | docstring |
| `cameraman._resolve_scene_ref` | gpt-image | ✓ 짧고 부정 적정 | 확인(부정 OK) |
| `generate_character_scene.py`(primary) | gpt-image-2 | ⚠ caller | "change only X+keep" 구조 + docstring |
| `decorate_cut.py` | gpt-image-1 | ⚠ ~1200단어 KR + **모델 은퇴** | 구조 점검 + §부록 마이그레이션 |
| `build_scene_refs.py`/`generate_ref_sheets.py`/`generate_stickers_ai.py` | gpt-image-1 | ⚠ **모델 은퇴** | §부록 마이그레이션 플래그 |
| `gen_still_fal.py`/`gen_still_t2i.py` | FLUX | ⚠ caller-supplied | 호출부 + 템플릿/docstring(negative/strength 없음) |
| `animate_seedance_i2v.py`(`_NO_TEXT_GUARD`) | Seedance | ⚠ 대문자 NO 9연발 | 공식 제약문구와 대조(공식도 "do not generate"라 OK지만 간결화 검토) |
| `cameraman` 모션조립/`_sanitize`/`_lint_seedance` | Seedance | ✓ 대체로 공식 일치 | 모션 degree 명시·카메라1 확인 |
| `director_shots.md`/`sora2_motion_lessons.md` | Seedance/Veo | ✓ 공식과 일치 | 공식 출처 링크 보강 |
| `animate_hero_veo3*.py` | Veo | ⚠ negativePrompt 활용도 | main "no" 금지·negativePrompt 명사 확인 |
| `gen_song.py` | Suno | ✓ 필드 분리 | Style/태그 docstring, 아티스트명 가드 |
| `gen_music.py` | Lyria-002 | ⚠ 보컬 섹션 혼입 위험 | instrumental-only 명시(§footgun) |
| `gen_bgm_library.py` | Suno | ✓ | 확인 |
| `writer_director.py`+`prompts/*.md` | Claude | ⚠ prefill? | **prefill 400(4.6+) 확인**, Structured Outputs, 캐시 breakpoint |
| `openai_vision.py` | gpt-4o-mini | ⚠ detail 명시? | `detail:"high"` 명시, 스키마 강제, 중립질문 |
| `motion_b_vlm.py`/`video_actions.py` | Gemini | ✓ thinkingBudget=0 | Pro 라우팅 시 주의(§footgun), video 프롬프트 뒤 |
| cameraman VLM 게이트 6종 | Gemini | ⚠ 유도질문/프레임수 | 중립질문·해상도·FPS 점검 |

**우선순위:** nano-banana(근본) → VLM 그라운딩(품질직결) → Claude prefill(깨짐 위험) → Veo negativePrompt → Seedance/gpt-image/FLUX/Suno.

---

## 부록: 모델 마이그레이션 플래그 (PD 확인 필요 — 별도 트랙, 이번 감사와 분리)

리서치 중 드러난 **버전/은퇴 이슈**. 프롬프트 최적화와 별개로 PD 판단 필요:

1. **`gpt-image-1` 종료(~2026-10-23).** 후속 `gpt-image-2.5`/`2.6`(sunburst/flare). "gpt-image-2" 이름 모델 없음. `input_fidelity`는 2.x에서 **생략**. → `decorate_cut.py`·`build_scene_refs.py`·`generate_ref_sheets.py`·`generate_stickers_ai.py`가 `gpt-image-1` 호출. **실제 작동하는지 확인 필요**(이미 에러일 수도). cameraman/`generate_character_scene`는 `gpt-image-2` 문자열 — 실제 라우팅 모델 확인.
2. **`gemini-2.5-flash-image` = 공식 legacy.** 신형(2.1/3.x Flash, Pro)으로 이전 고려.
3. **Claude prefill 미지원(Opus 4.6+) = 400.** Writer/Director가 JSON prefill하면 깨짐 — **즉시 확인**. `budget_tokens`도 4.7+ 400.
4. **`lyria-002` = instrumental-only.** 가이드의 보컬/가사 섹션은 Lyria 3용 — 혼입 금지.
5. **`thinkingBudget` deprecated(Gemini 3+/4에서 400).** 우리 2.5 핀은 OK, bump 시 `thinkingLevel`.

> 이 플래그들은 `notes/retrospective_*`/핸드오프로도 승계. 프롬프트 감사(Phase 2)는 **현재 호출 중인 모델 기준**으로 진행하되, 위 1·3은 "작동 여부"를 Phase 2 초입에 확인.
