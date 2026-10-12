# Session handoff — 2026-10-12 (RF 선택 품질-가중 · v2 전면 롤백[조회수 붕괴] · 성장 3베팅 · CTR 계기판)

**스파인:** PD "10/1 이후 너무 떨어졌어 — 퀄리티·자막·동일소스 여러개"에서 출발해 네 겹을 벗겼다 —
관통 교훈 넷: **(1) "A라서 그랬다"는 사후 서사를 코드로 검증하라** (과사용은 senior director가 vivid라
판정한 게 아니라 feeder 키버그+recency가 newest를 떠먹인 것 — PD 확인). **(2) 집계가 붕괴를 가린다**
(10/7 "채널 총도달 ~3,200 평탄=슬롯 희석일 뿐"이 per-video 중앙값 994→8 붕괴를 가림 — PD 직감이 이김).
**(3) 볼륨×반복은 품질과 바꾼 것이고 채널을 죽인다** (v2 소스3×문법3=9RF·6슬롯 → 자해). **(4) 추측 말고
API/DB에 실제 물어라** (impressions/CTR은 "Unknown identifier"=조회불가·함미하비 Slack 공급은 풍부).

## VM authoritative · push=deploy
- **VM HEAD `6f3b3de`** (전부 배포·검증). 운영 진실=VM DB / 라이브 YouTube(로컬 DB stale).
- 배포=`git push origin main`(deploy.timer 2분 폴). 런타임 플래그=`/etc/rianileo/deploy.env`(run_job.sh가 매 실행 source, 재배포 불요).

## SHIPPED (시간순 · 전부 배포)

### A. RF 선택 품질-가중 다양성 (`a51b979`, 회고 C_hookdiversity)
- 과사용의 지배원인은 `_candidate_clips` 키버그+recency 결정론(vivid 판정 아님). 과사용 수정도 tier A를
  **랜덤 셔플**이라 여전히 hook-blind → "다양성↔훅 trade-off"는 법칙 아니라 selector가 품질-blind였던 artifact.
- `producer._gather_context` reuse 재정렬의 tier 내부를 **hook-score 정렬+날짜 jitter**. ★1차 모션주도 실패를
  10일 라이브-DB 선택 시뮬이 잡음(수영 4개 쏠림)→**사건주도 재보정**(pd_notes 구체성+micro_behavior 주신호,
  모션=tiebreak). 시뮬: 0겹침·구체노트 14/60→60/60. 킬 `RF_HOOK_WEIGHTED=0`.
- 공급 재검증(PD "함미하비가 슬랙으로 많이 줘"): 최근 70일 Slack 263 usable 222 pd_notes 139 >> icloud 101/0 →
  얇은 공급은 틀린 전제. 사건주도 점수가 집는 게 바로 그 Slack 클립. (메모리 [[diversity_vs_hook_quality]])

### B. ★v2 프로덕션 모델 전면 롤백 (조회수 붕괴, 회고 C_v2rollback)
- 데이터: RF 48h **중앙값 994(pre-v2 9/18~29)→68→50→8**, 리텐 71%→21% — 붕괴가 v2 라이브(9/30)와 정확히 일치.
  근본 ①볼륨 희석(4→6슬롯) ②소스3×문법3=같은 footage 3연발(중복편 사망 357/9/12) ③편집 문법<순수 RF
  (std 445 > meme 245 ≈ velocity 243 > story **57**). 저engagement 홍수가 채널 전체를 알고리즘적으로 끌어내림.
- 롤백=`deploy.env` append override **`LAUNCH_MODEL=`**(빈값→4슬롯 pre-v2 craft)+**`EDIT_GRAMMAR_MODE=0`**.
  검증=effective_assignments(4슬롯 RF08/12:30/18+AV21)·유효플래그 off. 신규 품질픽스(A·그라운딩)는 유지.
- 예약 큐 문법편 6개(story2/velocity2/meme2, 같은소스 2쌍) `_do_veto`(youtube private+uploaded=0)로 veto,
  10/14 craft RF 재생산(ZTNVYU66FI8 등). (메모리 [[production_model_v2_senior_director]] = ROLLED BACK 표기)

### C. 성장 3베팅 (PD "성장해야만 해", 측정 기반·전부 가역, 메모리 [[growth_program_2026-10]])
- 진단(video_performance 540편): 하드 천장 ~1000–1170 브레이크아웃0·리텐>100%(리플레이)가 트리거·AV 리텐70%>
  RF58%인데 도달 낮음(패키징 실패)·패키징 도달 2배(None169→hook_search354).
- **베팅1 도달우선 패키징**(`cc9b9fe`): packaging A/B가 합성보상(0.6v+0.4r)때문에 안정화 안 됨→라운드로빈 멈춤.
  `channel_manager.stabilized_arm` reach-leader 분기+**`PACKAGING_OBJECTIVE=reach`**(기본)→hook_search 핀(VM검증). `=explore` 롤백.
- **베팅2 인트로범퍼 A/B**(`cc9b9fe`): 1.5s 브랜드 인트로가 Shorts 훅 앞=스크롤이탈/루프파괴. `cameraman._bumper_arm(card_id)`
  결정론 ~50/50 outro_only vs control, 4 assemble 지점 통일, `video_performance.bumper_arm` 기록. 킬 `BUMPER_AB=0`.
- **베팅3 아키타입+트렌드**(`9d17b2f`): 브레이크아웃 포맷 6종(첫경험/데뷔303%·기대반전·대상반응234%·나이역전·
  분리기다림·서스펜스)을 라이브 프롬프트(RF realfootage_concept.md+AV writer_story.md)에 prompt-authoring 통합
  (=반복 시리즈 shape, footage-first). 트렌드 라이딩은 이미 라이브(active_trends 10개·`_rf_trends_hint` 주입).

### D. CTR 계기판 보강 (`2a8c9d5`+`6f3b3de`)
- ★impressions/impressionClickThroughRate = Analytics API **"Unknown identifier"=조회불가**(Studio 전용, 실측).
  문자 그대로 CTR 계기판 불가 → 가용 최선 프록시: `video_metrics`가 likes/comments/shares/subscribersGained/
  averageViewDuration 추가수집(backward-compat)·`video_performance` 5컬럼(멱등 마이그)·`scripts/packaging_report.py`
  arm별(packaging+bumper) reach·ret%·dur·eng/1k·subs/1k 대시보드. 주간 밴딧 리포트(월10:00 Slack)에 결합(`6f3b3de`).
- 보상식 불변(측정용). readout: hook_search 도달1위 342. engagement는 collect 전진하며 채워짐(old행 NULL).

## 라이브 상태 (지금 돌고 있는 것)
- 프로덕션=**pre-v2 4슬롯 craft**(RF 08/12:30/18 + AV 21 KST), 영상당 distinct 소스, 문법 off.
- 선택=사건주도 hook-score(RF_HOOK_WEIGHTED=1)·패키징=hook_search 핀(PACKAGING_OBJECTIVE=reach)·범퍼 A/B(BUMPER_AB=1).
- 03:00 launch_selfheal·09:10 slot_topup·09:40 pd_reviewer·06:30 bandit collect(engagement 포함)·월10:00 리포트(+대시보드).

## NEXT / 관찰
1. **다음 03:00 배치 첫 실전 스팟체크** — pre-v2 craft 4슬롯·사건주도 선택·hook_search 패키징·범퍼 A/B가 신규편에 붙는지.
2. **~2주 측정 판정** — `scripts.packaging_report`로 ①bumper_arm별 리텐/도달(outro_only 승→인트로 영구 제거)
   ②reach-first 도달 개선 ③아키타입 적용편이 ~1000 천장 뚫는지.
3. 롤백 레버 전부 살아있음: LAUNCH_MODEL='' · EDIT_GRAMMAR_MODE=0 · RF_HOOK_WEIGHTED · PACKAGING_OBJECTIVE · BUMPER_AB.

[[diversity_vs_hook_quality]] [[production_model_v2_senior_director]] [[growth_program_2026-10]] [[view_data_concrete_hook_title]] [[slot_count_ab_2026-10]]
