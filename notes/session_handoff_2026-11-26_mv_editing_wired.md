# Session handoff — 2026-11-26 (PM) · 레퍼런스-구동 MV 편집 아키텍처 **배선 완료 + 프로토 완성**

**한 줄:** 아침 핸드오프의 "다음=콘티 Director 배선→프로토타입 렌더"를 **끝냈다.** edit_template(죽은 데이터였음)을
생성기에 배선해 mv04 6컷 드라이브 프로토(`proto_motion_final.mp4`, 30s)까지 렌더·전달, PD가 "줌 톱니 우려 해소·완성본 OK"
확인. 커밋 2개(d77082d feat·7a59f08 회고§4.14, **main·미푸시**). 설계=`notes/mv_reference_driven_editing_design.md`,
회고=§4.14 C_mveditgrammar.

---

## 이 세션 SHIPPED (커밋, main, 미푸시)
- **콘티 Director 배선** (`agents/mv_director.direct_cuts`): `edit_template` 소비(샷믹스/변주율/케이던스를 **구체 컷 수**로
  주입) + **씬 분할**(`scenes[]`+컷별 `scene_id`=장소/시간 블록, 씬 내 앵글은 과감히 다양) + **카메라 양방향 균형**(측면
  트래킹 지배·push-in과 static hold 양쪽 쏠림 금지). 리턴 `(cuts, art_direction, scenes)`.
- **edit_template 인프라** (`agents/mv_ref_director`): `build_edit_template`/`load_edit_template`/`edit_template_grammar_block`.
  신뢰 프레임분석(§4.12)을 믹스+변주율(인접샷 사이즈변경률 0.79)+케이던스로 증류. camera-move는 **측정 안 함→원칙**(신뢰
  분석기가 camera를 안 재므로 "한 척" 금지).
- **mv_producer**: `stage_reference`가 edit_template 로드(idempotent, homage early-return 위로)·`stage_director`가 scenes
  저장+scene_id 스레딩. manifest 기본값에 `edit_template`/`scenes`.
- **mv_still**: 컷의 scene 장소/시간 토큰 주입(씬 코히런스, MV_SCENE_SETTING) + **꼬리 연결 가드 LEO_TAIL**.
- **mv_render**: `_cam_phrase` 키워드 리졸버(Director 신규 어휘 "lateral tracking" 등을 i2v가 이해 — 안 하면 generic
  폴백으로 드라이브 모션 소실) + **anti zoom-sawtooth**(의도 push-in 외 전 컷 "초점거리 고정") + Seedance 꼬리 연결 lock.
- **검증(mv04 6컷 프로토)**: 샷믹스 WS/MS 46%·변주 ~90%·static death 50→21%·진짜 WS 실현·줌 강조컷 격리·씬 코히런스·정체성.
  c02 "레오 꼬리가 차 뒤 분리 부유" defect를 스틸+i2v 양 가드로 수정→완성본 reconcat. killsw MV_EDIT_TEMPLATE/MV_SCENE_SETTING.

## 날선 교훈 (회고 §4.14, 일반화)
1. **분석은 소비처에 배선돼야 산다** — edit_template가 있어도 생성기가 안 읽으면 죽은 데이터. 퍼센트 아닌 **구체 컷 수**로 줘야 실현.
2. **한 쏠림 고치면 반대로 쏠린다** — 미디엄 금지→CU/insert 쏠림, push-in 금지→static 50% 쏠림. 제약은 **양방향**. 스토캐스틱 1샘플에 과적합 금지(whack-a-mole).
3. **생성기 어휘 바꾸면 하류도 가르쳐라**(change-impact) — Director "lateral tracking"→i2v `_CAM` 몰라 폴백. open vocab=키워드 리졸버.
4. **줌 톱니의 근본은 모션 레이어** — 스틸 다양해도 Seedance 매 컷 push-in이면 concat에서 재발. 말 아닌 "초점거리 고정" 가드로 격리(PD 직관 정확).
5. **i2v가 해부 깬다** — 꼬리 분리. 스틸+Seedance lock **양쪽** 가드(한 레이어만 고치면 다른 게 깬다).

## ★다음 세션 TODO (우선순위)
1. **★랴니 흰 블레이즈가 두껍게 렌더** (PD 11/26 "마킹이 좀 두껍긴 하다 — 다음번에 집어넣고"). RYA 가드에 "THIN pencil-width
   white blaze (never thick/wide)"가 **이미 있는데 gpt-image가 미준수** = 가드-존재-미준수 class. → 가드 강화(더 강한 문구/ref-lock
   비중) + 재생성 검증. **PD가 명시적으로 다음 세션 과제로 지정.**
2. **보조 실사 레퍼 2편** 분석(`analyze_reference_frames`+`label_shot_sizes`)해 edit_template에 메인(BL)+보조 합성(메인 지배·보조 액센트).
   현재 edit_template은 메인 BL 57샷만.
3. **full mv04 26컷을 새 아키텍처로 재생산할지 PD 판단** — 프로토는 6컷. 전체 재생산은 비용↑라 PD go 필요.
4. **anti-zoom이 lateral tracking 컷에서 스케일 약간 드리프트**(c02 WS→medium) — 톱니는 아니나(PD 미지적) Seedance 트래킹이 당김.
   추후 트래킹 모션 강도/스케일 고정 조이기.

## 주의
- **미푸시**: PD 취침 중이라 무인 배포 회피(커밋만). MV 변경은 killswitch·MV레인 전용(RF 일상 생산 경로 안 건드림). PD/다음 세션이 `git push origin main`.
- 스크래치: `scripts/_mv04_proto.py`(프로토 드라이버, 커밋됨)·`data/tmp/mv/mv04proto/`(gitignore, 산출물).
- 정체성(§4.13 canonical)과 편집(§4.14)은 **별개 축** — 둘 다 per-cut 독립 생성의 다른 얼굴.
