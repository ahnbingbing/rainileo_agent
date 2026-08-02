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

## ★ durable fix 필요 (이번 세션은 수동 수습만 — 코드 근본수정 미완)
회고 D31·E10 참조. 우선순위 순:
1. **제목 캡션파생 (E10)** — 제목/설명이 컨셉 브레인스톰 상상에서 나와 그라운드된 캡션과 어긋남(참외 대소동 vs 휴식클립).
   Fix 방향: RF/AV 업로드 제목을 **번인된 captions.json의 실제 씬 캡션**에서 파생(또는 캡션 확정 후 제목 재생성).
   canon 나이/이름은 이미 chokepoint서 결정론 교정(canon.correct_canon_age_text) — 제목-내용 **정합**만 생성기 신뢰에 남음.
   위치: producer 업로드 경로(`_auto_upload_episode` title 소스) + writer/director 제목 생성.
2. **directive 리드타임 catch-up (D31)** — `LAUNCH_LEAD_DAYS=2`라 배치 뒤 도착한 `/concept` directive는 침묵 드롭(used_at=None).
   Fix 방향: directive 생성시 타겟 날짜 배치가 이미 났으면(launch_batch_videos에 그 날짜 있음) **다음 열린 날짜로 롤** 하거나
   board가 그 슬롯 재렌더 트리거. 위치: `arc.set_concept_directive`/board directive 핸들러 + `agents/launch.py`.
3. **orphan-fill 카드경유 (D31)** — 빈슬롯이 카드 파이프라인 밖(재사용 재업로드)으로 채워지면 clip-cooldown·슬롯 중복가드 우회.
   Fix 방향: 슬롯 채움/리필은 반드시 카드 생성→cooldown seed→예약 경로만. 위치: 빈슬롯 채우는 코드(board/self-heal fill).
4. **교차일 컨셉 dedup 생성기측** — Giri backstop은 배포(cap≤6)됐지만 **생성기 예방**은 미완. `_concept_lexical_collision`
   (intra-batch)을 최근 공개/예약 회차(last ~3d)까지 확장. 위치: `agents/producer.py` 컨셉 선정.
5. **nape 게이트 강화(선택)** — 현 게이트는 명백한 nape환각만 잡고 subtle은 못 잡음. 강화하려면 nape 전용 **밀도 높은 프레임 샘플**
   필요하되 정상 앞목흰색 오탐(false-fail) 절대 금지가 우선. 저위험이면 보류 권장.

## ★NEXT
- 9/9 전부 라이브 예약 완료 — 8/3·8/4 슬롯 spot-check(PD).
- 회고(B21/D31/E10)·handoff 푸시됨.
- 다음 03:00 배치서 Giri nape/RF빈약/교차일중복 캡 첫 실전 스팟체크(**과반려 X** 감시 — 특히 정상 앞목흰색·잔잔한 RF).
- durable fix 1~4 착수(PD 우선순위 확인).
