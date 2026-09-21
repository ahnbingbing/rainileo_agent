# Session handoff — 2026-09-21 (9/23 빈배치 인시던트 완전규명·수정 + v2 프로덕션 모델 코어 빌드)

**스파인:** "9/23 배치 0/4 전멸 + 슬랙에서 self-heal을 못 멈춤"에서 시작해, 프레임/로그로 파보니
근본은 **파서가 그라운딩 프로즈의 트림범위 `[0,8]`를 컨셉으로 오인**한 것(풀 부족도 LLM도 아님 —
PD 직감이 맞았다). 슬랙 무응답은 **stop이 정확-키워드셋+LLM경유**라 자연어·구두점에 다 미끄러진
것. 폭주는 **slot_topup에 전체 벽시계캡이 없어서**. 셋 다 고쳐 배포했고, 그 위에서 PD와 **v2
프로덕션 모델**(senior director·2일 사이클·footage 다양성·6슬롯·Day2 AV 시의성)을 설계·코어 빌드했다.
관통 교훈: **작동하는 폴백이 죽은 주력을 가리듯, "0 cuts=풀 부족"이라는 그럴듯한 표면이 파서 근본을
가렸다 — 아티팩트(raw_llm_text=완전한 컨셉, input_videos=53)가 진실.**

## VM authoritative · push=deploy
- **VM HEAD `199fac6`** (deploy.timer 2분 폴 정상). 하루 스케줄(KST, /var/spool/cron/crontabs/rianileo):
  03:00 launch_selfheal / 09:10 slot_topup / 09:40 pd_reviewer(APPLY=1) / */5 process_board_escalations
  / */30 ytcache. **cron 살아있음**(syslog로 9/21 03:00·09:10 발화 확인; `crontab -l`이 0줄로 나오는 건
  조회 이상일 뿐 — 스풀 파일 6489B 온전).
- **★내일(9/22) 03:00 배치는 v2 아니라 "고쳐진 기존 파이프라인"으로 9/24 생산** (`LAUNCH_MODEL` unset).
  9/23 실패 반복 안 함(파서 고침 검증). v2는 플래그 OFF라 라이브 무영향.

## SHIPPED — 인시던트 완전수정 (전부 VM 검증)
근본은 회고에 통합 예정. 커밋 순서:
1. **파서 근본**(`acd1c5e`) — `producer._robust_json_parse`가 첫 파스되는 슬라이스(프로즈의 `[0,8]`
   트림범위=`[int,int]`)를 반환 → dict필터 → 0 concepts → no_concept → 빈슬롯 → self-heal/topup 폭주.
   방아쇠=9/20 그라운딩 프롬프트(`98962b4`)가 클립읽기 프로즈↑. **D_nonjsonparse 4번째 재발.** Fix=
   concept-shaped 슬라이스 우선(cuts>structured>anything, 최대 우선), first-parseable는 최후. 회귀
   `scripts/_rf_parser_regress.py` — 합성 + 실아티팩트 1165개 스윕 0 zero-cuts. 라이브 재생산 검증
   (parsed_concepts=1, "축구장 랴니" Giri pass 렌더).
2. **슬랙 패닉-stop**(`f852e3d`·`394fffe`) — 저널 실증: PD가 `stop!`·`제작 멈추라고`·`셀프힐 멈춰줘`·
   `셀프힐 멈추게 할래!?` 다 보냈으나 무응답. 근본 4겹: ①워크룸 stop=정확-키워드셋("stop!"≠"stop"·NL무시)
   ②board채널=전텍스트가 LLM비서로(패닉stop이 느린LLM왕복 의존) ③`_handle_stop`/`_act_stop_renders`
   kill패턴에 부모 `slot_topup`/`launch` 없어 자식만죽고 재생성 ④veto는 0/4라 취소할 영상 자체가 없음
   (도구오선택). Fix=패닉stop을 `handle_thread_replies` 최상단·결정론·NL관용(짧은메시지≤28자+정지동사
   중지/정지/멈춰/멈추/그만/중단/stop/…, 모든채널/스레드무관, LLM앞단)+kill패턴에 slot_topup/launch/
   animate_seedance 추가. 대화체 오발 안 함으로 회귀검증.
3. **폭주 bound + stale lock**(`acd1c5e`) — slot_topup에 전체 벽시계캡 없어 gap마다 self-heal(5400s)×4=
   6h → `TOPUP_MAX_SECONDS=1800` 초과분 다음 실행 연기. `board_picker.lock`이 8/19부터 stale라 board
   자동 executor 한 달째 죽음(SIGKILL이 finally-unlink 건너뜀) → 죽은 holder/30분 초과면 자동 탈취
   (`process_board_escalations`). runaway slot_topup(2.25h) kill + lock 청소 완료.

## SHIPPED — v2 프로덕션 모델 코어 (플래그 `LAUNCH_MODEL=v2` OFF, dry-run 검증, 라이브 무영향)
PD가 9/23 인시던트 후 재설계 확정. 상세=메모리 `production_model_v2_senior_director`. 커밋 d6eea42~199fac6:
- **모델**: 배치1회=9 RF(senior director 소스 A/B/C × 3 grammar velocity/meme/story)+AV. **2일 사이클**:
  Day1=5RF+1AV(20:00)→4RF 이월 / Day2=이월4RF+2AV(08:00,20:00). **6슬롯** 08/09/13/18/20/21(저녁 피크,
  실측 21:00최강·12:30최약; PD Studio 시청자 피크 저녁 18-21). **Day2 AV=전날 인기편 판타지 재해석**
  (top1≥2×top2→승자1편 상상2가지 / 유사→top2 각각1개).
- **senior director**(`agents/senior_director.py`+`prompts/senior_director.md`, prompt-authoring): 배치
  쇼러너, 소스3개를 **서로 다른 발원지**서 originate(Phase1 고정 A=함미하비노트/B=VLM관찰/C=arc컨셉→
  안정화 후 자율). 소스별 스토리 바이블(컨셉+캐스트 5~7클립+관통선), anti-hallucination(knowledge_questions).
  dry-run: 3소스 다양·그라운딩·화면밖 되물음.
- **footage-다양성 게이트**(`agents/footage_diversity.py`): ★컨셉 아닌 **클립-오버랩**(산책3개 OK, 동일/
  ≥60% footage겹침→리롤). 회귀 6/6.
- **launch_v2**(`agents/launch_v2.py`): day_plan(2일 사이클·6슬롯)·plan_sources_to_grammar(dry-run
  wiring)·day1_winners(AV 지배/분산 분기). 회귀 12/12. grammar_slot에 `pool` 주입 파라미터(소스캐스트→grammar).
  **dry-run 검증: 3소스 → 9 grammar편, footage 다양성 0.**

## ★ NEXT (신규 세션 — PD가 품질 보고 배선여부 결정)
1. **PD 스팟체크**: v2 dry-run으로 나온 소스/캡션 품질 확인 → senior director 프롬프트 조정.
2. **v2 잔여 배선(task10)**: ①9 RF 풀렌더+업로드+스케줄(Day1 5+AV1) ②2일 carry(남은4 RF 핀) ③Day2
   판타지AV 렌더(day1_winners→상상, 랭킹은 됨) ④`launch_selfheal`/`slot_topup`에 `LAUNCH_MODEL=v2` 분기.
   → 9편 풀렌더 dry-run → PD 스팟체크 → 플립. **급하게 라이브로 밀지 말 것**(인시던트 교훈=검증부족).
3. 롤백=`LAUNCH_MODEL` unset(즉시 구모델). senior director/게이트/launch_v2는 배포됐지만 미배선.

## 상태 스냅샷 (세션 끝, 9/22 00:2x KST)
- VM 유휴(real launch/render procs 0), cron active, bot 12:05 재시작(패닉stop 라이브). v2 OFF.
  파서·캡·lock 다 라이브.
- 스케줄: 9/21=1, 9/22=4(full), **9/23=3/4** (12:30·18:00·21:00 채움, **08:00 빈칸**), 9/24=9/22 03:00 생산.
- ★9/23 08:00 못 채움: 파서 고침으로 컨셉은 정상 생산되나("테라스 탐험가 vs 화단 미식가" 등) **RF Giri가
  캡션-mismatch로 반복 반려(4/10)** → bounded self-heal(--rounds 2, ~82분) 소진, 성공 가망 없어 종료(캡 근접).
  파서 무관·폭주 아님(캡으로 bounded, 수동 종료해 밤새 유휴). 9/23은 테스트라 3/4로 마감.
  → NEW세션에서 08:00 채우려면 다른 클립/재캡션 or AV로. RF Giri 캡션-mismatch 반복은 별도 관찰거리.
