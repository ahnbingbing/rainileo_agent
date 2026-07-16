# Session handoff — 2026-07-16 (7/14→7/16 대형 세션)

**스파인:** 이번 세션의 반복 주제는 **"파이프라인이 말하는 것 = 영상이 실제로 보여주는 것"**.
제목·설명·캡션·나이·계절·장소·컨셉이 전부 *실제 콘텐츠와 일치*해야 한다는 PD 지적이 여러 형태로 왔고,
증상을 손으로 고치는 동시에 **결정론/그라운딩 durable 픽스**로 재발을 막았다. VM HEAD = **f801311**. 전부 배포·라이브.

## VM authoritative. push=deploy.
`git push origin main` → deploy 타이머(2분 폴)가 pull→smoke→봇 재기동. VM HEAD=f801311.
VM 렌더 실행: `sudo -u rianileo bash -c 'set -a; source /etc/rianileo/env; set +a; PATH=/home/rianileo/.local/bin:$PATH
PYTHONPATH=/home/rianileo/rianileo-agent /home/rianileo/rianileo-agent/.venv/bin/python -m agents.launch_selfheal --date … --lane … --slot … --rounds 3 [--no-upload]'`.
- 일배치 = **03:00 cron `agents.launch_selfheal` (rounds 기본 3)**. 로그 `data/logs/cron.launch.log`.
- **YouTube 상태는 반드시 API로 확인**(DB는 stale 가능). 제목/설명 수정은 `yt.videos().update(part=snippet)` — 재렌더 불필요.
- ⚠️ **맥↔VM SSH가 이 세션 내내 불안정**(IAP 255 간헐). 렌더는 detached(nohup)라 계속 돌지만 모니터링이 끊긴다. 재시도로 붙였다.
- ⚠️ **인라인 python one-liner 금지** — `\x27` 이스케이프가 nested SSH서 계속 깨졌다. **작은 .py를 scp해서 실행**하라(이 세션 표준).

## 손수정 (공개/예약 교체, 손실 0)
- **7/14 08:00 RF** zu8→**f7My6YojL4c**: 캡션 랴니도 발닦다 걸림 + 멸치→**청어**(grandmompapa 확인). 공개됨.
- **7/14 12:30 AV** 8Dyo→TNYC→**eUaJuKMHiTk**: 나이 할루시(20242013년생→2025 vs 2015)+제목. ★TNYC가 **유튜브에 조용히 삭제**돼(우리 로그엔 delete 없음=near-dup 자동제거 추정) 슬롯이 비어서 재업로드→복구. 공개됨.
- **7/15**: 배치 2/4(08:00AV·21:00RF 게이트실패)→채움: 08:00 **수박 절도단**(JtEianMHw38, self-heal), 21:00 RF(6MQd1dRNNLc).
- **7/16 18:00 RF** sVnFJEZ9PWI: 내용(레오가 랴니 참견+더위 늘어짐)≠제목("배꼽시계 먹방")→제목/설명 실제내용으로 교체.
- **7/17 재생산**: RF1230 벽샤넬-중복+밤클립 → 낮 컨셉 재렌더(**Y6NPA-5BF_I**, self-heal R1실패→R2 재롤성공); AV0800 Plan-ABC재탕 → **해변 물놀이**(**LoPnHDOInrg**). 그 뒤 RF1230 겨울·아기레오인데 "여름"제목→"생후3개월 아기레오 겨울 낮잠", RF2100 "벽샤넬"(뜻없는 조어)→"한밤중 벽에 코 콕"으로 교정.

## Durable 픽스 (전부 배포)
1. **생일년도 결정론 교정**(30742ce) — canon.`correct_canon_age_text` 통합(살차이+"N년생"/born→2015/2025 스냅), burn 6곳+upload 제목/설명. [[B14]]
2. **AV real-look lo-fi grade**(41741c4) — Step 3c `_apply_av_lofi_grade`(ai_vtuber·비판타지), PD가 은은한 결 택. ref-mode HD를 프롬프트론 못덮음→후처리.
3. **청어 canon 승격 + grandma→VLM 파이프라인**(5d6c6aa·73ed1bd·35b6736) — 근본=grandmompapa 대화지식이 episode_stories에만 쌓이고 **캡션 VLM엔 안 감**. harvest(_grandma_converse 사실추출→knowledge.remember_fact + backfill 228개)+inject(facts_block→RF그라운딩·공용 footage VLM·말맛 3곳). ★버그: facts_block row_factory 미설정→빈문자열 반환(고침). [[C12]]
4. **소고기=레오도**(1d48446) — PD 정정, canon 공동간식.
5. **컨셉 재탕(gag-token) dedup**(6ed3101) — 워터밤 두번. 임베딩 시도→기각(테마만 봄, 수박0.839>재탕0.742). **희소-개그토큰**: 전체이력서 DF 낮은 토큰(워터밤=1)이 substring 재사용시 차단, 흔한 테마어(에어컨) 허용. `CONCEPT_GAG_DF_MAX`.
6. **self-heal 1회 새컨셉 재롤**(0420962) — giri_fail=즉시 terminal이라 슬롯 빔이 근본. 비용실패시 완전 새컨셉 1회 재롤 후 terminal(실패컨셉은 exclude라 gag-dedup가 안뽑음). `SELFHEAL_REROLL=0`. **7/17 RF서 실전 검증(R2 성공)**.
7. **4 durable(f801311)**:
   - **제목/설명 from 실제내용+시점** — make_packaging에 `actual_captions_for_video`(burn된 VLM캡션)+`_content_era_note`(계절·펫나이 clip날짜서) 주입, 패키징프롬프트 '실제 캡션 우선+시점(겨울/아기레오) 준수+조어(벽샤넬) 금지'. 양레인.
   - **밤/낮 슬롯매칭** — launch `ctx['slot_hhmm']`→`_rf_long_candidates`가 `_tod_mismatch`로 시간대 맞는 클립 우선(밤클립 12:30 강등, soft). `RF_TOD_MATCH`.
   - **같은순간 dedup** — `_clip_capture_seconds`로 2분내 클립 접기+쿨다운 시드. `RF_MOMENT_DEDUP`.
   - **AV 장소 다양성** — 브레인스톰 야외(해변·공원·눈밭) 적극 제안 ≥절반 거실밖.

## ★★ NEXT (스팟체크)
**7/17 03:00 배치**가 durable 대부분(gag-dedup·재롤·제목from내용·시점·밤낮슬롯·같은순간·AV장소)의 **첫 실전**이다. 확인:
(a) 4/4 채워졌나(재롤 작동), (b) RF 두 슬롯 컨셉 안 겹치나, (c) 밤 클립이 낮 슬롯 안 갔나, (d) 제목·설명이 실제 내용+시점(계절·나이) 맞나, (e) AV에 야외 컨셉 나왔나, (f) 조어 없나.
- 미완 근본(렌더검증 필요, 네트워크 안정시): AV 윙크컷 배경 순간이동·RF 컷 과드롭 너무짧음(재롤이 안전망), grandma facts VLM주입이 recency-40캡(주제 관련성 랭킹 미구축).

## 교훈
- **파이프라인의 모든 발화는 실제 콘텐츠와 일치해야 한다** — 제목·나이·계절·장소·컨셉. "내용 작성할 때 시점 봐라"는 이 원칙의 한 형태.
- **소재가 흐르는 것 ≠ 사실이 권위 갖는 것** — owner 지식은 canon/knowledge로 승격돼 **footage 읽는 VLM까지** 닿아야 쓰인다.
- **결정론 > 프롬프트** — 러버스탬프/드리프트는 프롬프트론 못막는다. 신호를 계산해 게이트/교정으로.
- **증상수정 ≠ 재발방지** — 손수정 후 반드시 근본 durable.
