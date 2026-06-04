# Episode 03 — 부처님 오신날 (Ryani판)

**Publish target:** 2026-05-24 (Buddha's Birthday, KST)
**Format:** 9:16 vertical, ~20s, KO+EN burned-in captions
**Tone:** 한국 전통 수묵화 (sumukhwa) + 한지 + 떠다니는 연등.
EP02 Leo판이 중국 자금성/럭셔리 핑크 vtuber 톤이었다면 이 ep는 한국 부처님
오신날 정통 정서로 분기. 단아함, 빈 여백, 절제된 색감.

---

## 1. Structure (20s)

| seg              | length | source                                   | mood       |
| ---------------- | ------ | ---------------------------------------- | ---------- |
| intro_bumper     | 1.5s   | banner_card + 채널 theme music           | brand flash |
| cut1_ryani_greeting    | 4s | 1/1_141833 Ryani 카페 클로즈업          | 인사       |
| cut2_ryani_contemplate | 4s | 2/7_100934 Ryani 프로필 명상            | 고요       |
| cut3_ryani_with_leo    | 4s | 5/6_203421 Ryani+Leo 코 부비기          | 함께       |
| cut4_ryani_peaceful    | 4s | 1/1_150036 Ryani 빈티지 의자            | 평화 + holiday wish |
| outro_bumper     | 2.5s   | banner_card + 채널 theme music           | brand sign-off |

Bumper의 채널 theme music: **whistling-bright-kids-positive-claps** 권장
(휘파람 + 박수 + 키즈 — 펫 채널 brand에 완벽). 모든 에피소드에서 동일 사용
→ 채널 identity 일관성. 메인 BGM은 EP별로 다름 (Ryani판은 ambient flute 추천).

## 2. Captions (manifest: `scripts/prompts/episode_03_captions.json`)

| cut tag                    | KO                    | EN                       |
| -------------------------- | --------------------- | ------------------------ |
| `cut1_ryani_greeting`      | 안녕하세요, 랴니예요  | Hello, I'm Ryani         |
| `cut2_ryani_contemplate`   | 고요한 봄날           | A quiet spring day       |
| `cut3_ryani_with_leo`      | 함께라서 좋아요       | Better together          |
| `cut4_ryani_peaceful`      | 행복한 부처님 오신날  | A blessed Buddha's Day   |

EP02처럼 outro bumper에 별도 holiday wish 캡션 없음 — cut4 캡션이 메시지
받음. Bumper는 순수 brand visual + theme music.

## 3. Decoration / regen aesthetic (per cut)

전체 톤은 한국 sumi-e — 먹/한지/단정한 색감/빈 여백. Per-cut decoration은
`scripts/prompts/episode_03_regen_prompts.json` 참고. 핵심 통일 element:

- **배경**: 한지 paper-textured wash + 먹 brushstrokes
- **연등**: 1-3개 떠다니는 paper lanterns (warm gold + 다홍 deep red)
- **꽃**: ink-brush 연꽃 또는 매화 가지
- **글씨**: cut4에 calligraphy brush stroke silhouette (legible 아닌 gesture)
- **펫**: 실사 그대로 — Ryani 흰 마킹 (입가/가슴/발) 보존 필수
- **빈 여백**: 화면의 30-40% 정도 비워두는 한국 미학

## 4. Source photo prep

모두 JPEG (HEIC + 원래 JPEG 혼재). 전부 9:16 portrait 변환:

| cut | source                                                   | rotate | crop |
| --- | -------------------------------------------------------- | ------ | ---- |
| 1   | data/tmp/photos_2026_jpeg/med_2026_01_01_141833_*.jpg   | EXIF auto | center 9:16 |
| 2   | data/tmp/photos_2026_jpeg/med_2026_02_07_100934_*.jpeg  | EXIF auto | center 9:16 |
| 3   | data/tmp/photos_2026_jpeg/med_2026_05_06_203421_*.jpeg  | EXIF (orient=6) | center 9:16 |
| 4   | data/tmp/photos_2026_jpeg/med_2026_01_01_150036_*.jpg   | EXIF auto | center 9:16 |

cut3는 EP02랑 동일 photo source (5/6_203421) — Ryani판은 수묵화로,
Leo판은 핑크 vtuber로 같은 사진이 두 톤으로 사용됨. 의도된 분기.

## 5. Pipeline

EP02랑 동일한 단계 — manifest paths만 episode_03_*로 변경:

```
preprocess_for_i2v.py  (manifest=episode_03_sources.json)
  → data/tmp/episode_03_input/cut*.jpg

regen_vtuber_style.py  (prompts=episode_03_regen_prompts.json)
  → data/tmp/episode_03_regen/cut*.png

animate_episode_03.sh  (4 cuts, all Veo 3.1 lite gentle motion)
  → data/output/animated/cut*.mp4

build_bumpers.py  (--intro-music + --outro-music)
  → assets/branding/{intro,outro}_bumper.mp4  (재사용)

burn_captions.py  (manifest=episode_03_captions.json)
  → data/output/animated_captioned/cut*.mp4

assemble_episode.py  (--captions episode_03 + bumpers + main BGM)
  → data/output/episodes/episode_03_ryani_<ts>.mp4
```

전체를 한 번에 돌리고 싶으면:
```bash
bash scripts/run_episode_03.sh
```

## 6. Motion philosophy (수묵화 톤)

EP02 cut3 (dance party)는 dynamic motion이 필요해서 Vertex Veo 3.0 standard
까지 갔지만, Ryani판은 **모든 cut이 gentle motion 적합** → Veo 3.1 lite로
충분 (cost ~$2.40 total).

핵심 모션:
- Ryani: 천천히 깜빡, 귀 살짝 움직임, 가벼운 호흡
- Leo (cut3에만): 꼬리 swish + 코 부비기
- 환경: 연등 부드럽게 떠다님, 먹 mist 솔솔, 가끔 꽃잎 흩날림

## 7. TODOs

- [x] storyboard 확정
- [x] sources / captions / regen prompts manifest 작성
- [x] preprocess (sandbox에서 미리)
- [x] animate_episode_03.sh + run_episode_03.sh 작성
- [ ] AI regen on Mac (~$0.16, 4 cuts)
- [ ] Veo i2v on Mac (~$2.40)
- [ ] Build bumpers w/ theme music
- [ ] Burn captions
- [ ] Final assemble + verify
- [ ] Upload to YouTube Shorts as EP03
