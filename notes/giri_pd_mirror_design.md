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

## 코드 앵커 (현황 매핑 — 2026-11-13 실측)
- **Giri 렌즈**: `agents/reviewer.py:review()`(1711) — LLM 호출(1843, `VLM_MODEL` gemini-flash + `REVIEW_PROMPT` 66) + **결정론 게이트 시퀀스**(2047~2147, 각 게이트는 `report["점수"]` cap + 판정 강등 + `_*_override` 마커). canon `agents/canon.py`.
- **PD 그라운드트루스 이미 주입**: `_pd_groundtruth_block`(1302, `assets.pd_notes` 컬럼을 컷별 asset_id로 당겨 authoritative 주입, 호출 1752).
- **★pd_taste corpus는 이미 존재하지만 반쪽**: `agents/pd_taste.py`(테이블 `pd_selections` — lane/kind{concept,clip,cut,caption,schedule,tone}/decision{approve,veto,swap,correct,pick,reject}/reason, `log_selection` 52·`taste_digest` 82·`_SEED` 9행 118). **소비는 제너레이터만**(producer RF 3715·concept_brainstorm 430·still_select 126) — **Giri는 안 읽음**(review-learning이 제너레이터만 갱신, 검수기 미sync = 러버스탬프 drift의 코드 증거). **먹이기도 끊김**: 실측 12행·마지막 2026-06-22(veto→log 경로가 6월 이후 비활성).
- 배치 패턴(미러용): `scripts/_batch_gate_audit.py`(63, 날짜별 게이트 발화 audit→Slack)·`agents/pd_reviewer.py:review_batch`(339, 일 03:00). Slack: `agents/notify.py`는 **POST만**, **board 채널 READER 없음**(신규 필요). 세션 transcript: 디스크 비상주(캡처 필요).

## 빌드 플랜 (phased — 전부 OFF→스모크→플립)

**빌드 결과 (2026-11-13 — P0~P3 코드 완료, 커밋 92ca48b·2ab4baf·P1):**
- **P0 ✅** 소비(reviewer.py `taste_digest(for_review)` 주입, `GIRI_PD_TASTE` OFF) + 먹이기 보강(`/veto`·스레드 veto가 PD 이유 캡처, 항상ON). ★정정: 먹이기는 **안 끊겼었다** — `slack/app.py:_do_veto`가 VM에서 늘 로깅. 내가 본 로컬 "6-22 중단"은 **stale 로컬 DB 오진**(WHERE-THIS-RUNS: 운영 DB=VM). 실제 문제는 ①얇음(concept veto만·제네릭 이유) ②Giri 미소비. 둘 다 수정.
- **P2 ✅** PD 판단 스타일 렌즈(`PD_LENS_BLOCK`: skeptical·캡션↔footage 먼저·근본>표면, `GIRI_PD_LENS` OFF).
- **P3 ✅** 자가-감사 배치(`agents/giri_audit.py`): corpus+게이트열거→LLM 갭리포트→`notes/giri_audit_latest.md`+Slack. 코드 자동수정 없음. 스모크=얇은 로컬 corpus서도 실갭 6건(RF사진컷·컨셉드리프트·활동편성중복·과거veto유사·메모리레인구조).
- **P1 ✅코드(라이브 미검증)** `agents/pd_slack_corpus.py`: board 스레드 PD 코멘트 수집→corpus. 결정론 필터 검증. ★**NEEDS-LIVE-SLACK-SMOKE**(첫 conversations.replies 리더, 봇 history 스코프+PD식별 VM 검증 필요), OFF/opt-in.
- **남은 플립/배선**: ①`GIRI_PD_TASTE=1`·`GIRI_PD_LENS=1` VM 플립(스모크 후) ②giri_audit 주cron(run_job.sh)+세션시작 훅이 `giri_audit_latest.md` 읽기 ③P1 라이브 스모크→cron ④corpus가 VM서 실제 두터운지 확인.

**스파인:** 기반(pd_selections)은 있었는데 **Giri가 corpus를 안 소비**하고 로깅이 **얇았다**("먹이기 끊김"은 stale 로컬 오진). 새 인프라를 짓기 전에 **있는 걸 다시 흐르게**(소비+보강)가 최대 레버였다(RF 비주얼그래머의 "허용≠행동"과 동형).

### Phase 0 — 흐름 복구 (가장 싸고 큰 레버)
- **먹이기 재배선**: `/veto`·`/concept`·PD 캡션교정 경로가 `pd_taste.log_selection`을 부르는지 확인→끊긴 곳 복구(6월 이후 0행 근본). 각 이벤트에 **맥락**(무엇을 리뷰하다 나온 말) + 타임스탬프.
- **Giri 소비**: `taste_digest(lane)`를 `REVIEW_PROMPT`에 **PD 발화 컨텍스트**로 주입(제너레이터가 이미 쓰는 그 digest를 검수기에도). 킬스위치 `GIRI_PD_TASTE=0`. 스모크=digest 있는 에피 1편 리뷰가 PD 원칙 반영하는지 육안.
- 검증: 과거 PD가 veto한 유형을 Giri가 이제 감점하는지 회귀 1~2건.

### Phase 1 — Corpus 확장 (PD의 raw 발화로)
- **소스 통합**(설계 §1): pd_selections(veto/correct) + `assets.pd_notes` + `progress_log` PD 결정 + **Slack board/mp4-리뷰 스레드 READER 신규**(`slack_sdk conversations_history`, SLACK_BOARD_CHANNEL; veto 이유·리뷰 코멘트 당겨옴). ★retro는 *보조*로만.
- 저장: pd_selections를 확장하거나 `pd_utterances` 신규(발화+맥락+ts+출처). 킬스위치로 OFF 수집부터.

### Phase 2 — PD 판단 스타일 렌즈 (설계 §2·§3)
- corpus에서 **반복 flag**(dup·캡션↔footage·날조·"앞만 쓰기"·split·톤) + **PD 사고순서/프레이밍**("끝까지 돌려봐", "이건 근본이 아니지") 도출 → Giri `REVIEW_PROMPT`에 **PD 렌즈 블록**(skeptical 디폴트·구체·근본지향, 제네릭 루브릭 아님). 객관=결정론 게이트 유지, 주관=이 렌즈.
- 킬스위치 `GIRI_PD_LENS=0`. 스모크 후 PD 스팟체크 통과시만 플립.

### Phase 3 — 자가-감사 배치 (설계 §"자가-감사")
- 신규 `agents/giri_audit.py`(`_batch_gate_audit`/`review_batch` 미러): PD corpus를 읽고 현 Giri 게이트(2047~2147)를 열거→ **"PD가 반복해 말했는데 Giri가 아직 enforce 안 하는 기준"을 갭 리포트로 Slack**. ★블라인드 자동 코드업데이트 금지(LLM 자동합성 위험).
- 주기 cron(주 1회 등, run_job.sh) — 매 세션 아님. **세션 시작**엔 최신 갭 리포트만 싸게 읽어 보여줌.
- 각 갭은 **사람이 giri-update로 결정론 게이트 + 회귀** 적용(Phase 0~2가 흐름, 이게 지속 루프).

### 가드레일 (전 Phase)
- **PD 말=ground truth, retro=보조** / **결정론 > LLM 신뢰**(giri-update 비협상) / **OFF→스모크→플립**(v2 롤백 규율) / 자동 코드업뎃 금지(갭 surface만).

cf giri-update 스킬·[[giri_title_defect_class]]·[[grounding_pd_notes_gpt4omini]](pd_notes가 최고 소스)·
review-learning(제너레이터 짝 — 이번 플랜이 그 "검수기 짝"을 메움).
