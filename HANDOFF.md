# HANDOFF — real_footage 품질 작업 (2026-06-06)

> 새 shell이 이 작업을 이어받기 위한 단일 문서. CLAUDE.md 다음으로 이것부터 읽어라.

## ⛔ 출력 규칙 (절대)
도구 호출 코드(`<invoke>`/`<parameter>`/leading `count`/XML 함수블록)를 **답변 텍스트에 절대 쓰지 마라**. 실제 function-call 채널로만 호출. 답변 = 순수 산문. 에피소드 렌더되면 즉시 SendUserFile로 mp4 전달. (PD가 이것 때문에 수십 번 분노함.)

## 지금 목표
**real_footage YouTube Shorts 품질을 PD 합격선까지.** ai_vtuber와 별개 경량 파이프라인.

## ★ 방금 찾은 진짜 ROOT CAUSE (가장 중요)
PD가 "어제부터 같은 피드백이 반복, 수정이 반영 안 됨"이라고 한 이유:
- real_footage는 단일-패스 `realfootage_concept.md`로 grounded+flowing 캡션을 만든다.
- 그런데 렌더 단계 `agents/cameraman.py:1831` → `_vlm_post_render_caption_rewrite()` → `run_caption_agent()` (line 1283) 가 **그 캡션을 전부 다시 써서 덮어쓴다.**
- 그래서 프롬프트(주체정확성/랴니대사/여운/가독성)를 아무리 고쳐도 최종 영상엔 Caption Agent가 갈아엎은 캡션이 나옴.
- **첫 수정 작업: real_footage 단일-패스 경로에서는 `_vlm_post_render_caption_rewrite`를 SKIP 해야 한다.** (단일-패스 캡션이 이미 grounded이므로.)

## 아키텍처 (현재)
```
producer.propose_concepts(style_filter="real_footage")
  → _propose_realfootage_singlepass()  [agents/producer.py ~390]
       prompt: agents/prompts/realfootage_concept.md  (LLM cascade: OpenAI gpt-5-mini → Gemini → Anthropic)
       출력: title + cuts(asset_id, captions[], edit_effect) + 아티팩트 저장(data/output/artifacts/)
  → produce_and_render() 에서 real_footage면 _render_realfootage_direct()  [card-writer 우회]
       → cameraman.render_card(use_brain=False, concept=concept)
            → run_real_footage_pipeline()  [cameraman.py:1808]
                 - _prerender_* (interp/photo_i2v/chain/split)
                 - _trim_real_footage_clips()  [컷 trim + edit_effect + 마지막컷 여운 2s]
                 - ⚠️ _vlm_post_render_caption_rewrite()  ← 캡션 덮어씀, SKIP 필요
                 - burn_captions.py  [자막 번인]
                 - assemble_episode.py  [concat + BGM, 루미넌스 eq 비활성]
```

## PD가 반복 지적한 미해결 이슈 (전부 캡션 덮어쓰기 때문일 가능성 큼)
1. 첫 컷 캡션이 늦게 뜸
2. 랴니 플레이보우(놀자) 대사가 없고/레오 대사로 잘못 붙음
3. 주객전도: 할머니가 장난감 흔든 건데 "레오가 논다"로 표기 (영어 수동태→한국어 능동 오역)
4. 한국어/영어 캡션 width 안 맞음 (한국어가 한 줄 더) — burn_captions 폰트/줄바꿈 점검 필요
5. 마지막 캡션 후 너무 짧음 / 이야기 끝나다 만 느낌 (여운 부족)
6. 반전 전환이 급하고 캡션이 느려서 "레오가 신호 못 읽음"이 안 느껴짐

## 이미 반영된 것 (프롬프트/코드, 단 캡션은 덮어써져서 안 보였음)
- `realfootage_concept.md`: 컨셉 기획(STEP2) + 쿠들습격 baseline + 3축(위트/리듬/반전) + 5규칙(범인오용금지/공간연속성/킥/컷수무제한/여운) + 가독성(2 scene 분할) + 주체정확성 + 랴니대사
- `producer.py`: 컷 수 cap 제거, 아티팩트 저장(data/output/artifacts/realfootage_*.json)
- `cameraman.py:_trim_real_footage_clips`: 마지막 컷 여운 2s freeze
- `assemble_episode.py`: 루미넌스 eq 비활성(원본 보존), `import os` fix
- `CLAUDE.md`: 출력 규칙 박음

## ★ PD 구조 요청 (미완)
**각 단계 산출물(컨셉→스토리→캡션→렌더결정)을 아티팩트로 남기고, 그게 결국 Slack에 올라가야 한다.** 현재 아티팩트는 로컬 파일(data/output/artifacts/)에만 저장. Slack 포스팅 미연결. daily_pipeline의 Slack 경로에 단계별 산출물 포스팅 추가 필요.

## 기리(Giri) 검수 미동작
real_footage가 `_render_realfootage_direct` → `render_card` 직접 호출이라 `render_with_retry`(기리 검수 포함)를 우회함. 기리 검수를 real_footage에도 태우거나, 단일-패스 후 명시적으로 기리 호출 필요.

## 다음 작업 순서 (권장)
1. **`_vlm_post_render_caption_rewrite`를 real_footage 단일-패스에서 SKIP** (env 플래그 또는 concept.author 체크). → 이거 하나로 PD 반복 피드백 대부분 해결될 것.
2. E2E 돌려 캡션이 단일-패스 그대로 나오는지 확인 + 영상 전달.
3. 남은 캡션 이슈(width KO/EN, 첫 컷 타이밍, 여운) 실제 영상으로 검증하며 조정.
4. 기리 검수 real_footage 연결.
5. 단계별 아티팩트 → Slack 포스팅 연결.

## Git 상태
- 브랜치: main, approach-a-rollback, approach-b-flowing-narrator, approach-c-strip-constraints, approach-d-grounded-singlepass
- 현재 작업 브랜치: **approach-d-grounded-singlepass** (단일-패스 + 모든 최신 수정 여기 있음)
- 비교 결론: 단일-패스(A/D)가 흐름 좋음, Writer/Director 분리(B/C)는 메마름. → D 방향 확정.
- main에 머지 안 됨. D 브랜치가 최신.

## 핵심 파일
- `agents/prompts/realfootage_concept.md` — real_footage 컨셉+스토리+캡션 프롬프트 (단일 소스)
- `agents/producer.py` — `_propose_realfootage_singlepass`, `_render_realfootage_direct`
- `agents/cameraman.py` — `run_real_footage_pipeline`, `_trim_real_footage_clips`, `_vlm_post_render_caption_rewrite`(SKIP대상)
- `scripts/burn_captions.py` — 자막 번인 (KO/EN width 이슈)
- `scripts/assemble_episode.py` — 최종 조립

## 테스트 명령
```
OPENAI_FALLBACK_MODEL=gpt-5-mini .venv/bin/python -m agents.producer --date 2026-05-22 --style real_footage --no-slack
```
(05-22 = 자산 풍부, v2 재VLM 완료된 날짜)
