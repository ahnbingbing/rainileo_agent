# Session handoff — 2026-07-04 (eve)

아주 긴 세션(오전 핸드오프 이후 이어서). 7/5 배치 4슬롯 전량 재제작·재업로드 + durable 픽스 다수 +
비용 원장 재보정 + 회고/케이스스터디 정합 + 새 스킬. ★NEXT가 다음 세션 진입점.

## ★ NEXT — 우선순위 순

1. **durable 픽스 커밋** (PD 요청 시). 아래 SHIPPED의 코드/문서가 **미커밋**(로컬 즉시적용 중). 대상:
   `slack/app.py agents/cameraman.py agents/writer_director.py agents/reviewer.py agents/api_ledger.py
   scripts/api_cost_report.py CLAUDE.md notes/retrospective_2026-05_to_07.md .claude/skills/merge-retrospective/`
   + 새 문서 `notes/case_study_8_weeks{,_ko}.md`, `notes/*architecture.svg`. 스크래치 `scripts/_*.py`는 제외.
   ★커밋하면 **merge-retrospective 스킬**을 돌려 이번 머지의 배운점을 회고에 남길 것(이미 대부분 반영됨 — 확인만).
2. **grandma→AV 곶감꼭지 실렌더 검증** (task 미완). 라우터+소품이미지 Seedance주입 코드·백필 완료했으나
   **실제 AV 렌더로 검증 안 됨** — 다음 곶감꼭지 AV편에서 실물처럼 나오는지 확인. cameraman ref-mode가 컷
   프롬프트에 소품명 있으면 object_refs 실물사진을 full_refs에 주입(9한도). object_refs에 곶감꼭지 9각도 있음.
3. **호칭 게이트 caption-STAGE 예방** (B6 후속). 지금 `_canon_honorific_gate`는 Giri에서 **차단만**(위반시
   슬롯 빔). 생성단(caption_agent/writer_director 캡션 finalize)에 탐지+타깃 재작성을 붙여 **빈 슬롯이 아니라
   교정**되게. (Giri 차단 + caption_agent.md 규칙으로 현재도 안전하나, 예방이 낫다.)
4. **episode_stories 소재 큐레이션** (원 task #2, 여전히 미착수). 282행 할머니대화 원문 → LLM discrete 소재
   (트레잇·사건) 추출+dedup+태깅. concept_brainstorm이 pd_notes/[컨셉]을 읽으니 정제하면 AV/RF 소재 질↑.
5. **7/5 배치 공개 스팟체크**. 재업로드 4슬롯이 7/5 공개: 08:00 계곡 `BBYun3os5tU` / 12:30 일광욕
   `QJs_jnQZqQE` / 18:00 아침루틴 `FLKghVqaHx4` / 21:00 삼계탕 `VcZnVMv9ZrE`.
6. **grandma 사진 forward 라우터 관찰**. 봇 라이브(PID 재시작). 새 사진 업로드가 설명 보고 object_refs/
   background_refs로 라우팅되는지 실제 인입에서 확인.

## SHIPPED 이번 세션 (전부 미커밋, 로컬 즉시적용)

### durable 파이프라인 픽스
- **grandma 설명 자동매핑 forward 픽스** (`slack/app.py`) — 파일→봇질문→설명 패턴의 후속 텍스트를 pending
  업로드에 짝지어 **pd_notes(owner ground truth)에 맥락누적**. 캐시(dedup-safe asset_id)+Slack히스토리 폴백.
  옛 코드는 notes(JSON blob) 1줄만 붙여 VLM필드 깨뜨림(supersede). producer가 pd_notes를 진실로 읽음.
- **판타지 컷 공간매칭** (`cameraman._in_reality_set`) — 현실 set_description/scene_ref/omni는 현실 공간 컷에만.
  AV21 cut3 꽃길↔빈거실 깜빡임 근본. 실렌더 검증(episode_av_20260704_125852).
- **closer-integrity 게이트** (`writer_director._ensure_resolution_before_wink`) — 스토리가 상상 절정에서
  끝나면 윙크컷에 현실복귀 payoff 캡션 folding. AV21 "이야기 없이 끝" 해결.
- **RF trim_start 드롭 버그** (`cameraman`) — 핀한 concept-cut trim_start을 asset에서만 읽어 항상0이던 것
  cc우선으로. RF 삼계탕·일광욕 재make의 세그먼트 선택 근본.
- **Giri photoreal 오판 금지** (`reviewer.py`) — AV가 실사/RF처럼(lo-fi) 나오는 걸 style감점 reject하던 오판
  수정: 무훅룰 CONTENT재범위 + 절대 non-penalty. 회귀 style 2→9. [[giri_photoreal_av_not_penalized]]
- **윙크 컷 set_description/spatial_lock 제외** (`cameraman._is_wink`) — 윙크 i2v가 방 재묘사로 배경 morph
  하던 것(햅삐 중간 배경생성). 윙크는 자기 still이 배경앵커.
- **호칭/나이 canon 게이트** (`reviewer._canon_honorific_gate`) — 오빠/형(연상남자없음)·레오=시니어·랴니=막내
  결정론 차단(캡≤5). 회귀 8/8. caption_agent.md 규칙을 코드로 강제.
- **grandma 사진 → AV 라우터** (`slack/app.py._route_grandma_photo` + `cameraman` 소품 이미지 주입) — owner
  설명 보고 prop→object_refs/space→background_refs 라우팅 + AV 컷이 소품명 언급시 실물사진을 Seedance ref로.
  **곶감꼭지 근본**(AV가 자꾸 틀리게 그려 하비가 실물 올림; object_refs가 텍스트만 소비돼 이미지가 Seedance에
  안 감 = A7 이미지가 텍스트 이김). 백필: object_refs 12(곶감꼭지 9각도) + background_refs 67→72.
- **비용 원장 재보정** (`api_ledger.py`+`api_cost_report.py`) — 영수증 대비 ~3배 과소집계였음. seedance
  $0.30→0.90/콜, 텍스트 토큰기반(gpt4.1 $4/opus $18/gemini $0.5 per1M), 과거 2822행 백필. 아침리포트 현실화
  (~$41/일). authoritative=제공자 영수증.

### 7/5 배치 4슬롯 전량 재제작·재업로드 (PD 승인)
- 08:00 RF 계곡 물놀이 `BBYun3os5tU` (기존 유지)
- 12:30 RF **여름 아침 일광욕** `VdaD-QH4WfU→QJs_jnQZqQE` (무사건 템플릿→7/4 fresh 이벤트)
- 18:00 AV **이렇게 다른 아침 루틴** `7poBXMKiwJ4→FLKghVqaHx4` (캡션깨짐/겉핥기/호칭오빠/윙크morph 다 수정)
- 21:00 RF **복날 삼계탕 먹방** `xX9EZEsxQjo→VcZnVMv9ZrE` (pd_notes 기준 먹는구간 트림, 레오 가시)
- (그리고 7/4 21:00 AV 꽃길여왕 `VtjEL9fwkZA→HlqMfi7QONs`도 이 세션서 교체)
- reupload는 옛 제목 유지 → 매 교체마다 카드 제목/테마/payload 새내용 갱신함(알려진 갭).

### 프로세스
- **새 스킬 `merge-retrospective`** + CLAUDE.md 트리거: 머지 후 한일·배운점을 회고에 **무조건** synthesize.
- **회고·케이스스터디 정합**: §0.5 기원·본질(스티커→2레인), §4.2 A2/A7/A8/A15, §4.3 B0/B5/B6, §4.5 D3b/D3c/D6,
  §5 4주체 분리(arc/채널매니저/매크로/기리). 정본 = `notes/case_study_8_weeks_ko.md`.

## 이번 세션 관통 교훈 (반복 패턴)
- **규칙은 프롬프트에 있어도 강제(게이트) 없으면 LLM이 무시** — 호칭 canon(caption_agent에 있었으나 "오빠"),
  캡션 그라운딩, 무훅. **결정론 게이트가 유일한 보장**. (증상수정≠재발방지 계속.)
- **이미지가 텍스트를 이긴다** (A7 재확인) — 곶감꼭지는 텍스트 object_refs로 안 되고 실물 이미지가 Seedance에
  도달해야. 룩·소품·정체성은 소스(ref)가 정한다.
- **검수기 오판도 실패** — Giri가 옳은 photoreal 렌더를 틀린 이유로 죽인 것(러버스탬프의 역방향).
- **내 손-디렉티브가 canon을 뒤집을 수 있다** — AV18 성격역전. PD 지시대로 짜되 canon(나이/역할) 먼저 확인.
- **자체 원장은 렌더 소각·개발비를 놓친다** — 진짜 총비용은 제공자 영수증.

## 상시 주의 / 알려진 갭
- 미커밋 스크래치 `scripts/_*.py` 다수 (main 제외). 커밋은 PD 요청 시.
- content_hash(md5) dedup은 완전동일만 — 재인코딩 near-dup은 phash 2차 follow-up.
- GCS 이전: 디스크(맥 ~32Gi) 스테이징 제약으로 미실행(런북 준비완료). 근본 인프라 과제.
- 맥 네트워크 불안정 = 렌더 재시도 잦음.
- PD: "그 부분(회고/케이스) 업데이트 하라고 할게" — 추가 지시 대기.
