# Session handoff — 2026-09-13 (grammar A/B 라이브화 + RF 쿨다운 런어웨이 근본 / 오후 후속: 라이브 배치 PD 리뷰 → 품질 3근본 + 교체)

**스파인:** 이 세션 = 하나의 인시던트에서 시작해(PD "셀프힐이 안 멈춰"·"컨셉이 없대"·"9-13 다 안 찼어") 두 근본을
파고, edit_grammar 3-arm을 **라이브 프로덕션에 배선**했다. 관통 교훈 셋:
1. **우회 경로는 자기가 우회한 경로의 숨은 계약을 전부 상속한다.** grammar 렌더가 `produce_and_render`를 건너뛰자
   그 경로가 조용히 이행하던 계약(카드 생성·CHECK·NOT NULL·하류 가드)을 전부 잃고 "성공했는데 예약 0"으로 샜다.
2. **완화의 임계는 '실제 소비되는 부분집합'에 걸어라 — 전체 크기가 아니라.** 전-기간 쿨다운이 fresh 창을 굶겼는데,
   아카이브가 수년치라 전체 풀 기준 완화는 절대 안 터졌다.
3. **비싼 전체 실행 전에 싼 하위 단계를 격리 검증하라.** 카드 INSERT(5초) 격리 테스트가 tone/agent CHECK 제약을
   17분 렌더 전에 잡았다.

## VM authoritative · push=deploy
- **VM HEAD**: `18639cc` (오후 후속 = e797b7f fix + 18639cc retro). `rianileo-deploy.timer`(2분 폴, **active**)가 pull→smoke→봇 재기동.
- 하루 스케줄(KST): 03:00 launch_selfheal / 09:10 slot_topup / 09:40 pd_reviewer. **cron 재시작됨**(인시던트 때 stop분).
- `LAUNCH_LEAD_DAYS=2` — 03:00 배치는 발화일 +2를 만든다(09-13 03:00 → 09-15).

## 인시던트 타임라인 & 두 근본
PD가 "$440 크레딧 자동충전 실패로 배치가 안 돈 것 같다"고 보고했으나 — **크레딧 무관**(로그가 진실). 근본 둘:

### 1. RF 쿨다운이 fresh 창을 굶김 = 런어웨이 근본 (C_cooldownrelax · 486254e)
- asset_id writeback(db71e3d, 지난 세션)이 전-기간 쿨다운(`RF_USED_CLIP_ALLTIME`)을 **비로소 제대로** 채우자,
  배제되는 게 하필 **최근-사용 = 가장 신선한** 클립. 09-13 진단: fresh(≤75일) 26개가 **전부 쿨다운·생존 0**.
- writer가 "신선 우선"인데 쓸 fresh 클립 0 → 홈/신선 컨셉 캐스팅 실패 → footage 부족 → 재제안, self-heal 라운드마다
  반복 → PD가 "안 멈춰"로 체감. 전체 풀은 커서(아카이브 수년치) 기존 <6 완화가 발동 0.
- **Fix**: `_propose_realfootage_singlepass` recency-aware 완화 — fresh 생존 < `RF_FRESH_RELAX_MIN`(8)이면 **쿨다운된
  fresh만 재-admit**(`RF_FRESH_RELAX_DAYS`75; 옛 footage 전-기간 배제·이웃/최근7일-공개 하드플로어 유지). 라이브 로그
  검증: `신선 클립 부족(0<8) — 자동완화: 최근 75일 사용클립 20개 재-admit`.
- self-heal wall-clock 하드캡 `SELFHEAL_MAX_SECONDS`/`RF_SLOT_MAX_SECONDS`(b378348).

### 2. Anthropic streaming (이번 세션 초, 4608872 — 지난 핸드오프서 오귀속)
- SDK 0.116.0이 고-max_tokens non-streaming create() 거부("Streaming is required") → 3 사이트를 messages.stream()로.
  작동 폴백이 죽은 주력(RF 캐시·AV opus)을 가렸던 근본. 회고 D_streamguard.

## edit_grammar A/B 라이브화 (D_grammarlive)
**설계 확정(PD "컨셉이 달라서 같은 영상으로 변인해도 돼")**: 3 grammar = **footage 통제 · edit 변인**.
- `agents/grammar_slot.py:produce_grammar_episodes_shared` — 한 번 캐스팅(story, 5역할 최대 커버)→velocity/meme/story를
  **같은 클립으로 병렬 렌더**. `agents/edit_grammar_writer.py:propose_grammar_copy(fixed_clips=)` = copy-only 모드.
- **오케스트레이션 함정**: launch_selfheal가 슬롯을 **슬롯별 개별 launch_pipeline(slot_filter)** 호출 → (a)필터된
  assignments면 모든 슬롯이 문법 index0, (b)호출당 캐시는 3번 재빌드. Fix = 무필터 `day_assignments`로 문법 해석 +
  **모듈-레벨 캐시**(`launch.py:_GRAMMAR_AB_CACHE`, target 키)로 프로세스 내 슬롯 호출들이 한 빌드 공유.
- **고아 4겹**(예약 단계서만·1렌더사이클 간격 순차 노출): ①카드 없음(`_auto_upload_episode`는 `output_video_path`로
  조회) ②`runs.agent` CHECK(→'cameraman') ③`cards.tone_primary` NOT NULL ④RF 16s 가드가 velocity **설계상 15.6s**를
  gutted-stub 오탐. Fix = `_persist_grammar_card` + grammar 전용 `RF_GRAMMAR_MIN_SECONDS`(12).
- 2 vCPU에서 3 병렬 CPU 렌더 thrash(loadavg 7 > 2-wide) → `GRAMMAR_RENDER_CONCURRENCY=2` 캡.
- **기본 ON**(dac41bc; 인시던트-트리아지 revert 4f7f670을 되돌림). 킬스위치 `EDIT_GRAMMAR_MODE=0` → 다음 배치 표준 RF.

## SHIPPED — 라이브
- **09-13 4/4**(같은 물놀이 footage A/B): 12:30 velocity `ACwb6d5zGMQ`(물줄기 직격)·18:00 meme `LUApXRa8Na8`·
  21:00 story `dHQ6knakFa0`(페이오프-선공개)·08:00 AV `qcMxc3R7Pss`. 제목 전부 그라운딩 구체-훅.
- **09-14 4/4**(PD "잘 됐어"): 08:00 AV `t5nyCA0XCKI`·12:30 `pRPnfqInHOM`·18:00 `KncdjyqFkzI`·21:00 `oaIBnqP-0cA`.
  09-12 03:00 배치가 cron 정지로 스킵된 갭 → full `launch_selfheal --date 2026-09-14` 수동 채움.
- 커밋: 486254e(cooldown relax)·f8689a3/481222d(shared A/B)·3220b55/08e3d98/aa10bb1(카드 4겹)·2bf2ce3(grammar
  최소길이)·dac41bc(기본 ON)·d399812(retro). + 세션 초 4608872/db71e3d/d559a4e/b378348/6fe509d.

## 결정 잠금 (재논의 불필요)
- **grammar A/B = daily 기본 ON** (laun치-월). RF 3슬롯 = velocity/meme/story, 같은 footage. 이후 밴딧(B5).
- **AV는 grammar 검증용으로 재실행 불필요**(PD) — 정규 daily 배치엔 1 AV 포함(3rf1av 유지).

## 세션 후속 (09-13 오후) — 라이브 grammar 배치 PD 리뷰 → 품질 3근본 + 교체 (C_grammarquality · e797b7f)
PD 리뷰: "새 RF 풀이 적다 + 앞 범퍼 없어짐 + 9/13 story 분수 처음·마지막에 랴니 없음". 셋 다 grammar 우회 경로가 뿌리.
**스파인 확장(위 교훈 1의 날 세움):** 예외를 던지는 계약은 트리아지에 걸리지만, **조용히 실패하는 계약(범퍼)은 로그로
안 보인다 — 우회 경로는 산출물끼리 diff(표준RF엔 범퍼·grammar엔 없음, 프레임 보면 즉시)로 계약을 열거하라.**

### 근본 3 (모두 `scripts/impact_edit.py`·`agents/grammar_slot.py`, 다음 배치부터 자동 적용)
1. **범퍼 실종 = D_grammarlive의 5번째(조용한) 고아.** grammar body는 `assemble`이 직접 만들고 `assemble_episode.py`
   (범퍼 붙이는 유일한 곳)를 우회 → 전 grammar 편 범퍼 無. Fix=`assemble`이 `_wrap_bumpers`로 intro+body+outro 감쌈
   (body 자체 오디오 보존, WxH/FPS/setsar+오디오트랙 보장, gotcha #8/#9). 스모크 1.5+body+2.5 검증.
2. **subject-blind 윈도우**(분수 랴니 실종). story가 컷 구간을 모션 에너지만으로 골라(`best_motion_window`) 분수는
   물튀김=모션최대=랴니 프레임밖. Fix=`salient_motion_window`(모션 × **공간 집중도**, block-pool 프레임차 상위1/8 블록
   비중)를 cold_open/payoff에 배선. 달리는 개는 둘 다 높아 무회귀·확산모션(물/비/바람)만 밀림(합성필드로 검증).
3. **A/B가 timeslot 교란**. grammar↔슬롯 정적매핑이 story를 거의 매일 21:00(최강)에 고정 → 문법효과·슬롯빨 분리불가.
   Fix=`edit_grammar_for_slot`에 날짜 로테이션(offset=ordinal%3) → 12일간 각 문법이 4슬롯 균등 순회(검증). 단일 소스라
   launch.py 매핑 전 경로 전파. **밴딧(B5) attribution 개선**.

### 라이브 교체 (조회수 손실 0)
- **meme** `LUApXRa8Na8` → **`FkFNT6HVG0I`**(기존 body 범퍼-wrap, 재캐스팅 없음). **story** `dHQ6knakFa0` →
  **`mG0WjiQ3v74`**(원본 4클립 강제-재렌더로 분수 컨셉 유지+salient로 랴니 확보+범퍼, 프레임 스트립 검증). 둘 다
  예약-비공개 상태라 발행 전 교체=손실0. **velocity `ACwb6d5zGMQ`는 유지**(이미 공개 796조회·범퍼-only 결함→PD 결정).
- 교체법=`_auto_upload_episode`(같은 카드 output_video_path 갱신→재업로드→이전 vid 자동 veto). 카드는 `date`+
  `youtube_video_id`로 찾음. veto는 eventually-consistent(publishAt 잔존→1회 재시도 내장).
- ★롤백(함정): **공개 편 재렌더는 producer 재실행이 아니다** — 재실행하면 원본 클립이 uploaded=1 쿨다운 배제→비결정
  재캐스트로 **다른 에피(레오-실외)** 생성. 원본 캐스트를 강제하라. 미래과제=grammar copy/역할 카드 미영속(clip
  asset_id만)→충실 재현 불가, 영속화 필요.
- gotcha=VM 수동 렌더는 `sudo systemd-run --uid=rianileo -p EnvironmentFile=/etc/rianileo/env -p PYTHONPATH=<repo>`
  (SSH 끊김 견딤·env 로드). LLM 캐스트 ~11분/편. `.env` 참조는 하네스가 차단(EnvironmentFile 경로만).

## ★ NEXT
- **09-13 오후 교체분 발행 스팟체크(오늘 밤)**: 18:00 meme `FkFNT6HVG0I`·21:00 story `mG0WjiQ3v74`가 **범퍼 달고**
  공개되는지 (story는 분수 hook에 랴니 + payoff 벤치 랴니 확인). velocity는 그대로 공개 중.
- **09-15 배치(오늘 03:00 생산) = 3근본 첫 자동 실전**: (a)RF 3편 전부 **범퍼 있는지**(assemble wrap), (b)story arm에
  물/확산 footage 걸릴 때 salient가 피사체 잡는지, (c)grammar↔슬롯 로테이션으로 story가 21:00 아닌 슬롯에도 가는지,
  (d)09-11 세션 항목: cron 정상발화·cooldown recency완화·고아 재발 없음.
- 정상 2일-선행 리듬 복귀 확인: 09-13→09-15, 09-14→09-16 …
- 미착수(선택): **grammar copy/역할 카드 영속화**(충실 재렌더 가능하게, C_grammarquality 미래과제)·B5 밴딧(edit_grammar
  arm→다음달 자동 선택)·틱톡 주말 구현(PD 숙제=dev앱 video.upload 키).

cf. 메모리 [[grammar_live_shared_ab_and_cooldown_relax]] · 회고 §4.4 C_cooldownrelax·C_grammarquality · §4.5 D_grammarlive · §4.1 표.
