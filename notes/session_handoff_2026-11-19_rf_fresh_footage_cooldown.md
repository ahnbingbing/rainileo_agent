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
