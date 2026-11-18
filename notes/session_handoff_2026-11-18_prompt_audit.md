# Session handoff — 2026-11-18 · 모델별 프롬프팅 최적화 감사 (완료, 배포됨)

**한 줄:** 11-18 핸드오프 §8 TODO(모델별 프롬프트 최적화 감사)를 **Phase 1-3 완료 + main 배포**했다.
근본="모델마다 공식 프롬프팅이 다른데 확인 없이 범용으로 질렀다"(MV 카툰화 근본). 2커밋 라이브
(`26788a6` 감사 + `de077af` retro §4.7). 남은 건 **미추적 FLUX 파일 결정**과 **Claude .md 별도 감사**.

---

## ✅ SHIPPED (origin/main, VM deploy.timer 자동배포)

**`26788a6`** 모델별 프롬프팅 감사 Phase 1-3:
- **Phase 1** `notes/model_prompting_guides.md` (신규) — 8모델 **1차 공식출처**(URL 인용) 가이드:
  nano-banana·gpt-image·FLUX·Seedance·Veo·Suno/Lyria·Claude·VLM. §0 관통원칙(부정 처리표) +
  모델별 상세 + **우리 사이트→모델 교차표** + **마이그레이션 플래그**.
- **Phase 2** 14개 생성 사이트 핸즈온 교정(실물 프롬프트 + docstring→가이드):
  - nano 부정→긍정: `regen_end_frame`(abandoned)·`regen_vtuber_style`·`gen_still_multiref`·
    `generate_character_scene`·`_gen_overlay_gemini` + `mv_prompt_guards` **긍정변주 `*_POS`** 추가.
  - 풋건 docstring: `gen_song`(아티스트명)·`gen_music`(lyria-002 instrumental-only)·
    `gen_still_fal/t2i`(no-negative/보존절)·`animate_seedance_i2v`(공식대조,플래그)·`animate_hero_veo3`(negativePrompt명사).
  - `openai_vision` detail:high **이미 정렬** 확인(Structured Outputs는 플래그만).
- **Phase 3** 가드 배선 = Level-1(docs+docstring): 전 생성기 docstring이 가이드를 가리킴.
- **모델 전략(PD 결정)**: gpt-image-2 **포토리얼 primary 유지**, Gemini 이미지 **폴백 전용 축소 +
  legacy `gemini-2.5-flash-image`→`gemini-3-pro-image-preview`** 업그레이드. `models.py IMAGE_GEN` +
  스크립트 4개를 `IMAGE_GEN_MODEL` env 노브로 통일. (thinkingBudget 미설정이라 Pro 400 리스크 없음.)

**`de077af`** 리트로스펙티브 **§4.7 C_modelpromptspec** + 메모리 `model_prompting_guide` 영속.

**검증:** py_compile OK · canon 가드 clean · **deploy smoke PASS**(10 모듈) · nano 샘플 A/B(PD 확인:
"훨씬 낫다", 단 마킹은 ref-lock 필요). **데일리 실동작 변화 = 1개**(Gemini 이미지 폴백 모델), 나머지 무해/휴면/MV.

## ★ 핵심 교훈 (retro §4.7 / 메모리 model_prompting_guide)
- **부정이 모델을 두 진영으로 가른다**: nano/FLUX/Seedance=긍정전용(negative 필드 없음, "NO X"가 그 명사 그림),
  Veo=negativePrompt 명사(main "no/don't" 금지), gpt-image/Claude=부정 1급. ← 우리가 부정 싫어하는 모델에
  부정 남발하던 게 MV 카툰·환각 근본축.
- **마킹 충실도=ref-lock**(레퍼런스가 운반), 프롬프트(긍정이든)론 드리프트. 긍정-재작성과 별개·보완.
- **모델 선택 전제는 썩는다**: nano 쓰던 이유(로컬 OpenAI 無)가 사라짐 → gpt-image-2 primary.
- **작동 가드는 이론으로 churn 금지**(Seedance NO-list·openai_vision BOTH-탐색 편향교정 보존, 플래그만).

## ☐ 다음 세션 — 결정/할 일
1. **미추적 FLUX 파일 결정 (PD 확인 대기):** `scripts/gen_still_fal.py`·`gen_still_t2i.py`는 **원래 git
   미추적**(이전 커밋된 적 없는 기존 툴, board 작성 추정). 이번 감사로 docstring만 추가됨. **영향=기능/프로덕션
   0**(추적코드가 import 안 함·VM 데일리 파이프라인이 호출 안 함, 수동 MV 인터루드 툴). 안 커밋 시 유일 효과=
   docstring이 로컬전용·파일 미버전(이미 그랬던 상태). **권장=track+커밋**(저위험 위생). `git add` 두 파일만.
2. **Task #4 (deferred): Claude 텍스트 프롬프트 감사** — `writer_director.py` + `agents/prompts/*.md`.
   **`prompt-authoring` 스킬 + pipeline-change-impact 필수**(.md 편집 규칙). 대부분 "확인/플래그":
   prefill은 Opus 4.6+에서 400이나 **우리는 prefill 안 씀=안전**. 개선 플래그=Structured Outputs(JSON 보장)·
   캐시 breakpoint(변동값 뒤 cache_control=0% 히트)·최소캐시(4.7=2048/4.8=1024 토큰).
3. **마이그레이션 플래그(별도 트랙, 가이드 부록):** `gpt-image-1` 은퇴(~10/23)→setup/Lane-2 스크립트만 영향
   (데일리 무관, gpt-image-2 핫패스 작동 확인)·`gemini-2.5-flash-image` legacy·`lyria-002` instrumental-only.

## 배포 확인 (전원 off 전 미완 시 다음 세션)
- push 완료(GitHub `de077af`). VM deploy.timer(2분 폴)가 pull→smoke→`rianileo-bot.service` 재기동.
  **확인법:** `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo
  --tunnel-through-iap --command='. /etc/rianileo/deploy.env; cd "$APP_DIR" && git rev-parse --short HEAD'`
  → `de077af`면 집음. (이 세션에서 백그라운드 확인 시도함 — 결과 progress_log 참조.)

cf 메모리: `model_prompting_guide`·`mv_generator_prompt_footguns`·`cli_quality_drift_root`·
`mv_character_consistency_lock`. 가이드 전문: `notes/model_prompting_guides.md`. retro: §4.7.
