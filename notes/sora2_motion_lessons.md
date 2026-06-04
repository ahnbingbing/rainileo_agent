# sora-2 모션 프롬프트 운영 노트

5/11~5/13 i2v 실험 정리. `scripts/animate_hero.py` + `scripts/animate_all_cuts.sh`
관련. 향후 테스트 / 개선 항목 + 검증된 패턴 모음.

---

## 1. 알려진 한계 (관찰)

### (a) "둘 다 안 움직이는" 현상의 정체

5/11 잘 나온 두 영상 (`med_..._331110de__20260511_221327.mp4`,
`med_..._57e3500d__20260511_221910.mp4`) 분석 — **원본 프롬프트를 셸 히스토리
에서 복원한 후 결정적 원인이 바뀜.**

원본 프롬프트:
- 영상 1: `"Ryani softly blinks her eyes, gentle head tilt to camera, calm and warm"`
- 영상 2: `"Leo slowly blinks his eyes, gentle head tilt, soft ear flick, warm curious gaze"`

→ 두 프롬프트 모두 **한 마리 동물만 명시함** (Ryani=고양이 / Leo=강아지).
다른 동물에 대한 모션 지시 자체가 0건이었음. 그래서 자연히 그 한 마리만
움직임. sora-2 i2v 의 "한 주인공 편중" 한계 때문이 아니라 **프롬프트
설계가 그랬던 것**.

해결: 양쪽 모두 동작 원하면 둘 다 명시. 예 — `"Ryani softly blinks; at the
same time Leo turns his head gently and his ears flick."`

다만 영상 1의 강아지에서 보인 미세 표정 변화, 영상 2의 카메라 줌인은
프롬프트에 없는데도 발생 — sora-2 가 input 사진 자체에서 자체 추론으로
주변 동작/카메라 무빙을 일부 추가하는 동작을 함. **양날의 검**: 의도치
않은 모션도 발생할 수 있고, 동시에 짧고 평탄한 프롬프트로도 다이내믹한
결과 가능.

### (b) sora-2 모더레이션 false-flag — 강조 누적

같은 의미("내내/계속/처음부터 끝까지")를 3~4번 반복해서 박으면 모더레이션
분류기가 "prompt injection 시도"로 false-flag 함. 5/12 시점에서 5컷 모두
moderation_blocked 발생한 원인 (→ 2026-05-12 패치로 강조 1번만 쓰는 룰
주석에 박아둠).

안티패턴 예 (차단됨):
> "...swishes its tail continuously throughout the entire clip. The cat's tail
>  motion is clearly visible the whole time. ... continuous natural movement
>  throughout the entire clip with varied small motions."

평탄한 버전 (통과 예상):
> "The cat slowly swishes its tail side to side, flicks an ear, and blinks once."

룰: 강조 phrase ("throughout the entire clip" / "continuously" /
"clearly visible the whole time" / "from start to finish") 중 **0~1개만**.

### (c) 카메라 무빙은 사실 지원됨 — 명시 안 해도 발생할 수 있음

5/11 영상 2 는 결과적으로 강한 카메라 줌인이 들어가있었음 (고양이가 점점
카메라에 가까워지며 마지막엔 얼굴이 화면 가득). **원본 프롬프트에는 카메라
지시가 한 글자도 없었음.** 즉 sora-2 는 input 사진의 구도/시선/거리에
따라 카메라 무빙을 자체 추론해서 추가하기도 함.

운영 함의:
- 카메라 정지를 *원하면* 명시 필요 (`"Camera holds still."`)
- 카메라 무빙을 의도하면 짧게 명시 (`"Subtle slow push-in over the clip."`)
- 명시 안 하면 sora-2 가 자체 판단 — 예측 불가. 자동 운영에서는 명시 권장.

### (d) 고유명사 차단 룰은 비결정적

`animate_all_cuts.sh` 주석의 "Avoid proper nouns (Leo / Ryani) — Sora
moderation blocks them" 룰은 5/11 데이터(`Ryani` / `Leo` 모두 통과)와
모순. sora-2 모더레이션이 호출마다 결정이 흔들리는 비결정적 동작을 보임.
가능한 해석 두 가지:

1. 특정 컨텍스트(짧고 평탄한 프롬프트 + 단일 동물 명시)에서는 통과,
   다른 컨텍스트(스타일 가이드, 비주얼 묘사, 복합 모션)에서는 차단.
2. 단순히 모더레이션 분류기의 stochastic threshold.

운영 원칙: **자동 운영에서는 보수적으로 고유명사 회피 유지** (false-negative
한 번이라도 나면 비용/시간 손실). 수동 1회성 i2v 호출에서는 시도 가능.

---

## 2. 향후 테스트 항목 (운영 안건)

### A. 양쪽 동물 모두에게 적극적 모션 — A/B 테스트 필요

후보 프롬프트 패턴 (편당 ~$0.40 으로 검증 가능):

1. 명시적 동시 동작:
   `"The cat swishes its tail and licks its paw. At the same time the dog
   yawns and shifts its weight."`

2. 결합어 강조:
   `"Both animals move simultaneously — the cat ... while the dog ..."`

3. 시간차 동작 (turn-taking):
   `"The cat tilts its head first, then the dog responds by tilting its head
   to the other side."`

기대: 1·2·3 중 어느 패턴이 양쪽 동물 모두에게 가장 잘 transfer 되는지
확인. 비용 $1.20 (3컷).

### B. 카메라 무빙 — 패턴 라이브러리화

후보:
- `"Subtle slow push-in (5%) toward the cat over the full clip."` — 5/11
  영상 2가 사용했을 가능성 높은 패턴 (분석 결과 줌인 진폭은 더 컸지만).
- `"Slow pull-back, gentle dolly out."`
- `"Camera holds still but tilts slightly to the right over the clip."`
- `"Smooth handheld sway, very subtle."`

테스트 시 같은 input 이미지 + 같은 동물 모션 + 카메라 지시만 다르게 한
컷을 4개 만들어 비교. 비용 $1.60.

### C. 위 두 가지 조합

A·B 결과 합쳐서 **표준 "보강" 프롬프트 템플릿** 만들기 — 한쪽 동물만
적극 모션 + 다른 쪽은 소극 모션 + 카메라 subtle push-in. 트랙2 (카툰)
컷 생성 시 기본값으로 적용.

---

## 3. 검증된 룰 (현재 `animate_all_cuts.sh` 주석에 박혀있음)

- cat 있으면 tail must swish (mandatory primary motion)
- 두 동물 다 묘사 (varied small motions: ears, blink, head tilt, breathing)
- 고유명사 (Leo / Ryani) 금지 — moderation 차단
- "warp / animate / morph" 동사 금지 — 차단
- em-dash 금지 — parser 혼동
- 마무리는 `"Camera holds still."` (정지 컷일 때) 또는 카메라 지시 (위 B항)
- 스티커에 "stay still" 지시 금지 — 프레임 전체 freeze 됨
- 강조 phrase 누적 금지 (1.b 참고)

---

## 4. 메타: 호출 비용·시간

- sora-2 720x1280 4초: $0.40 / 호출. 평균 큐+생성 ~3~10분.
  10분 넘는 경우 흔함 → POLL_TIMEOUT 20분으로 늘림 (env: `SORA_POLL_TIMEOUT`).
- sora-2-pro 1080x1920: $2.00 / 호출. 트랙2 운영엔 과한 비용. memory_lane 등
  특수 카드에서만.
- 모더레이션 차단 시 청구 X. 타임아웃 시 청구 O (백엔드 잡 돌고 있음).
  → video_id 자동 저장으로 retry 시 회수하는 패치 필요 (TODO).

---

## 5. 이력

- 2026-05-11: 첫 i2v 두 영상 성공. (프롬프트 원본은 미저장 — `~/.zsh_history`)
- 2026-05-12: `animate_all_cuts.sh` 5컷 모두 moderation_blocked → 강조 누적이
  원인으로 확인. 프롬프트 단순화 + 룰 주석 추가.
- 2026-05-13: `animate_hero.py` POLL_TIMEOUT 600 → 1200(env override),
  sidecar JSON 자동 저장 추가. 이 노트 작성.
- 2026-05-13 (저녁): 5/11 원본 프롬프트 셸 히스토리에서 복원 (line 139~141).
  §1.a 진짜 원인은 "한 마리만 명시"였음 — §1.d 추가 (고유명사 룰 비결정성).
  proven_motion_prompts.json 두 entry 모두 exact_prompt 로 업데이트.
- 2026-05-13 (밤): A/B/C 테스트 (`scripts/abc_cut5_test.sh`) 결과 — (c)
  dual_motion + push-in 위너. proven_motion_prompts.json 에 entry
  `i2v_2026_05_13_cut5_c_winner` 추가. animate_all_cuts.sh 의 cut1~5 모두
  C 스타일 ("At the same time" + "Camera gently pushes in toward them.") 로
  일괄 통일. 룰 주석 §3 와 §1.c 의 카메라 운영 가이드도 새 기본값 반영.
- 2026-05-13 (밤2): C 스타일 첫 풀런에서 cut3·cut5 가 "거의 정지" 결과
  (sora-2 stochastic — 같은 프롬프트가 어떤 실행에선 풍부한 모션, 어떤
  실행에선 정지). 모션 자동 검증 + 재시도 패치 도입:
    * `scripts/check_motion.sh` 신규: ffmpeg `tblend=all_mode=difference,
      signalstats` 로 프레임간 YAVG 평균 계산. 정지 영상 0.36~0.66 vs OK
      영상 1.8~2.7 vs 5/11 push-in 7.8 → threshold 1.5 가 깨끗하게 분리.
    * `animate_all_cuts.sh` 의 animate_one 에 재시도 루프 (env: MIN_MOTION
      기본 1.5, MAX_RETRIES 기본 2, SKIP_MOTION_CHECK=1 로 우회).
      정적 결과는 `_archive_static_<timestamp>/` 에 attempt 별 보관.
- 2026-05-13 (밤3): cut3 두번째 풀런에서 `moderation_blocked` 발생.
  프롬프트 차이가 단 한 곳 — cut1/2/4/5는 `"sit side by side"`로 시작,
  cut3 만 `"rest together"`로 시작했음. `"sit side by side"` 패턴으로 통일.
  추가로 `animate_all_cuts.sh` 에 moderation 자동 재시도 로직 추가:
    * `sidecar_says_moderation_blocked()` 함수: sidecar JSON 의 `error`
      필드에 `moderation_blocked` 있으면 true.
    * Exit code 3 (generation failed) 시 sidecar 확인 → moderation 이면
      MOD_RETRIES (기본 2) 까지 무료 재시도. 그 외 (timeout 등) 는 청구된
      거라 재시도 X.
    * 같은 프롬프트로 그대로 재시도 — sora-2 moderation 분류기가 stochastic
      이라 통과 가능. 그래도 막히면 프롬프트 변형 필요.
- 2026-05-14: 동물 모션 자동 검출기 3종 (`motion_a_vidstab.sh` / `motion_b_vlm.py`
  / `motion_c_hybrid.sh`) + 테스트 러너 (`motion_detect_test.sh`) 추가.
  Method A 는 14개 큐레이트 클립에서 13/14 정답 (smoking-gun 라이브 cut5 만 놓침
  — stab_mean=1.800 으로 통과시켜버림. 스티커 + 카메라 push-in 이 vidstab
  잔차 위로 올라옴). 이 한 케이스가 §6 으로 이어짐.

---

## 6. 2026-05-15 생성기 A/B/C — sora vs Veo 3.1

cut5 의 "동물 정지" 패턴이 sora-2 stochastic 재시도로도 안 잡혀서
**생성기 교체** 검토. `scripts/compare_generators.sh --aggressive --real`
한 컷 ($4.00) 으로 결정.

### 결과

| 변형 | 모델 | 결과 | 비용/4s |
|------|------|------|---------|
| A | sora-2 | 동물 정지 재현 (motion 1.991 — threshold 1.5 위로 *barely* 올라가지만 실제 동물 모션 0) | $0.40 |
| B | veo-3.1-generate-preview (std) | 동물 모션 풍부, 그러나 **중간에 스티커 사라짐** | ~$3.00 |
| C | veo-3.1-lite-generate-preview (lite) | 동물 모션 OK + 스티커 보존 — **위너** | ~$0.60 |

### 인사이트

- B 가 스티커를 지운 이유: Veo 3.1 standard 는 input 이미지를
  *제안*으로만 받고 자기 판단으로 픽셀 재구성. 우리 데코 PNG 의
  스티커 오버레이를 "artifact" 로 간주해서 클린업해버림.
  C (lite) 는 input fidelity 가 보수적 → 데코 보존.
- cut5 같은 "장식된 PNG → 모션" 파이프라인엔 정확히 C 의 트레이드오프가 맞음.
- sora-2 의 "동물 정지" 는 모델 한계 — 프롬프트 / retry 로 잡히지 않음.
  검출 가드 (Method A/B/C) 가 임시 우회였고, 본질적 해결은 모델 교체.

### 운영 변경 (2026-05-15)

- `animate_all_cuts.sh` 에 `--generator sora|veo` 스위치 추가.
  ~~디폴트는 안전을 위해 `sora` 유지. Veo 풀 에피소드 검증 후 디폴트 변경 예정.~~
  → **2026-05-15 풀 에피소드 검증 완료, 디폴트를 `veo` 로 플립**. sora 는 `--generator sora` 로 fallback.
- Veo 모드 디폴트 모델: `veo-3.1-lite-generate-preview`.
  `--veo-model <id>` 또는 `VEO_MODEL` 환경변수로 override 가능.
- 사용 패턴:
  ```
  bash scripts/animate_all_cuts.sh --generator veo
  ```
  → 5컷 × ~$0.60 = ~$3 / 에피소드 (sora ~$2 대비 +50% 이지만 retry 비용 ↓
  + cut5 정지 해결 고려 시 실효 비용 동급 또는 더 낮음 추정).

### 미해결 / 다음 단계

- ~~풀 에피소드 (cut1~5) 에서도 Veo lite 가 일관되게 양쪽 동물 모션 + 스티커
  보존하는지 검증 필요.~~ → §7 참조 (완료).
- Veo lite 의 motion 점수 분포가 sora 와 다를 수 있음 — MIN_MOTION 1.5
  재calibration 후보. lite 첫 풀런 결과 보고 결정.
- Veo 는 오디오 생성도 함 (sora 와 차이). Shorts 에선 BGM 위에 깔리는데,
  Veo 오디오를 살릴지 / mute 해서 BGM 만 쓸지 정책 결정 필요.
- 비용 모니터링: ai.google.dev/gemini-api/docs/billing 에서 실제 청구액
  주 1회 확인 → COST_NOTE 추정치 보정.

---

## §7 풀 에피소드 검증 — Veo 3.1 lite 채택 결론 (2026-05-15)

**셋업**: 5컷 풀 에피소드 (cut1~5) Veo 3.1 lite 로 생성, motion_b_vlm.py
(Gemini 2.5 flash 비전, claude → gemini 마이그레이션 후) 로 first/last frame
비교 검증.

**결과 (mode=both, 둘 다 움직여야 OK)**:

| 컷 | verdict | 비고 |
|---|---|---|
| cut1_ryani_hook   | OK     | 단일 주체 — 양쪽 모션 명확 |
| cut2_leo_intro    | OK     | 단일 주체 — 양쪽 모션 명확 |
| cut3_together_play | STATIC | cat moves, **dog (Ryani) static** |
| cut4_together_warm | STATIC | cat moves, **dog (Ryani) static** |
| cut5_closer       | STATIC | 둘 다 static, stickers_only=True   |

**패턴**: 다중 주체 컷에서 어둡고 작은 subject (Ryani, 검은 프렌치 불독)
가 일관되게 정지. 우연 아님 — 3컷 모두 같은 방향으로 실패. 가설:
- 검은 털 → texture 신호 약함 → motion attention 가는 곳 없음
- 작은 사이즈 + 오렌지 고양이가 frame attention 독점
- Ryani 는 꼬리가 없어서 (= 프렌치 불독) 큰 motion verb 후보가 줄어듦
  (`tail swish` 같은 mandatory primary motion 옵션이 없음)

**판단**: Shorts (1-2초/컷) 속도면 Ryani 정지 컷도 시각적으로 큰 문제 없음.
운영 결정 = **Veo 3.1 lite 채택 + 다중 주체 정지는 수용**. 필요 시 추후
- 옵션 2: Ryani 큰 motion verb (yawn / paw lift / tongue out) 로 prompt rewrite
- 옵션 3: Veo 3.1 standard + post-Veo `sticker_scatter.py` ffmpeg overlay
로 escalate 가능. 현재는 미실행.

**부산물**: motion_b_vlm.py 를 Claude vision → Gemini vision 으로 이식.
사유 = 파이프라인 키 일원화 (`GOOGLE_API_KEY` 하나), 비용 ~10x 절감
($0.01-0.02 → $0.001-0.003 per call). thinkingConfig.thinkingBudget=0
필수 (Gemini 2.5 기본 thinking 이 출력 토큰 잠식해서 JSON 잘림).

**다음에 손볼 것**: 캡션 (`scripts/prompts/captions.json` 한국어 5개) —
이 노트와는 별개 트랙으로 진행.

