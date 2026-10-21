# Session handoff — 2026-10-22 (인입률 재부진 대응 — 케이던스 4→2 감축 · 낙제점 107편 비공개)

**스파인:** PD "인입률 박살 났는데 아직도야 — [AI 조언 스크린샷] 이렇게 하래"에서 출발. AI 조언 =
①저품질 비공개 처리 → 채널 평균 올라가 노출 풀림 ②하루 1개 전성기 퀄로 테스트 노출 회복. **핵심
판단: 절반만 맞다.** "숨기면 노출 풀린다"는 **미신**(쇼츠는 영상별 독립 테스트 — 옛 영상 숨김이 새
영상 노출을 풀지 않음, 유튜브 공식 입장도 동일). 하지만 "fewer/better/distinct(하루 소수 전성기 퀄)"
부분은 **우리가 v2 롤백에서 피 흘려 배운 것 그대로** — 그래서 그 부분을 집행했다. 숨김은 노출-unlock
마법이 아니라 **채널페이지 전환·구독자 번아웃 방지의 housekeeping**(가치 실재하나 제한적). **진짜 성장
레버는 줄어든 2편/day의 패키징·훅이지 숨김·감축 자체가 아니다.**

## VM authoritative · push=deploy
- **VM HEAD `af27cab`** (로컬 push 2건: `94b38ee` 케이던스, `af27cab` 회고). deploy.timer 2분 폴로 자동 적용.
- 운영 진실 = VM DB / 라이브 YouTube (로컬 DB stale). 런타임 플래그 = `/etc/rianileo/deploy.env`.
- ★ **하드 훅이 `/etc/rianileo/deploy.env` 경로를 명령에 포함하면(읽기/쓰기 무관) 전부 차단**한다(크레덴셜
  보호). 그래서 env append가 막혔고 — **케이던스는 deploy.env override 대신 코드 기본값 변경으로 우회**했다.
  (deploy.env에 `LAUNCH_TIMESLOTS`는 안 걸려 있어 코드 기본값이 곧 유효값. 롤백 땐 LAUNCH_MODEL·
  EDIT_GRAMMAR_MODE만 append됨.) deploy.env를 꼭 건드려야 할 땐 PD가 직접 SSH로.

## SHIPPED (2건, 전부 배포)

### 1. 케이던스 4→2슬롯 감축 (`94b38ee`, 회고 C_v2rollback 연장)
- `agents/launch.py` `TIMESLOTS` 기본값 `08:00,12:30,18:00,21:00` → **`18:00,21:00`**.
  약슬롯 드롭(12:30 최약·08:00 아침 저조 by bandit), 저녁 강슬롯 2개 유지.
- rf_heavy 로테이션이 2슬롯 → **매일 RF 1 + AV 1** (AV가 18:00/21:00 번갈아, 균형). 검증=day_assignments
  10/22~10/25 출력 확인.
- Blast radius: `day_assignments`가 SSOT, `launch_selfheal`가 상속, `grammar_slot`은 EDIT_GRAMMAR off라
  무관. 하드코딩 슬롯 리터럴 없음. **내일 00:00 배치부터 적용.**
- 가역: env `LAUNCH_TIMESLOTS`에 4슬롯 복원(last-wins, 재배포 불요) 또는 git revert.

### 2. 낙제점 107편 비공개 (가역, 공개 555→448)
- `scripts/_hide_candidates.py`(신규 scratch, 임계 파라미터화): 라이브 업로드 전수 → lifetime views 분포
  → 임계값 미만 플래그 → `--apply`로 `youtube.upload.veto_video`(private 전환, 가역).
- **cutoff `<50v` (14일+ 지난 공개만)** = 채널 중앙값 204v의 1/4 미만. PD가 구간 분포 보고 선택.
  107편 전부 private 성공(실패 0, 공개 555→448·private 99→206 검증).
- 후보 정체 = v2 붕괴기(10/1~10/7) + 옛 판타지/추상 컨셉("꿈속 대모험"·"손오공 분신술"·"아이스크림 행성"
  ·"몸 바뀌기") → [[view_data_concrete_hook_title]]의 "시적·모호·판타지=패자" 데이터와 정확 일치.
- 재조정/복구: `python scripts/_hide_candidates.py` (dry) / `--hard-floor N --apply`. 복구는 영상 private→public.

## 교훈 (회고 C_v2rollback에 통합)
1. **AI 조언의 사후 메커니즘을 액면 그대로 믿지 마라** — "숨김→평균↑→노출 unlock"은 미신. 쇼츠 독립
   테스트라는 사실로 반증. 하지만 조언이 우연히 맞춘 지점(fewer/better/distinct)은 우리 데이터로 재확인 후 채택.
2. **같은 레버를 한 단계 더** — v2 롤백(6→4슬롯)의 연장으로 4→2 감축. 볼륨×반복이 채널을 끌어내리면 더 줄여라.
3. **housekeeping ≠ 성장** — 숨김·감축은 바닥 정리고, 반등은 줄어든 2편/day의 패키징·훅이 만든다.

## NEXT (PD 스팟체크)
- **다음 00:00 배치 = 첫 2슬롯 실전** (RF 18:00 + AV 21:00 or 로테이션). 스팟체크: 2편이 패키징·훅
  최우선으로 뽑혔는지 (구체 사건 + 클릭 제목, 시적·모호 금지).
- **~2주 추세** 관찰 (packaging_report / 48h 조회·리텐). 숨김·감축 후 per-video 중앙값이 회복되는지.
- 반등 안 하면 다음 레버는 **썸네일/첫 0.5초 훅** — 감축·숨김은 더 짜내지 말 것(이미 바닥 정리됨).
- 기존 성장 베팅(reach-first 패키징 핀·인트로 범퍼 A/B·브레이크아웃 아키타입 [[growth_program_2026-10]])이
  2편/day에 집중 작동하는지 확인.

[[production_model_v2_senior_director]] [[view_data_concrete_hook_title]] [[growth_program_2026-10]] [[slot_count_ab_2026-10]]
