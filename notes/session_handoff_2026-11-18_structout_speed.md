# Session handoff — 2026-11-18 (2) · Structured Outputs 배선 + 속도 측정(정정)

**한 줄:** 모델별 프롬프팅 감사(별도 핸드오프 `_prompt_audit`) 이후, **Structured Outputs S1·S2를
구현·배포**하고 **속도 레버를 프로덕션 실측**했다. ★측정에서 내 "2× thrash" 해석이 **틀렸음을 정정**:
thrash는 문제가 아니고 **렌더 지속시간 자체**가 레버다. 다음 = 지속시간 레버 구현(PD가 **다운스케일 고민 중**).

---

## ✅ SHIPPED (origin/main, VM `de077af`+ 라이브)

**Structured Outputs (플래그→구현):** "프롬프트로 JSON 애원 말고 스키마로 강제"(가이드 §VLM). 값어치 큰 곳만.
- **S1 `5141ca7`** Gemini `responseSchema` — `scripts/video_actions.py`의 `actions()`(액션배열)+`clip_knowledge`
  (중첩). 큰 truncation-prone 출력에 constrained decoding. 실클립 검증 OK.
- **S2 `6df8d06`** OpenAI strict `json_schema` — `agents/openai_vision.py` `ground_frames`(9필드). gpt-4o 수용 검증.
- 둘 다 kill-switch(`STRUCT_OUT_GEMINI`/`STRUCT_OUT_OPENAI`, 기본 ON)+스키마거부 시 무스키마 재시도+기존 파서 폴백.
- **설계원칙**: 스키마=기존 프롬프트 모양 미러(동작 무변, 강제만). **작은출력 게이트**(cameraman `_cut_*`·
  motion_b_vlm·reviewer)는 소형출력+thinkingBudget0라 truncation 無 → **의도적 스킵**(churn위험>이득).
- **S3 (Claude Writer/Director `output_config`) = DEFER**(task #7). 복잡 중첩스키마+loose파서 이미 작동=최저ROI.
  할거면 별도 세션서 레인별·패스별 concept 스키마 신중히. (anthropic 0.103.1 `messages.create(output_config=)` 지원 확인됨.)
- retro §4.7에 반영(`ffc7452`).

---

## ★ 속도 측정 — 결론 (정정 포함, 전부 프로덕션 실측·무료)

**측정 방법(다음에도 이걸로):** 레거시 테이블 `retry_log`·`daily_proposals`는 **stale**(RF-only 전환 후 안 씀).
현재 경로 = **`runs` 테이블**(`agent`,`started_at`,`finished_at`,`status`,`card_id`). VM DB는 `rianileo` 전용
(`-rw-------`)+sqlite3 CLI 없음 → `sudo -n -u rianileo .venv/bin/python`(패스워드리스 OK)로 쿼리. SQL에 작은따옴표
금지(ssh 중첩따옴표 지옥) → 문자열은 `char(...)` 코드, 날짜리터럴 피하고 `ORDER BY ... DESC LIMIT`.

**★정정 — "카드당 2× 렌더=thrash"는 오독이었다:** 파보니 카드당 = **0초 no-op run 1개 + 진짜 렌더 1개**
(`'ok,ok'`, dur `'0.0, <big>'`). thrash 아님. 그 0초 run이 per-day 평균에 섞여 **실제 렌더시간을 절반으로
과소보고**했다. (교훈: 집계 전 raw를 봐라 — digging이 오독을 잡았다.)

**정확한 그림:**
- **thrash는 문제가 아니다**(통제됨) — PD 가설 "프롬프트로 나아졌나"에 대한 답. 11-17 품질바닥 수정이 폭주를 막음.
- **진짜 레버 = 렌더 지속시간.** 최근 RF 렌더 ~4–20분(전형 **7–9분**), 전체 **54%가 10분 초과**, 병리적 6.3시간
  멈춘 렌더·일부 `error`(gutted/missing-source 잔여)도. `rf_render_stage_profiling`(ffmpeg 4K 재인코드 41%+
  직렬 VLM 43%)과 일치.

---

## ☐ 다음 세션 — 지속시간 레버 (측정 끝, 구현 단계)

### ★ PD 고민 지점 = 다운스케일 (핵심 trade-off, 결정 필요)
"trim 720p 프록시 다운스케일"이 **품질손실 아니냐**는 PD 우려. **뉘앙스가 중요**:
- 우리 최종 딜리버리 = **쇼츠 1080×1920(1080p)**, 4K 아님. 그런데 iPhone **4K 소스를 trim/assembly/burn 매
  단계 재인코드** → 1080p로 납품되는 제품에 4K 연산을 낭비. → **작업 해상도를 딜리버리(1080p)로 맞추기 =
  품질손실 아님**(YouTube가 어차피 ~1080p로 전송). "720p **최종**"이면 손실이지만, 그건 제안이 아님.
- 설계 결정: (a) 최종=1080p 작업(4K 재인코드 제거, 손실 0이 기본안) vs (b) 720p는 **VLM 프록시에만**(그라운딩은
  4K 불필요) vs (c) 720p 최종(비권장, 손실).

### 품질-무관 레버 (다운스케일 고민 없이 먼저 가능)
1. **직렬 VLM 그라운딩 병렬화** (render의 ~43%·품질영향 0) — gate/action/tail-trim/grounded/face 직렬→병렬.
2. **stream-copy where possible** — trim이 재인코드 대신 `-c copy`로 가능한 구간(키프레임 정렬) 활용.
3. **멈춘/에러 렌더 처리** — 6.3시간 outlier·`error` 렌더에 wall-clock 캡/조기 중단.

### 최대 레버 (다운스케일 — PD 결정 후)
4. **trim 단계 해상도 정합**(4K→1080p 작업) — profiling의 "최대레버"(ffmpeg 41%). 손실 0 기본안(위 (a)).

**권장 순서: 1(VLM 병렬, 공짜 무손실)·3(stuck/error 캡) 먼저 → 그다음 PD 다운스케일 결정 후 4.**
전부 `cameraman` 렌더경로 load-bearing → 각 변경 kill-switch + STAGE_TIMING=1(`agents/stage_timing.py`, 이미
있음)로 before/after 계측 + pipeline-change-impact. 돈 드는 e2e 렌더 전 공짜 runs-테이블 재측정으로 확인.

cf 메모리: `rf_render_stage_profiling`(스테이지 내역)·`cli_quality_drift_root`(작동코드 churn 금지)·
`model_prompting_guide`. task #7(S3 defer)·#8(2×조사 완료=no-op).
