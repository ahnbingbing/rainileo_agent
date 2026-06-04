# Session Log — 2026-05-13

오늘 한 일 정리. 내일 깨면 §"이어서 할 일" 부터 보면 됨.

---

## TL;DR

1. 트랙2 카툰 5컷 i2v 운영 — sora-2 호출 신뢰성 문제 다수 발견·해결.
2. A/B/C 테스트로 위너 프롬프트 패턴 (dual motion + camera push-in) 확정.
3. `animate_all_cuts.sh` 에 자동 검증·재시도 로직 2중 도입 — 정적 결과(유료) + moderation_blocked(무료) 둘 다 자동 처리.
4. cut1·2·4 완성. cut3·cut5 미완 — 마지막 패치 들어가기 전 버전으로 user 가 실행했어서 mod_retry 가 안 돌았음. 깨끗한 상태로 정리해놨으니 내일 그냥 재실행만 하면 됨.

---

## 이어서 할 일 (내일 첫 명령)

**중요: 단순 재실행 NO.** 모션 검증 로직에 근본 한계 발견 (§10). 먼저 검증
방식 개선 결정 후 재실행.

### §10 발견 요약
야간 재실행 결과 새 cut3·cut5 가 motion check 통과(또는 max-retry 후 keep)
했음에도 user 가 보기엔 여전히 "스티커만 움직임 / 동물 정지" 였음. 프레임
분석 결과:

| 컷 | full YAVG | top / center / bottom | 진단 |
|---|---|---|---|
| cut3 new | 0.628 | 0.80 / 0.74 / 0.41 | 전부 낮음 — 정말 정지 (retry 다 쓰고 keep 된 거) |
| cut5 new | 1.592 | 0.48 / **2.71** / 1.33 | center 만 높음 — 카메라 push-in 으로 가짜 모션 통과 |

근본 원인: YAVG 는 "프레임간 픽셀 변화량" 일 뿐, "주체 동작" 이 아님.
카메라 push-in 만 들어가도 (center pixel 들이 zoom 으로 이동) YAVG 가 충분히
올라가서 threshold 통과해버림. 스티커 fade in/out 도 마찬가지로 점수 보탬.
cut5_c winner (abc test) 의 mean 2.73 과 cut5 new 의 1.59 가 너무 가까워서
단순 mean threshold 로는 좋은 모션 vs 카메라-only 를 분리 불가.

### 내일 결정해야 할 것 — 두 옵션

**(A) Camera-compensated motion (자동, 추가 비용 0, 구현 비용 ↑↑)**
- ffmpeg `vidstab` 또는 opencv 로 카메라 글로벌 모션 (translation + zoom) 추정
- 카메라 모션 compensate 후 residual = subject motion 만 측정
- 새 metric 으로 threshold 재캘리브레이션 필요

**(B) VLM 기반 검증 (반자동, 호출당 ~$0.01, 구현 비용 ↓)**
- 영상 first/last 프레임 추출 → Claude vision (또는 GPT-4V) 에 "Did the cat
  or dog visibly change pose between these two frames? Yes/No + brief reason"
- 정확도 가장 높음. 비용 미미. API 키 관리 추가 필요.
- 시즌1 5컷 단위면 호출 5번 × $0.01 = $0.05 — 무시할 수준.

**(C) 하이브리드 (추천)**
- 빠른 1차 필터: YAVG mean (현재 로직 그대로) 로 명백히 정적인 거 거르기
- 2차 정밀: 1차 통과한 영상에 대해서만 VLM 호출로 subject motion 확정
- 두 단계 다 통과해야 final OK

### 임시 응급 처방 (내일 옵션 결정 전 잠깐만 쓸 거면)
- `MIN_MOTION=3.0` 으로 threshold 올려서 카메라-only 통과 차단. 단 정확
  threshold 는 더 많은 샘플 필요. cut5_c winner 가 2.73 이라 3.0 이면
  위너도 가끔 떨어질 수 있음. 안정 운영용은 아님.

### 그 외 손볼 거
- §6 의 cut3/cut5 야간 run 의 mod-retry 미작동 원인 (스크립트 patch 시점
  vs 실행 시점 race) 는 이미 해결됨 (지금 다음 run 부터는 정상 작동).
- 새 cut3 (0.628) 와 cut5 (1.592) mp4 는 user 가 거부했으므로 사용 X.
  다음 run 시 둘 다 재생성 필요.

---

## 1. 5/11 영상 프롬프트 복원 + 라이브러리화

5/11 22시쯤 잘 나온 mp4 두 개의 원본 프롬프트를 `~/.zsh_history` 에서 복원:

- 영상 a (head rub): `"Ryani softly blinks her eyes, gentle head tilt to camera, calm and warm"`
- 영상 b (push-in): `"Leo slowly blinks his eyes, gentle head tilt, soft ear flick, warm curious gaze"`

발견:
- 두 프롬프트 모두 **단 한 마리만** 명시 (Ryani 또는 Leo). 나머지 동작·카메라 무빙은 전부 sora-2 자체 추론. 즉 짧고 평탄한 프롬프트로도 다이내믹한 결과 가능, 하지만 의도하지 않은 모션도 함께 발생할 수 있는 양날의 검.
- 두 프롬프트 모두 고유명사 (Leo/Ryani) 포함이었는데 통과 — `animate_all_cuts.sh` 의 "Avoid proper nouns" 룰이 비결정적임을 시사. 자동 운영에서는 보수적으로 회피 유지.

→ `notes/proven_motion_prompts.json` 에 entry 두 개 저장. 트랙1(실사) 운영 중 시나리오 매칭 raw 푸티지 없을 때 i2v 백업으로 사용.

## 2. A/B/C 위너 결정 (`scripts/abc_cut5_test.sh`)

같은 input (cut5_closer.png) 으로 3가지 프롬프트 스타일 비교, 비용 $1.20:

| 라벨 | 스타일 | 결과 (motion score) | 평가 |
|---|---|---|---|
| (a) baseline | 37단어, "Camera holds still." | 0.356 | 거의 정지 |
| (b) compressed | 17단어, 카메라 미지정 (5/11 스타일) | 1.064 | 보통 |
| (c) dual_pushin | "At the same time" + push-in | 2.730 | **위너** |

C 스타일 채택:
```
An orange tabby cat and a small black French bulldog sit side by side.
The {animal_A} slowly {action_A}.
At the same time the {animal_B} {action_B}.
Camera gently pushes in toward them.
```

→ `notes/proven_motion_prompts.json` 에 `i2v_2026_05_13_cut5_c_winner` entry 추가. `animate_all_cuts.sh` cut1~5 프롬프트 모두 C 스타일로 일괄 통일.

## 3. 모션 자동 검증 — `scripts/check_motion.sh` 신규

C 스타일 첫 풀런에서 cut3·cut5 가 "거의 정지"로 나왔음 (sora-2 stochastic — 같은 프롬프트가 어떤 실행에선 풍부한 모션, 어떤 실행에선 정지).

지표: ffmpeg `tblend=all_mode=difference, signalstats` 로 프레임간 YAVG 평균 계산.

| 영상 카테고리 | YAVG mean 범위 |
|---|---|
| "거의 정지" (사용자 BAD 판정) | 0.36 ~ 0.66 |
| "스티커만 움직임" | 0.53 ~ 0.66 |
| 사용자 OK (cut1/2/4) | 1.80 ~ 2.18 |
| 5/11 head-rub 검증 영상 | 1.88 |
| abc (c) 위너 | 2.73 |
| 5/11 push-in 드라마틱 | 7.82 |

→ **threshold 1.5** 가 깨끗하게 분리. `check_motion.sh` 기본값.

Usage:
```bash
bash scripts/check_motion.sh path/to/clip.mp4 [threshold]
# exit 0 = sufficient motion, 1 = static, 2 = error
```

## 4. `animate_all_cuts.sh` 재시도 2중화

### 4-a. 정적 결과 자동 재시도 (유료, 패치1)

생성 후 `check_motion.sh` 호출 → 정적이면 mp4 + sidecar 를 `_archive_static_<timestamp>/` 로 이동 + 같은 프롬프트로 재시도, MAX_RETRIES (기본 2) 까지.

### 4-b. moderation_blocked 자동 재시도 (무료, 패치2)

생성 실패 (exit 3) 시 sidecar JSON 의 `error` 필드 확인:
- "moderation_blocked" 있으면 → 무료라서 같은 프롬프트로 재시도 (MOD_RETRIES 기본 2). Stochastic moderation 이라 통과 가능.
- timeout / API 에러 등 그 외 → 청구된 거라 재시도 X.

### 새 env vars

| Var | 기본 | 설명 |
|---|---|---|
| `MIN_MOTION` | 1.5 | YAVG threshold |
| `MAX_RETRIES` | 2 | 정적 결과 재시도 횟수 (유료) |
| `MOD_RETRIES` | 2 | mod_blocked 재시도 횟수 (무료) |
| `SKIP_MOTION_CHECK` | 0 | 1이면 모션 검증 완전 우회 |
| `SORA_POLL_TIMEOUT` | 1200 | 컷당 폴 타임아웃 초 (env override, animate_hero.py 패치) |

### sidecar JSON 패치 (`scripts/animate_hero.py`)

5/13 오전에 한 거 다시 정리:
- 매 호출 시작 시 `<out>.meta.json` 자동 저장: prompt, video_id, model, size, started_at
- 완료/실패/타임아웃 시 status_final, error, finished_at 업데이트
- 타임아웃 나도 video_id 가 sidecar 에 남아있으므로 백엔드 잡 회수 가능 (`client.videos.download_content` 으로 수동 회수 — 자동화는 아직 TODO).

## 5. cut3 "rest together" 학습

C 스타일 풀런 두번째에서 cut3 만 mod_blocked. 차이는 단 한 곳:
- cut1/2/4/5: `"sit side by side"` 시작 → 통과
- cut3: `"rest together"` 시작 → 차단

→ cut3 도 `"sit side by side"` 로 통일. 5컷 전부 동일 prefix.

## 6. 5/13 마지막 시도 흐름 (사용자 야간 run)

```
cut1/2/4  → 22:10~22:15 통과
cut3 attempt1  → 정적 → archive
cut3 attempt2  → 정적 → archive
cut3 attempt3  → mod_blocked (free)  ← mod-retry 가 발동했어야 했는데...
cut5 attempt1  → 정적 → archive
cut5 attempt2  → mod_blocked (free)  ← 여기도 마찬가지
```

원인: user 가 실행한 시점의 `animate_all_cuts.sh` 가 mod-retry 패치 들어가기 **직전** 버전이었음. 스크립트 마지막 수정 시각 22:35:16 vs cut5 mod_blocked 종료 22:35:42 — 패치가 적용되지 않은 상태로 돌아간 거.

지금은 패치 다 들어가 있고 stale sidecar 정리됐음. 내일 그대로 재실행하면 정상 작동.

---

## 7. 오늘 누적 비용 (sora-2 호출)

| 항목 | 횟수 | 비용 |
|---|---|---|
| 5/13 아침 첫 풀런 (스택 강조로 모더 차단) | 5 × mod_blocked | $0 |
| A/B/C 테스트 (`abc_cut5_test.sh`) | 3 paid | $1.20 |
| C 스타일 첫 풀런 (cut1~5 OK 4개 + cut5 정적 1개) | 실제로는 OK 5개 paid | $2.00 |
| cut3 + cut5 재시도 run | 3 정적 paid + 2 mod_blocked free | $1.20 |
| **누계** | | **~$4.40** |

크레딧 밸런스 모니터링 필요. 다음 run worst case $2.40 추가.

---

## 8. 파일 변경 요약

신규:
- `scripts/abc_cut5_test.sh`
- `scripts/check_motion.sh`
- `notes/sora2_motion_lessons.md`
- `notes/proven_motion_prompts.json`
- `notes/session_log_20260513.md` (이 파일)

수정:
- `scripts/animate_hero.py` — POLL_TIMEOUT env override, sidecar JSON 자동 저장
- `scripts/animate_all_cuts.sh` — 프롬프트 단순화 → C 스타일 통일 → 정적 자동 재시도 → mod 자동 재시도, 4단계 진화

Archive 폴더:
- `data/output/animated/_archive_pre_pushin_20260513/` — 5/13 아침 단순화 버전 5컷
- `data/output/animated/_archive_static_user_flagged_20260513/` — user 가 정적이라 지목한 cut3+cut5 첫 C 스타일
- `data/output/animated/_archive_static_20260513_222627/` — 자동 재시도가 모은 정적 attempt들 + 패치 이전 mod_blocked sidecar들

---

## 9. 미완 / 다음 안건

### 단기 (내일~모레)

- [ ] cut3 + cut5 재실행해서 5컷 완성 (위 §"이어서 할 일")
- [ ] 5컷 완성 후 트랙2 (카툰) episode 1 video 조립 단계 — TTS + 자막 + BGM 합치기
- [ ] 만약 mod_retry 2번 다 써도 cut3/cut5 막히면 프롬프트 단어 변형 (예: "swishes its tail" 의 다른 동사로 교체)

### 중기 (이번 주)

- [ ] 트랙1 (실사+TTS+자막) 파이프라인 스캐폴딩
- [ ] 트랙3 (Kling 가상) 파이프라인 스캐폴딩
- [ ] Writer Agent 컨셉카드 스키마에 `render_track` (1/2/3) + `slot` (morning/noon/evening) 필드 추가
- [ ] `metrics_d7` 테이블 추가 (YouTube Analytics D+7 데이터 수집용)
- [ ] launchd 3 슬롯 등록 (morning/noon/evening 자동 업로드)

### 장기 (한 달 후)

- [ ] 한 달 라틴 스퀘어 로테이션 완료 후 데이터 정리
- [ ] Multi-Armed Bandit (Thompson Sampling) 도입해서 위너 트랙 수렴
- [ ] 자동 video_id 회수 패치 (타임아웃 시 백엔드 잡 sidecar 통해 재다운로드)

### Tech debt

- [ ] sora-2 모더레이션 비결정성 정량화 — 같은 프롬프트 반복 호출해서 통과률 측정 (비용 발생, 우선순위 낮음)
- [ ] check_motion.sh 의 threshold 1.5 가 다른 컷/시즌에도 맞는지 검증

---

## 10. 야간 재실행 결과 분석 — motion check 의 근본 한계

(위 "이어서 할 일" §10 참조)

### 새 cut3 frame 분석
- 첫 프레임 vs 마지막 프레임: 시각적으로 완전 동일.
- 동물 둘 다 포즈 변화 0. 스티커 (top-right ❤️, bottom-left 🐾) 도 거의
  변화 없음. 0.628 mean 은 미미한 디테일 노이즈.
- 즉 max_retries 다 써도 sora-2 가 매번 거의 정지 영상 생산.

### 새 cut5 frame 분석
- 첫 프레임: 화면 가득 ✨ 스파클 별 + ❤️ + ✓ 스티커
- 마지막 프레임: 스티커 대부분 사라짐, 카메라 push-in 으로 고양이 크게 보임
- 고양이/강아지 자체 포즈는 거의 그대로 — 카메라 줌만 들어간 것
- center 60%x50% YAVG=2.71 인 이유는 push-in 으로 인한 픽셀 이동량,
  실제 주체 동작 아님

### 결론
지금의 `check_motion.sh` (full-frame YAVG mean ≥ 1.5) 는:
- ✓ 명백히 정지인 영상 (mean < 1.0) 은 정확히 거름
- ✗ 카메라 push-in 만 있고 주체 정지인 영상은 통과시킴
- ✗ 스티커만 활발히 움직이는 영상도 부분 점수 줌

→ 메트릭 자체를 더 정교하게 (camera compensation 또는 VLM) 가야 함.

---

내일 봐. 잘 자.
