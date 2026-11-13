# Giri 재설계 — PD의 말과 사고방식으로 (PD 2026-11-12)

> **다음 세션 착수용 설계.** 지금은 설계만, 구현은 다음.

## 북극성 (PD 발화)

> "기리는 사실 핸드오프나 retro보다는 **내가 한 말들을 모아서 내 피드백 방식으로 사고**할 수 있게
> 설계해야하는데." — PD 2026-11-12

Giri는 **PD의 거울**이어야 한다. 지금 Giri는 러버스탬프로 drift했고(giri-update 스킬이 그 치료),
더 깊은 문제는 **소스가 틀렸다**는 것:

- **retro/handoff = 내가 synthesize한 2차 가공물.** 내 해석·요약이 섞인 lossy 증류라, 그걸 소스로 하면
  Giri는 *"내가 생각하는 PD"*의 거울이지 **진짜 PD**가 아니다.
- **ground truth = PD의 raw 발화.** PD가 실제로 한 말들 + PD가 판단하는 *방식* 자체.

## 설계 — 3단계

### 1. PD 발화 수집 (corpus)
PD가 실제로 한 피드백을 모은다. 소스:
- **Slack** — board 채널 + mp4 리뷰 스레드(`/veto` 이유, 리뷰 코멘트). `agents/notify` / SLACK_BOARD_CHANNEL.
- **세션 대화** — CLI 세션에서 PD가 한 지적(이 세션처럼: "항상 앞만 쓴다", "분할/전환 원했다", "같은 나들이
  중복"…). 세션 transcript / progress_log `[board]`·`[CLI]`.
- **`pd_notes` / `pd_taste`** — 이미 PD 선택·설명 축적(함미하비 클립 설명 등).
- **progress_log** — board↔CLI 공용 로그의 PD 결정.

각 발화에 **맥락**(무엇을 리뷰하다 나온 말인지) + 타임스탬프. ★retro는 *참고*로만, 소스 아님.

### 2. PD 판단 스타일 도출 (taste + reasoning profile)
수집한 발화를 분석:
- **무엇을 반복해서 flag하나** — dup·캡션↔footage 불일치·날조·"앞만 쓰기"·split 원함·톤…
- **어떻게 지적하나** — 어디서 심각도를 높이나, PD 특유의 프레이밍("끝까지 돌려봐", "이건 근본이 아니지"),
  칭찬 vs veto의 경계.
- **PD의 사고 순서** — 뭘 먼저 보나(훅? 캡션? 중복?).
→ 이게 PD의 "판단 스타일" = Giri가 흉내 낼 대상.

### 3. Giri가 PD처럼 사고
- **결정론 게이트** — 객관적인 것(dup·그라운딩·나들이 재사용·제목 CAP)은 코드로(giri-update 규율,
  "LLM 못 믿으면 결정론"). 이미 있는 것: `_outing_reuse_gate`·`_clip_reuse_gate`·title gates.
- **PD 스타일 LLM 렌즈** — 주관적인 것(미감·톤·"이게 재밌나")은 LLM이 **PD 발화 corpus를 컨텍스트로**
  PD 방식으로 판단(skeptical 디폴트, 구체적, 근본 지향). 제너릭 루브릭 아님.

## 자가-감사 배치 (체계화)

PD 메타: "배치로 돌아야 / 세션시작마다?" → 안전한 합집합:
- **주기 배치**(매 세션 아님 — 과함): PD 발화 corpus를 읽어 **"PD가 반복해 말했는데 Giri가 아직
  enforce 안 하는 기준"을 갭 리포트로 surface**. ★블라인드 자동 코드업데이트 금지(LLM 자동합성 위험).
- 각 갭은 **사람이 결정론 게이트 + 회귀로 적용**(giri-update).
- **세션 시작**엔 최신 갭 리포트만 싸게 읽어 보여줌(전체 감사 X).

## 가드레일
- **PD의 말 = ground truth, retro = 보조.** 내 요약을 PD로 착각 금지.
- **결정론 > LLM 신뢰** (giri-update 비협상).
- **빌드 OFF → 스모크 → 플립** (v2 롤백 규율).

## 코드 앵커 (다음 세션)
- Giri 렌즈: `agents/reviewer.py`(CHECK0·점수차원·판정맵), canon `agents/canon.py`.
- PD 발화 소스: Slack API(board 채널)·`pd_notes`/`pd_taste` 테이블·`agents/progress_log`·세션 transcript.
- 자가-감사: 신규 배치(예 `agents/giri_audit.py`) + run_job.sh cron.

cf giri-update 스킬·[[giri_outing_reuse_gate]]·[[grounding_pd_notes_gpt4omini]](pd_notes가 최고 소스)·
review-learning(제너레이터 짝).
