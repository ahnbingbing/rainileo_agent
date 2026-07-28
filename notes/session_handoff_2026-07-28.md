# Session handoff — 2026-07-28

**스파인:** **프레임이 ground truth. 생성기가 primary 방어, Giri는 프레임을 맞게 샘플할 때만 무는
backstop. 자동 판정기(self-heal 진단)는 자신 있게 틀린다 — 아티팩트가 ground truth. 파이프라인
뒤쪽 게이트가 버리는 것을 앞쪽 선택/조립이 모르면 헛돈다.** 7/28·7/29·7/30 세 배치에 대한 PD 리뷰에서
출발했고, 겉보기 증상들이 몇 개의 근본으로 수렴했다.

## VM authoritative. push=deploy.
- `git push origin main` → 배포 타이머(2분 폴)가 pull→smoke→봇 재기동. **VM HEAD = `3d49099`**.
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
  DB/스크립트는 `sudo -u rianileo`. YouTube 상태는 **라이브 API로 확인**(DB stale 가능).
- ★수동 재렌더/재업로드/스크립트는 반드시 `deploy/run_job.sh <script.py>` 래퍼로(PYTHONPATH·시크릿·ffmpeg PATH).
- 인라인 SSH heredoc에서 **괄호 `()`가 셸을 깨뜨린다** — 작은 `.py`/`.sh`를 `gcloud compute scp`로 올려 실행.
- 손수정 도구: `recaption_finish.py`(footage 보존 재번인+재조립, $0) → `reupload_episode.py --card <8자> --video <mp4> [--title '...']`
  (옛 영상 삭제先·업로드後 — 옛 vid가 404면 새 업로드 안 됨; 그땐 카드 youtube_video_id=NULL 후 재업로드).

## SHIPPED (durable, 배포됨)

### 7/28 — 캡션 지어냄 + 조어 컨셉 (c0a71c8·538f78d·6f0ebe9)
- **RF 캡션이 화면에 없는 모션/장소를 지어냄** → 생성기 `_RF_ACTION_SYS`(비트 카덴스를 '캡션 앵글이
  나아감'으로 재정의·모든 모션동사 프레임 그라운딩·정지 클립은 관찰자 읽기·표면/목적지 정체 소파≠침대·
  창가≠현관) + Giri line95(지어낸-모션 cap을 제자리 모션 뒹굴/스캔까지 확장)·line99(표면·목적지 정체). cap≤5.
- **AV Writer가 통하지 않는 조어를 컨셉/제목으로('관조봇')** → writer_story/realfootage 실재어 규칙 +
  ★결정론 게이트 `_coined_concept_gate`(제목/테마만 보는 단일목적 LLM, 불투명 조어만 flag, 실재어·고유명·
  canon 가족어[랴니엄마/함미/하비] allow, fail-safe). cap≤6, 양레인.

### 7/29 — 상호작용/감정 + 윙크 tempo + 과장 테마 (7c67634)
- **RF 캡션이 상호작용·감정을 지어냄**('눈빛으로 대화'=실제 다른 각도·'시무룩'=실제 그루밍) → 생성기
  `_RF_ACTION_SYS`(펫-상호작용은 서로 향할 때만·감정은 행동과 일치, 그루밍≠시무룩) + Giri CHECK0 확장 cap≤5.
- **윙크가 systemic하게 압축**(모든 AV 윙크가 tempo_factor 미설정 → assemble 1.3배속 → 7초→5.4초 "너무 짧다")
  → `_build_wink_cut`/`_fold_wink_into_closer` 양 경로 `tempo_factor=1.0`(결정론, 유닛검증 solo/two=1.0).
- **과장 테마**(잔잔한 더위식히기를 '방구석 워터밤'으로) → writer_story 정직-프레이밍 원칙 + Giri SOFT 노트.

### 7/30 — RF footage 게이트 (0b28d31)
- **RF 빈 슬롯 근본**: RF 선택이 게이트(face-leak crop·min-length·availability)가 뭘 버릴지 모른 채 골라
  self-heal 6라운드 gutting. pre-render footage 게이트마저 floor 8s(렌더 min 14보다 느슨)+원시 clip길이 추정.
  → floor **8→11**(렌더 min RF_MIN_SECONDS에 정렬) + **has_human 컷 usable 할인 0.6**(RF_HUMAN_DISCOUNT,
  face-leak 드롭 반영, croppable 장클립 hard-block 안 함). 유닛검증: no-human 장클립 PASS·human 11s REJECT·
  human 85s PASS(과반려 없음).

## 손수정 (라이브, 예약-비공개, 프레임 검증)
- **7/28**: RF0800 `QZmo-emTfmo`(침대→소파·창가→현관) · RF1800 `-FYp3EVRW54`(뒹굴/스캔 삭제→죽은 척,
  제목도 "살아있니 레오야…?") · AV2100 `BZVE3npCoOU`(관조봇 제거+제목 교체)
- **7/29**: AV0800 `H21r2GVjpHc`(윙크 풀길이+더위식히기) · RF1230 `tWENITRU2sQ`(자동 8회 실패→핸드큐레이션
  여름낮잠, Giri9) · AV1800 `XZp2QRLhBT0`(관조→느긋) · RF2100 `XbkkxmQxhSk`(눈빛교환→각자각도·시무룩→그루밍)
- **7/30**: 08:00 `Ps02HVWw9jU`(기존) · 12:30 `C2ierxdu6aw`(관조→차분+윙크 tempo, 보너스) ·
  18:00 `x0LJYVNtU2U`(Giri8) · 21:00 `wLyjo5vIzdA`(Giri9)

## 회고 (notes/retrospective_2026-05_to_07.md)
- **B16** 인접 게이트 있어도 스코프갭으로 샌다(규칙 넓히기, 새 cap 남발 금지).
- **B17** 홀리스틱 리뷰어는 조어 제목 러버스탬프 → 단일목적 게이트라야 문다.
- **B18** 편집 인텐트(윙크 여유)를 필드로 안 실으면 assemble 기본값이 조용히 이긴다.
- **B19** 리뷰어 LLM-산문 규칙은 sparse 프레임/주관 판단서 안 문다 → primary는 생성기, Giri는 backstop.
- **C19** 선택은 게이트가 뭘 버릴지 알아야(원시길이 선택→렌더 gutting); front-run 게이트 floor는 자기가
  front-run하는 가드 기준과 같아야.

## ★NEXT (PD 스팟체크 / 미결)
1. **다음 03:00 배치(7/31~) 첫 실전 스팟체크** — 이번 세션 durable이 첫 배치에 먹는지:
   ①footage 게이트(floor 11+has_human 할인)가 RF 빈 슬롯 줄이는지 + **과반려로 오히려 슬롯 비우지 않는지**
   ②윙크 tempo 1.0 실전(윙크 안 눌리는지) ③조어/관조 게이트 ④RF 상호작용/감정 그라운딩 ⑤과장 테마 SOFT 노트.
2. **★iCloud 싱크 수렴 버그(이번에 발견)** — 트렁케이트/corrupt 자산(예 `med_2017_09_06_023919_icloud_5db13be2`,
   120 bytes)이 VLM 실패 → un-ingested 백로그서 안 빠짐 → 청크 루프가 같은 4개를 무한 재처리(imported=4·
   backlog=4 반복, 300라운드까지 헛돔). 이번엔 kill로 멈춤(신규 8개는 임포트+GCS export 완료). **Fix 방향**:
   VLM-실패/truncated 자산을 ingested-with-error로 마킹하거나 backfill 후보서 제외해 루프 수렴시켜라
   (icloud.sync backfill 픽 로직). cf [[icloud_vlm_zerobyte_fix]](0바이트는 fix됨, truncated는 별건).
3. **RF 여름 footage 얇음 + 6월 클립 일부 미보유(availability)** — 손큐레이션도 06-28 등 미보유에 걸림.
   손큐레이션 시 **시간/포즈 안 박은 중립 제목**이라야 Giri title-vs-clip 반려를 피함(캡션 arc는 생성기가 프레임서 씀).
4. **UK97AOlk_ls 원인불명 삭제(7/28)** — 자동 삭제자 없음 확인(하드삭제 경로는 reupload뿐, 전부 로그됨;
   봇 활동 0). 일회성 YouTube측 제거 추정, -FYp3EVRW54로 재업로드 후 안정. 재발 시 조사.

## 기타 운영 메모
- iCloud 싱크 = Mac 로컬(`scripts/icloud_full_sync_chunked.sh`, osxphotos+Photos). 매 라운드 GCS export
  (`gs://rianileo-assets/db_sync/assets.jsonl`), VM `ingest_register --import` cron(30분)이 upsert.
- board↔CLI 공유 로그 `notes/progress_log.md`(`agents.progress_log`)에 세션 진행 다 남김.
- 03:00 launch_selfheal 크론이 유일한 자동 배치(LEAD_DAYS=2 → 7/28 배치가 7/30 생산). RF는 $0,
  self-heal 6라운드; AV는 유료라 일찍 terminal.
