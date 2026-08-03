# Session handoff — 2026-08-03

**스파인:** 프레임이 ground truth. 리뷰를 받으면 먼저 **재캡션 vs 재렌더 vs 재선택**을 판정하라 —
캡션이 프레임과 어긋나면 재캡션, footage 자체가 못 쓸 수준(주체가 안 보임/너무 짧음)이면 재선택.
결정론 가드는 **모든 경로가 수렴하는 마지막 choke point**에 둬야 우회가 없다(render-only 가드는 salvage가 우회).

## VM authoritative · push=deploy
- `git push origin main` → 배포 타이머(2분 폴)가 pull→smoke→봇 재기동. **VM HEAD = `4721d47`**.
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
- ⚠️ **API 키는 env에 있지만 `set -a` 없이 소싱하면 export 안 됨** — 수동 렌더/스크립트는 반드시
  `deploy/run_job.sh <script.py|-m mod>` 로 실행(deploy.env+env를 set -a로 export). `_pd_launch.sh`도 동일.
- ⚠️ **SSH가 Mac 네트워크로 자주 끊김(~15-20s)** — 긴 렌더는 `systemd-run --unit=<x> --uid=rianileo ...`
  transient unit으로 띄우면 세션과 무관하게 완주(이번 세션 AV 렌더 방식). setsid `&`는 SSH 드롭에 안 죽는다
  보장이 약함. 큰 mp4 scp는 자주 실패 → **몽타주를 VM에서 만들고 작은 JPG만 내려받아** 프레임 검증.
- ⚠️ 하네스가 `.env`/env 파일 grep·값 출력(길이 포함)을 차단 — 키 존재확인은 `run_job.sh`로 `os.environ` 검사.

## SHIPPED — 8/5 배치 PD리뷰 4건 (전부 라이브)
PD: "유튜브에 3개뿐 / 0800RF 캡션 엉망 / 1800RF 랴니 다 잘려 안보여+너무 짧아 이게 통과된게 맞아 / AV 하나 더 / RF 퀄 떨어지네".
- **왜 3개만** = 21:00 AV 후보가 12:30 AV와 같은 간식-레오 테마 → dedup에 걸려 미업로드 → 슬롯 빈 채(설계상 junk<빈칸).
- **08:00 RF** `MjDh7vRMwF8`→`lhczArSGpVg` — **재캡션**(cut1 초록쿠션 삭제, 4컷). cut2 "미동도 없음"→"발 꼼지락 꿈"
  (움직이는 프레임을 정반대로 캡션한 오독 교정), cut4 "고개 갸웃=하이라이트", cut5 "뭐라는 거냐 인간". 56s→44s.
- **18:00 RF** `M-W7cmCl8vk`→`XabyxiGD4po` — 원본이 6.3초짜리 뒷모습 walk(정면 구간 0)라 **재선택**. 신선 개울
  수영 클립(2026-07-14 `63e3993b`)으로: 랴니 헤엄 정면, 1280×720 가로 → **blur-fill 9:16**(움직이는 피사체라
  크롭 대신 블러배경 채움, 항상 또렷). 17초. 물-매니아 canon 부합.
- **12:30 AV** `LHyullpP5yc` — PD 불만 없어 유지.
- **21:00 AV** 신규 `0BTXIXzqQAg` — "생일 밤 별 구경"(밤 창가 별보기+케이크 촛불+윙크, Giri 9/10). directive
  렌더(`systemd-run` + `run_job.sh`), `scripts/_pd_schedule.py`로 21:00 예약. 간식 테마 회피.
- 재캡션/재선택 빌드는 `scripts.recaption_finish`(원본 per-cut 세그먼트 재활용, $0), reupload는
  `scripts/reupload_episode.py`(같은 publishAt, 비공개라 조회수 손실 0). RF0800은 원본 workdir
  `cameraman_b78b1646_*/animated/cut{2..5}` 재조립(cut1 제외).

## durable (배포·VM검증 70eb06a) — 회고 C21
"이게 통과된 게 맞아"의 진짜 범인 = **salvage가 render-only 14s 가드를 우회해 10.3s stub 업로드**. 두 겹 fix:
1. **footage-sufficiency가 NULL duration에 blind** (`producer._probe_clip_seconds`): 게이트가
   `_use = min(_req,_act) if _act else _req` — 클립 `duration_sec`가 NULL이면 요청 43초를 그대로 크레딧
   (6.3s 클립이 floor 11s 통과). → NULL이면 ffprobe로 실측+DB 백필. 프로브 실패 시 추정치 유지(net이 잡음).
   `RF_FOOTAGE_GATE` 경로. 부수효과: NULL 백필로 `_drop_tiny_clips`도 개선.
2. **RF 최소길이 가드를 업로드 choke point로** (`producer._auto_upload_episode`): render·salvage·수동이 전부
   funnel하는 자리에서 RF < `RF_MIN_SECONDS`(14)면 예약 거부(슬롯 비움 > stub). AV는 render_style 가드로 면제.
   `RF_UPLOAD_MIN_GUARD=0` reverts. 회귀검증(VM): 10s REJECT(upload 미호출)·15s 통과(오탐 0).
- **미착수(soft)**: RF 캡션 생성기가 움직이는 클립에 "미동도 없음" 단정(프레임 오독). 절대-무동작 단정은
  video 컷에서 위험 → 긍정 관찰 유도할 **생성기-측 가이드** 필요. 결정론화는 오탐 위험이라 보류 권장.

## ★NEXT
- 8/5 4/4 라이브 예약 완료 — PD 스팟체크(특히 21:00 AV 밤별보기 canon·1800RF 개울 랴니 가시성).
- durable 70eb06a **다음 03:00 배치가 첫 실전** — 스팟체크: RF footage 프로브(NULL duration 백필)·업로드
  최소길이 가드가 **과반려 없이**(정상 ≥14s RF 통과) gutted stub만 거르는지. 빈 슬롯이 생기면 self-heal이
  재제안으로 채우는지(안 채우면 손큐레이션).
- 반복 관찰: **왜 3개만**의 구조적 뿌리 = 같은날 같은 테마 AV 중복 시 재제안이 *다른* 컨셉을 못 내면 슬롯이 빔.
  AV_DEDUP_GATE(4473d19)는 dedup은 하지만 distinct 재제안 보장은 약함 — 반복되면 재제안 다양성 강화 검토.
