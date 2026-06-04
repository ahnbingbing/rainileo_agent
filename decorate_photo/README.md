# decorate_photo

룰 기반 웹툰 오버레이 데코레이터. 펫 사진은 **건드리지 않고**, PIL 로 그린 vector 도형들(halo / 하트 / sparkle / 발바닥 / 집중선 / 말풍선 / 리액션 텍스트 / 폭발 캡션) 만 합성함.

```
원본 사진  ──►  오버레이 레이어(PIL ImageDraw)  ──►  alpha 합성  ──►  decorated PNG
                                                              └►  9:16 vertical PNG
```

원본 픽셀은 1%도 안 바뀜.

---

## Quick start

```bash
python3 decorate_photo.py --image path/to/photo.jpg
```

결과:
- `output/decorated.png`           — 원본 비율 그대로
- `output/decorated_vertical.png`  — 1080×1920 (쇼츠 바로 사용)

---

## CLI

| 플래그 | 기본값 | 설명 |
|---|---|---|
| `--image` | required | 입력 사진 경로 |
| `--mood` | `affectionate` | `affectionate / mischievous / cute / surprised / calm / playful` |
| `--mode` | `playful` | `clean / playful / extra_cute` (스티커 밀도 프리셋) |
| `--caption` | sampled | 메인 캡션 직접 지정 (풀에서 안 뽑음) |
| `--reaction` | sampled | 작은 리액션 텍스트 직접 지정 |
| `--caption-pos` | `lower-left` | `lower-left / lower-right / upper-left / upper-right` |
| `--subject` | heuristic | `x0,y0,x1,y1` 픽셀로 펫 bbox 강제 지정 |
| `--face-points` | none | `x,y;x,y` 양 볼 좌표 (지정 시 blush 활성) |
| `--halo-center` | bbox top-center | `x,y` 픽셀로 halo 가 정확히 어디 위에 뜰지 지정 (한 펫 머리 위로 정확히 맞추고 싶을 때) |
| `--seed` | random | 같은 레이아웃 재현용 |
| `--font` | auto | 한글 TTF 경로 강제 지정 |
| `--no-vertical` | – | 9:16 출력 스킵 |
| `--no-text` | – | 캡션/리액션 텍스트 없이 시각 요소만 |
| `--config` | `./style_config.json` | 다른 설정 파일 사용 |
| `--pools` | `./text_pools.json` | 다른 텍스트 풀 사용 |

---

## 한글 폰트

스크립트가 자동으로 찾는 순서:

1. `decorate_photo/fonts/*.{ttf,otf,ttc}` — 여기에 직접 넣을 수 있음 (최우선)
2. 환경변수 `DECORATE_PHOTO_FONT`
3. **macOS**: AppleSDGothicNeo, AppleGothic
4. **Linux**: NanumGothic, NotoSansCJK
5. 없으면 Pillow 기본 (한글 안 보임)

맥 사용 시 보통 1번 또는 3번에서 자동 발견됨.

---

## 튜닝 — "이거 바꾸고 싶다" → "여기 수정"

### 🔼 스티커 더 많이
- `--mode extra_cute` 로 실행, 또는
- `style_config.json` → `modes.<mode>.density.*` 의 숫자 올림

### 🔽 스티커 더 적게
- `--mode clean` 로 실행, 또는
- 해당 mode 의 density 낮춤

### 🗯️ 리액션 텍스트 더 많이
- `style_config.json` → `modes.<mode>.density.small_reaction` 과 `micro_text` 올림
- `text_pools.json → reaction_text.<mood>` / `micro_text.<mood>` 에 단어 추가

### 💬 캡션 톤 바꾸기
- `text_pools.json` → `main_caption.<mood>` 에서 추가/삭제
- 활성 mood 키가 없으면 `default` 풀을 사용함
- 한 컷에 고정 캡션 쓰고 싶으면 CLI 로 `--caption "오늘 너무 귀여움"`

### 🎨 색 팔레트
- `style_config.json` → `palette.*` 의 hex 코드 수정
- 주요 키: `heart_pink`, `halo`, `blush`, `sparkle_yellow`, `outline` 등

### 💗 mood 별 비율 (예: 핑크 줄이고 노랑 늘리기)
- `mood_bias.<mood>.hearts` ↓, `sparkles` ↑
- 최종 스티커 개수 = `density × mood_bias`

### 📍 스티커 위치 — 펫이 가려질 때
- 자동 추정은 "가운데 70% 폭 × 위쪽 20%~85% 높이" 영역을 펫이라고 가정
- 어긋나면 CLI 로 직접 지정:
  ```bash
  --subject 200,300,900,1500
  ```
  (x0,y0,x1,y1 픽셀) 안쪽은 스티커 금지 영역, 바깥쪽(negative space)에만 배치됨

### 😊 blush (양 볼 핑크) 추가
- 자동으로 추정 안 함 (얼굴이 가려질 위험 큼)
- CLI 로 좌표 지정:
  ```bash
  --face-points 380,620;720,620
  ```
- 또는 `style_config.json → face_points` 에 박아둠

### 🅰️ 폰트 크기 조정
- `style_config.json → sizes.caption_font_pct` (메인 캡션, %는 이미지 height 기준)
- `reaction_font_pct` / `micro_font_pct` 도 마찬가지

### 🖍️ 라인 굵기
- `style_config.json → outlines.*` 수정
- 캡션 말풍선 라인 두께는 `bubble_stroke`

---

## Mode 와 Mood 의 차이

- **Mode** = 스티커 **밀도** 프리셋. `clean` 은 sparse, `extra_cute` 는 packed.
- **Mood** = 스티커 **선택** 편향. `affectionate` 는 하트·halo 많이, `playful` 은 발바닥·집중선 많이.

같이 쓰면:
- `--mode extra_cute --mood affectionate` = 최대 하트 폭격 모드
- `--mode clean --mood calm` = 미니멀 halo + sparkle 만
- `--mode playful --mood mischievous` = 발자국 + 집중선 + 작은 효과

---

## 입력 단일 펫 vs 두 펫

이 도구는 펫 *bbox* 만 알면 동작함. 두 펫이 같이 있는 사진이라도 둘을 묶어서 하나의 subject bbox 로 잡고, 스티커는 그 바깥 negative space 에 배치함. **얼굴/몸을 절대 가리지 않음.**

가까이 붙어 있는 모먼트일 때 둘 사이의 강조 표시가 필요하면 — 그건 일부러 안 넣어. 대신 위쪽 빈 공간에 sparkle/하트가 가서 시선이 그쪽으로 이동.

---

## 출력 사용

`output/decorated_vertical.png` 가 1080×1920 으로 바로 쇼츠/Sora i2v 에 사용 가능.

기존 `animate_hero.py` 와 조합:
```bash
python3 decorate_photo.py --image input.jpg --seed 42
python3 ../scripts/animate_hero.py --image decorate_photo/output/decorated_vertical.png
```
