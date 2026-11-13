# RF 비주얼 그래머 — iOS 포토앱 차용 (화면 분할 + 전환) (PD 2026-11-12)

> **다음 세션 착수용 설계.** 지금은 설계만.

## 북극성 (PD 발화)

> "내가 사실 원했던 것은 iOS 포토앱에서 차용하고 싶었던 것은 **화면별 분할이나 넘어가는 방식들**이었거든?
> 근데 그게 잘 적용이 안되었구먼?" — PD 2026-11-12

Apple Photos **Memories**에서 빌리려던 핵심 = **화면 분할(split-screen 레이아웃)** + **전환(transition
방식: 디졸브·켄번스 모션·매치컷)**. 지금 RF는 **스트레이트 컷 + 0.4s 디졸브**뿐 — 이 그래머가 안 쓰인다.
(중복/트림 불 끄느라 묻힌 *진짜 하려던 미감*.)

## 근본 = "허용 ≠ 행동" (또)

인프라는 **다 있다**:
- **split_screen** — `split_horizontal`(좌우 540×1920) / `split_vertical`(상하 1080×960) +
  `secondary_asset_id`. cameraman `_prerender_split_cuts`(5473). Writer 프롬프트에도 있음
  (`writer_realfootage.md`: 둘이 나란히/동시→split_screen, 전→후→before_after).
- **before_after** — 2컷(setup→freeze/zoom).
- **전환** — `RF_CHAIN_TRANSITION=crossfade`(0.4s), 켄번스(zoom/pan), `RF_PHOTO_FLASH`.

그런데 **Writer가 거의 안 고른다** → 스트레이트 몽타주가 디폴트. 사진(1/40)·트림(항상 앞)이랑 **똑같은
패턴**: 소프트 옵션이라 Writer가 스킵. 검증기 `writer_director.py:2156`("split_screen인데 split 컷 없음")는
*사후* 체크지 *능동 배치*가 아님.

## 수정 방향 (photo/trim 고친 것과 같은 철학)

### 1. Director가 능동 구성 (LLM, 의도)
- **split** — footage가 "둘이 동시에 뭔가"면 Director가 split_horizontal/vertical 배치(Writer 디폴트 아님).
- **before_after** — "전↔후/작을때↔지금"이 있으면 Director가 2컷 구성.
- **메모리믹스 전환** — 시대/장소 섞을 때 디졸브·켄번스로 "넘어가는 방식"을 Director가 지정.
- = earned-connection 루프(`rf_footage_aware_direction_design.md`)의 Director가 **편집 그래머까지** 소유.

### 2. 기계적인 건 파이썬 (결정론)
우리가 합의한 "**기계적=파이썬, 창의=LLM**" 분리:
- **전환 선택** — 사진 컷엔 켄번스, 모션 연속이면 매치컷, 시대전환이면 디졸브 길이↑ 등을 **clip 속성으로
  파이썬이 할당**(트림 자동분산과 같은 방식). "항상 0.4s 디졸브" 단조로움 탈출.
- **split 레이아웃** — 두 subject의 공간 배치(가로/세로)를 프레이밍 신호로 결정론 선택.

### 3. Apple Memories 레퍼런스
- Apple이 **언제 무엇을** 쓰는지 분석(분할은 비슷한 순간 병치에, 디졸브는 시간점프에, 켄번스는 스틸에)
  → 그 규칙을 우리 Director/파이썬 할당에 매핑. ★단 우리 차별점(earned-connection, 애플 못하는 창의연결)
  유지 — 애플 복제가 아니라 그 위.

## 함정 / 가드레일
- **촌스러움 금지** — 전환·분할 남발은 싸구려(과거 오버레이 떡칠 교훈). 의도적일 때만.
- **split은 footage가 받쳐야** — 두 subject가 실제로 동시에 있어야(없는데 억지 분할=날조). footage-aware 필수.
- **빌드 OFF → 스모크 → 플립.**

## 코드 앵커 (다음 세션)
- split: `cameraman.py:_prerender_split_cuts`(5473)·`_run_split`(1542)·`writer_director.py:2156` 검증·
  `writer_realfootage.md` 그래머.
- 전환: `RF_CHAIN_TRANSITION`/xfade(`assemble_episode.py`)·켄번스(`edit_effect`)·`RF_PHOTO_FLASH`.
- Director 능동배치: B1 `run_rf_director`/`director_realfootage.md`(footage-aware 설계와 합류).

cf [[rf_footage_aware_design]](earned-connection 루프·기계적=파이썬)·[[rf_editing_direction_memories]]
(추억 그래머)·mv_agent_feedback_log(v2v·오버레이 촌스러움 교훈).
