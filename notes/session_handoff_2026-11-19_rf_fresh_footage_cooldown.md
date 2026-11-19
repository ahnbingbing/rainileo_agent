# Session handoff — 2026-11-19 · RF 빈 슬롯 / 신선 footage / 쿨다운 축 (MV와 분리)

**범위:** RF 데일리 프로덕션 전용(빈 슬롯·footage 선택·쿨다운·캡션·밝기). **MV 작업은 별도 핸드오프**
(`mv_pipeline_build.md` 등)로 분리 — 이 세션엔 MV 미포함.

**한 줄:** "빈 슬롯"의 근본은 **신선 footage 고갈이 아니라, 여러 선택 레이어가 신선 never-used 클립(앨범에
인입된 최근 레오 footage 포함)을 과차단**해 파이프라인이 2016 pre-Leo 아카이브로 폴백한 것. ★PD 교정으로
완성: **쿨다운은 파일+트림 단위여야지 visual(vphash)이 아니다.**

---

## ✅ SHIPPED (origin/main, 전부 killsw·배포됨)

순서대로 (각 레이어가 다음 레이어를 드러냄 — 아래가 근본에 가까움):

1. **`1f14099`** 클립+장면 엔딩 쿨다운 (원 과제: 연속 2 라이브가 같은 소파 엔딩 — `RF_SCENE_MOTIF_COOLDOWN`).
2. **`8582dcd`** `_drop_unavailable_av_cuts`에 DROP-UNAVAIL 디버그 로그(왜 미존재인지 asset+src 기록).
3. **`410ed4c`** 신선 소진 시 **never-used(과거) 폴백**, 최근-사용 재-admit은 아카이브도 <6일 때만 (`RF_STALE_FALLBACK`).
4. **`b11cf0e`** **never-used 아카이브 우선 surfacing** — 연도별 쿼터를 quality순 아닌 never-used 우선 (`RF_ARCHIVE_NEVERUSED_FIRST`).
5. **`4c0a984`** 캡션 프롬프트(`realfootage_concept.md`): 과거 footage를 **"과거로서"**(시점마커 필수 + 동작은 화면이 보여주는 만큼=과잉특정 금지). 구 "메모리레인=가끔·과거 회피"가 ③④와 모순 → supersede.
6. **`e6d9a2d`** ★**fresh never-used floor** — `_diversity_sample`(loc×act, 100)이 104개 중 12개만 올려서(최근 footage가 소수 장소에 몰려 샘플이 과거로 채워짐) 신선 레오가 탈락 → 샘플 후 `_A`(fresh never-used) 상위 최대 30개 재추가 (`RF_FRESH_NEVERUSED_FLOOR`). **+ 밝기**: `assemble_episode` 컷별 luma 정규화를 **아웃라이어만**(median서 12pt+) **올바른 스케일(/255)** 0.7× 부분수렴으로 재활성(구버전은 /100 과교정이라 PD가 껐던 것) — `ASSEMBLE_LUMA_NORMALIZE/_OUTLIER/_STRENGTH`.
7. **`31039e9`** never-used를 visual 쿨다운서 면제(⑧에 subsume됨, 하네스로 잔존).
8. **`c8d1f31`** ★**PD 교정: 쿨다운=파일+트림, visual 아님.** `RF_VISUAL_COOLDOWN` 기본 **OFF**. vis_phash "비슷한 룩" 게이트가 신선 never-used 72개(최근 레오 포함)를 드롭→fresh 풀 0으로 굶겨 2016 폴백시킨 근본. 이제 쿨다운 축 = **파일**(all-time asset_id `_recently_used_rf_assets`) + **트림**(`_recently_used_rf_segments`). vis_phash는 `_diversity_sample` 내부 분산용으로만 잔존, 재사용 게이트 아님.

**검증(라이브 self-heal 로그):** 8라운드 grind→빈슬롯이던 슬롯이 7~9/10 통과. ⑧ 후 재검증: `이미 쓴 클립 N개 제외
(쿨다운)` — "비슷한 룩" 드롭 사라짐, "신선 0<8" 폴백 사라짐, **캐스트에 med_2026이 최다**(최근 레오 footage 선두).

**실제 채움:** remaining gaps NONE(스케줄 꽉 참). 단 아래 ★교체 대상 참고.

---

## ★ NEXT (미완 — power-off로 중단, 재개 지점)

1. **랴니-솔로 2편 재생산·교체** (PD 승인, 아직 안 함). ⑥⑧ 수정 **전에** 채워진 거라 둘 다 **레오 없는 2016
   랴니 솔로**다 — 이제 수정됐으니 신선 레오 버전으로 교체:
   - `yPUet0oqTUg` "여름 대야 랴니" → **11/20 21:00** (publish 2026-11-20T12:00Z)
   - `FIM_bOYNxT0` "겨울 경기장 랴니" → **11/21 18:00** (publish 2026-11-21T09:00Z)
   - 방법(메모리 `rf_fix_production_reproduction`): VM `run_job.sh -m agents.launch_selfheal --date <d> --slot <slot>
     --lane real_footage --no-upload` → 프레임 검증(최근/레오 캐스트 확인) → `scripts/reupload_episode.py --card <old_vid>
     --video <new mp4>`로 교체.
2. **밝기 A/B 판정 대기.** B 샘플(차 안 컷 luma 124→109, 아웃라이어만) PD께 전달함. luma 수정은 ⑥에 **기본 ON으로
   배포됨** — PD가 B 괜찮다 하면 유지, 과하면 `ASSEMBLE_LUMA_STRENGTH` 낮추거나 `ASSEMBLE_LUMA_NORMALIZE=0`.
3. 다음 09:00 cron(수정 코드)이 자동으로 신선 footage 슬롯 채움 지속 — 첫 실전 스팟체크.

---

## 진단 메타교훈 (반복 금지)

빈 슬롯을 **가용성·Mac경로·RAG·풀크기로 수차례 오진**(측정은 다 "멀쩡")했다. 진짜 근본은 **라이브 Giri/pool
로그 + PD의 "로컬 앨범에 신선 레오 있잖아"** 로 잡았다. 교훈: ①슬롯 비면 **실 self-heal 전체로깅으로 반려
사유부터** 읽어라(풀 측정 추측 금지) ②"신선=never-used(한번도 안 쓴 것)도 포함, 날짜 아님" ③선택 파이프라인은
**여러 레이어**(diversity 샘플·visual 쿨다운)가 각각 신선 풀을 깎으니, 한 레이어 고쳐도 다음이 또 굶긴다 —
끝까지 캐스트가 실제 신선인지 확인. 상세: 메모리 `empty_slot_fresh_exhaustion_neverused`, 회고 §4.9.

## 킬스위치 요약
`RF_SCENE_MOTIF_COOLDOWN` / `RF_STALE_FALLBACK` / `RF_ARCHIVE_NEVERUSED_FIRST` / `RF_FRESH_NEVERUSED_FLOOR`(30) /
`RF_VISUAL_COOLDOWN`(기본0) / `RF_VISUAL_COOLDOWN_NEVERUSED_EXEMPT` / `ASSEMBLE_LUMA_NORMALIZE`(기본1)·`_OUTLIER`(12)·`_STRENGTH`(0.7).

---

# 이어서 — 2026-11-19 저녁 · both-pet 디렉티브 신뢰성(B) + ★미룬 상류근본

**한 줄:** PD "왜 자꾸 기존이랑 다르게 퀄 낮게?" 재현 중, **신선 footage 풀 fix는 '풀'만 고쳤고
컨셉 프로포저+게이트가 both-pet 디렉티브를 실행 못 함**이 드러남. 생성기·검수기 3+1건 수정 배포(B).
단 **진짜 최종근본 = 신선 both-pet이 애초에 슬롯 후보 풀에 안 들어오는 pool-build 레벨**이고, 이건
**PD 지시로 다음 세션에 미룸.**

## ✅ 이번에 한 것

- **11/20 21:00 교체 완료(A).** 재생산 중 유일하게 제대로 나온 양펫편 `iaFDwAUBuys`(「랴니 축구 MVP vs
  레오 거실 황제」, 신선 2026 레오컷)로 `yPUet0oqTUg` veto 교체. 6/10이던 건 **location 게이트가 의도적
  cross-cut(실외↔실내)을 '장소 오표기'로 오판**한 것(아래 #3로 수정). 라이브 예약 검증 완료.
- **B 배포 (`d3fed32` + `b779b7b`, 전부 killswitch, RF 전용):**
  1. `RF_BOTHPET_FLOOR`(12) — 디렉티브가 both-pet이면 `avail_videos` 선두에 both-pet 클립 보장
     (`producer._propose_realfootage_singlepass`). **주 레버.** 근본: 쿨다운+`RF_OVERUSED_LOC_CAP`이
     both-pet이 군집한 'home' 클립을 깎아 Writer에 재료가 안 감.
  2. `RF_BOTHPET_ENFORCE`+`RF_BOTHPET_MAX_TRIES`(3) — both-pet 지시인데 솔로로 나오면 구조게이트가 재제안.
  3. `GIRI_BOTHPET_GATE`(reviewer `_subject_location_grounding_gate`) — 디렉티브=both-pet인데 솔로면
     결정론 cap ≤6/수정필요. `GIRI_CROSSCUT_EXEMPT` — split/양펫 cross-cut은 '장소 오표기' 면제.
     + 야외어휘 보강(운동장/경기장/축구장/들판/잔디/필드/field).
  4. `b779b7b` — floor가 **pre-cap 신선 both-pet 우선** 재admit(아카이브 fallback 전에). `concept["_directive_wants_both"]` 스탬프.
  - 디렉티브는 `next_directive()` 1회만 fetch(showrunner LLM 이중호출 제거). 회귀=게이트4+디렉티브6+클립3 전부 PASS.
    AV/grammar 무영향(RF early-return, 필드 게이트). `수정 필요`는 GIRI_PASS_VERDICTS 아님(auto-ship 안 됨).
- **E2E(11/21) 검증으로 메커니즘 작동 확인 + 잔존근본 노출:** floor 발화·enforce 재제안·**Giri가 솔로/era-mix/
  주체소거를 4/10으로 제대로 반려(러버스탬프 끝)**. BUT floor가 **아카이브 both-pet(아기 레오)**만 끌어와 era-mix→
  grind. 즉 아래 ★근본.

## ★ 미룬 근본 (다음 세션 최우선, PD 지시로 defer)

**신선 2026 both-pet 클립(437개, ≥3s)이 애초에 슬롯 후보 풀(`context["available_videos"]`)에 안 들어온다.**
`RF_BOTHPET_FLOOR`는 프로포저 레벨이라 **풀에 이미 있는 것만**(`_avail_precap`+`_arch_field`) 재admit 가능 —
pool-build 단계(diversity-sample loc×act 100개 + 쿨다운)가 both-pet을 미보장하면 floor엔 아카이브 both-pet밖에
안 남아 **아기레오 era-mix**가 난다. **먼저 측정**(미완, SSH IAP rate-limit로 중단): 11/21 후보 풀에 신선 both-pet이
몇 개인지, 빠진 게 diversity-sample 탓인지 쿨다운 탓인지. **수정 방향(택1)**: (a) **pool-build 레벨 both-pet floor**
(fresh-never-used floor `e6d9a2d`와 같은 자리에서 both-pet 쿼터 보장), 또는 (b) 프로포저 floor가 **DB를 직접 쿼리**해
신선 both-pet을 주입(풀빌드 독립). 진단 후 결정.

## 상태 / 미결

- **11/21 18:00 = 기존 `FIM_bOYNxT0`(2016 랴니솔로) 그대로 유지** — B로 재생산했으나 **Giri 미통과**(era-mix/주체소거)
  라 교체 안 함(PD 조건 "통과하면 교체", era-mix 쓰레기보다 유지가 나음). 위 근본 수정 후 재생산→프레임검증→
  `reupload_episode --card FIM_bOYNxT0`로 교체.
- VM에 11/21 selfheal grind 떠 있을 수 있음(--no-upload라 스케줄 미접촉, 5시도 후 자동종료). 다음 세션 시작 시 `pkill`.

## 운영 교훈 (메모리 영속)

- **VM SSH 255 반복 = IAP 터널 rate-limit** (매 `gcloud ssh` 새 터널 + 빠른재시도가 유발). 해결 = **ControlMaster
  멀티플렉싱**(`-o ControlMaster=auto -o ControlPath=~/.ssh/cm-rianileo-%C -o ControlPersist=600`)으로 단일터널
  재사용 + 빠른재시도 금지 + 거대 base64를 `--command`에 박지 말고 `scp`. 메모리 `vm_ssh_controlmaster`.
- **B 킬스위치:** `RF_BOTHPET_FLOOR`(12)·`RF_BOTHPET_ENFORCE`·`RF_BOTHPET_MAX_TRIES`(3)·`GIRI_BOTHPET_GATE`·`GIRI_CROSSCUT_EXEMPT`.
