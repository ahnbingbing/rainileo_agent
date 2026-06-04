# 스티커 자산

영상 컷 위에 흩뿌려지는 PNG 스티커들. 카테고리별로 폴더가 나뉘어 있고,
렌더 스크립트가 폴더를 자동 스캔하기 때문에 **PNG를 드롭하기만 하면**
다음 렌더부터 바로 사용돼요. 코드 수정 필요 없음.

## 폴더 구조

```
assets/stickers/
├── hearts/       # 하트 (분홍, 빨강, 골드, 마젠타 등)
├── sparkles/     # 반짝이, 별, 4점 스파클
├── paws/         # 발자국 (분홍, 갈색, 흰색)
├── cute/         # 일반 귀여운 장식 (구름, 꽃, 리본)
├── closing/      # 클로징 컷 전용 (달, 시계, 9시 강조)
└── music/        # 음표 (놀이 컷에서 BGM 강조)
```

## 새 스티커 추가하는 법

1. 받은 스티커 PNG를 적절한 카테고리 폴더에 그냥 떨군다.
   - 파일명은 자유 (영문 권장, 예: `heart_red_glossy.png`)
   - 배경은 **반드시 투명 (alpha)** — RGBA PNG
   - 권장 크기 240×240 또는 그보다 큰 정사각형
   - 흰색 외곽선과 그림자가 이미 베이크되어 있으면 좋음 (없어도 동작)
2. `python3 scripts/render_episode_1.py` 다시 실행.
3. 끝. 새로 받은 스티커가 자동으로 스캐터에 포함됨.

## 카테고리별 사용 시나리오

| 카테고리 | 어디서 쓰이나 |
|----------|---------------|
| `hearts/` | 모든 컷에서 자주 사용 — 따뜻한 분위기 |
| `sparkles/` | 모든 컷, 특히 솔로 인트로와 놀이 컷에서 강조 |
| `paws/` | 단짝 컷, 놀이 컷 — 펫 채널 정체성 |
| `cute/` | 가족 컷, 일반 장식용 |
| `closing/` | 마지막 컷 (저녁 9시 클로징) 전용 |
| `music/` | 영상이나 신나는 컷에서 BGM 강조 |

각 컷의 카테고리 조합은 `scripts/render_episode_1.py`의 `CUTS` 리스트
안 `StickerPack(["categories"], count=N)` 인자로 정해져요. 더 화려하게
만들고 싶으면 `count`를 6 → 8 같은 식으로 올리면 됨.

## 솔로 인트로 라벨 스티커

컷 1, 2의 큰 라벨 스티커("랴니" 핑크 하트 / "레오" 골드 하트)는
`hearts/heart_pink.png`와 `hearts/heart_gold.png`를 자동으로 찾아서 써요.
다른 디자인의 하트로 바꾸고 싶으면:

- 옵션 A: `heart_pink.png` / `heart_gold.png` 파일을 직접 교체 (가장 간단)
- 옵션 B: `scripts/render_episode_1.py`의 `_PINK_HEART = find_sticker("hearts", "pink")` 부분에서 부분 일치 키워드 변경

## 권장 다운로드 처

(라이선스가 영상 소셜미디어 사용을 허용하는지 꼭 확인할 것)

- **Flaticon Premium** (월 $10 구독) — 카테고리별로 정돈된 스티커 팩 다수
- **Freepik** — 무료/프리미엄 혼합. PNG 스티커 검색 시 `transparent` 필터
- **Iconfinder** — 개별 구매 또는 구독
- **OpenMoji** (https://openmoji.org) — CC BY-SA 4.0, 완전 무료 (출처 표기 필요)
- **Twemoji** (https://github.com/jdecked/twemoji) — CC BY 4.0

라이선스 표기가 필요한 스티커를 쓸 경우 영상 description에 `Stickers: OpenMoji (CC BY-SA 4.0)` 같은 문구를 넣어주세요.

## 현재 스타터 세트 (PIL 자동생성)

`scripts/bootstrap_stickers.py`로 21개 PNG를 자동 생성해 놓은 상태.
프로 스티커로 교체하면 이 자동생성본은 지워도 되고 그냥 두면 풀에 함께 섞여요.
지우고 싶으면:

```bash
# 스타터 세트만 지우고 새로 받은 스티커는 유지
rm assets/stickers/*/heart_*.png
rm assets/stickers/*/sparkle_*.png
rm assets/stickers/*/star_*.png
rm assets/stickers/*/paw_*.png
# (등등)
```

## AI 생성 스티커 (gpt-image-1)

`scripts/generate_stickers_ai.py`로 OpenAI gpt-image-1 모델을 호출해
투명 PNG 스티커를 만들 수 있어요. `.env`에 `OPENAI_API_KEY` 설정 필요.

### 한 카테고리 단발 생성

```bash
python3 scripts/generate_stickers_ai.py --category hearts --count 8 \
  --style "3D puffy glossy heart, kawaii cottagecore, thick white outline, soft drop shadow"
```

### 전체 베이스 라이브러리 한 번에 (~131장, ~$5.24, ~15분)

```bash
bash scripts/generate_all_stickers.sh
```

생성되는 카테고리 — 15개:
hearts, sparkles, paws, cute, closing, music, weather, cozy, food,
faces, bubbles, labels, ryani_face, leo_face, rianileo

### 색감 테마 (--color-theme)

| 옵션 | 색감 | 용도 |
|------|------|------|
| `all` (기본) | 12색 파스텔 믹스 | 일반 컷, 공통 풀 |
| `ryani` | 핑크/코랄/피치/블러시/체리 | 랴니 솔로 컷, 랴니 라벨 |
| `leo` | 골드/버터/앰버/오렌지/허니 | 레오 솔로 컷, 레오 라벨 |
| `cool` | 블루/라벤더/민트/세이지 | 클로징 (밤 9시) 컷 |

파일명에 테마 자동 태깅: `hearts/hearts_ryani_ai_*.png` vs `hearts/hearts_leo_ai_*.png`.

### 텍스트 라벨 (category=labels, --text)

```bash
python3 scripts/generate_stickers_ai.py --category labels \
  --text "happy birthday,one year together,best day ever" \
  --style "3D puffy glossy pill banner, script font, kawaii cottagecore"
```

영어 짧은 문구가 가장 안정적. 한국어는 모델이 종종 깨뜨림.

## 에이전트 온디맨드 생성 (Concept Card 연동)

Writer Agent가 컨셉 카드 작성 시 `sticker_additions` 배열에 필요한
스티커를 선언하면, Cameraman Agent가 렌더 직전 자동 생성한다.

### Concept Card 예시 일부

```json
{
  "theme": "레오 입양 1주년",
  "sticker_additions": [
    {
      "category": "seasonal_anniversary",
      "count": 6,
      "style": "3D puffy glossy birthday cake with candles, balloons, gift box, party hat, confetti — kawaii cottagecore, thick white outline, soft drop shadow",
      "color_theme": "leo",
      "rationale": "레오 입양 1주년 기념인데 베이스 라이브러리에 케이크/풍선/축하 스티커가 없음. 레오 테마(골드/앰버)로 통일."
    },
    {
      "category": "labels",
      "style": "3D puffy glossy ribbon banner, script font, kawaii cottagecore",
      "text": "one year together,best day ever,welcome home leo",
      "rationale": "기념일 영문 라벨 3개 — 영상 후반 컷에 띄움."
    }
  ]
}
```

### 자동 처리 흐름

```bash
# Writer Agent → 카드 생성 (sticker_additions 포함)
# PD가 슬랙에서 /pd-approve

# Cameraman Agent 또는 수동:
python3 scripts/process_card_stickers.py data/concept_cards/2026-11-15.json

# 생성 완료 후 렌더 진행
python3 scripts/render_episode_N.py
```

`process_card_stickers.py` 옵션:

- `--dry-run` : 생성 안 하고 어떤 호출이 일어날지 미리보기
- `--skip-existing N` : 카테고리 폴더에 AI 파일이 N개 이상 있으면 스킵 (재실행 안전성)

### 카테고리 이름 정책

- 신규 시즌/이벤트 카테고리는 `seasonal_<event>` 형식 권장
  - `seasonal_halloween`, `seasonal_christmas`, `seasonal_birthday_ryani`
- 카테고리 폴더가 없으면 generator가 자동 생성
- 한 번 만들어진 카테고리는 누적되어 다음 동일 이벤트 때 재사용됨
