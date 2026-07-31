# Session handoff — 2026-07-31

**스파인:** **프레임이 ground truth. 생성기가 primary 방어, Giri는 프레임을 맞게 샘플할 때만 무는
backstop. 자동 판정기(self-heal 진단·face 게이트)는 자신 있게 틀린다 — 아티팩트가 ground truth.
파이프라인 뒤쪽 게이트가 버리는 것을 앞쪽 선택/조립이 모르면, 그리고 홀리스틱을 오버라이드하는
단독-fail을 단일 비결정 신호에 걸면, 좋은 콘텐츠가 죽고 슬롯이 빈다.** 7/28~7/31 네 배치의 PD 리뷰에서
출발했고, 겉보기 증상들이 몇 개의 근본으로 수렴했다. (7/28~30 요약은 notes/session_handoff_2026-07-28.md에도.)

## VM authoritative. push=deploy.
- `git push origin main` → 배포 타이머(2분 폴)가 pull→smoke→봇 재기동. **VM HEAD = `667ba50`** (코드 마지막 = `e075758`).
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
  DB/스크립트 `sudo -u rianileo`. YouTube 상태는 **라이브 API로 확인**(DB stale 가능 — 8/1 확인 참고).
- ★수동 작업은 반드시 `deploy/run_job.sh <script.py>` 래퍼(PYTHONPATH·시크릿·ffmpeg PATH). 직접 `.venv/bin/python`은
  `GOOGLE_API_KEY`/`agents` 임포트 실패.
- 인라인 SSH heredoc에서 **괄호 `()`·f-string 중첩따옴표·`\$`가 자주 깨진다** — 작은 `.py`/`.sh`를 `scp`로 올려 실행.
- 손수정 툴: `recaption_finish.py`(footage 보존 재번인+재조립, $0) → `reupload_episode.py --card <8자> --video <mp4> [--title '..']`
  (삭제先·업로드後 — 옛 vid 404면 새 업로드 안 됨; 그땐 카드 youtube_video_id=NULL 후 재업로드).
- 카드 없는 빈 슬롯 채우기: `upload_short(mp4, title, desc, publish_at_iso=..)` → 카드 youtube_video_id/publish_at/state='published' UPDATE.

## SHIPPED (durable, 배포됨)
- **7/28 캡션 지어냄 + 조어 컨셉** (c0a71c8·538f78d·6f0ebe9): RF `_RF_ACTION_SYS` 모션·표면/목적지 그라운딩
  + Giri line95(제자리 모션)·line99(소파≠침대·창가≠현관) + AV **결정론 조어 게이트** `_coined_concept_gate`.
- **7/29 상호작용/감정 + 윙크 tempo + 과장 테마** (7c67634): RF 상호작용(서로 향할 때만)·감정(그루밍≠시무룩)
  그라운딩 + **윙크 systemic tempo압축 fix**(`_build_wink_cut`/`_fold_wink_into_closer` `tempo_factor=1.0`) + 과장테마 정직프레이밍.
- **7/30 RF footage 게이트** (0b28d31): floor **8→11**(렌더 min 정렬) + **has_human 컷 할인 0.6**(face-leak 반영). 빈 RF슬롯 근본.
- **7/31 AV face 게이트** (e075758): hard 얼굴결함(orb/melt/floating)은 **2차 독립 face-체크 corroborate될 때만 단독 fail** —
  flaky 단일 VLM 오탐(orb)이 홀리스틱 9점 AV를 죽여 슬롯 비우던 근본. AV_FACE_CORROBORATE=0 리버트.

## 손수정 (라이브, 예약-비공개, 프레임 검증)
- **7/28**: RF0800 `QZmo-emTfmo` · RF1800 `-FYp3EVRW54`(죽은척) · AV2100 `BZVE3npCoOU`(관조봇 제거)
- **7/29**: AV0800 `H21r2GVjpHc`(윙크풀길이+더위식히기) · RF1230 `tWENITRU2sQ`(핸드큐레이션) · AV1800 `XZp2QRLhBT0`(관조→느긋) · RF2100 `XbkkxmQxhSk`
- **7/30**: 08:00 `qfsI3ML2WKQ`(수석전문가) · 12:30 `C2ierxdu6aw`(관조→차분) · 18:00 `dHusRh7BRIg`(얼음하키 AV, RF중복 교체) · 21:00 `5lfG5N9fzc4`(발재간·37s)
- **7/31**: 08:00 `gyNowKOyxkQ`(수박 AV, 빈슬롯) · 12:30 `gdxvfwfpziA`(파도결투) · 18:00 `jwXywrWv35g`(서유기 분신술 AV) · 21:00 `WrUsFrEp8WI`(수영달인)

## AV 손큐레이션 노하우 (이번 세션 검증)
- **`render_av_one` 디렉티브 패턴**으로 손큐레이션 AV 제작(CONCEPT_BRAINSTORM=0). 기대→반전→여운 구조 + 단일 거실 +
  또렷한 액션 + 윙크 명시. 얼음하키·수박·**서유기 분신술** 모두 성공 — Seedance가 **여러 레오 분신 + 판타지 반짝**도 훌륭히 렌더(놀라움).
- **produce_and_render가 빈 리스트 반환**해도 mp4는 디스크에 있음: 홀리스틱 Giri가 9점이면 그 mp4를 `upload_short`로 직접 예약 가능
  (7/31 AV0800 수박 = face 게이트 오반려였고 실제 9점 → 우회 예약). 이제 e075758로 근본 해결됐지만 이 우회는 유효한 ops.
- **손큐레이션 함정**: ①촬영 시각 인접 클립(연속 버스트)은 중복(7/30 235601 vs 235557 = 4초차) — timestamp 근접도 확인.
  ②하드코딩 제목이 클립과 모순되면 Giri title-vs-clip 반려(여름'밤'vs아침·'랴니 발라당'vs레오) → 시간/포즈 안 박은 **중립 제목**, 캡션 arc는 생성기가.

## 회고 (notes/retrospective_2026-05_to_07.md)
B16 스코프갭 과소반려 · B17 조어 러버스탬프→단일목적 게이트 · B18 윙크 tempo systemic · B19 생성기 primary·Giri산문 soft ·
B20 얼굴 게이트 alone-fail이 단일 flaky콜 의존→과반려 · C19 선택은 게이트가 뭘 버릴지 알아야.

## ★NEXT (PD 스팟체크 / 미결)
1. **★8/1 배치 불일치 확인 필요**: DB·라이브 API 둘 다 **4/4 유효**(08:00 RF hfkLXHV5iX8·12:30 AV su3dBF7NUrs·18:00 RF
   ZbB15UIDq7E·21:00 AV wPKbRermexs, 전부 private 예약·중복 아님)인데 PD는 "3개"라 하심. **어느 슬롯이 비어 보이는지 PD 확인**
   필요(뷰 stale 가능). PD 새 규칙: **배치가 4개 안 되면 RF로라도 맞춘다** — 실제 빈 슬롯 확인 시 즉시 RF로 채울 것.
2. **다음 03:00 배치들 첫 실전 스팟체크** — 이번 세션 durable 전부의 첫 프로덕션: footage 게이트(빈 RF슬롯↓·과반려X)·
   **face corroboration**(빈 AV슬롯↓)·조어/관조 게이트·윙크 tempo(안 눌림)·RF 상호작용/감정 그라운딩·과장테마.
3. **★iCloud 싱크 수렴 버그**(7/28 발견, 미해결): truncated/corrupt 자산(`med_2017_09_06_023919`, 120 bytes)이 VLM 실패→
   un-ingested 백로그서 안 빠짐→청크 루프 무한(imported=4·backlog=4 반복). Fix 방향=VLM-실패/truncated 자산을 ingested-with-error
   마킹 or backfill 후보서 제외. cf [[icloud_vlm_zerobyte_fix]].
4. **RF 여름 footage 얇음 + 6월 클립 일부 미보유**(availability). 해변/파도 등 특정 클립은 단일(동반 없음)이라 길이 제약(RF1230 17.3s).

## 기타 운영 메모
- iCloud 싱크 = Mac 로컬(`scripts/icloud_full_sync_chunked.sh`), 매 라운드 GCS export, VM `ingest_register --import` cron(30분).
- board↔CLI 공유 로그 `notes/progress_log.md`(`agents.progress_log`).
- 03:00 launch_selfheal 크론이 유일 자동 배치(LEAD_DAYS=2). RF는 $0 self-heal 6라운드; AV는 유료라 일찍 terminal.
- self-heal LLM 진단은 자주 환각(없는 파일 지목) — `low_confidence`/`⚠️` 플래그(C16 가드) 믿고 로그 원문·아티팩트로 재검증.
