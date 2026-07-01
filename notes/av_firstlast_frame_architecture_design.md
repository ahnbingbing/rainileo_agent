# AV 풀 아키텍처 설계 — 콘티가 각 컷 첫+끝 프레임 생성 → interp 보간

PD 2026-07-01 ★NEXT. 봉준호 콘티 원칙의 나머지 절반. 설계 + blast radius (본격 $50 렌더 전
PD 검수용). [[cuesheet_decoupled_from_caption]] 1단계(캡션↔큐시트 분리)는 commit 9cb6ec5.

## 1. 목표
현재 표준 AV = "컷당 **첫 프레임 스틸 1장**만 콘티로 생성 → Seedance가 마지막 프레임을
모션으로 알아서 만든다(i2v forward-motion)". 한계 = 끝 상태를 콘티가 통제 못 함 → 원근/포즈/
연속성이 Seedance 환각에 맡겨짐.

→ 목표 = "콘티(연출 큐시트)가 각 컷 **첫 프레임 + 마지막 프레임 둘 다** 생성하고, Seedance
**interp**가 그 사이만 보간." 봉준호의 두 키프레임 콘티처럼 양 끝을 감독이 그린다.

## 2. 핵심 통찰 (왜 지금 가능한가)
- Seedance interp = `--image first --last-frame last`. **BytePlus 규칙: first/last frame과
  reference_image 혼용 불가** → interp는 캐릭터 ref도, scene_ref 배경 앵커도 못 붙인다.
  즉 **배경 + 캐릭터 마킹을 두 스틸 안에 미리 베이크**해야 한다.
- 그런데 그게 정확히 **AV_PRECISE_STILL(Fix 2, [[av_source_precision_fixes]])**이 하는 일:
  깨끗한 scene_ref 배경 + 캐릭터 ref를 합성해 방-앵커된 스틸 1장을 만든다.
  → 이걸 **포즈 다른 두 장(첫/끝)** 으로 확장하면, 두 스틸이 같은 방·같은 마킹을 공유 →
  interp가 방을 유지하면서 양 끝 포즈만 보간. **배경붕괴(=ref 강제의 이유)가 구조적으로 해소.**
- 보너스: 단일공간 락 컷을 i2v→ref로 강제하던 코드(cameraman 6317)의 존재 이유가 interp엔
  사라진다(두 스틸이 배경 앵커). 윙크 체인 드리프트([[av_chain_only_continuous]])도 끝 프레임을
  명시하니 줄어들 여지.

## 3. Blast radius (양 레인 정합, pipeline-change-impact)
### Director (`writer_director.py` + `agents/prompts/director_shots.md`)
- 컷마다 **두 스틸 프롬프트**: `first_still_prompt`(=현 regen_prompt, 시작 포즈) +
  신규 `last_still_prompt`(끝 포즈/도착 상태). 둘은 같은 비트의 START/END 키프레임.
- motion 있는 AV 컷에 `seedance_mode="interp"` 세팅(정적/응시 컷은 i2v 유지 가능).
- 캡션은 **안 건드림**(분리 원칙 — 이게 요점).

### Cameraman (`agents/cameraman.py`)
- **스틸 생성(Step 2, `_av_precise`/`_compose_av_still`)**: 같은 clean scene_ref + 같은 캐릭터
  ref로 **두 장**(first/last) 합성. 마킹/방 동일성 필수(다르면 interp가 morph).
- **디스패치(Step 3, ~6294)**: AV `mode=="interp"` 분기 신설 → `animate_seedance_i2v.py
  --mode interp --image <first.png> --last-frame <last.png>`. 현재 interp는 RF 전용
  (1124행 `elif mode=="interp" and style=="real_footage"`, `_prerender_interp_fills`는 real
  클립에서 프레임 추출) → **AV는 추출 대신 생성**한 두 스틸을 넘기는 별도 경로.
- interp의 i2v→ref 강제(6317) **건너뜀**(interp는 두 스틸이 배경 앵커).
- **duration**: fast model interp = **4s 상한**(animate_seedance 197행; i2v/ref는 5s). AV interp
  컷은 4s 렌더 → 에피소드 길이는 기존 post-trim(AV_CUT_OUTPUT_SECONDS)이 흡수.

### animate_seedance_i2v.py
- interp 모드 이미 지원(`--image`/`--last-frame`, content first_frame+last_frame). **변경 거의 없음.**

### Validators / Giri / 연속성
- 마킹 게이트·perch 연속성([[av_chain_only_continuous]], 펫 순간이동 cap)을 **두 스틸 다** 검증.
- 끝 프레임이 명시되니 연속성 판정에 유리(끝 상태가 데이터로 존재).

### 캡션 레이어 — **무영향**(분리 지점). retime이 clip duration(4s)에 클램프하므로 OK.

## 4. 비용 / 리스크
- 비용: 컷당 스틸 2장(~$0.08) vs 1장(~$0.04). Seedance 호출 수 동일. **한계비용 미미, 통제력 큼.**
- 리스크 A: interp 모션이 i2v forward-motion보다 **뻣뻣**할 수 있다(통제 vs 생동감). ← 싼 1컷
  테스트로 확인할 핵심.
- 리스크 B: 두 스틸이 배경/마킹 불일치 시 morph → 반드시 같은 scene_ref+char ref+canon.
- 리스크 C: 4s 상한(에피소드 길이·페이싱). post-trim으로 흡수되나 확인 필요.

## 5. 롤아웃 = 플래그 게이트 (A/B가 env 1개)
- `AV_INTERP_ARCH=1`(기본 0). off=현행(A), on=첫+끝 interp(B). 한 줄로 A/B 토글 → 같은 말티즈
  컨셉 두 번 렌더 비교. 좋으면 main, 아니면 플래그째 묻고 as-is.

## 6. 검증 순서 (PD 검수 전 싼 단계만)
1. 말티즈 cut3_peak(레오가 누나를 앞발로 툭 — 명확한 A→B 모션)으로 **첫 스틸(레오 접근, 발 듦) +
   끝 스틸(레오 발이 랴니에 닿음)** 2장 생성(~$0.08) → 원근/연속성/방·마킹 동일성 눈으로 확인.
2. (선택) 그 두 스틸로 interp 1컷 렌더(~$0.30) → 모션이 생동감 있는지 확인.
3. 결과 프레임 PD에 보고 → **GO면** 풀 배선 + 같은 컨셉 B안 풀 렌더 → A/B → 커밋 결정.
   **$50 풀 렌더·커밋은 PD 검수 후.**

---

## 7. 판정 (2026-07-01, PD 검수) — ❌ REJECTED, 커밋 안 함

PD가 (b)를 골라 풀 배선(`AV_INTERP_ARCH=1` 게이트) + 말티즈 B안 풀 렌더까지 실행
(`episode_av_20260701_081827.mp4`) → **B가 A보다 나쁨. 아키텍처 폐기, cameraman 워킹트리 revert.**

**PD 지적**: "2d 애니느낌 + 원근감 사라짐 + 배경이 왜 계속 똑같아. 큐시트 잘못 만든거야."

**근본 원인 (구조적, 내 큐시트 실수가 아니라 방법 자체)**:
- interp는 BytePlus 규칙상 배경을 **두 스틸에 구워넣어야** 한다(first/last frame + ref 혼용 불가).
- 그 "구워넣기" = **정밀스틸 합성**(gemini가 clean scene_ref 위에 캐릭터 ref를 붙임). 그런데 이건
  **이미 검증된 지뢰**: `AV_PRECISE_STILL`을 PD가 default OFF 시킨 바로 그 이유 —
  "flat composite had NO depth → pets oversized/pasted-flat (원근 깨짐), illustrated/2D".
  cameraman 6166–6172 주석에 그대로 남아있다. interp는 이 합성에 **의존**하므로 같은 2D/평면화를
  피할 수 없다. i2v(A)는 gpt-image 스틸→Seedance라 원근·실사감을 지킨다(배경 약간 흔들리는 대신).
- 추가로 단일 scene_ref + 프레이밍 락 → **모든 컷 배경·앵글 동일 = 단조/정지 느낌**. 이건 내 선택이라
  고칠 수 있지만, 위 2D 문제는 못 고친다.

**중간에 발견해 고쳤던 것(그래도 최종 판정은 뒤집지 못함)**: 첫/끝 스틸을 독립 생성하면
프레이밍 드리프트/좌우 스왑 → 끝 스틸을 **첫 스틸의 EDIT로 파생**(ref 제거) 하면 연속성은
잡힌다. 즉 "연속성"은 풀렸으나 "실사감/원근"이 근본적으로 안 됨.

**3사 모델 합동 판정 (PD 지시 "openAI·claude·gemini가 함께 확인"; `scripts/_ab_panel.py`)**:
matched 프레임 2×2 몽타주로 A vs B 독립 채점 →
- 실사감: **A 만장일치 3-0** (gpt-4.1·claude·gemini)
- 원근/뎁스: A 2-1 / 샷 다양성: B 2-1 / 종합 승자: **A 2-1** (openai만 B 선호=다이내믹/다양성)
- gemini: "B's pets appear slightly rendered or pasted into the scene, less natural ground contact"
  = PD의 2D/원근 지적을 교차확인. ⇒ 단독판정 아닌 jury로도 **A 우세(단, 만장일치 아님)**. 재사용
  가능한 멀티모델 배심 = 향후 품질판정 신뢰장치.

**결론**: 첫+끝 프레임 interp는 우리 렌더 스택(합성 스틸 의존)에선 **실사감과 상충**한다. A(ref-mode,
Seedance가 끝 상태를 만듦)가 정답. 이 방향 재시도는 **합성이 아닌 다른 배경-베이크 수단**(예: 실사
스틸 기반 img2img, 혹은 Seedance 1.0 Pro Scene Chaining 부활 시)이 생기기 전엔 무의미.
플래그·큐시트·edit-파생 코드는 미커밋 폐기(git이 실험 이력 보존). [[render_cost_and_av_drift]]
