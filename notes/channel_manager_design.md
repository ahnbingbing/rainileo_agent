# 채널 매니저 에이전트 — 설계 (agents/channel_manager.py)

> 한 줄: Giri가 *개별 회차*를 대중 시각으로 거르는 게이트라면, 채널 매니저는
> *채널 전체*를 데이터로 운영하는 전략가다 — 패키징(제목/태그)·배치 할당·
> "어디 더 넣을지" 추천을 한 에이전트가 소유한다. **이미 있는 엔진은 재활용,
> 빠진 결정·패키징 레이어만 새로 얹는다.**

## 왜 (지금의 구멍)

- **업로드 패키징이 약한 고리**: 컨셉의 "스토리 제목"이 그대로 유튜브 제목이 되고,
  해시태그는 고정 3개(#랴니#레오#펫), 설명은 제목 복붙. 핀 카드는 "Ryani & Leo"로
  추락. 검색/훅 최적화 로직이 0이다. (`producer.py:2647,3272` / 컨셉 프롬프트엔 hashtag 필드 없음)
- **bandit이 측정만 하고 조종을 안 함**: `agents/bandit.py`가 lane/timeslot/arm
  포스테리어·P(best)를 다 계산하는데 `choose_*`를 아무도 안 쓴다. `launch.day_assignments`는
  고정 2일 라틴스퀘어. (`bandit.py:377,388` dead-wire / `launch.py:73`)
- **"어디 더 넣을지" 추천 부재**: board_agent는 *예약된 것*만 보고(로그+stale DB),
  *추천*은 안 한다.
- **Giri는 이미 대중-렌즈** ("스크롤하는 유튜브 시청자 대역")지만 회차 게이트일 뿐,
  포트폴리오(누적해서 뭐가 이기나) 뷰가 없다.

## 재활용 vs 신규

| 레이어 | 재활용(있는 것) | 신규(얹을 것) |
|---|---|---|
| 리워드/포스테리어 | `bandit.py` Normal-Normal + P(best), `video_performance` | arm 차원 확장(패키징 톤) |
| 성과 수집 | `youtube/analytics.py` 48h views/retention | CTR·노출·트래픽소스·7/28d 윈도우 |
| 대중 리뷰 | Giri 렌즈(`reviewer.py`/giri spec) | 포트폴리오 전략 뷰 |
| Slack/제어 | `board_agent.py` NL 라우터+확인플로우+status | "뭘/어디 더" 추천 인텐트 |
| 업로드 경계 | `youtube/upload.py:upload_short`(건드리지 않음) | 풍부한 `draft.{title,description,hashtags}` 생성기 |

## 핵심 원리 — 패키징은 "실험 arm", 안정화까지 회전

RF/AV를 런칭월 A/B로 돌리듯, **패키징 톤도 측정 가능한 실험 차원**으로 본다.
세 전략을 **회차마다 번갈아** 붙이고 성과로 승자를 학습, 안정화되면 승자로 수렴.

세 톤 (arm):
1. `hook_search` — 클릭 유도 훅 제목 + 검색 키워드 태그 균형 (한국 시청자 우선, 영어 보조)
2. `hook_strong` — 호기심·감정 훅 최우선("풀 뜯어먹는 강아지 봤나요?" 스타일), 태그 보조
3. `search_strong` — 해시태그·키워드·카테고리로 발견성 최대화

회전·학습:
- 배정은 bandit과 같은 결: 초반 균등 회전(round-robin by card_id) → `video_performance`에
  `packaging_arm` 컬럼 추가 → 리워드(동일 48h 공식) 누적 → Thompson으로 회차마다
  arm 샘플 → P(best)가 임계 넘으면 "안정화"로 승자 고정(수동/자동 토글).
- 이건 **lane×timeslot arm과 직교하는 새 marginal**: `choose_packaging()` 추가.

## Phase별 빌드 (순서대로, 각 단계 PD 컨펌 + change-impact 스킬)

### Phase 1 — 패키징 에이전트 (할당 무관, 즉시 가치)
- 신규 `agents/channel_manager.py:make_packaging(concept, lane, card_id, arm=None)`
  → LLM이 훅 제목 + SEO 설명 + 컨셉별 해시태그(한/영) 생성. arm=None이면 `choose_packaging`로 회전 배정.
- 신규 프롬프트 `agents/prompts/channel_manager_packaging.md` (대중+검색 렌즈, 3톤 정의, 채널 사실 주입: @ryani_n_loe, 랴니/레오 캐논, 금지어).
- 소비: `producer.py`의 `draft.{title,description,hashtags}` 채우는 자리를 이걸로 교체
  (기존 정적 디폴트 supersede). `_auto_upload_episode`는 그대로.
- DB: `cards.payload_json.draft.packaging_arm` 기록(성과 귀속용).
- 안전: 업로드 자체는 안 바꿈(메타 값만 풍부해짐). 라이브 가기 전 1편으로 PD 컨펌.

### Phase 2 — MAB 루프 연결 (할당을 데이터가 조종)
- `launch.day_assignments`가 고정 라틴스퀘어 대신 `bandit.choose_lane/choose_timeslot`를
  쓰되, **탐색 보장**(라틴스퀘어를 prior로, 데이터 부족 슬롯은 강제 탐색) — 순수 그리디로
  굶는 arm 방지. 패키징 arm도 `choose_packaging`로 주입.
- change-impact: launch/producer/arc/bandit/Slack 보고 전부 추적.

### Phase 3 — 채널 상태 추천기 ("어디 더 넣을지")
- `channel_manager.recommend()` = 라이브 YouTube API(stale DB 금지, cf [[verify_youtube_state_via_api]])
  + `video_performance` + 포스테리어 → "지금 밀어야 할 lane/timeslot/테마, 과소·과다 노출, 빈 슬롯".
- analytics 확장: CTR·노출·트래픽소스·7/28d. Slack(board_agent 라우터에 `recommend` 인텐트).

### Phase 4 — Giri 포트폴리오/대중 렌즈 강화
- Giri는 회차 게이트 유지. 채널 매니저가 **누적 성과→어떤 테마/패키징이 이기나**를
  컨셉 단계(concept_brainstorm)로 피드백. PD가 "리뷰어가 진짜 대중 시각이 아니다" 느끼면
  giri spec 렌즈를 그 단계에서 날카롭게(prompt-authoring).

## DB 추가(요약)
- `video_performance.packaging_arm` (Phase 1/2)
- 신규 `packaging_log`(card_id, arm, title, tags, generated_at) — A/B 귀속 (Phase 1)
- analytics 확장 컬럼: ctr, impressions, traffic_source_json, views_7d, views_28d (Phase 3)

## 안정화(stabilize) 토글
- 각 실험 차원(lane/timeslot/packaging)별 `P(best) ≥ θ AND n ≥ N` → "안정화" 플래그 →
  해당 차원은 승자 고정, 탐색 중단. 그전까지는 회전/탐색. RF/AV 안정화와 같은 철학.
