# Episode 04 — 우리집 평범한 하루 (일상)

**Style:** Concept #3 — 실사 video clips + narrator-style 자막 ((괄호)),
   like @tvhesuinbada4267 channel reference. No AI gen, no stylization.
**Format:** 9:16 vertical, ~20s, KO+EN top-positioned captions, BGM faded.

---

## Concept comparison (channel-wide styles)

| EP | Style | Source | Caption tone |
| -- | ----- | ------ | ------------ |
| 02 Leo판  | AI vtuber neon kawaii (Chinese palace feel) | photos → AI regen → Veo | 1인칭 펫 voice ("안녕!") |
| 03 Ryani판 | 한국 수묵화 + 연등 (sumukhwa) | photos → AI regen → Veo | 단정 시적 ("고요한 봄날") |
| **04 일상**  | **실사 video clips (no AI)** | clips → trim + caption only | **(괄호) narrator 톤** |

EP04 carves out a 3rd lane that's authentically slice-of-life. Same channel
bumpers (theme music + CTA) for brand consistency.

## 1. Structure (20s)

| seg              | length | source                                | mood       |
| ---------------- | ------ | ------------------------------------- | ---------- |
| intro_bumper     | 1.5s   | banner + 채널 theme music             | brand flash |
| cut1_sit_together     | 4s | 2026-03-04 둘이 정면 응시 + 청소로봇   | hook       |
| cut2_leo_munching     | 4s | 2026-05-05 Leo 캣니스 풀 close-up     | Leo 일상   |
| cut3_ryani_sleeping   | 4s | 2026-01-01 Ryani 쿠션에 자기 (trim window 8-12s) | Ryani 일상 |
| cut4_cuddle_together  | 4s | 2026-04-11 둘이 얼굴 맞대고 잠        | closing    |
| outro_bumper     | 2.5s   | banner + 채널 theme + CTA             | brand sign-off |

## 2. Captions (manifest: `scripts/prompts/episode_04_captions.json`)

**Style differs from EP02/03**: top-positioned, marker/handwritten,
parenthetical narrator voice.

| cut tag                    | KO                            | EN                              |
| -------------------------- | ----------------------------- | ------------------------------- |
| `cut1_sit_together`        | (오늘도 둘은 같이 멍...)      | (Sitting together, again)       |
| `cut2_leo_munching`        | (레오는 풀 뜯는 중)           | (Leo on his greens)             |
| `cut3_ryani_sleeping`      | (랴니는... 또 자는 중)        | (Ryani: still asleep)           |
| `cut4_cuddle_together`     | (밤이 되면 결국 같이)         | (Comes night — together again)  |

## 3. Pipeline (no AI, $0 cost)

```
extract_clips_ep04.py:
  for each cut:
    ffmpeg -ss <trim_start> -t <trim_dur> -i <source>
           -vf "scale+pad to 1080x1920, drawtext(KO top), drawtext(EN below)"
           -an  → data/output/animated_captioned/<tag>.mp4

build_bumpers.py:
  (one-time setup, shared across EPs)
  banner + theme music + CTA → assets/branding/{intro,outro}_bumper.mp4

assemble_episode.py:
  4 captioned cuts + intro/outro bumpers + main BGM → final mp4
```

Single command:
```bash
bash scripts/run_episode_04.sh
```

## 4. Caption font

- **Primary**: Nanum Pen Script (`~/Library/Fonts/NanumPen.ttf`)
  - Install: `brew install --cask font-nanum-pen-script`
  - Best handwritten/marker feel for the 일상 narrator tone
- **Fallback**: Pretendard ExtraBold (already installed for EP02/03)

`extract_clips_ep04.py` hunts for Nanum first, falls back automatically.

## 5. Sources

All from `data/assets/clips/2026/`:
- 2026-03-04 105836: `_27997f71.mp4` — 5.2s, 둘 정면 sit
- 2026-05-05 124151: `_1dd62157.mov` — 4.6s, Leo 풀 close-up
- 2026-01-01 204650: `_975b9fa0.mov` — 36s, Ryani 자기 (trim 8-12s)
- 2026-04-11 110559: `_c632b1e8.mov` — 8.1s, cuddle

## 6. TODOs

- [x] storyboard 확정
- [x] manifests 작성 (sources + captions)
- [x] extract_clips_ep04.py + run_episode_04.sh
- [x] sandbox E2E (4 cuts + assemble) 20.0s 검증
- [ ] Mac에서 Nanum Pen Script 폰트 설치
- [ ] `bash scripts/run_episode_04.sh` 실행
- [ ] 결과 검수 + upload
