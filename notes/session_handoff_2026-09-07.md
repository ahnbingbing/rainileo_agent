# Session handoff — 2026-09-07 (9/5~9/7 대형세션: 표면 아래 진짜 근본들)

**스파인:** 이번 세션의 거의 모든 문제가 **표면 증상이 진짜 근본을 가린** 경우였다 — "풀이 작다/빈 슬롯/비용이 비싸다/신선 클립이 안 만들어진다"는 전부 액면과 달랐다. **전제부터, ledger/프레임/촬영일을 진실원으로 검증하라.** PD 도메인지식("많잖아", "왜 신선 클립이 안 써져")이 aggregate 진단을 여러 번 이겼다.
- 풀은 **커졌다**(dur≥12 1022→1553), 빈 슬롯은 **시의성-AV 슬롯**의 렌더불가였다, 비용은 **시스템 프롬프트 아니라 주입 컨텍스트**였다(다이어트 1%), 신선 클립은 **없는 게 아니라 선택 편향으로 안 쓰였다**(151개 usable 방치).
- non-JSON 만성실패는 **truncation도 모델거부도 아닌 파서 버그**(프로즈 서문 + 한국어 `[컨셉]` 브래킷)였다 — JSON은 응답 안에 멀쩡히 있었다.

## VM authoritative · push=deploy
- **VM HEAD eca39a8** 이후 (오늘 다수 durable 배포·pull 검증). `git push origin main` → deploy.timer 2분 폴.
- **하루 스케줄 (KST)**: 03:00 `launch_selfheal`(4슬롯 생산+6R self-heal, ~4.5h) / 09:10 `slot_topup --slack`(03:00이 못 채운 빈슬롯 backfill = "아침 self-heal"의 정체) / 09:40 `pd_reviewer`(리뷰+재렌더).
- VM DB는 `sudo -u rianileo`로 열어야(0600). run_job.sh는 rianileo 전용 env 소싱 → `sudo -u rianileo bash -lc 'PROMPT_SET=x run_job.sh ...'`.

## SHIPPED — durable (커밋순, ★=큰 레버)
1. **★non-JSON 근본** (f999a4d): 모델이 JSON 앞에 프로즈 서문("Looking at candidates: [컨셉]…")을 붙이고, 그 한국어 `[]` 브래킷이 greedy `\[.*\]`를 속여 프로즈 `[`를 잡음 → char1 실패(JSON은 응답 안에 있음). Fix=문자열-인식 **균형-괄호 스캔**(모든 `[`·`{` 후보 파싱시도). AV `_parse_json_loose` + RF `_robust_json_parse` 둘 다. ★라이브 AV 4/4 성공 0에러(前 ~1/3 실패, 배치 61회). **버려지던 1/3 컨셉 회수 = 빈슬롯·churn·폴백낭비 최대 감소.**
2. **Writer truncation 재시도** (defcf61): `_call_anthropic` truncation을 provider장애로 오취급→OpenAI 45s타임아웃(16k 생성불가)→non-JSON. Fix=truncation시 max_tokens 2배 escalate(cap 32000), circuit UP유지.
3. **비용 귀속** (256f675): `_log_text` 스택워크로 caller별 `stage` 기록(전엔 flat 'cascade'). ★gotcha=9/5前 데이터는 'cascade'로 남음(레거시).
4. **RF 프롬프트 캐싱** (e472e70 + escalate 9d8531e): 재제안이 동일 92K 풀 재전송 → 정적(system+풀)을 `call_text_cached`(Anthropic system cache_control)로, cache_read ~90%↓. escalate=truncation시 cache 유지(12k→24k). RF_PROMPT_CACHE 가드.
5. **시의성-AV 폴백** (9d8531e): 하루 첫 AV슬롯(08:00)이 `require_timely` 강제 → Seedance 렌더불가(스핑크스 수수께끼 등) → 6R 실패 → 빔(4개 다 못만든 근본). Fix=giri_fail 2R후 `SELFHEAL_DROP_TIMELY=1`→일반 렌더가능 AV로 폴백해 채움.
6. **하네스 일관성** (abe6e83): 실산책=양펫 하네스 전컷/야외상상(look:fantasy)=bare/실내=bare. 단일 predicate `_cut_wants_harness`를 still-regen+motion 둘 다 사용(전엔 still bare↔motion harness 불일치 드리프트).
7. **조회수 데이터 → 구체 훅 제목** (ab82ad3): 시적/모호 제목 매장(0-11뷰) vs 구체사건 훅 900-1000뷰. writer(AV+RF)+Giri cap≤6. RF>AV 도달(AV는 retention높은데 클릭실패=패키징).
8. **첫 만남 canon** (1bddfd8): 랴니↔레오는 오래된 한집 남매(레오 2025-09 합류)—"첫 만남/코인사 첫인사" 금지(남산이엔 있던 규칙 갭). 바닥에 코박음=먹기이지 코인사 아님. Giri cap≤5. (9/8 08:00 하비 사료 먹방을 '첫 만남 관찰기'로 발행한 근본.)
9. **재캡션 제목 재생성** (0e6df93): recaption RF·AV가 새 캡션에서 make_packaging로 제목 재생성(전엔 캡션만 바뀌고 제목 stale).
10. **신선footage 선택편향** (eca39a8): 최근 에피 90% 옛 memory-lane(2016-2021), 신선 함미하비 클립 151개 방치. 근본=①writer가 잔잔한 신선보다 드라마틱 옛것 선호 ②pd_reviewer가 컷이슈 고칠때 "similar era(2020) footage 찾아라" era-lock 디렉티브를 [PD지정 최우선]으로 써서 신선-우선 프롬프트 덮음. Fix=pd_review.md 재선택/재렌더 **신선 기본·era-lock 금지** + stale era-lock 디렉티브 5개 청소.
11. **비용 컨텍스트 컷** (ab17b4a): RF available_photos 100→40(RF_PHOTO_POOL_MAX). 사진=2차 브릿지, ~15K토큰/콜↓.
12. **v2 프롬프트 다이어트 인프라** (ca8ce58 loader + 779942c 4파일 + 5a7b243 v1 모순3패치): PROMPT_SET=v2 A/B 인프라. ★결론=품질동등하나 **토큰 ~1%뿐**(구조적으로 작음, 비용은 컨텍스트에). 진짜 가치=찾은 v1 모순 3개(컷수 5~6vs상한없음·해요체 위반예시·윙크캡션 last vs all).

## 비용 현황 (오늘 측정)
OpenAI **$48/day → ~$14/day 급감**(캐싱+non-JSON+사진컷). 남은 #1=RF단일패스 OpenAI폴백($13.9, truncation→오늘 escalate로 해결예정). $96.4 'cascade'는 9/5前 레거시(귀속 이미 작동).

## 조사만 (PD 액션/후속)
- **★iCloud "Ryani & Leo" 앨범 인입 12일+ 정지**(8/24~): 라이브러리엔 146+ 신규(비디오30)인데 앨범 태깅 0 → 파이프라인 못봄. 함미하비 Slack이 대신 인입(151 usable). PD 앨범 확인이 신선공급 근본 레버. notes/ingestion_rate_drop_2026-09-04.
- **알고리즘 노출/CTR**: impressions/CTR 미수집(Studio만), AV 썸네일=텍스트없는 프레임그랩(CTR병목).
- **RF 그라운더가 화면밖 pd_notes(하비 사료)를 약하게 반영**(9/8서 '냄새 탐험'으로 보수화, PD_RERENDER_DIRECTIVE로 강제).

## gotcha
- **표면 ≠ 진실**: 풀크기(raw 1553 vs writer가 본 33)·비용(image vs text cascade)·콜수(234 vs 81거대콜)·빈슬롯(시의성 slot)·신선클립(선택편향). ledger/프레임/촬영일이 진실.
- 균형-괄호 파서: 한국어 `[]`가 greedy 정규식을 속인다(프로즈 서문 흔함).
- pd_reviewer era-lock이 memory-lane 자기강화 루프를 만든다(자동화가 서로 강화).
- recaption RF는 모션 재그라운딩이라 화면밖 맥락 약함 / systemd probe는 제안후 stop+`clear_stop()` 필수(6h TTL 렌더막음).

## ★NEXT (다음 03:00 배치 = 오늘 fix 대량 첫 실전)
- **스팟체크**: ①non-JSON 실패↓(f999a4d) ②08:00이 03:00에 채워짐·아침 topup 조용(9d8531e) ③RF가 Anthropic-cached 유지·OpenAI $↓(캐싱+escalate) ④RF가 **신선 클립(2026-08+) 쓰기 시작**·"N년 전" 회상↓(eca39a8) ⑤하네스 일관·시적제목 cap·첫만남 cap 발화.
- 여전히 memory-lane이면 **writer 프롬프트 신선-우선 강화**(잔잔한 신선 클립으로도 좋은 스토리)가 다음 레버.
- 추석 3편(9/24-26 한복 성별)·가을상상 2편(9/10·9/13) 디렉티브 등록됨 — 생산시 한복 성별·판타지 스팟체크.
- v2 프롬프트: A/B 렌더는 무의미(토큰 1%)로 결론, v2 채택 여부 PD 결정(깨끗한 프롬프트용).
