# Session handoff — 2026-07-03 (새벽)

7/3 아침 배치 스팟체크 → PD 리뷰 3건 증상수정 + 파이프라인 근본 픽스 2건(main 반영).

## ★ NEXT (다음 세션 첫 작업)
1. **7/4 00:00 배치 스팟체크 — 새 게이트 첫 적용 확인.** 이번에 박은 ①결정론 컨셉-재탕 게이트
   (`RF_DEDUP_GATE`)와 ②Giri 지어낸-이동 cap이 첫 배치에서 실제로 무는지 확인
   ([[pipeline_dedup_and_caption_motion_gates]]). RF가 재탕·지어낸-액션 없이 나오는지 눈으로.
2. **AV21 Shorts 표지 (rGZf-Oj-U9s).** 코드로 못 고치는 YouTube Shorts 제약 — 그리드/검색엔 정면
   반영됐지만 **Shorts 피드 표지 프레임은 폰 앱에서만 수동 지정** 가능(⋮→수정→표지). PD가 폰에서
   해줘야 하는 잔여 1건. 우리 API 업로드는 매 Short마다 이걸 못 정함(구조적, 전 Short 재발).

## 7/3 배치 (00:00 자동, 4/4 성공 → PD 리뷰 후 2편 교체)
- 08:00 AV `feOgLscJxJo` — 밥투정 챌린지. **Giri 7/10**(캡션-동작 미스매치: 엉덩이실룩/꼬리잡기/
  츄르반짝 프레임에 안 보임 + 엔딩 줄임말). 리트라이 소진, PD 검토대상이었으나 **미조치로 통과됨**.
- 12:30 RF `c0vRUebshMA`→**`AyT3XeWCP_g` 교체** — 6/25 b696xSvikRA(단단한 간식) 재탕이라 PD 지적.
  "여름 흙길 원정대"(2026-06 야외 클립 4개 직접 핀, Giri 9/10)로 재렌더. 제목·태그·DB theme 동기화.
- 18:00 AV `xyVLN9KIh2c` — 입장 바꿔놀이(역할교체). Giri 9/10, 이상無.
- 21:00 RF `0TWavqrsac8`→**`Pyqy8WPgXAM` 교체** — 노곤히 뻗은 레오인데 캡션이 "일어나 탐정 탐험"
  지어냄. "햇살에 녹는 게으른 레오"로 recaption. publishAt 보존, 옛 영상 2개 삭제확인.

## SHIPPED (durable, main `97b1478`)
[[pipeline_dedup_and_caption_motion_gates]] — 두 근본 픽스. **증상수정(에피소드)만 하지 말고
파이프라인 게이트를 고치라는 PD 요청**으로 진행.

1. **결정론 컨셉-재탕 게이트.** 기존 dedup은 exclude_concepts(형제슬롯+최근14일 업로드)를 Writer
   프롬프트에 "다르게 해라" 텍스트로만 주입(LLM-조언) + `_is_redundant_vs_batch`도 LLM → RF1230이
   6일 전 동일 premise 재탕인데 통과(footage 달라 freshness도 통과, 과거-업로드 컨셉 체크 자체가
   없었음). 픽스=`concept_brainstorm._concept_lexical_collision`(핵심 명사 ≥2 겹침, 조사strip+
   단단/딱딱 synonym+펫명/계절 stoplist, **결정론**). 배선 **양 레인**: `best()` 결정론 스킵
   (AV line728 + RF-brainstorm line2559), RF 싱글패스는 producer `[재탕검증]` 게이트
   (`RF_DEDUP_GATE=1`, 위치/시점검증과 동일 재제안 패턴). launch가 양 레인 exclude에 14일 업로드 시드.
2. **Giri 지어낸-이동/진행/장면전환 cap ≤5.** CHECK 0의 "주장 kick 보여야 ≤5"는 단발 액션만 →
   RF21의 "일어섬→탐정 출동" 지어낸 아크를 못 잡음. reviewer.py CHECK 0에 "CLAIMED MOTION/
   PROGRESSION/SCENE-CHANGE MUST HAPPEN"(일어남·떠남·도착·장소이동·이벤트를 프레임서 못 보면
   fabricated arc=CHECK0 거짓) 통합. 양 레인 공통.

**회귀검증**(실제 Giri 재실행): 원본 RF21(지어낸 캡션)=점수5/수정필요/불일치3(전엔 자동통과),
흙길 원정대(실제 이동)=점수9/업로드/불일치0(오탐無).

## 이번 세션 배운 것 / 주의
- **썸네일 "안 바뀐다"의 진실**: 처음에 나는 maxres 키 존재만 보고 "캐시"라 단정 → 오판.
  진짜는 **YouTube Shorts 커스텀 썸네일이 피드 표지에 안 먹는 구조적 제약**. origin(그리드/검색)엔
  정상 반영, Shorts 피드 표지는 폰 앱 수동만. API `thumbnails.set`으론 표지 프레임 못 정함.
- **증상수정 ≠ 재발방지**: PD가 "원인 수정한 거지?"로 재확인 → 에피소드 고침(✓)과 게이트 고침(✗)을
  명확히 구분해 보고해야 함. 이번엔 PD 요청으로 게이트까지 갔음.
- **컨셉 자동피커가 카페로 수렴**: 신선 footage가 집-휴식/카페/산책에 몰려 자동 RF가 카페 재탕+Giri
  0점 반복 → 클립 직접 핀(use_brain=False, cut에 asset_id+captions[])이 우회 정답. GCS서 클립
  받아(`icloud/gcs.download_to`) 프레임 눈으로 확인 후 캡션 작성.
- 맥 네트워크 불안정 여전(렌더 재시도 잦음). GCP 이관은 별도 세션 LOCKED.

## 미결(선택)
- 08:00 AV `feOgLscJxJo`(7/10 캡션-동작 미스매치)는 미조치 통과 — 공개 지남. 필요시 recaption.
- 커밋 정책: 이번 durable은 PD 승인 후 main 반영. 일회성 `scripts/_*.py`(렌더/검증)는 미커밋(노이즈).
