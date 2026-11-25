# AV 핸드오프 — 생성 일관성(cross-shot drift) 통합 아키텍처 (2026-11-25)

**한 줄:** MV(「네온 미드나잇 크루즈」)에서 컷 간 drift(차·운전대·펫 크기)를 잡는 **엔티티별 캐노니컬 레퍼런스(star-shaped)** 방법을 PD+arXiv 리서치로 확립하고 **MV 레인에 배선**했다. 같은 근본이 **AV 레인**에도 있다(PD: "집 사진을 에셋으로 줬는데도 나중에 드리프트"). 이 문서는 그 **AV 통합 작업**을 다음 세션이 집어 들 수 있게 남긴다. 리서치·근본·회고는 `notes/retrospective_2026-05_to_07.md §4.13`, 메모리 `crossshot_consistency_canonical`.

---

## 왜 (근본 — arXiv 합의)
컷을 **독립 생성**하고 차/운전대/크기 같은 **개체의 '모양'을 텍스트로만** 잠그면 drift한다 — 텍스트는 톤은 주나 **지오메트리를 못 잠근다**. 답(training-free·black-box 적용 가능, GroundShot·InfinityStory·EntityBench·EM-Vid·A2RD):
1. **엔티티별 protected 캐노니컬 레퍼런스 뱅크** — 캐릭터 + **소품** + **장소/세트** 각각 고정 1장(+보조). 장소는 **전경 제거+인페인팅한 clean 배경**.
2. **★star-shaped 앵커링** — 모든 등장이 **같은 캐노니컬에 직접** 참조. **체인 금지**(앞 컷 이어받기 = drift 누적).
3. **배경+캐릭터를 미리 합성한 키프레임 → black-box i2v** (= 우리 gpt-image 스틸→Seedance가 이미 이 SOTA 경로).
4. **VLM 검증 + re-anchor** — 각 샷을 캐노니컬과 대조해 drift를 잡고 재생성.

## MV 레인(완료, 참고 구현) — cbaf0e4·ca425b3
- `scripts/mv_still.py`: manifest **`canonical_ref`**(차+펫 앵커)를 매 스틸 gpt-image-2 ref-lock에 추가 + verbatim `CANONICAL_LOCK` 제약. killsw `MV_CANONICAL_REF`.
- RYA 식별 교정: "whippet-slender"(과도한 얇음) → **정상 컴팩트 프불**(얇지도 넓지도). `character_sheets.md`의 미묘한 진실과 PD 실물(`assets/character_ref/ryani_real2.png`)에 맞춤. 캐노니컬 = `assets/character_ref/mv04_canonical4.png`.
- **AV도 이 캐노니컬로 잡으라**는 PD 지시 있음(#2).

---

## ★AV 레인에서 "집 사진 줬는데 왜 드리프트했나" (코드 근거)
올바른 메커니즘은 **이미 있다** — 집행이 샜을 뿐이다. `agents/cameraman.py`:
- `scene_ref`(line ~576 `_scene_ref…`): 세트별 **캐노니컬 빈 방** 레퍼, `data/set_library.json[set_anchor].scene_ref` → `assets/scene_refs/<set_anchor>.png`, 전 에피 공유. `_scene_ref_is_clean()`가 살아있는 동물/사람 있으면 "매 컷 bleed" 경고(= InfinityStory의 clean 배경 요건).
- **누수 ①(star-shaped 미집행):** 주석(PD 2026-06-14) 그대로 — *"set_anchor가 매니페스트에 안 꿰여서 매 렌더가 **fresh GPT 방 생성** → 벤치/벽난로 drift"*. 캐노니컬이 있어도 **스레딩이 새서 중간에 새 방으로 폴백** = 집 drift의 직접 원인. `SCENE_REF_OVERRIDE` env는 임시 핀.
- **누수 ②(체인):** `chain_from_prev` / `__chain_from_prev__`(line ~1479, Tier 3) = 컷을 **앞 컷 마지막 프레임에서 이어받음** = GroundShot이 경고한 **체인(drift 누적)**.
- **누수 ③(검증 없음):** 각 컷을 캐노니컬과 대조해 drift 잡는 VLM 루프 부재.

## 다음 세션 할 일 (AV 통합 — 우선순위)
1. **set_anchor 스레딩 누수 fix(최우선)** — `set_anchor`를 Writer/Director→매니페스트→cameraman까지 **샐틈없이** 꿰서, 알려진 세트는 **절대 fresh 방으로 폴백 안 하게**. 폴백이 필요하면 1회 생성 후 **그 세트의 캐노니컬로 승격·고정**(library에 저장). → "집이 매번 미묘하게 달라짐" 소거. `pipeline-change-impact`로 블래스트 추적(Writer가 set_anchor 내나? Director가 넘기나? manifest 필드? cameraman이 읽나?).
2. **chain_from_prev 폐기/star-shaped 전환** — Tier 3 체인을 **캐노니컬 직접 참조**로 교체(또는 최소화). 체인은 drift를 누적하므로 기본 금지, 꼭 필요하면 캐노니컬 re-anchor를 섞어라.
3. **clean 배경 강제** — scene_ref는 항상 전경 제거된 clean 방(`_scene_ref_is_clean` 통과). 아니면 생성 단계에서 전경 인페인팅.
4. **VLM 게이트(`MV_REVIEW_VLM` 스텁 배선)** — 각 스틸/컷을 캐노니컬(펫·세트)과 대조: 정체성 일치·개체수·아티팩트·세트 drift. 실패 시 재생성/재앵커. (MV `agents/mv_reviewer.py:_vlm_still_checks` 스텁과 공유 가능.)
5. **#2 AV 캐노니컬 적용** — AV 캐릭터/세팅 앵커를 MV의 `canonical_ref`와 같은 결로. 엔티티 뱅크(펫 + 반복 세트)를 per-episode/arc로.
6. **엔티티 뱅크 일반화** — 캐노니컬을 **장소 포함**으로(지금 MV는 차+펫만). `data/set_library.json`을 "엔티티 뱅크"로 확장(캐릭터·소품·장소 각 protected 캐노니컬 + 보조).

## 주의 (프로덕션)
- AV 레인(cameraman.py)은 **VM 데일리 프로덕션**이 돈다 → 변경은 양 레인 영향·VM 배포(`git push`→deploy.timer)·회귀 추적 필수(`pipeline-change-impact`).
- 전부 **kill-switch + graceful fallback**로(기존 동작 보존). star-shaped/체인 변경은 데일리 RF/AV 렌더에 직접 영향.
- 리서치 출처: GroundShot(arxiv 2606.20799)·InfinityStory(2603.03646)·EntityBench(2605.15199)·EM-Vid(2605.23610)·A2RD(2605.06924, 환경 96.7%)·FilmWeaver(2512.11274)·CANVAS(2604.13452).

## 미완 (MV 쪽, 이 세션 꼬리)
- mv04 canonical 전체 재렌더(26컷) 완성본 — 이 세션에 돌림(PD 승인). 완료 후 전달.
