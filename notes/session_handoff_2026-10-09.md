# Session handoff — 2026-10-09 (RAG 가속 · 조회수/인입 진단 · 슬롯 A/B · RF 과사용 근본 + 예약분 전량 교체)

**스파인:** PD의 세 신호(RAG 재태깅·"view수 안나와"·"이 영상 너무 많이 썼잖아")가 각각 다른 근본을 드러냈고,
관통 교훈 넷 — **(1) 대체/신규 경로는 옛 경로의 cross-batch 계약을 자동 상속하지 않는다**(캡션 그라운딩
우회와 동형으로 clip-dedup·pool-key·budget 설정이 전부 재발), **(2) per-video 최적화 ≠ 채널 최적화**(도달이
보존되면 슬롯 증설은 자기잠식), **(3) 제외(exclusion)는 다양성이 아니다**(선택 로직 자체가 다양해야 — 미사용
우선+회전), **(4) 다양성 ↔ 훅 품질은 trade-off**(과사용되던 클립은 *좋아서* 반복 선택된 것이라, 얇은 신선
인입에서 다양성을 강제하면 평균 훅이 떨어진다 — ★미해결 근본).

## VM authoritative · push=deploy
- **VM HEAD `99eb5f6`** (전부 배포·검증). 운영 진실=VM DB / 라이브 YouTube(로컬 DB stale).
- 배포=`git push origin main`(deploy.timer 2분 폴).

## SHIPPED (시간순 · 전부 배포)

### A. RAG 재빌드 가속 + 검증 (`7dd3e71`, `36c7b2f`)
- `asset_embed.embed_texts` 건당-1콜 → **배치**(~32/콜, 배치 실패 시 그 청크만 건당 폴백해 single-blip-abort
  내성 보존). 26k 인덱스 재빌드가 ~26k 순차요청(수시간) → 수백 콜.
- 전체 라이브러리 **앙상블 재태깅**(gpt-4o+mini union) 결과를 재빌드: `data/asset_embeddings.npz`
  **25,916벡×3072**. RAG 검색 품질 테스트 통과(랴니수영/레오창밖/둘이산책/눈밭/카페/사료 전부 정확·subject
  필터·솔로↔듀오 구분, score 0.75~0.91). 회고=§4.5 "전수 재태깅 아님" supersede(비용은 전수가 아니라
  건당-1콜 재빌드였다).

### B. 조회수/인입 진단 → 약슬롯 다운웨이트를 **측정 A/B로** (`d30e090`→`6607bce`, `7c8efe0`·`a6631d1`)
- PD "요즘 view수 안나와"·"인입(=채널 유저 유입) 안좋아". 진단(채널 ground truth): **유입 ~98%가 Shorts
  피드**, 일일 채널조회 ~3,200 **평탄**(지속 하락 아님), 10/01 하루 절벽(678)+**4→6슬롯(v2 9/30) 희석**이
  per-video 48h조회를 끌어내린 주범. "교체본 늦은 재업로드" 가설은 **대조로 반증**(예약=실제공개 초단위 일치).
- `bandit.laggard()`(=`stabilized()` 거울) + `launch_v2.day_plan`에 **`V2_SLOT_MODE=ab`**(기본): 2일 블록
  교대 arm0=6슬롯/arm1=5슬롯(약슬롯 09:00 드롭), produce/carry 주기와 **위상 정렬**(교란 제거). SSOT
  `effective_assignments`에서 드롭→self-heal/topup phantom-fill 없음. `=downweight` 항상드롭/`=off` 6슬롯.
- PD 지적("슬롯↓=브레이크아웃 복권↓")으로 **블라인드 컷 대신 A/B**. `scripts/slot_ab_report`가 arm별
  **채널 일일 총 도달** 비교(per-video 아님)+발행수로 arm 오염 노출. 교훈=라이브 전략분기는 측정장치로 출하.

### C. RF 과사용·제목불일치 근본 (`690a0fe`·`4a1dd7c`·`d41f591` + 부수 `15f10d9`·`08d2351`·`8d4c534`·`47edb13`·`99eb5f6`)
PD "10/10 13:00 너무 많이 썼잖아·랴니만인데 제목에 레오·밤밭도 아니고". 예약 RF가 9/30~10/1 소수 클립을
×15~17회 재탕+중복 컨셉 3군(거실 3편 동일 등)+오라벨. **풀은 건강**(미사용 1,558)→품귀 아닌 **선택편향**.
근본(지배 순):
1. **`senior_director._candidate_clips` 키 버그**: `ctx.get("best_videos")`를 읽는데 실제 키는
   `available_videos` → 캐스팅 풀이 **빈 리스트** → 최신순 A/B feeder(grandma-notes 12+behaviors 15)로만
   캐스팅 → 매 배치 같은 9/30~10/1 클립. (`4a1dd7c`)
2. **선택 다양성 결여**(PD "제외 안해도 다양하게"): `_gather_context`의 best_videos를 **미사용 우선 +
   신선창(RF_DIVERSE_RECENT_DAYS=150) + 날짜시드 회전**으로 티어링(fresh-unused→other-fresh→old). 제외 없이도
   다양. ★첫 컷은 전-era 미사용-우선이라 2015 클립이 선두로 와 2026-09 year-flatten 기아 재발→신선창으로 교정.
   (`4a1dd7c`→`d41f591`)
3. **cross-batch 쿨다운**: `grammar_slot._fresh_pool`이 `uploaded=1`만 제외→예약 대기 클립 재선택. 예약된
   카드(video_id)+최근 21일(GRAMMAR_CLIP_COOLDOWN_DAYS)도 제외. (`690a0fe`)
- 부수 버그(교체 작업 중 발굴): `burn_captions` sys.path 미커밋(`15f10d9`)·pd_reviewer rerender가
  **veto-후-예약**이라 충돌 시 빈 슬롯(→**veto-먼저** `08d2351`)·렌더 스텝 타임아웃 하드600s(→env
  `RENDER_STEP_TIMEOUT` `8d4c534`)·era_reuse(DB 팩트)가 LLM 2-pass에 막힘(→결정론 클래스 우회 `47edb13`)·
  **gemini-2.5-pro가 thinking_budget=0 거부**(flash 설정 잔재)→조밀 action-caption+그라운딩 게이트 전멸+
  except의 미정의 `mid` NameError(→budget 모델별 0/-1, mid→ta `99eb5f6`). pd_reviewer `_SLOT_UTC`에 v2
  슬롯 추가+`--lanes` 필터(AV 제외) `690a0fe`.

### D. 10/9~10/10 예약 RF 8편 전량 교체 (PD 승인: "공개하되 근본 수정 필요")
- AV 3편 유지, **RF 8편 veto + 결정론 드라이버**(`/tmp/rf_replace.py`: veto→신선 컨셉 propose→표준 RF
  렌더→슬롯 예약, SLOT_COLLISION_GUARD=0)로 신선 footage 재생성. 새 video_id, 제목 내용일치(밤밭 제거):

  | 슬롯 | old → new | 훅 |
  |---|---|---|
  | 10/9 09:00 | iKTOrS25-ds → VGWUn3IHh0k | 3 |
  | 10/9 13:00 | UGYRCxIrA3Q → Lz5CCaE7nvI | 3 |
  | 10/9 18:00 | JksYJyhpJuM → yrzcFPsEi_Q | 5 |
  | 10/9 21:00 | u_f5lf5VGNg → tlV2Ei9-kf4 | 6 |
  | 10/10 08:00 | aShP801WDX4 → P86lHMy006Q | 6 (수정필요) |
  | 10/10 13:00 | FnjB3PfzSNE(밤밭) → TAe9vK_CK3Q | **9** ✓ |
  | 10/10 18:00 | 2qveqZxSC24 → Sp2SlVEvQac | 8 (수정필요) |
  | 10/10 21:00 | H4110KQoIX0 → tRyVRnSQbs0 | 5 (수정필요) |

## ★ 미해결 근본 — NEXT (이번 교체가 드러낸 진짜 숙제)
**다양성 ↔ 훅 품질 trade-off.** 과사용되던 클립들은 senior_director가 *가장 vivid해서* 반복 선택한 것.
reuse-aware 선택은 **미사용**을 우선하지만 미사용 ≠ 고-훅 — 남은 신선 클립이 차분(낮잠/앉기)이라 교체본
훅이 3~8로 들쭉날쭉. 얇은 신선 인입(인입 정체, 함미하비 Slack만 2~8/일)이 근인.
- **수정 방향 ①(엔진)**: 다양성 점수를 **품질-가중**으로 — 미사용 *그리고* 고-모션/고-훅(clip_motion_peak,
  framing, both-pets) 클립을 우선. 순수 reuse-asc가 아니라 `reuse_penalty × quality_bonus`. `_gather_context`
  티어링(현재 fresh-unused→fresh→old)에 품질 2차정렬 추가.
- **수정 방향 ②(공급)**: 좋은 신선 footage 인입 증대 — iCloud "Ryani&Leo" 앨범 재급식(현재 slack만, 얇음).
  인입이 늘면 다양성·훅 둘 다 산다.
- **수정 방향 ③(드라이버)**: `/tmp/rf_replace.py`는 **Giri 게이트 없이 force-schedule**(수정필요도 예약).
  재사용하려면 Giri-게이트+리롤(컨셉 N개 시도→통과분 채택) 추가. (이번엔 PD가 "이대로 공개" 승인이라 미적용.)

## 기타 NEXT / 관찰
- **슬롯 A/B 판정 ~2026-10-21**: `.venv/bin/python -m scripts.slot_ab_report --days 16` → arm별 채널 총도달
  비교(5slot>5%=집중 `V2_SLOT_MODE=downweight` / 6slot>5%·TIE=볼륨유지 `=off`). 메모리 `slot_count_ab_2026-10`.
- **다음 00:00 배치 첫 실전**: 근본 수정(키버그·다양성·쿨다운) 적용된 첫 produce — RF가 과사용 안 하고
  다양·신선 캐스팅하는지 스팟체크(훅 품질은 위 미해결 근본이라 여전히 변동 가능).
- gotcha: VM SSH가 로컬 좀비 IAP 터널로 255 반복→`pkill -f start-iap-tunnel` 후 재시도. `git rev-parse`를
  `--command="..."` 큰따옴표 안에 넣으면 로컬 전개됨(HEAD 빔). 다중 따옴표 veto는 스크립트 파일+scp로.
