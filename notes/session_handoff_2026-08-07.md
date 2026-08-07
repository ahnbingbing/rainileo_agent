# Session handoff — 2026-08-07

**스파인:** 프레임이 ground truth. 리뷰는 재캡션 vs 재렌더 vs 재선택 판정 먼저. 그리고 이번 세션의 큰
교훈 — **"VM에 프로세스가 없는데 증상이 있으면 다른 머신(맥)을 의심하라"**, **"availability는 raw
path .exists()가 아니라 local_path()+GCS로 판정하라"**, **"Giri는 결함 게이트지 편집 최적화기가 아니다
— 주관적 편집판단은 생성기가 primary"**.

## VM authoritative · push=deploy
- `git push origin main` → 배포 타이머(2분 폴)가 pull→smoke→봇 재기동. **VM HEAD = `ebcf721`**.
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
- 리포 = `/home/rianileo/rianileo-agent`(소유자 rianileo). 명령은 `sudo -u rianileo bash deploy/run_job.sh …`.
  ⚠️ `run_job.sh -c "<multiline>"`는 sudoers 매칭 실패 → **한 줄 python -c만**(개행/특수문자 피하라).
- ⚠️ **수동 작업/자동 배치 둘 다 VM에서 돈다.** 맥 launchd는 이제 Photos 인제스트(icloud-sync/petlabel/
  slack-sync)만. 나머지 맥 프로덕션-중복 잡은 `unload -w`로 껐다(아래 유령렌더 참고).

## SHIPPED — 8/7 PD 리뷰 3건 (전부 라이브, 비공개 예약 교체)
- **08:00 RF0800** `vMK5IqoN_fE`→`pTn2sjqemgo` — **재편집**. 원본(med_223938, 202초 청어 먹방 세션)에서
  레오 청어 먹방 16초를 **cut1(앞)**, 발라당 눕기 18초를 **cut2(뒤)**로. 제목도 청어 리드. $0(recaption_finish).
  ★PD가 "34초에 청어"라고 바로잡음 — 내가 앞 10초만 스트립 떠서 놓쳤다(전체 span으로 떠라).
- **12:30 AV1230** `jWBWhgl425w`→`Wcl5MpX61fY` — **재캡션**. 리들명을 PD 지시대로 "랴니 엄마의 꼬리
  수수께끼"로(cut1 리들명+cut5 미제). $0.
- **21:00 RF2100(관계없는 길고양이)→신규 AV** `NaxNmzXTTdA` — **오마카세 셰프 레오의 참치 해체쇼**
  (render_av_one all-fantasy directive, Giri통과). 이후 PD 지시로 cut3 "과연 랴니의 운명은?"→"과연 레오의
  실력은?" 재캡션. 옛 RF2100 카드 archived.

## SHIPPED — 빈 슬롯 복구 → 8/7·8/8·8/9 전부 4편
이미 렌더됐지만 예약 안 된 고아 6편을 빈 슬롯에 예약(`_fill_slots`, private). 중복0·2RF+2AV 밸런스.
- 8/7: pTn2sjqemgo·Wcl5MpX61fY·Dk3M07OWHnY(꿀잠)·NaxNmzXTTdA
- 8/8: aKp5jZ0mFd8(얼음썰매)·4-mCZF2gAF8·8CLO9difK6w(자연다큐)·pwvUL6DKgbg(여름스냅)
- 8/9: Uv-ZaOkdz08(소파궁둥이)·fIbpZnn6T0I·ftBXdvYlvao·fS7uxK958qE(숨바꼭질)

## durable 코드 (커밋 5건, 전부 배포·검증)
1. **`0ebcb7e` RF gutting 근본** — PD "2000개 넘는데 왜 마르냐"가 맞았다. 비디오 2959개 중 VM 로컬은
   626개뿐, **2333개는 GCS미러엔 있는데 VM에 없음**(iCloud 클립 file_path=Mac경로). `_trim_real_footage_
   clips`(5056)·프리페치(8697)가 GCS fetch를 **`source_uuid` 있을 때만** 호출 — `download_to`는 file_path
   만으로 되는데도. uuid 없는 클립 unavailable 드롭 → 실질 RF풀 185개. Fix=uuid게이트 제거+local_path
   매핑. 검증: uuid없는 GCS-only 클립 fetch+trim PASS. **RF풀 185→2233 face-safe.**
2. **`d16feaf` 고아 로깅** — 렌더OK인데 업로드/예약 실패(video_id=null)면 조용히 done 처리돼 슬롯이 빈다
   (8/9 21:00 사례). `_auto_upload_episode` 3개 skip지점(no-card/rf-too-short/upload-exception)에
   greppable `[ORPHAN-SKIP]`, launch_selfheal이 output-but-no-video_id를 `[ORPHAN]`+슬랙으로 라우드 표시.
   로깅만, 동작 무변경. **다음 배치서 `grep [ORPHAN-SKIP]`으로 고아 원인 관찰.**
3. **`5931fa4` petlabels 좀비 fix** — `df -g`(macOS 플래그)가 리눅스서 에러→free_gb 빈값→`[0<3]`참→매
   라운드 60s pause 무한루프(4h50m 라벨링0). Fix=`df -BG --output=avail`+폴백 + MAX_PAUSE_STREAK 가드
   (10회 연속 below-floor면 abort). 검증: `free_gb=[8]→PROCEED`.
4. **`ebcf721` 편집 게이트 3종** — [[giri_defect_gate_editorial_gates]]. Giri=결함게이트라 편집품질 못 봄.
   ①RF훅우선(사건컷 앞·길게): `editor.md`(RF 컷순서 최종결정=RF0800 근본)+`realfootage_singlepass.md`+
   Giri '훅 묻힘' cap≤6. ②캡션 스포트라이트(쇼케이스 절정=퍼포머, 조연운명X): `caption_agent.md`+Giri
   '스포트라이트 오프레이밍' cap≤6. ③RF 원거리배제: `realfootage_singlepass.md` 강화+Giri '주체 원거리·
   정체불명' cap≤6. 회귀: gate2 CAUGHT+known-good오탐0, gate1 Giri MISS(주관적,예상)—생성기가 primary.

## 운영 근본 — 유령 렌더 (코드 아님)
슬랙에 "Seedance 1/60"·"6/6 캐릭터 생성"이 뜨는데 VM엔 렌더 프로세스 0. 근본=**맥 launchd
`com.rianileo.launch`가 GCP 이전 후 안 꺼져 launch_selfheal 배치를 VM 크론과 중복 실행**(새벽3:02~).
★판별자=Seedance 예산 숫자(맥 /60 vs VM /40). 조치: 맥 배치(PID)+렌더 kill, 맥 프로덕션-중복 launchd
12개 `launchctl unload -w`(launch·slack봇·board-esc·bandit·api-cost·metrics·ytcache·bgm·gmp·revlm).
★유지: `icloud-sync`·`petlabel-backlog`·`slack-sync`(Photos 라이브러리가 맥에 있어 iCloud→GCS는 맥에서만).
교훈: **VM에 프로세스 없는데 슬랙 알림 오면 로컬 맥부터 `ps aux`/`launchctl list`로 확인.**

## ★NEXT
- **다음 03:00 배치가 durable 4건 첫 실전** — 스팟체크:
  - RF gutting 사라졌나(RF 슬롯이 face-safe 풀 2233에서 채워져 빈슬롯 감소).
  - `[ORPHAN-SKIP]`/`[ORPHAN]` 로그로 고아 발생 시 원인 보이나(렌더OK·예약실패 근본은 아직 미조사 — 로깅으로 관찰 후 조사).
  - RF 컷이 사건-우선으로 편집되나(editor.md), AV 쇼케이스 캡션 스포트라이트, RF 원거리클립 안 뽑히나.
  - 과반려 없나(gate 캡이 정상 컷 안 죽이나).
- **petlabels** 이제 df 정상 → 라벨링 재개될 것(07:00 맥 잡은 유지). VM 크론 petlabels도 df 고쳐짐.
- **미완(형식):** merge-retrospective 문서(notes/retrospective_2026-05_to_07.md) 미반영 — 교훈은 커밋
  메시지+메모리 2개(giri_defect_gate_editorial_gates, mac_launchd_rogue_and_gcs_fetch_root)에 있음.
- **gotcha:** 프레임 검증 스트립은 전체 span 균등샘플(앞10초만 뜨면 뒷장면 놓침, [[frame_strip_full_span]]).
