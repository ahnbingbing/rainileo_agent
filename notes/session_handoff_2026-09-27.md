# Session handoff — 2026-09-27 (v2 6슬롯 배치: 죽은 플래그를 실배선하고 라이브 플립·첫 실전 5/6)

**스파인:** PD의 두 신호 — "새벽 배치가 왜 2개만 만들었나" · "준비해둔 하루 6개 배치로 바꾸자" —
를 파보니, ①4슬롯 파이프라인의 **구조적 취약**(배치시점 렌더 + 90분 벽시계 캡 직렬 self-heal →
슬롯 2개가 재작업 필요하면 라운드2 예산이 없어 빵꾸)이 "2/4"의 근본이었고, ②"준비된 v2"는 실은
**dry-run 스캐폴딩뿐이고 `LAUNCH_MODEL=v2`를 프로덕션이 안 읽는 죽은 플래그**였다(9/21 "구현 완료"는
초록불 dry-run이 완전 미배선을 가린 것). 관통 교훈: **플래그는 프로덕션 진입점이 그걸 읽고
렌더/예약 프리미티브까지 배선돼야 "플립 준비 완료"다 — dry-run 통과가 아니라 진입점에서 플래그를
grep해 확인하라.** 그리고 **어휘 기반 premise 게이트(역할스왑)는 비용절감 pre-filter지 authority가
아니다 — 진짜 백스톱은 Giri 의미 캡 + 리롤.**

## VM authoritative · push=deploy
- **VM HEAD `e7c5e18`** (전부 배포·검증). `LAUNCH_MODEL=v2` **라이브**(`/etc/rianileo/deploy.env`,
  cron 자식 `enabled=True` 검증). 롤백 = deploy.env에서 그 줄 제거 → 다음 배치부터 4슬롯.
- 하루 스케줄(KST) 그대로: 03:00 launch_selfheal / 09:10 slot_topup / 09:40 pd_reviewer(APPLY=1)
  / */5 process_board_escalations / */30 ytcache. **launch_selfheal가 flag ON+무필터 시 run_v2_batch로 위임.**

## v2 모델 요약 (6슬롯 · 2일 사이클)
- 슬롯(KST): **08:00 / 09:00 / 13:00 / 18:00 / 20:00 / 21:00** (AV=20:00 저녁피크, 이월일은 08:00도 AV).
- **생산일**: senior_director → 3소스(A 함미하비노트·B VLM관찰·C arc, footage-다양성 <60% 게이트) →
  각 소스 3 grammar(velocity/meme/story) = **9 RF 렌더** → 5편 당일 예약 + **4편 익일 carry 핀** + 20:00 AV 1편(비-timely).
- **이월일**: 핀된 4 RF 예약(launch `_pinned_episode_for`) + 08:00·20:00 timely AV 2편.
- 결과 볼륨: 하루 6편(생산일 5RF+1AV · 이월일 4RF+2AV).

## SHIPPED (전부 배포·검증)

### 1. v2 프로덕션 배선 (`02eadd5`, flag off로 최초 머지)
- `agents/launch_v2.run_v2_batch` — 오케스트레이터(생산/이월 분기, 기존 `produce_grammar_episodes_shared`·
  `_auto_upload_episode`·`_pin_grammar_card` 재사용).
- `agents/launch.effective_assignments(target)` — **하루 슬롯의 단일 진실원**(v2 6슬롯 day_plan / else 4슬롯).
  launch_pipeline·launch_selfheal·slot_topup.find_gaps·pin_episode·launch.main 전부 경유.
- `launch_pipeline`에 `assignments_override`(v2가 임의 (lane,slot) 구동). `slot_occupancy`+producer
  `SLOT_COLLISION_GUARD` **6슬롯 스냅**(안 하면 v2영상 4슬롯키 오스냅→충돌오탐/유령갭).
- `grammar_slot.edit_grammar_for_slot`는 v2서 None(롤링윈도우 → senior-director grammar 대체).
- `produce_grammar_episodes_shared`에 소스별 `tag`(3소스 파일명 충돌 방지). 회귀 `_launch_v2_regress` 28건.
- **플래그 OFF = 라이브 4슬롯 바이트-동일**(검증됨).

### 2. 멱등 생산일 가드 (`6adb18d`)
cron이 수동 실행한 날짜를 재타겟해도 헛렌더/중복핀 안 하게: fresh RF 슬롯이 다 찼고 익일이 이미 핀되면
RF 렌더/핀 스킵(AV는 계속 처리 → 빈 AV는 채움). 핀도 이미-핀된 carry 슬롯은 건너뜀(`_pinned_episode_for`).

### 3. AV 리롤 + 역할스왑 게이트 (`75eac28`, 9/30 첫 실전 giri_fail 근본)
- **`V2_AV_REROLL`(기본 1)**: run_v2_batch AV가 Giri 실패 시 새 컨셉 1회 재롤(4슬롯 SELFHEAL_REROLL 미러).
  나쁜 컨셉 하나가 배치 내내 슬롯 비우던 문제 해결. 한 번 추가 Seedance(유계).
- **`_AV_ROLESWAP_RX` 보강**: "리듬"(=루틴 동의어)·수동 "바뀐/뒤바뀐"·"서로 바뀐 <물건>"이 게이트를 우회해
  금지 프리미스가 렌더까지 샜다(Giri만 렌더비 후 잡음). +리듬/생활패턴/페이스/일과 + 수동형 동사 추가.
  ★코드/회고에 명시: **어휘 게이트=비용절감 pre-filter, authority 아님**; 백스톱=Giri 의미캡+리롤.

회고: §4.5 **D_v2wiring**(+9/30 첫 실전 후속). change-impact/merge-retrospective 완료.

## 라이브 스케줄 현황 (2026-09-27 20:46 KST 기준, ground truth)
- **9/29** (플립 前 4슬롯 하이브리드): 08:00 `KXjWBYkPK5g` · 13:00 `NkmdZsjL8hs` · 18:00 `KFMAGoSF1Ks`.
  **21:00 RF 빔**(RF salvage-orphan 버그로 self-heal 실패, ~50분 그라인딩 후 정지 → slot_topup 위임).
  v2 6슬롯 관점의 09:00/20:00은 원래 없던 슬롯.
- **9/30** (v2 첫 실전, 수동 `LAUNCH_MODEL=v2` 주입 실행): **5 RF 예약** 08:00 `i7PKxSlSX4s`·09:00
  `JDVUKctY_zE`·13:00 `__G1J6x3zQ4`·18:00 `Vt7u7m5YEKU`·21:00 `lgge2rntbcU`. **20:00 AV 빔**(Giri 5/10 —
  역할스왑+무스토리+캡션미스매치; 이제 리롤/게이트로 보강됨). **10/1용 4 RF 이월 핀**(09/13/18/21).
- **10/1**: 라이브엔 빔(핀은 렌더된 카드일 뿐 아직 미예약 — 이월일 배치가 예약).

## cron 롤아웃 스케줄 (LEAD_DAYS=2)
- **9/28 03:00 → 타겟 9/30**(생산일, 대부분 채워짐): 멱등가드로 RF 스킵 + **20:00 AV 재시도**(리롤+게이트 첫 라이브).
- **9/29 03:00 → 타겟 10/1**(이월일): **핀된 4 RF 예약 + 2 AV** → v2 **이월일 흐름 첫 실전**.
- **9/30 03:00 → 타겟 10/2**(생산일): cron發 **full v2 생산 첫 실전**(9 RF + AV).

## ★ NEXT (스팟체크 순서)
1. **오늘 밤(9/28 03:00) 9/30 20:00 AV 재시도** — 리롤+역할스왑 게이트의 첫 라이브. 채워지는지, 리롤이
   과-Seedance 안 쓰는지, 게이트가 과반려 안 하는지.
2. **9/29 03:00 = 이월일 첫 실전** — 핀 4편이 제대로 예약되나(`_pinned_episode_for` 매칭), AV 2편.
3. **9/30 03:00 = full v2 생산 첫 실전** — 9 RF 다양성/품질, 예약5+이월4, 비용(AV), 빈슬롯.
4. 전반 감시: footage 다양성 게이트, AV giri_fail 빈도, 비용($/day), coherence.

## 미배선 후속 (플립 무관, 문서화)
- **day1_winners 판타지 재해석**: LEAD_DAYS=2라 이월일 배치 시점에 생산일이 미발행(48h 데이터 없음) →
  신호만 로깅, carry AV는 일반 timely로 렌더. 리드타임 모델 확정 후 배선.
- **bandit v2 timeslot arm**: 아직 4슬롯 버킷(측정 B5 연기). v2 측정 신뢰 전 6슬롯 배선 필요.
- **PD mp4-in-thread 리뷰**: run_v2_batch는 텍스트 써머리+video_id만(4슬롯의 스레드 mp4 업로드 미이식).
  현재 리뷰는 Slack 써머리 + `/veto <video_id>`.
- **RF salvage-orphan 버그**: 캡션-salvage/too-short 재렌더가 카드 output_video_path를 갱신 못해
  `[ORPHAN-SKIP] no-card-for-output` → 예약실패→재롤 루프(9/29 21:00을 못 채운 실제 근본, v2 grammar RF는
  `_persist_grammar_card`로 카드 있어 덜 노출). 별건 근본수정 권장.
- **9/29 전환일**: 플립 전 4슬롯 콘텐츠 + v2 slot_topup이 6슬롯 갭(09/20/21)을 채우려 할 수 있음(하이브리드).

## Gotchas (이번 세션)
- **`.env` 읽기 차단**: auto-mode 분류기가 `/etc/rianileo/deploy.env`·`env` **읽기**를 크레덴셜 보호로 차단.
  → 값 grep/cat 금지, append는 내용 무출력 `tee -a`(>/dev/null)로, 검증은 python 불리언(`enabled()`)으로만.
- **Monitor 55분 타임아웃**: 긴 렌더(9 RF ~90분·AV ~40분)는 한 Monitor로 못 덮음 → 재장착 or 저널 직접 tail.
- **VM SSH = ahnbingbing 로그인**: 레포/로그/cron은 `rianileo` 소유 → `sudo -u rianileo` + 절대경로 필요.
  로그: `/home/rianileo/rianileo-agent/data/logs/` + `journalctl -u <unit>`.
- **긴 렌더는 systemd-run transient**(`--unit=... --uid=rianileo`)로 띄워 SSH 드롭 견디게.
- 수동 v2 실행 = `sudo -u rianileo env LAUNCH_MODEL=v2 bash $R/deploy/run_job.sh -m agents.launch_selfheal --date <YYYY-MM-DD>`.

메모리: [[production_model_v2_senior_director]](라이브 플립됨) · [[streaming_guard_masked_dead_primary]](죽은주력 자매교훈).
