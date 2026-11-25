# Session handoff — 2026-11-26 · MV 편집 아키텍처(레퍼런스-구동) + drift 일관성

**한 줄:** mv04(「네온 미드나잇 크루즈」) drift-수정 최종본을 완주·전달했고, PD가 최종본을 프레임단위로 리뷰하며
**per-cut 독립 생성의 천장**(연결성 없음·줌 톱니·전부 미디엄·댄스 비트맹)을 드러냈다. 그 근본을 **레퍼런스-구동 편집
아키텍처**로 잡기로 하고 — **설계문서 + 신뢰 레퍼 분석 인프라(균등 24fps + 결정론 컷 + VLM 샷사이즈) + edit_template(메인
BL, 실데이터) + 스틸 shot-size 강제**까지 지었다. 남은 건 보조 레퍼·콘티 Director 배선·프로토타입 렌더. 설계=
`notes/mv_reference_driven_editing_design.md`. drift 일관성 축=`notes/session_handoff_2026-11-25_av_consistency.md`·회고 §4.13.

---

## 이 세션 완료 (전부 커밋/배포 — main)
- **mv04 drift-수정 최종본** — canonical 전체 재렌더(26컷), 차(navy MGB) 일관·랴니 정상빌드·신스웨이브 타이틀·wrap 자막·페이싱(med 2.7s)·립싱크·눈연기. **전달됨.**
- **일관성(drift) MV판** (cbaf0e4·ca425b3): `mv_still` per-MV `canonical_ref`(차+펫 이미지앵커 매 스틸 ref-lock, killsw MV_CANONICAL_REF)+verbatim CANONICAL_LOCK. RYA 식별 교정(whippet-slender 과잉→정상 컴팩트, PD 실물 `ryani_real2.png`·캐노니컬 `mv04_canonical4.png`).
- **리서치(arXiv)** 회고 §4.13·메모리 `crossshot_consistency_canonical`: drift 근본=텍스트가 지오메트리 못잠금→엔티티별 protected 캐노니컬 star-shaped(체인금지)+배경+캐릭터 합성키프레임→i2v(=우리 스틸-우선=black-box SOTA)+VLM검증. (GroundShot·InfinityStory·EntityBench.)
- **레퍼런스-구동 편집 설계문서** (0c06e31~9a76b09): 세 축 분리(①장소/시간=씬 내내 유지 ②앵글/샷사이즈=씬 안 과감히 다양 ③의상/컨셉=같은 장소여도 변화 가능, 레퍼 학습). 메인 ref 1 + 보조 N(평평 평균 금지). 분석→생산제약(YDKMN). **완전 복제 금지.**
- **★신뢰 레퍼 분석 인프라** (86f4c58·048e90d) — PD 교정("모델한테 샷 나누라 하지 말고 균등 24fps"): `agents/mv_ref_director.analyze_reference_frames`(ffmpeg fps=24→numpy 프레임차→컷=diff 스파이크→0.25s 플래시 병합, **결정론·같은영상 같은결과**) + `label_shot_sizes`(샷 mid_frame 배치 VLM→WS/MS/CU/ECU). **Gemini 비디오요약(9/627/timeout 불안정) 폐기.** BL 검증=**57샷 median 1.54s**.
- **edit_template**(`data/tmp/mv/mv04/edit_template.json`, 메인 BL 실데이터): 샷믹스 **MS.28/WS.26/CU.25/insert.12/ECU.09**(진짜 다양), **77% 컷이 샷사이즈 변경**(변주/하드컷 리듬). ← 우리 mv04가 빠뜨린 문법.
- **스틸 shot-size 강제** (0f8eabf): `mv_still.build_prompt`가 shot_size를 프레이밍으로 강제(WS=차 작게·CU=얼굴·ECU=디테일·insert=소품), 캐노니컬 미디엄 bias를 이김. killsw MV_SHOTSIZE. **검증**=드라이브 구간 재생성시 진짜 WS(차 작게·거대 네온도시)·타이트 CU·디테일 insert = 변주 실현, 같은 씬 유지.

## ★최종본 PD 피드백 → 근본 (다음 세션이 알 것)
| 피드백 | 근본 | 상태 |
|---|---|---|
| 연결성 없음(드라이브인데) | **메타근본 = per-cut 독립 생성** + 씬 장소/시간 미유지 | 설계·일부배선 |
| 33-40·1:05 줌 톱니 | R1(per-cut 리셋+줌) + R2(전부 미디엄) | shot-size 강제 ✅·측면트래킹 카메라 미배선 |
| 50-52 좋은 와이드 너무 짧음 | 히어로 샷 길이 미배분 | 미완 |
| 16-18·27 댄스 비트맹·불필요 | R3(i2v가 노래 비트 모름) | c05/c08 자연모션 재렌더 완료 |
| 1:01 댄스 짧음 | R3 길이 | 미완 |
★PD 핵심: "모든 씬 연속이면 안 돼" — 연속성=**씬(같은 장소/시간) 코히런스**지 카메라 흐름 아님. 씬 안 컷은 완전 다른 앵글 OK.

## 다음 세션 할 일 (우선순위)
1. **보조 레퍼 2편 = 실사 시네마틱**(nightcall 애니 버림)으로 교체 → `analyze_reference_frames`+`label_shot_sizes`로 **신뢰 재분석**. edit_template에 메인+보조 합성(메인 지배·보조 액센트).
2. **콘티 Director 배선** — Director가 컷들을 **씬(장소/시간 블록)으로 분할** + `edit_template`에 매핑(샷사이즈 시퀀스·케이던스·하드컷 리듬). 컷마다 {shot_size(이미 스틸 강제됨)·scene_id·camera_move}.
3. **씬 장소/시간 유지** — 한 씬 모든 컷에 그 scene의 **캐노니컬 배경/장소 레퍼 + 시간대 토큰**(star-shaped). (= 일관성 축 레버. 캐노니컬 뱅크를 **장소 포함**으로 일반화.)
4. **카메라 변주** — 측면 트래킹을 지배로(레퍼 50%), 균일 push-in 금지(줌 톱니 소거). 히어로/댄스 샷 **길게**.
5. **프로토타입 렌더** — 드라이브 한 구간을 위로 짜서 스틸(샷사이즈 다양)→i2v(측면트래킹)→조립. 연결성(씬 코히런스)·변주·톱니소거 검증.
6. (의상/컨셉 축은 mv04 휴면 — 향후 컨셉 MV.)

## 주의 (PD 반복 교훈)
- ★**"한 척" 금지** — 무조건 **진짜 분석한 것만**. (이 세션에 보조 분석 실패를 정직히 보고하고 신뢰 인프라로 전환함.)
- **완전 복제 금지** — 레퍼=문법/조명/앵글/미감 영감, 콘텐츠/피사체/세계는 우리 것(펫=실제동물).
- 분석→**생산 제약**(노트 아님). 메인 ref 지배, 보조는 액센트(컨셉 흐리지 말 것).
- AV 레인(cameraman.py) drift 통합은 별도 핸드오프(`session_handoff_2026-11-25_av_consistency.md`): set_anchor 누수fix·chain_from_prev 폐기·VLM게이트.
