# Session handoff — 2026-07-24 (07-23→07-26 대형 세션)

**스파인:** **자동 판정기(self-heal 진단 LLM · Giri 리뷰어)가 오늘 4번 자신 있게 틀렸다 — 매번 실제
아티팩트(로그·캡션 JSON·프레임·라이브 스케줄)가 ground truth였다.** 그리고 launch/self-heal 파이프라인의
연쇄 근본 수정. 시작 질문은 "07-25 새벽 배치가 왜 1편만?"이었고, 파고들수록 겉보기 원인≠진짜 근본이
반복됐다(회고 C16·C17·B15·D29·D30).

## VM authoritative. push=deploy.
- `git push origin main` → 배포 타이머(2분 폴)가 pull→smoke→봇 재기동. **VM HEAD = `c9cb964`**(코드 최신;
  `c238e18` 회고문서는 pull 대기 중이나 비기능).
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
  **IAP 간헐 실패(255) — 재시도로 붙음**(이번 세션 킬 명령 3회 연속 255→회복 후 성공).
- DB는 `sudo -u rianileo`로만 열림(600). YouTube 상태는 **라이브 API로 확인**(`agents.reconcile.list_scheduled_videos`).
- ★인라인 heredoc/중첩 이스케이프 깨짐 반복 — 작은 `.py`/`.sh`를 `gcloud compute scp`로 올려 실행(이번에도 그렇게 함).

## ★★ 운영 교훈 — 수동 리필/재렌더 (이번에 $6 낭비하며 배움)
- **수동 배치/슬롯 재렌더는 반드시 `deploy/run_job.sh` 래퍼로**:
  `bash deploy/run_job.sh -m agents.launch_selfheal --date 2026-07-25 --slot 21:00`.
  run_job.sh가 `cd APP_DIR` + `PYTHONPATH=$APP_DIR` + TZ + 시크릿을 세팅한다. **`.venv/bin/python -m agents.launch_selfheal`
  를 직접 부르면** cameraman이 띄우는 `scripts/burn_captions.py` 서브프로세스가 `import agents`에서 **`ModuleNotFound:
  agents`**로 죽는다(번인 단계, Seedance 다 쓴 뒤라 돈만 태움). 메모리 gotcha 그대로 밟았다.
- **reschedule/veto (라이브 스케줄 손수정)**: `from youtube.upload import veto_video, get_youtube`.
  veto=`veto_video(vid)`(private+publishAt 클리어). reschedule=`yt.videos().update(part="status",
  body={"id":vid,"status":{"privacyStatus":"private","publishAt":"<UTC ISO>","selfDeclaredMadeForKids":False}})`.
  **둘 다 eventually-consistent** — update 직후 read-back은 옛 값(stale). 잠시 후 재확인해야 진짜 반영 보임.
- **detached 실행**: `sudo -u rianileo bash -c 'cd APP_DIR && setsid nohup bash <script> > data/logs/<log> 2>&1 </dev/null &'`.
  SSH 끊겨도 산다. 완료 감시는 백그라운드 `for i in $(seq …); do pgrep -f <script> || break; sleep 30; done` + 로그 tail.

## SHIPPED (durable, 배포·검증) — 6개
1. **`8eaf220` D29 — Seedance runaway 카운터 per-episode 리셋.** `_SEEDANCE_CALL_COUNT`가 모듈 전역·리셋 없음 →
   한 프로세스가 AV 여러 편(배치 2슬롯 + self-heal 라운드) 렌더 시 40캡에 **누적** → 뒤 슬롯이 자기 몫(~10컷)은 멀쩡한데
   41에서 터져 좋은 슬롯을 비움(07-25 08:00). Fix=`render_card` ai_vtuber 분기서 `_reset_seedance_budget()`(RF는 Seedance
   미사용+두 레인 병렬스레드라 레이스 회피 위해 AV 분기에만). **실전검증: 08:00 재렌더가 10/40만 쓰고 완주.**
2. **`4698875` C16 — RF 자투리클립 + 사전 재료게이트 + self-heal 진단 파일검증.** 07-25 12:30 근본=4컷이 0.7s
   iOS 라이브포토 버스트 1클립 세그먼트→collapse→5.2s gutted(self-heal은 없는 파일 `branding_cards.py`를 날조). Fix:
   ①`_tiny_clip_ids/_drop_tiny_clips`(duration_sec<`RF_MIN_CLIP_SECONDS=1.5`, 201개 burst 드롭) ②`_propose_realfootage_
   singlepass`서 collapse 후 achievable content초<`RF_MIN_CONTENT_SECONDS=8`이면 `[재료검증]` 재제안(`RF_FOOTAGE_GATE=1`)
   ③`diagnose_failure`가 LLM `fix_file` 실존 확인→없으면 `low_confidence`+⚠️.
3. **`dad4720` C17 — canon 이름 로마자 교정.** 21:00 RF의 "Lani"는 레오 오타 아니라 **랴니(Ryani) 로마자 드리프트**
   (KO 랴니 정상·EN만; Giri 진단방향도 틀림). `correct_canon_names_text`(Lani/Lanni/Riani/Ryanie/Ryany/Ryanni→Ryani)를
   `correct_canon_age_text`에 **fold**→번인·업로드 제목/설명 **모든 chokepoint 자동 상속**.
4. **`ceb060e` B15 — Giri 반려에 프레임근거 요구(과엄격도 검수기 실패).** 21:00 RF를 Giri가 "cut3 레오 없음"으로 환각-반려했으나
   **프레임엔 레오가 식탁 위 명백**(직접 확인). `caption_vs_clip_mismatches` 성립요건을 좁힘: 프레임이 명백히 반박할 때만,
   ①sparse 프레임서 펫-부재 추론 금지(결정론 그라운딩 게이트가 이미 강제) ②스토리보드-이탈 감점 금지(클립 실체로 판단).
   정당한 CHECK-0 거짓 캡(킥/이동/표면)은 보존. **회귀검증: 같은 mp4 4/수정필요→7/소폭수정, cut3 환각 소멸.**
5. **`f99f27f` C18 — RF self-heal lane-aware(공짜니 끈질기게).** RF는 Seedance $0 → 실패해도 `max(rounds,
   RF_SELFHEAL_ROUNDS=6)`까지 매 라운드 fresh 컨셉 reroll·절대 terminal 아님; AV는 EXPENSIVE terminal 캡 유지(추가
   라운드는 RF만 소비). **실전검증: 21:00 RF가 R6에서 통과(3라운드였으면 빈 슬롯).**
6. **`c9cb964` D30 — 배치가 라이브 스케줄 읽어 빈 슬롯만 생산.** 07-25가 4슬롯에 9개, 07-24 6개 중복 근본=배치가
   `day_assignments` 4슬롯을 **무조건 다** 렌더+예약(카드-교체 bd12680은 슬롯-중복 못 막음)+내 리필 반복. Fix=
   `run_with_selfheal`이 생산 전 `reconcile.list_scheduled_videos`(고아 포함) 읽어 찬 KST 슬롯 제외(무필터 03:00
   배치만; --slot/--lane 우회; API 실패시 전부 생산 폴백; `LAUNCH_SKIP_FILLED=1`).

**새 env 노브**: `RF_MIN_CLIP_SECONDS=1.5` · `RF_FOOTAGE_GATE=1` · `RF_MIN_CONTENT_SECONDS=8` ·
`RF_SELFHEAL_ROUNDS=6` · `LAUNCH_SKIP_FILLED=1`. (전부 0/기본으로 리버트 가능.)

## 손수정 (라이브 스케줄)
- **07-25 빈 슬롯 3개 재렌더 성공(4/4 완성)**: 08:00 AV `uD4UeDIKoKU`(8/10)·12:30 RF `BFILRKmcvZE`(9/10)·
  21:00 RF `mthFsh8n3hU`(7/10, R6통과)·18:00 AV `9cdGYLgvgRc`(원래분).
- **07-24/07-25 중복 대량정리**: 여름 여분 4편→빈 07-27 재예약(`_Jz_E5bRLqI` 08:00물놀이·`_cee5rTRne0` 12:30화장실·
  `S_9CA4BTfy4` 18:00펠프스·`mF_S3M5AVAA` 21:00에어컨), 비계절 4편 veto(`Ay6k0OHy_w8`가을·`4di23b40s28`겨울농구·
  `H1USoSqmgQc`낮잠·`H7EHp1uMuUU`앞발). **결과: 07-24~27 전부 슬롯당 1개, DUP 0.**

## ★NEXT / 열린 항목
- ★**모든 durable의 첫 실전은 07-25/07-26 03:00 배치.** 스팟체크: ①slot-skip(07-27 이미 재활용분으로 찼으니 배치가
  건너뛰나 — 07-25 03:00 배치가 07-27 생성분인데 스킵해야 정상, **아직 미확인**) ②RF 6라운드 끈기 ③Giri 과반려 감소
  ④Seedance per-episode 리셋 ⑤재료게이트 ⑥Lani→Ryani.
- ★**07-27은 카드 없는 고아 4편으로 채움** — arc/analytics는 모름(라이브 스케줄엔 있어 배치는 스킵함). 추적 갭이지만
  경미. 필요시 최소 카드 등록([[schedule_orphan_reconciler]] 손수정 레시피 참고).
- **21:00 RF는 잘 안 채워지는 슬롯**(R6까지 감) — fresh 재료가 face/temporal/too-short/preachy 게이트에 반복적으로 걸림.
  버그 아니라 그 날 재료 문제일 수 있으나, 재발 잦으면 근본(그 footage 클러스터) 봐야.
- **PD 리뷰 대기**: Slack 스레드의 07-25 4편 + 07-27 재활용 4편(특히 07-27에 비슷한 "펠프스" 수영 2편 있음 — 별로면 하나 veto).
- **known-hard 미해결**: RF 캡션↔클립 CONTENT 그라운딩(cut2/cut3형, C11~C14). B15가 Giri 과반려는 줄였지만
  그라운딩 게이트 자체는 정지프레임에 soft. 근본은 별건.
- **참고 비용**: 첫 리필서 PYTHONPATH 누락으로 08:00 AV 1회분 ~$6 낭비(위 운영교훈). 이후 run_job.sh로 전환.

전문 회고 D29·D30·C16·C17·C18·B15 참조. VM HEAD `c9cb964`.
