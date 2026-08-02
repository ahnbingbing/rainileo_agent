# Session handoff — 2026-08-02

**스파인:** 프레임이 ground truth. 리뷰 피드백을 받으면 **먼저 "재캡션인가 재렌더인가"를 판정**하라 —
"캡션/제목만 바꿔"·"내용이 이미 ~랑 유사한데"류는 **재캡션만**(재렌더 금지, 원본 veto 금지=복구불가).
결정론 backstop은 파이프라인 **마지막 mutation 뒤**에 둬야 프레임을 본다(앞단 스틸게이트는 이후 i2v 드리프트를
못 봄). 검수 게이트 강화는 **정상 마킹 오탐(false-fail) 안 만드는 것**이 subtle-catch보다 우선.

## VM authoritative. push=deploy.
- `git push origin main` → 배포 타이머(2분 폴)가 pull→smoke→봇 재기동. **VM HEAD = `49da5ba`**.
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
  DB/스크립트 `sudo -u rianileo`. YouTube 상태는 라이브 API로 확인(youtube.oauth.get_youtube).
- ⚠️ `/home/rianileo/`로 직접 scp 권한실패 → `/tmp`로 scp 후 `sudo -u rianileo cp`.
- ⚠️ **VM은 2 CPU** — 동시 렌더 3+개는 서로 굶긴다(Seedance는 API-bound라 CPU 낮아도 느림). 렌더는 순차 체인으로.
- ⚠️ `_pd_launch.sh`(이번 세션 생성) = deploy.env/env 소싱 **후** `YOUTUBE_AUTO_UPLOAD=0 CONCEPT_BRAINSTORM=0`
  강제 → 렌더만 하고 수동 예약(중복슬롯 버그 회피). `.env` 읽기는 하네스가 차단(키명만 grep 가능).
- 수동 툴(이번 세션): `scripts/_pd_av_render.py`(directive AV 렌더)·`_pd_schedule.py`(신규 예약+veto)·
  `_pd_retitle.py`(재업로드 없이 제목만 API 패치, 조회수 리셋 없음)·`_pd_food.py`(하드빌드 RF 재트림).

## SHIPPED — 8/3·8/4 배치 PD리뷰 9건 (전부 라이브 교체/신규)
**8/3 (4편 완성 — 기존 3편뿐이던 문제 해결):**
- 08:00 RF `QbQtq1gQ2Ig` — 재사용 orphan(수영, 카드없음 `Af9Ps`)을 신선 "나란히 휴식"으로 교체+veto.
- 12:30 AV `gFt9HByYTvA` — "찝쩍거리는 레오 1탄"(재캡션, footage에 레오 앞발 어택 있음).
- 18:00 RF `yDLMKRm-AeM` — "솔치 청어 둘다 대기중"으로 시작(재캡션).
- 21:00 AV `1oxkCPxoqjc` — **아이스크림 행성**(board 요청, directive 리드타임 밖이라 미반영됐던 것 → 수동 렌더). 비주얼 훌륭·canon 정상.
**8/4:**
- 08:00 AV `ub3OUWgjMas` — 제목 "찝쩍거리는 레오 2탄!". ★★내 실수: PD는 캡션/제목만 바꾸라 했는데(내용이 1230AV와 유사=이미 찝쩍) 내가 **재렌더**($3)+원본 eTh1s veto·삭제(복구불가). PD 지적→재렌더본 유지+제목단순화로 수습. [[recaption_not_rerender]].
- 12:30 RF `g_iH2SO4uh8` — "사료 타이틀 방어전"(원본 173s에서 사료먹는 104~152s 재트림 하드빌드+코믹 재캡션+리타이틀).
- 18:00 RF `SA8CFlvD0z8` — "랴니 축구 모음집"(단일클립 리프레이밍 재캡션).
- 21:00 RF→AV `7v2lmZpJ4-Q` — 헬리콥터 혓바닥(RF 혀타이밍 어긋남+내용 얇음 → AV 전환). PD가 "헬리콥터/프로펠러 느낌 더" 요청 → 항공 코미디로 재캡션(이륙준비/관제탑 레오/활주로 발라당 착륙). 기존 s8hi3 veto.

## durable (배포 49da5ba) — 회고 B21/D31/E10
- **Giri nape-white 게이트(reviewer `_check_ryani_nape`)**: 랴니 목뒤 흰마킹(삐용이 번짐)을 렌더게이트(3프레임)+홀리스틱 둘 다 놓쳐 9점 러버스탬프 → 최종 렌더 프레임을 ryani_solo.png ref와 비교, AV한정, cap≤5. ★한계: 회귀서 이 1230AV의 subtle 마킹은 못 잡음(정상 앞목흰색 오탐은 안 함=known-good pass). 명백한 nape환각용.
- **RF 빈약→레인부적합** cap≤6(AV 무훅의 RF짝)·**교차일 컨셉중복** cap≤6(최근 회차 제목 리뷰컨텍스트 주입, 1탄/2탄 시리즈 예외).
- 미해결 durable(회고 D31): board /concept가 배치(LAUNCH_LEAD_DAYS=2) **뒤** 도착하면 침묵 드롭(used_at=None) → 다음 열린 날짜 롤/재렌더 필요. 빈슬롯 채움이 카드 파이프라인 밖(orphan)이면 cooldown·중복가드 우회.
- 미해결(회고 E10): 제목은 컨셉 브레인스톰 상상이 아니라 **그라운드된 캡션(클립 진실)**에서 파생해야(참외 대소동 제목이 휴식 클립에).

## gotcha
- `veto_video(delete=True)`는 **영구 삭제** — 재캡션할 원본을 지우면 복구불가. 재렌더/veto 전 재캡션 가능성 먼저 판정.
- 렌더 workdir prefix = card_id(`cameraman_<card>_<ts>`). produce_and_render가 Giri 수정필요면 RENDER_OUTS=[]지만 mp4는 디스크에 있음(직접 예약 우회).

## ✅ 9/9 전부 완료 (헬리콥터 포함 라이브 예약됨)

## ✅ durable fix 4건 코드 완료 (배포 4473d19, 회고 D31·E11·B21)
검증까지 완료 — 다음 03:00 배치가 첫 실전:
1. **directive 리드타임 롤** — `arc.set_concept_directive(roll_if_late=True)`(slack/board만): 타겟 배치 이미
   났으면 다음 열린 날짜로 롤+PD 안내. 수동 렌더 스크립트는 roll_if_late=False라 무영향. `CONCEPT_DIRECTIVE_ROLL=0`.
   검증: `_next_open_batch_date(2026-08-03)=2026-08-05`.
2. **AV 결정론 concept-dedup** — `producer` AV 경로에 RF와 동일 렉시컬 게이트+1회 재제안(LLM콜). `AV_DEDUP_GATE=0`.
   의미적 재탕(밈vs무빙밈)은 Giri 교차일 캡이 담당(짝).
3. **제목↔캡션 결정론 guard** — `_auto_upload_episode` `TITLE_CAPTION_GUARD`: 제목이 번인 캡션과 content
   명사 공유 0이면 캡션 유래 제목 폴백. 보수적. 검증: 참외제목∩휴식캡션=∅→발화, 정상제목→미발화.
4. **orphan-슬롯 surfacing** — `launch_selfheal` SKIP_FILLED이 카드백킹 vs orphan 구분: orphan-슬롯은
   loud 경고(재사용 의심·`reconcile --veto` 안내). 자동삭제는 PD 수동배치 삭제 위험이라 안 함.
- **미착수(선택)**: nape 게이트 강화(subtle 케이스) — 정상 앞목흰색 오탐 위험이라 저위험이면 보류 권장.

## ★NEXT
- 9/9 전부 라이브 예약 완료 — 8/3·8/4 슬롯 spot-check(PD).
- durable fix 4건 코드 완료·배포(4473d19). **다음 03:00 배치가 첫 실전** — 스팟체크:
  · Giri nape/RF빈약/교차일중복 캡 **과반려 X**(특히 정상 앞목흰색·잔잔한 RF).
  · directive 롤(board /concept 늦게 주고 이동 안내 뜨는지)·AV dedup·제목guard·orphan 경고 발화 확인.
