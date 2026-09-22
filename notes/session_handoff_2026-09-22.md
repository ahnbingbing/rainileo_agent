# Session handoff — 2026-09-22 (합찌 자막 + 15.9s 빈슬롯 두 근본, 둘 다 "렌더러/게이트가 근본을 가림")

**스파인:** PD의 세 신호 — "9/24 배치 하나 비었어" · "AV 자막에 자꾸 '오늘도 합찌'가 들어가" ·
"15.9s 트림 하한 여유두게" — 를 프레임/로그/DB ground truth로 파보니 셋 다 **그럴듯한 표면이
진짜 근본을 가린** 같은 계열이었다. ①"합찌"는 폰트 오독도 LLM 오탈자도 아니라 **Seedance가
프레임에 직접 구운 환각 텍스트**(DB·소스엔 "햅삐"만; 번인 전 원본 컷이 진실). ②"15.9s"는 트림도
floor도 아니라 **coherence gate가 themed_compilation을 1컷으로 gutting**한 시그니처(본문 11.9s +
범퍼 4s). ③9/24 12:30 빈슬롯은 바로 그 gutting + 타임아웃 캡 2개. 관통 교훈: **화면의 글자/짧은
길이의 출처를 DB·코드에서 못 찾으면 렌더러/게이트가 만든 것 — 프레임이 ground truth고, "생성기가
안 하리라 기대하는 것/게이트가 정당히 드롭한 결과"까지 결정론으로 상한해야 한다.**

## VM authoritative · push=deploy
- **VM HEAD `a69d06e`** (코드) — docs 커밋 `d78da15`(회고)는 런타임 무관, deploy.timer 다음 틱에 pull.
  두 런타임 가드(Seedance no-text `aaa79b0`, coherence viability `a69d06e`) 라이브 배포 확인.
- 하루 스케줄(KST) 그대로: 03:00 launch_selfheal / 09:10 slot_topup / 09:40 pd_reviewer(APPLY=1)
  / */5 process_board_escalations / */30 ytcache. **다음 03:00 배치가 두 가드의 첫 라이브**(9/26 생산, LEAD_DAYS=2).

## SHIPPED (전부 배포·검증)

### 1. "오늘도 합찌" 근본수정 — Seedance 화면-텍스트 환각 억제 (`aaa79b0`)
- **근본**: AV 윙크 클로저의 raw Seedance 컷(`seedance_raw/…/cut6_wink_ending.mp4`)에 이미
  "오늘도 합찌 ❤"가 구워져 있었다 — AI가 한글을 못 써 "햅삐/해피"를 깨뜨린 화면 캡션 + 컬러 하트.
  그 위에 우리 drawtext가 진짜 "오늘도 햅삐 / Happy as ever"를 얹어 **오탈자 이중 자막**. 카드
  payload·DB 전수 스캔엔 "합찌" 0건(정확히 "오늘도 햅삐 ♥"만). API `watermark:False`는 자기
  워터마크만 죽이지 이 생성 텍스트는 못 막는다.
- **Fix**: 모든 Seedance 호출이 거치는 **단일 초크포인트 `scripts/animate_seedance_i2v.py:submit_job`**
  (i2v/interp/ref 전 모드·양 레인)에 결정론 no-text 네거티브("NO on-screen text/letters/Hangul/
  emoji/watermark, clean frame") 상시 부착(멱등). director_shots.md가 이미 그래픽=overlay_fx 후처리로
  규정(Seedance 텍스트 미생성 전제)이라 **설계 강제**지 신설 아님.
- **검증**: 해피-윙크 트리거 재현 렌더(공식 ref + 윙크 프롬프트) → 전 구간 텍스트 0. ★단
  텍스트 환각은 **확률적**이라 근절 증명 아님 → PD 스팟체크 권장, 재발 시 후처리 OCR/crop 백스톱.
- 회고 §4.2 **A28**. change-impact 완료.

### 2. "15.9s 빈슬롯" 근본수정 — coherence gate viability 가드 (`a69d06e`)
- **근본**: 반복되는 **정확한 15.9s**는 `_rf_cross_cut_coherence_gate`의 과드롭 —
  themed_compilation(여러 outing) 캐스트가 cut1 앵커 기준 전부 incoherent 판정돼 드롭 → **1컷만
  생존**(본문 ~11.9s + 범퍼 intro1.5+outro2.5=4s = 15.9s < 16s upload floor) → ORPHAN gutted-stub →
  빈슬롯/90분 self-heal 그라인드. 9/24 12:30·8/17 18:00·9/3 21:00 전부 정확히 15.9s(= "1컷 생존"
  시그니처). floor를 낮추면 stub을 발행하므로 답 아님. content_gutted-reroll(C15)은 풀이 얇으면 복구 실패.
- **Fix**(face gate `RF_FACE_ALLDROP_GUARD`와 동형): 드롭 계산 후 anim_dir/{tag}.mp4를 ffprobe해
  드롭이 본문을 **floor+여유(RF_MIN_SECONDS−4s범퍼+`RF_COHERENCE_MARGIN_SECONDS`2s = 14s) 아래로
  gutting하면 가장 긴 드롭 컷부터 되살려**(재-admit 최소) floor 위 여유 확보. 전 컷 살려도 짧으면
  **무복원 → downstream floor 정직 거부**(길이 날조 금지, 진짜 재료부족은 여전히 걸림). 유지 컷은
  unlink/caption-pop 전에 drop에서 빠져 mp4+캡션 보존(half-applied 없음). loud 로깅(`:shield:`).
- **롤백/조절**: `RF_COHERENCE_VIABILITY_GUARD=0` / `RF_COHERENCE_MARGIN_SECONDS`.
- **검증**: 되살림 로직 단위 3케이스 + **실제 mp4로 gate 함수 통합 E2E**(9/24형 → 최장 cut5 복원,
  body 29.4s, 짧은 cut2/3/4 계속 드롭, 캡션/파일 정합). RF 전용·AV 무영향·Giri는 미세-stitch
  backstop 유지. 회고 §4.4 **C15 확장**.

### 3. 9/24 12:30 빈슬롯 채움 → 라이브 4/4
- 신선 RF 재롤 성공 `iu6YoI_cr5Q` ("냄새 신세계? 타일 물가 레오 여름탐정", 공개예정 2026-09-24T03:30Z,
  ≥16s·Giri 통과, PD veto 가능). 얇은 풀이라 self-heal 여러 컨셉 재롤 끝에 R1 성공. fill-0924-1230
  transient 유닛 정지·selfheal 프로세스 0. (가드 배포 전 구코드로 돌아 채움 — 다음 배치부터 가드 적용.)

## 상태 스냅샷 (세션 끝, 9/22 KST 오후)
- VM 유휴(real launch/render 0), cron active, 두 가드 라이브.
- 스케줄: **9/24 = 4/4**(08:00 B1gP9Hl6Yfw 레오간식 · 12:30 iu6YoI_cr5Q 여름탐정 · 18:00 0KVx0TBTABs
  가을랴니 · 21:00 5Zg6WWu3LKc 송편추석). 9/23=3/4(08:00은 이전 세션서 RF Giri 캡션-mismatch로 미충족).
- 회고 A28+C15확장, 진행 로그 3건 기록.

## ★ NEXT (다음 세션)
1. **다음 03:00 배치 = 두 가드 첫 라이브 스팟체크**:
   - Seedance no-text: AV 클로저(및 전 컷) 화면 텍스트 0 확인(윙크 "합찌" 재발 여부). 재발 시
     확률적이므로 후처리 OCR/crop 백스톱 착수.
   - coherence viability: 배치 로그 `:shield:` viability 가드 발화 시 그 에피 스팟체크 —
     **과복원으로 어색한 outing-stitch**가 보이면 Giri가 잡거나 veto, 심하면 `RF_COHERENCE_VIABILITY_GUARD=0`.
2. **더 깊은 근본(선택)**: coherence gate가 **cut1을 무조건 앵커**로 삼는다 — cut1이 오히려 outlier면
   coherent 다수를 버리고 outlier 1컷을 남긴다(viability 가드가 "최장 복원"으로 완화하나 근본은 아님).
   진짜 fix는 **최대 coherent 클러스터를 앵커**로(다수 유지·소수 드롭). 관찰 후 필요시.
3. **15.9s 시그니처 후속 관찰**: 가드 적용 후에도 15.9s ORPHAN이 남으면 = 재료가 진짜 부족한
   케이스(정상 거부) vs 다른 gutting 경로. 로그로 구분.
4. 롤백: 두 가드 다 env 플래그로 즉시 무력화(`RF_COHERENCE_VIABILITY_GUARD=0`; Seedance 가드는
   revert `aaa79b0`). v2 프로덕션 모델은 여전히 플래그 OFF(무영향).
