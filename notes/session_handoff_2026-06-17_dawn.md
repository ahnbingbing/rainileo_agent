# 세션 핸드오프 — 2026-06-16 → 06-17 새벽 (마라톤 세션)

> 브랜치: `approach-d-grounded-singlepass` · 모든 작업 커밋됨(d67c980 → 2058c6b).
> 이 세션의 한 줄 요약: **AV prefetch 버그 고침 + 6/17 4슬롯 완성. 그러나 RF에 게이트/
> brainstorm을 과하게 추가하다 오히려 RF를 망쳤고, PD 지시로 위험한 변경을 되돌림.**

---

## 1. 지금 상태 (운영)

### 6/17 = 4/4 슬롯 완전 예약 (YouTube 예약-공개)
| 슬롯(KST) | 레인 | video_id | 내용 |
|---|---|---|---|
| 08:00 | AV | `T1VDqYViMHQ` | (기존) |
| 12:30 | RF | `4u4EZHbSlB4` | **레오의 첫 카페 나들이** (손수 제작) |
| 18:00 | AV | `P9huQKtlMrs` | (기존) |
| 21:00 | RF | `Fr7jqwMkGXA` | **레오의 셀프 관리 타임 / 그루밍 롱테이크** (손수 제작) |

veto: 12:30에 끼어있던 자동생성 junk `lIGnh81-eK4`("각자의 방식…")는 private 처리(슬롯에서 제거).

### launchd
- `com.rianileo.launch` = **03:00 KST**(PD가 01:00→03:00 변경), `agents.launch_selfheal` 실행.
- **이 배치는 6/18을 만든다** (내일치). 6/17은 자동으로 안 채워지므로 위처럼 수동으로 채움.

### 워킹트리 미커밋 변경 (PD: "의도된거야" — 건드리지 말 것, 3시 배치에 그대로 적용됨)
`arc.py, canon.py, caption_salvage.py, retry_loop.py, reviewer_macro.py, writer_director.py,
caption_agent.md, character_sheets.md, director_shots.md, producer_propose.md`

---

## 2. 손수 제작한 6/17 RF 2편 (PD: "널 믿고")

PD가 실제 footage를 직접 지정 → 내가 영상-first·재미있는 캡션으로 제작.

- **카페** (`rf0617cafe000000`): 2026-06-13 같은-날 카페 클립 3개(125601 탐방 / 143905 레오+랴니 /
  133354 낮잠). 탐방 컷은 유리문 너머 **배경 사람 얼굴** 때문에 하드룰로 드롭 → 나란히+낮잠 2컷.
- **그루밍 롱테이크** (`rf0617groom00000`): `med_2026_06_11_123101` **@44–66s** pre-trim(클로즈업
  그루밍, PD가 보여준 그 장면), 빡빡한 재미 캡션 7줄.
- 빌드: `scripts/build_0617_rf_cards.py`(카드+자산 등록) → `cameraman --no-brain` 렌더 →
  **내 캡션을 직접 re-burn**(burn_captions+assemble) → `scripts/schedule_0617_rf.py`(업로드+예약).
- ⚠️ **왜 re-burn?** VLM Step-4b 캡션 리라이트(`_vlm_post_render_caption_rewrite`)가 `author==
  'realfootage_singlepass'`일 때만 스킵됨 → 손수 카드의 캡션을 계속 덮어씀. uncaptioned 트림 컷에
  내 캡션을 다시 burn해서 우회. (개선거리: 이 리라이트를 끄는 깨끗한 env 플래그.)

---

## 3. 오늘 밤 커밋 — 무엇을 유지/되돌렸나

### ✅ 유지 (검증된 개선)
| commit | 내용 |
|---|---|
| `d67c980` | **AV prefetch 일괄 다운로드** — 사진당 osxphotos 재호출(라이브러리 전체 스캔)을 `--uuid-from-file` 1회로. 6/17 AV 0/7→정상의 직접 원인 해결. |
| `d967663` `8278e1d` | 쿨다운이 archive/photo 풀 + 'approved' 상태 카드까지 커버(allowlist→denylist). |
| `70369fb` `396ffef` `ec43796` | **시점 게이트 A–D**: 현재나이 프레이밍 / 다년 무앵커 / 아기클립 미라벨 / 가짜 단일사건. |
| `f9763cb` | 그라운딩 게이트가 캡션 **장소** 모순(실내 vs 야외)도 잡음(frame 기반). |
| `67072a2` | **gutted-guard**: 소스누락으로 절반 미만 컷만 남으면 슬롯 실패(junk 금지). |
| `3db8ba3` | **주인공-우세 게이트**: 펫이 프레임에 없는 사람/풍경 컷 드롭(실 VLM로 검증). |

### ↩️ 되돌림 (PD 판단 — 이것들이 RF를 "산으로" 보냈음)
| commit | 무엇/왜 |
|---|---|
| `2a90f12` | **concept-brainstorm OFF** (RF). 억지 드라마 컨셉('매복의 달인','침묵의 엄마') 유발. writer-direct 복귀. |
| `794aebe` | **사진 길게 절대 금지**. photo-majority 풀비트 실험 되돌림(7–14s 정적 사진 오프닝 = retention 사망). |
| `71b3d09` | **세션-쿨다운 OFF**. 클립 1개 쓰면 그날 footage 전체를 잠가서 코헤런트 같은-날 묶음을 못 찾게 함("이걸 왜 못찾아"=카페날 통째 cooled). 정확한 asset_id 쿨다운으로 복귀. |
| (regression) `bd74bcc`→`43175b9` 사진 로직은 위 `794aebe`로 최종 정리됨. |

**메타 교훈**: 추가형 게이트가 RF를 **net-악화**시킬 수 있다. 새 변경은 "실제로 도움이 되는지"
검증한 뒤에만 유지. (오늘 쿨다운 4종·brainstorm·사진로직을 쌓다가 writer에게서 좋은 footage를
빼앗아 잡탕을 강요함.)

---

## 2b. 캡션 정정 (PD 검수) — "자는 내용" 오류 + 왜 놓쳤나
PD: 두 RF가 좋은데 **자는 내용이 틀렸다**. 카페 마지막 컷은 자는 게 아니라 졸음 캡션 뒤에
**다시 카메라를 똘망똘망 쳐다보며 깸**(자다 바로 깸); 그룸은 잠 한 톨 없이 **끝까지 꼼꼼히
닦음** ("끝까지 꼼꼼히, 우리 레오!"). 캡션만 고쳐 기존 veto·재업로드함:
- 카페: 구 `moN_ymTh04I`→**veto**, 재업로드 **`4u4EZHbSlB4`** (cut3: "잠깐 쉬어볼까 했지만 / 어?
  다시 똘망똘망, 구경 재개!").
- 그룸: 구 `g0s3RmHWCRI`→**veto**, 재업로드 **`Fr7jqwMkGXA`** (끝 3줄: "발끝까지 야무지게 / 한 톨도
  안 봐줘요 / 끝까지 꼼꼼히, 우리 레오!"). 둘 다 끝-프레임으로 일치 검증함.

**왜 놓쳤나 (근본)**: 캡션을 **정지 프레임 한 장의 포즈**(눈 감은 듯 졸려 보이는 컷)만 보고
"잠"으로 단정함 — 클립의 **실제 동작 ARC**(특히 *끝* 부분: 깸 / 계속 닦음)를 확인하지 않음.
→ **교훈: closer/여운 캡션은 클립이 *어떻게 끝나는지*가 결정한다. 캡션은 start→mid→END
프레임(또는 영상 전체)의 실제 동작에 grounding할 것. 한 컷의 포즈로 추정 금지.** (RF 6요건
#5 "캡션=클립 실제 내용"의 구체 사례; 손수 제작·VLM 둘 다 해당.)

---

## 3b. ⚠️ AV가 자동 배치에서 계속 실패하는 사유 (PD 요청 — 파악 완료)

**증상**: 03:00 자동 배치(self-heal, 6/18용)에서 AV 슬롯이 반복 실패:
```
:arrow_down: 렌더 전 자산 사전 다운로드 4개 (일괄 1회, 예산 600s)
:warning: 사전 다운로드 실패 med_2016_07_15_010839_ic …(×4)
:white_check_mark: 사전 다운로드 0/4 완료
:x: 렌더 실패: too few cuts left after dropping unavailable photos (1) — skip slot
```
AV 참조 사진은 **거의 다 iCloud-only**(efficient-storage로 프루닝) → prefetch가 0개를 받으면
모든 컷이 드롭 → "too few cuts" → 슬롯 비움. RF는 일부 클립이 로컬이라 덜 치명적, AV는 치명적.

**근본 원인 = prefetch의 간헐적 0/N (하드 실패 아님)**:
- 같은 사진을 **격리해서 받으면 정상**(med_2016_07_15_010839 = 1/1, 21.9초). 즉 사진은
  멀쩡히 다운로드 가능.
- 배치 로그는 0/3·0/1·0/4로 실패하다가 **R3에서 4/4 성공** → **간헐적**.
- 한 번의 osxphotos 호출이 **Photos 라이브러리 전체를 ~20초 스캔 + PhotoKit로 iCloud 다운로드**.
  self-heal 루프가 슬롯을 빠르게 반복 렌더하며 osxphotos/PhotoKit을 연타 → **PhotoKit 스로틀/
  contention**으로 다운로드가 조용히 0이 됨(스캔만 하고 파일은 안 받힘).
- **내 일괄-다운로드 fix(d67c980)가 스캔 횟수는 줄였지만(사진당→1회), 옛 per-photo 경로의
  재시도(3회 backoff)를 빼먹음** → 일시적 스로틀에 그대로 0 반환. (= 회복력 회귀)

**고칠 방향 (구현 X, 핸드오프 기록)**:
1. **`download_assets_by_uuids`에 재시도 추가** — 일부 uuid가 안 받히면 짧은 딜레이 후 1–2회
   재시도(옛 `_ensure_local`의 backoff를 일괄 버전에 복원). 가장 직접적.
2. **사전 워밍(pre-warm)** — 배치 시작 전, 내일 필요한 AV 사진을 미리 로컬로 받아두기(렌더
   시점에 PhotoKit 안 타게). efficient-storage가 매 AV 렌더마다 옛 사진 여러 장을 즉시
   재다운로드하는 구조가 근본 취약점.
3. **루프 부하 완화** — self-heal이 슬롯을 연타하지 않게(딜레이) PhotoKit 스로틀 회피.
4. (참고) 내가 수동으로 6/17 08:00 AV를 돌렸을 땐 격리 상태라 6/6 성공했음 → 부하가 변수.

`icloud/sync.py:download_assets_by_uuids` (재시도 없음, 여기 손볼 것) ·
`cameraman.py:_prestage_concept_assets` (호출부, 0개여도 진행→드롭).

---

## 4. PD가 정리한 RF 6대 요건 (아침 안정화 기준)

1. **영상-FIRST** — 사진 아님. 사진은 짧은 버스트만, **절대 길게 X**, 아니면 드롭.
2. **신나는 톤** ("축구 정말 잘해요!") — 도사/잠언/추측-체 금지.
3. **주인공(펫)이 화면 주인공** — 사람 장악 프레임 OUT (주인공-우세 게이트).
4. **시점 일관** — 같은-날 vlog OR '그때 vs 지금' 비교. 가짜 연속 단일사건 금지, 시점 라벨,
   과거에 현재 나이 프레이밍 금지.
5. **캡션 = 클립 실제 내용**(장소/행동/주체/시점), 날조 금지.
6. **진짜 사건 컨셉** — '각자의 방식' 무사건 공존 X. 시스템이 **코헤런트 같은-날·같은-장소
   footage를 찾아 묶어야** 함(흩뿌리지 말 것).

---

## 5. 아침/다음 세션 할 일 (우선순위)

0. **[빠른 수정] AV prefetch 재시도 복원** — `download_assets_by_uuids`에 transient 0/N 재시도
   추가(§3b). AV가 매 자동 배치에서 실패하는 직접 원인. 옛 코드엔 있던 backoff를 일괄 fix가
   빼먹은 거라 회귀 복원 = 저위험. **이게 안 되면 자동 AV는 계속 빈 슬롯.**
1. **RF를 '예전' 품질로 안정화** — 위 6요건 기준으로, **각 남은 변경이 실제로 도움 되는지 검증**
   하며. (검증 없이 게이트를 더 쌓지 말 것 — 이 세션이 그 위험을 증명.)
2. **다양성 풀-빌드가 같은-세션 클립을 흩뿌리는 설계 이슈** — 코헤런트 같은-날 묶음을 못 만들게
   함. `producer._diversity_sample` / 쿨다운 / over-used-location 캡이 상호작용. (진짜 설계 과제)
3. **VLM Step-4b 캡션 리라이트** — 손수 캡션을 덮어씀. 깨끗한 비활성 플래그 추가 고려.
4. **카페 탐방 컷 재활용** — 좋은 탐방 클립(125601)이 배경 사람 얼굴로 드롭됨. 얼굴 없는 카페
   탐방 대체본 찾으면 카페 에피소드를 3컷으로 보강 가능.
5. (참고) AV는 이번 세션에서 안정적. RF가 집중 대상.

---

## 6. 빠른 참조 (코드 위치)

- RF 시점 게이트: `agents/producer.py:_rf_temporal_coherence` (체크 A–D)
- 주인공-우세/그라운딩: `agents/cameraman.py:_rf_caption_grounding_gate` (`pet_absent_tags`,
  `frame_ok`=이미지 품질 전용, place 체크)
- 쿨다운: `agents/producer.py:_recently_used_rf_assets` + `_rf_is_cooled`(이제 exact-id만)
- 사진 처리: `agents/cameraman.py` generate_manifests(`_rf_has_video`, 사진=짧게/드롭)
- prefetch 일괄: `icloud/sync.py:download_assets_by_uuids` + `cameraman:_prestage_concept_assets`
- 손수 빌드/예약: `scripts/build_0617_rf_cards.py`, `scripts/schedule_0617_rf.py`
- RF 프롬프트: `agents/prompts/realfootage_concept.md` (활성 single-pass)

관련 메모리: [[session_handoff_0617_dawn]] [[rf_subject_prominence_gate]] [[rf_gutted_render_guard]]
[[memorylane_temporal_consistency]] [[visual_similarity_freshness]] [[rf_video_first_photo_flash]]
[[rf_concept_brainstorm_gap]]
