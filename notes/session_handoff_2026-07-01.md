# Session handoff — 2026-07-01 (새벽)

## ★★ NEXT (PD 지시, 오늘 07:00 이후): AV 풀 아키텍처 구축 + A/B

PD가 취침하며 건 작업. **07:00 이후 Claude 다시 열면 이게 첫 작업이다.**
(CronCreate 예약도 걸었으나 이 런타임은 durable을 무시하고 session-only로만 걸려
세션 종료 시 사라진다 — 그래서 **이 문서가 신뢰 트리거**다.)

### 무엇을
봉준호 콘티 원칙 = "비주얼 큐시트(Director 연출 필드)가 렌더를 끌고, 캡션은 별도 레이어
(1:1 아님)". **1단계(캡션↔큐시트 분리)는 이미 commit `9cb6ec5`로 main 반영**
([[cuesheet_decoupled_from_caption]]). 이번은 **나머지 절반**:

> 현재 표준 AV = "컷당 **첫 프레임 스틸 1장만** 콘티로 생성 → Seedance가 마지막 프레임을
> 모션으로 알아서." → 목표 = "콘티가 각 컷 **첫 프레임 + 마지막 프레임 둘 다** 생성 →
> 그 사이를 보간(interp)." 즉 연출 큐시트가 양 끝 프레임을 다 그린다.

### 어떻게 (blast radius — 본격 전 PD에 설계+범위 보고부터)
- prompt-authoring + pipeline-change-impact 스킬 따를 것(양 레인 정합).
- **Director**(`writer_director.py`/`director_shots.md`): 컷별로 `first_frame` 스틸 프롬프트 +
  `last_frame` 스틸 프롬프트를 둘 다 산출. seedance_mode로 컷별 디스패치.
- **Cameraman**(`cameraman.py`): 두 스틸을 콘티 프롬프트로 생성(`_av_still_compose_prompt`
  계열) 후 Seedance interp(`animate_seedance_i2v.py --mode interp`, content[]=first_frame+
  last_frame)로 보간. 현재 interp는 RF 갭필 전용(`_prerender_interp_fills`) → 표준 AV로 확장.
- **제약**: interp 4s cap. BytePlus 규칙 = first/last frame과 `reference_image` **혼용 불가**
  → 캐릭터 ref는 스틸 생성 단계에서만 쓰고, Seedance interp엔 두 프레임만. [[seedance_modes]].
- ★비용: 풀 AV 렌더 ≈ $50. **본 렌더 전 1컷 스틸(≈$0.04)로 first+last가 원근/연속성
  실제 개선되는지 싸게 검증** 후 진행. 맥 네트워크 불안정(컷당 ~10분, 재생성 잦음).

### A/B 테스트 + 커밋 정책
- **A 베이스라인(현재 방식)** = AV18 말티즈 챌린지. card `665f881f`, 영상 `R_oe8yDFyY8`
  (7/1 18:00 KST 예약), 컨셉 `data/tmp/av18_maltese_concept.json`,
  완성본 `data/output/episodes/episode_av_20260701_011932.mp4`.
- 풀 아키텍처 완성 후 **같은 말티즈 컨셉을 B안으로 재렌더** → A vs B 프레임/완성본 비교.
- **B가 더 좋으면** → 브랜치→main FF 커밋. **아니면** → 아키텍처 변경 **커밋하지 말고
  as-is 유지** + 결과 보고. (PD: 검증된 것만 신뢰성 main에 — GCP 이관 대비.)
- 완성 mp4 SendUserFile 즉시 전달. 툴콜 텍스트 누출 금지.

---

## 이번 세션 SHIPPED (commit `9cb6ec5`, main)

`fix(av): decouple visual cue-sheet from captions; preserve wink line-break`

1. **큐시트↔캡션 분리** (`cameraman.py`): young-ref 셀렉터가 컷 텍스트 뭉치에 섞인
   `captions`를 읽어 캡션의 "2015"를 2015년 강아지 클립으로 오인 → puppy ref를 끼웠다
   (말티즈 "나 2015년생인데~" cut1이 애기랴니로 나온 근본원인). 이제 비주얼 경로는 캡션을
   안 읽고 **큐시트 필드(`subject_era` + Director 비주얼 산문)만** 읽는다.
2. **출생연도 제외**: "OO년생"(present age 선언)은 과거-footage 연도 매칭에서 제외
   (`(?<!\d)(2015|2016|2017)(?!\s*년?\s*생)`).
3. **Ryani-solo 스코핑**: 모호한 아기/강아지/연도 키워드는 **Ryani-solo 컷에서만** young
   판정. 투샷에서 "아기 레오" 묘사가 옆의 시니어 Ryani를 puppy로 만들던 잠재버그 차단.
   both/Leo 컷은 구조 신호(Ryani-stamped subject_era, 7년+ gap)만.
4. **burn `\n` 보존** (`burn_captions.py`): 이번 세션 추가한 `_strip_unrenderable`가
   `" ".join(text.split())`로 `\n`까지 공백 처리 → AV는 ko 한 칸에 `ko\nen` 스택이라
   윙크 "오늘도 햅삐 / Happy as ever"가 한 줄로 붙는 회귀. → 라인별 정규화 후 `\n` 재결합.

검증: AV18 재렌더 — cut1 present 시니어 랴니(회색 주둥이), cut3 시니어 누나 vs 아기 레오가
앞발로 툭→쿨 무시(세대 대비+페이오프), 윙크 2줄. 라이브 API로 `R_oe8yDFyY8` private+18:00
예약 확인.

(이전 세션 trend-feed 학습 fix는 `13a5d1f`로 이미 main. 월드컵 veto+삭제.)

## 7/1 배치 슬롯 (publishAt KST)
- 12:30 `85b8c4a7` → `Bb7SJKkvbZw` (published)
- **18:00 `665f881f` → `R_oe8yDFyY8` (말티즈 챌린지, 이 세션 재업로드)**
- 21:00 `9f78dcd2` → `1HGF4KKDyJs` (published)
- (08:00 슬롯은 publishAt 06-30T23:00Z 라 위 쿼리 밖)

## 상시 주의
- 맥 네트워크 불안정 = 렌더 hang/재생성·봇 wedge 근본원인 → GCP 이관(별도 세션 LOCKED).
- 커밋은 PD 요청 시에만. 브랜치 → `git push origin <branch>:main` FF → 로컬 main 동기화.
- 일회성 `scripts/_*.py` 렌더 스크립트는 main 커밋 제외(노이즈).
