# HANDOFF — 런칭 라이브 + 품질/인프라 (2026-06-07 갱신)

> 새 shell은 CLAUDE.md 다음으로 이걸 읽어라. 브랜치: **approach-d-grounded-singlepass** (미머지).
> **상태: 런칭 자동화 ON** — 매일 00:00 4편 제작→다음날 예약공개. 첫 배치 6/8 00:00→6/9 공개.

## ⛔ 출력 규칙
도구 호출 코드를 답변 텍스트에 쓰지 마라. 매 턴 도구가 실제 실행됐는지(결과 반환) 확인. 말로만 "실행합니다" 금지 — 이게 과거 3일 날린 원흉.

## 운영 메모
- **producer를 동시에 2개 돌리지 마라** (DB/파일 충돌). 로컬 E2E와 Slack `/test`도 동시 금지.
- Slack 리스너는 launchd `com.rianileo.slack`로 상시 실행. 코드 바꾸면 `launchctl kickstart -k gui/$(id -u)/com.rianileo.slack`로 재시작해야 반영 (단 프롬프트 .md는 런타임 read라 재시작 불필요).
- E2E 테스트는 **실모델**로: `OPENAI_FALLBACK_MODEL` 미설정(기본 gpt-5). gpt-5-mini로 돌리면 스토리 품질 급락 — 과거 실수.
- 테스트 시 기리 retry 상한 낮추려면 `RF_GIRI_MAX_ATTEMPTS=3` (프로덕션 기본 100).

## 두 레인
- **real_footage**: 단일-패스(`realfootage_concept.md`) → `_render_realfootage_direct` → `_render_realfootage_with_retry`(기리 통과까지). 실제 클립 기반.
- **ai_vtuber**: Writer/Director(`writer_director.py`, `writer_story.md`/`director_shots.md`) → `render_with_retry`(기리). GPT 캐릭터 regen → Seedance. 원테이크+윙크 엔딩.

## 2026-06-06~07 완료 (활성)
### real_footage 품질
- 단일-패스 캡션 보존(VLM 재작성 SKIP), KO/EN 자막 분리 계층
- **크롭(인간 얼굴 절대 금지)**: `_vlm_pet_crop_filter` — 펫 bbox를 다 담는 최소 9:16 창을 만들고 사람 반대쪽으로 밀어 얼굴 제외. **회전 메타데이터 버그 수정**(추출 프레임 치수 사용). 펫 안 잘리고 9:16 꽉참.
- **zoom-freeze 제거**: ken_burns/zoom/pan은 zoompan d=dur*30이라 영상을 첫 프레임에 freeze → "정지 화면 줌인". real_footage 영상 컷에서 zoom류 효과 전부 drop(static), 실제 영상 모션 재생.
- 마지막 컷 여운: freeze 대신 원본 충분하면 실제 영상 재생
- photo_i2v: 캐릭터 모션 보장(모션 측정→부족시 재생성, standard 모델) + **ai_vtuber 랴니/레오 캐논 주입**(`_append_character_canon`)으로 캐릭터 드리프트 방지. 사진=보조재(중간 0~2개, finale는 영상)
- prop 정확성("녹색 채소 담긴 그릇"→"초록 그릇" 오표기 금지), 단일장소/저모션클립 회피/가짜인과 금지, 톤 3레인, 자막 촘촘/연속
- 기리 검수 연결(우회 해소) + 통과까지 retry, 슬랙 리포트 파리티(format_slack_report)
- VLM 태그 수정(추측금지/포즈/배경사람) + 불확실→PD 문의 큐(`pd_correct_asset --list-uncertain`)

### ai_vtuber 품질
- 윙크 엔딩: 5→7초, 직전 장면 연결 비트+윙크 holding(뜬금없음 해소), **wink_subject를 Writer LLM이 스토리 기반 결정**(약올린 주동자가 윙크; `_pick_wink_subject` 패턴은 fallback)
- 캡션 변별: 원테이크 동작중계 금지(setup→반응→펀치라인, 랴니 반응 활용)

### 🔴 자막-영상 불일치/누락 (심각, 양 레인 공통) — 2026-06-07 수정
- **자막 누락(공백)**: 자막 연속성 강제를 **av에도 적용**(첫 0.1s부터 컷 전체 빈틈없이, 윙크 컷만 예외). av 원테이크가 첫 ~2초 공백이던 문제.
- **재교정 안 됨("VLM 캡션 0컷 재작성")**: 캡션 에이전트 컷 tag가 captions.json 키와 안 맞아 0개 반영 → **index fallback 매칭** 추가.
- ※ 미검증: av E2E 재실행으로 확인 필요(작성 시점 진행 중).

### 인프라
- **중간영상 자동정리**: 렌더 성공 시 `_prune_tmp_workdirs`(최근 `CAMERAMAN_TMP_KEEP`=6 유지). 기존 16GB/335개 → 8개로 일회성 정리함.

## 🟢 런칭 라이브 — 2026-06-07 대규모 업데이트 (전부 ON)
> 전체 운영/우선순위는 README "런칭 자동화" + `notes/first_month_plan.md`.
> 결정/상태는 메모리 [[launch_month_experiment]] [[character_knowledge_v2]].

### 런칭 시스템 (첫 달 explore 4편/day A/B)
- **`agents/launch.py`**: `day_assignments`(2 av+2 rf, 레인×시각 라틴스퀘어, 14일 7/7 균형), `publish_at_for`(지난 슬롯 익일 롤), `launch_pipeline`(레인별 propose→기리 게이트 렌더→슬롯 시각 예약공개→실패 슬롯 비움). `--max-slots`(시운전). CLI 기본 타깃=내일. 모듈 상단 load_dotenv(cron용).
- **launchd `com.rianileo.launch`**: 매일 **00:00** 다음날치 제작→예약공개(하루 veto/답변 버퍼). `LAUNCH_START_DATE=2026-06-09`(Day1=첫 공개). **첫 자동 배치 6/8 00:00→6/9 Day1.**
- **검수 전환**: 블로킹 PD 승인 폐지 → 기리 통과=자동 예약공개 + 슬랙 스팟체크. `/launch` `/veto`(`youtube.upload.veto_video` 비공개/삭제 + uploaded=0 회수).
- **측정 루프**: `youtube/analytics.py`(48h views+retention) + `video_performance` 테이블 + `agents/bandit.py`(population reward + 3-level Thompson lane/timeslot/arm + P(best) + choose). launchd bandit-collect(06:30)·bandit-report(월10:00). `/bandit`.
- **`ARC_ENABLED=1`**(.env). cards에 `youtube_video_id`/`youtube_publish_at` 컬럼.

### 컨셉 디렉티브 우선순위 (`arc.next_directive`)
1. **PD `/concept <날짜> <내용>`** (pd_concept_directives 테이블) — 최우선, ARC off여도 작동
2. **런칭 인트로 오버레이** (`_launch_intro_directive`, LAUNCH_START_DATE 기준) — Day1 둘함께 자기소개("안녕! 나는 ~예요" rf·av 둘 다, rf=실제클립+1인칭캡션) / Day2 레오 단독 / Day3 랴니 단독, 과거⇄현재 메모리레인 + 인터랙션 우선
3. **arc 시즌 플랜** LLM 디렉티브
→ `/concept` 안 쓴 날은 arc가 기본.

### 캐릭터 지식 시스템 v2 (환각 방지, 3층)
- **① VLM 자동 프로파일** `agents/pet_profile.py` — 펫별 클립 태그 집계(activity/intent/looking_at/props). 관찰 경향(참고용).
- **② PD 권위 사실** `arc.CHARACTER_FACTS` + `character_sheets.md`. ★랴니=물 매니아(분수에 뛰어듦)·펠프스급 수영·겨울 얼음썰매, 2016 아기때만 무서워함("물 공포"는 거짓). 레오=고양이라 물 피함.
- **③ 모르면 물어보기** `agents/knowledge.py` + `character_facts` 테이블 — 컨셉이 `knowledge_questions` 출력→`producer.resolve_knowledge_questions`(dedup, 주1 블로킹/주2+ 논블로킹)→PD 답 영구저장+주입. `/knowledge` `/answer`.
- 셋 다 arc 플랜/디렉티브 + rf 컨셉 프롬프트에 주입. **물 공포 환각 사건**이 이 시스템을 만든 계기.

### rf 품질 (시운전 피드백 반영)
- **BGM 다양화**: rf가 flat 단일맵→`_pick_bgm_track`(mood당 3-5곡 해시) 라우팅(generate_manifests).
- **속도**: speed_* `RF_MAX_SPEED=1.2` 클램프 + 컷마다 다르게.
- **location_type 버그 수정**: VLM v2가 location_specific을 notes JSON에만 쓰고 컬럼은 NULL→"주방 오인". `_coarse_location` 매핑 추가 + 302행 백필(cafe 5→64).
- **컨셉 규칙**(realfootage_concept.md): 사실 억지삽입 금지(클립에 있을 때만)/과거→현재 연결/위치 단정 금지/왜찍었나(펫하우스 적응)/런칭=인터랙션/캡션 단순 금지. 톤=컨셉별, 소개=vlog 캐주얼 우대.

### 안정성
- **Gemini/LLM 타임아웃 가드**: VLM Client `http_options(timeout=VLM_TIMEOUT_MS=90s)`(cameraman/reviewer/tag_assets) + llm_cascade OpenAI/Gemini(LLM_TIMEOUT_S=120). 57분 hang(무한대기)→graceful 폴백. **자동 런칭 전 필수였음.**
- tag_assets_vlm: 1초 미만 클립 프레임추출 폴백(0초 재시도).

## 다음 후보
- 6/8 00:00 첫 자동 배치 결과 모니터링(슬랙) → veto/answer로 운영
- split_screen/cross_cutting ffmpeg 외 미구현 편집효과
- 레오 눈색 드리프트(호박색 vs chartreuse) 캐논 강제
- data/output/episodes(5GB) 정리 정책
- 월말 `/bandit choose`로 다음달 비율 결정

## 핵심 파일
- `agents/launch.py` — 4슬롯 라틴스퀘어 스케줄러 + launch_pipeline + ask_cb(지식 Q&A)
- `agents/producer.py` — propose/render, 쿨다운, 아크/사실 주입, 기리 retry, `resolve_knowledge_questions`, `_auto_upload_episode`(publish_at)
- `agents/cameraman.py` — run_real_footage_pipeline, 크롭(`_vlm_pet_crop_filter`), 캡션 연속성+ko/en(generate_manifests), photo_i2v+캐논, zoom-drop, `_pick_bgm_track`(BGM 다양화), speed 클램프, VLM 타임아웃, `_prune_tmp_workdirs`
- `agents/arc.py` — 시즌플랜 + `next_directive`(PD/concept>인트로오버레이>플랜) + `CHARACTER_FACTS` + `_learned_facts`(③+①) + `set/get_concept_directive`
- `agents/knowledge.py` — 모르면 물어보기(③): character_facts 테이블, facts_block, collect/resolve
- `agents/pet_profile.py` — VLM 행동 프로파일(①)
- `agents/bandit.py` — av-vs-rf Thompson + video_performance + collect/choose
- `youtube/{upload,analytics,oauth}.py` — 업로드(+veto_video)/48h성과/OAuth
- `agents/writer_director.py` — `_build_wink_cut`, `_pick_wink_subject`, `_stamp_years_ago`, caption agent
- `agents/reviewer.py` — 기리(자막공백/킥/얼굴/캐릭터 룰)
- 프롬프트: `realfootage_concept.md`, `writer_story.md`, `caption_agent.md`, `character_sheets.md`, `tag_assets_vlm.py`
- `notes/first_month_plan.md`(런칭 설계), `notes/slack_commands_guide.txt`(명령 전체)
- launchd: `launch`(00:00)·`bandit-collect`(06:30)·`bandit-report`(월10:00)·`icloud-sync`(06:00)·`slack-sync`(15분)

## 테스트
```
.venv/bin/python -m agents.producer --date 2026-05-22 --style real_footage --no-slack
.venv/bin/python -m agents.producer --date 2026-05-22 --style ai_vtuber --no-slack
RF_GIRI_MAX_ATTEMPTS=3 ...   # 검증용 retry 상한
```
(05-22 = 자산 풍부, v2 재VLM 완료)
