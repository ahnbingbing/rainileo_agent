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
- 21:00 RF→AV `<PENDING>` — 헬리콥터 혓바닥(RF 혀타이밍 어긋남+내용 얇음 → AV 전환). 렌더중, 예약 전 PD 확인.

## durable (배포 49da5ba) — 회고 B21/D31/E10
- **Giri nape-white 게이트(reviewer `_check_ryani_nape`)**: 랴니 목뒤 흰마킹(삐용이 번짐)을 렌더게이트(3프레임)+홀리스틱 둘 다 놓쳐 9점 러버스탬프 → 최종 렌더 프레임을 ryani_solo.png ref와 비교, AV한정, cap≤5. ★한계: 회귀서 이 1230AV의 subtle 마킹은 못 잡음(정상 앞목흰색 오탐은 안 함=known-good pass). 명백한 nape환각용.
- **RF 빈약→레인부적합** cap≤6(AV 무훅의 RF짝)·**교차일 컨셉중복** cap≤6(최근 회차 제목 리뷰컨텍스트 주입, 1탄/2탄 시리즈 예외).
- 미해결 durable(회고 D31): board /concept가 배치(LAUNCH_LEAD_DAYS=2) **뒤** 도착하면 침묵 드롭(used_at=None) → 다음 열린 날짜 롤/재렌더 필요. 빈슬롯 채움이 카드 파이프라인 밖(orphan)이면 cooldown·중복가드 우회.
- 미해결(회고 E10): 제목은 컨셉 브레인스톰 상상이 아니라 **그라운드된 캡션(클립 진실)**에서 파생해야(참외 대소동 제목이 휴식 클립에).

## gotcha
- `veto_video(delete=True)`는 **영구 삭제** — 재캡션할 원본을 지우면 복구불가. 재렌더/veto 전 재캡션 가능성 먼저 판정.
- 렌더 workdir prefix = card_id(`cameraman_<card>_<ts>`). produce_and_render가 Giri 수정필요면 RENDER_OUTS=[]지만 mp4는 디스크에 있음(직접 예약 우회).

## ★NEXT
- 헬리콥터 혓바닥(8/4 21:00) 렌더 완료 → 스팟체크 → PD 확인 후 예약(기존 `s8hi3` RF veto).
- 회고(B21/D31/E10)·이 handoff 푸시(로컬 커밋 `docs(retro)` 대기중).
- 다음 03:00 배치서 Giri nape/RF빈약/교차일중복 캡 첫 실전 스팟체크(과반려X).
- durable 미완: directive 리드타임 catch-up·orphan-fill 카드경유·제목 캡션파생.
