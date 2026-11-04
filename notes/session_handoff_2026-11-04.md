# Session handoff — 2026-11-04 (오리지널 MV 대량 + 할로윈 MV 필터 대장정 + RF-only 배포)

**스파인:** PD의 "오리지널 뮤비" 트랙을 밀며 (1) 새 주제 곡 4개 생성, (2) 메인 배치 **RF-only** 배포,
(3) **할로윈 MV를 "실사 위 할로윈 필터"로 만드는 초장기 삽질 → 결국 Fal.ai Kling O3 video-to-video가
정답**임을 확립·업로드. 이번 세션 최대 교훈 = **실사를 테마답게 하는 건 오버레이 "합성"이 아니라 영상
"변환(v2v)"이다.** 교훈 다수가 메모리·회고·CLAUDE.md에 반영됨 — [[mv-agent-spec]] [[original-mv-direction]]
[[view_data_concrete_hook_title]], retrospective §4.5 **C_overlayvsv2v**.

## 발행/예약 (YouTube)
- **할로윈 MV "오늘 밤 주인공"(실사 v2v 필터 + AV 댄스)** — **`Kl8x-c-y3X0`** · **11/4 13:00 KST** 공개 예약
  (빈 슬롯 — 요즘 18~21시에 몰려서 그 전 1시로). 제목 "박쥐 된 댕냥이 🦇 오늘 밤 주인공 (랴니레오 할로윈 MV)".

## SHIPPED (배포됨 — main)
- **메인 배치 RF-only화** (`64a2848`, 배포) — AV를 매일 배치에서 완전 제거(특별일 AV 포함). AV는 이제
  오리지널 MV 레인(배치 밖). `_assign_rfonly` 단일 SSOT 게이트, 가역=`LAUNCH_SPECIAL_AV=1`(특별일 AV 복귀)·
  `LAUNCH_LANE_MIX=3rf1av`. 회귀 특별일 RF-only 체크 추가 ALL PASS. 회고 §4.4에 11/1 진화로 반영.
- **이번 세션 끝 커밋**(아래 "커밋됨") — fal.ai 툴링 + make_mv 색/범퍼 수정 + 회고/문서.

## 곡 (생성·전달, PD pick 대기 多)
- **엄마 기다리기** = `waitmom_a_2`(어쿠스틱 K-인디, 38s) 확정
- **오늘 밤 주인공(할로윈)** = `hween_a_2`(버블리 댄스팝, 28s) → **MV 발행 완료**
- **내 소파야(소파 사수)** = `sofa_a_long`/`_2`(41/39s) — **pick 대기**
- **산책냥(신규)** = A/A2(발랄 팝)·B/B2(그루비 산책), 킬포 "아 몰라… 랴니엄마 따라했어" — **pick 대기**
  (레오 리드줄 산책 실사 많음 → MV 만들기 좋음)
- 곡 아카이브: `assets/bgm/generated/`(가사·스타일 복원 가능).

## ★할로윈 MV 필터 — 정답 워크플로우 (durable, 로컬 scripts)
합성·오버레이는 전부 천장 못 넘음(수제 파티클/스톡 글리터/Canva/나노바나나 생성 오버레이 = 납작 클립아트).
**정답 = video-to-video.**
- **`scripts/_fal_v2v.py`** — Fal.ai **Kling O3**(`fal-ai/kling-video/o3/pro/video-to-video/edit`)로 실사
  컷 리스타일. 모션 보존 + 배경/그레이드에 테마 녹임. ★strength 파람 없음 → **"펫 리얼 100% 보존, 배경만
  테마" 프롬프트 강제**(기본은 펫까지 CG 재렌더). AV 컷은 필터 제외. `FAL_KEY` in .env(PD 발급),
  `pip install fal-client`, 다운로드 SSL=certifi(gotcha#3).
- **`scripts/_gen_overlay_gemini.py`** — 나노바나나(`gemini-3-pro-image-preview`)로 그린스크린 엘리먼트
  소스 생성(프롬프트에 premium/photoreal 명시). 로컬 OpenAI 키는 이미지권한 無.
- **make_mv 수정**: 컷별 `grade` 지원·타이틀 무드테마(`title_style`)·**HLG태그 bt709 교정**(출력 `-c:v copy
  *BT709`, 요즘 아이폰 HLG가 오렌지캐스트 근본)·**범퍼 무가공**(지오메트리만·range변환 금지, 음악제거는
  `-map 0:v:0 -map 1:a:0` 무음). 피날레 길이=노래길이로 맞춰 **끝 화면 얼림(tpad) 제거**.
- 조립: `data/tmp/mv/topics/build_hween_v2v.py`(v2v 실사 + AV 댄스 + 자막) → 타이틀 overlay → 무음 범퍼
  concat → bt709.

## 핵심 교훈 (반복 실수 방지)
- **실사 테마화 = 합성 아니라 v2v**(Kling O3). 애니메이션 오버레이 소스=나노바나나, 모션=Seedance/Kling.
- **전 컷 통일 오버레이·글리터 떡칠 = 촌스러움**(PD 제일 싫어함). 요소는 컷마다 v2v가 통합.
- **HLG HDR 태그 누수 = "요즘 색 빨개"의 진짜 근본** → 최종 bt709 SDR 태그 강제.
- **완성 에셋(범퍼) 손대지 마라** — range '수정'이 되레 틀어짐(패치 meanRGB 수치검증).
- **돈 드는 렌더(v2v/Seedance/나노바나나)는 PD 컨펌 먼저.** 미감은 추측 말고 **PD 레퍼런스(share.google
  → `google.com/share.google?q=<id>` 302 → 소스URL)로 캘리브레이션**.

## 문서 반영
- 회고 `notes/retrospective_2026-05_to_07.md` §4.5 **C_overlayvsv2v**(+ RF-only 11/1 진화 §4.4).
- `notes/mv_agent_feedback_log.md` §4.5 = v2v 최종정답(죽은 시도 supersede) + 스테이지별 전 교훈.
- 메모리 `mv_agent_spec` = v2v 필터 스테이지 추가.
- `CLAUDE.md` = FAL_KEY 셋업 + "실사 시즌필터 = fal.ai v2v" + 색 gotcha + **☐ FUTURE TODO**.

## ☐ 향후 할일 (미착수)
- **RF 프로덕션 파이프라인에 fal.ai v2v 시즌필터 배선**(PD 2026-11-04, 아직 하지 말 것 — 설계부터).
  결정 필요: 어떤 클립에 적용·일일 비용예산·파이프라인 삽입지점(clip-select 후 burn/assemble 전)·피사체보존
  프롬프트 템플릿·킬스위치. 설계 + PD 승인 전 프로덕션 ON 금지. (CLAUDE.md "FUTURE TODO" 참조.)
- 소파·산책냥 곡 PD pick → MV 제작. 다른 주제(엄마 기다리기 등) MV.
- MV 자동생성 에이전트 착수(포맷 고정됨 + v2v 필터 스테이지 확정).

## NEXT (다음 세션)
세션 교체 → **다음 노래 + 동영상 제작**(PD 지시). 산책냥/소파 pick 받아 MV부터.
