# Episode 02 — 부처님 오신날 (Buddha's Birthday)

**Publish target:** 2026-05-24 (Buddha's Birthday, KST)
**Format:** 9:16 vertical, ~20s, KO+EN burned-in captions, BGM faded in/out
**Tone:** 동양적 매력 + 신나고 힙 하이브리드 (lucy_bday 레퍼런스의 vtuber-esque
overlay를 부처님 오신날 톤으로 변환: 디스코볼→연등, 풍선→연꽃잎, 핑크→핑크+골드)

---

## 1. Structure (20s total)

| seg              | length | source                                    | motion                                    |
| ---------------- | ------ | ----------------------------------------- | ----------------------------------------- |
| intro_bumper     | 1.5s   | `assets/branding/channel_banner.png`      | static + sparkle overlay (ffmpeg, no Veo) |
| cut1_peony_greeting | 4s  | `2026-05-06_20:31:16` Leo + peony 부케    | Veo i2v                                   |
| cut2_sunbathe_meditate | 4s | `2026-04-18_16:36:37` Leo 일광욕(plant) | Veo i2v                                   |
| cut3_dance_party | 4s     | `2026-05-06_15:39:47` 둘 wrestle          | Veo i2v                                   |
| cut4_cuddle_peace | 4s    | `2026-04-20_12:36:13` 둘 cuddle           | Veo i2v                                   |
| outro_bumper     | 2.5s   | `banner_card_1080x1920.png`               | slow zoom + 연꽃 sparkle (ffmpeg)         |

Bumpers는 pre-rendered해서 `assets/branding/intro_bumper.mp4` /
`outro_bumper.mp4`로 저장 → 다음 에피소드에서 그대로 재사용.

## 2. Captions (manifest: `scripts/prompts/episode_02_captions.json`)

| cut tag                    | KO                          | EN                          |
| -------------------------- | --------------------------- | --------------------------- |
| `cut1_peony_greeting`      | 안녕! 부처님 오신날이에요   | Hi! It's Buddha's Day       |
| `cut2_sunbathe_meditate`   | 햇살 받으며 명상 중         | Sunbathing & meditating     |
| `cut3_dance_party`         | 근데 가끔은 신나게          | But sometimes we party      |
| `cut4_cuddle_peace`        | 단짝이랑은 평화로워         | Peaceful with my bestie     |
| `_outro_caption` (overlay) | 행복한 부처님 오신날!       | Have a blessed Buddha's Day!|

## 3. Source photo prep

모두 landscape 촬영이라 portrait 9:16으로 변환 필요:

| cut | source file                                             | rotate | crop strategy |
| --- | ------------------------------------------------------- | ------ | ------------- |
| 1   | `med_2026_05_06_203116_icloud_d3c5c667.jpeg`           | 90° CW | center-crop, peony 부케 + Leo 얼굴 보존 |
| 2   | `med_2026_04_18_163637_icloud_a61bf9ca.jpeg`           | 90° CW | Leo 전신 + plant 보존 |
| 3   | `med_2026_05_06_153947_icloud_32f780a4.jpeg`           | 90° CW | 둘 다 보존 (Leo 발 + Ryani 얼굴) |
| 4   | `med_2026_04_20_123613_icloud_16ea2825.jpeg`           | 90° CW | 둘 머리 + cuddle 자세 |

## 4. Decoration / vtuber overlay plan (per cut)

| cut | overlay elements |
| --- | ---------------- |
| 1   | 작은 연꽃잎 floating, 핑크+골드 sparkle, 살짝 후광(halo), "안녕!" 말풍선 |
| 2   | 활짝 핀 꽃 sparkle, 햇살 광선, 흩어지는 꽃잎, 차분한 lotus 패턴 |
| 3   | 댄스 음표 ♪♬, 한국 색지 confetti, 폭죽 ✨, "댄스" emoji, 연꽃볼 (디스코볼 대신) |
| 4   | 잔잔한 연등 lanterns 배경, 별 sparkle, 평화 vibe — minimal |
| outro | 큰 네온 "행복한 부처님 오신날!" 사인, 연꽃 케이크/떡 자리, 한복색 ribbon (보라/파랑/노랑) |

Decoration 처리는 두 옵션:
- A. AI image regen (decorate_photo/ 모듈 활용) — 사진 자체를 vtuber-styled로 한 번 재생성한 다음 i2v
- B. ffmpeg overlay PNG (decoration PNG 따로 만들어서 overlay 필터로 합성) — 사진 자체는 사실적으로 두고 위에 스티커만

Lucy_bday 레퍼런스는 A 스타일 (사진 자체가 합성됨). 그쪽으로 가는 게 톤 일치.

## 5. Pet identity (animation prompts 작성 시 반드시 포함)

- **Leo**: orange tabby cat, juvenile/young adult, ~3kg, gold-amber eyes
- **Ryani**: black French bulldog, small (~7kg), **no tail (Frenchie)**, brindle hints, blue collar in some shots
- Multi-subject 컷 (cut3, cut4)에서는 Ryani가 정적이어도 OK — Episode 01 학습
  (`notes/sora2_motion_lessons.md` §7)

## 6. BGM 선택

Episode 01에서 쓴 곡과 겹치지 않게. 후보 (이미 다운로드돼 있는 것 중):
- `assets/bgm/natureseye-always-chanting-intro-outro-10373.mp3` — chanting 톤,
  부처님 오신날 직접 fit (단 분위기 약간 over-spiritual일 수 있음)
- 차분 + 동양 톤 다른 BGM은 추가 셀렉션 라운드에서 결정

## 7. TODOs (Episode 02 production)

- [x] storyboard 확정
- [x] captions manifest 작성 (`scripts/prompts/episode_02_captions.json`)
- [ ] source photos rotate + 9:16 crop preprocessing
- [ ] decorate_photo로 vtuber-style regen (4 cuts) — 선택사항
- [ ] animation prompts 작성 (cut1-4) — `scripts/prompts/animate_v3_cut*.txt`
- [ ] Veo i2v 실행 (4 cuts)
- [ ] burn_captions.py에 `--manifest` 인자 추가 (episode별 분리)
- [ ] intro/outro bumper.mp4 pre-render
- [ ] assemble_episode.py에 bumper 자동 prepend/append 기능 추가
- [ ] BGM 셀렉트 + 전체 어셈블
- [ ] 검수 + 업로드
