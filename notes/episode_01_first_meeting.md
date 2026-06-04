# Episode 01 — 첫 만남 (Ryani × Leo)

**Published:** 2026-05-19 (YouTube Shorts)
**Format:** 9:16 vertical, ~20s, KO+EN burned-in captions, BGM faded in/out

랴니(검정 프렌치 불독)와 레오(주황 태비)의 첫 인사·놀이·단짝 선언. 7-clip
sora 베이스라인을 5-cut Veo 에피소드로 재구성한 첫 완성작.

---

## 1. Narrative arc (5 cuts, ~4s each)

| # | tag | KO caption | EN caption | beat |
| - | --- | ---------- | ---------- | ---- |
| 1 | `cut1_ryani_hook`    | 안녕! 나는 랴니예요    | Hi! I'm Ryani               | hook — 랴니 정면 인사 |
| 2 | `cut2_leo_intro`     | 그리고 나는 레오예요   | And I'm Leo                 | 레오 등장 |
| 3 | `cut3_together_play` | 같이 놀 때가 제일 좋아 | Playing together is the best | 둘이 노는 장면 |
| 4 | `cut4_together_warm` | 벌써 단짝이 됐어요    | Best buds already            | bonding shot |
| 5 | `cut5_closer`        | 이제 자주자주 만나요!  | See you often from now on!   | closer / outro |

원본은 sora-era 7-clip에 7 캡션이었음 (`CAPTIONS_COPY_PASTE.txt`). Veo로 옮기면서
"10살 차이 / 한 가족이에요" 두 캡션을 드롭. 5컷에서도 narrative arc는 유지됨
(intro → intro → play → bond → outro).

## 2. Pipeline

```
[ photos ] → animate_all_cuts.sh (Veo 3.1 lite i2v)
           → data/output/animated/cutN_*.mp4
[ animated ] → burn_captions.py (drawtext + Pretendard)
            → data/output/animated_captioned/cutN_*.mp4
[ captioned + bgm ] → assemble_episode.py (concat + afade)
                   → data/output/episodes/episode_<ts>.mp4
```

세 단계 모두 단일 명령으로 자동화됨. CapCut 의존성 0.

### 2.1 Animation
- `scripts/animate_all_cuts.sh` (default `GENERATOR="veo"` — 2026-05-15에 flip)
- Generator: Google Veo 3.1 lite preview (`veo-3.1-lite-generate-preview`)
- A/B/C에서 Veo가 sora 베이스라인 대비 안정적 (full-ep validation 통과)
- 멀티 subject(둘 다 등장)에서는 한 subject(랴니)가 정적인 경향 → 수용
  - 자세히는 `notes/sora2_motion_lessons.md` §7 결론

### 2.2 Captions
- 양식: `scripts/prompts/captions_bilingual.json` (manifest 단일 소스)
- 빌드: `scripts/burn_captions.py` — drawtext 두 줄 chain (KO 위, EN 아래)
- 폰트: Pretendard (Bold for KO, Medium for EN) via
  `brew install --cask font-pretendard`
- 위치: `y=h-text_h-{KO=320,EN=235}` (화면 바닥 기준)
- 페이드: 0.3s fade-in @ t=0.25s, hide 0.25s before end

### 2.3 Assembly
- `scripts/assemble_episode.py` — ffmpeg concat 필터 + afade
- BGM: `assets/bgm/<선택곡>.mp3` (loop+atrim → exact 20s)
- Mix: volume 0.55, fade-in 0.5s, fade-out 2.0s (last cut의 caption disappear와 자연스럽게 겹침)

## 3. Toolchain lessons (이거 다음에도 똑같이 따라할 것)

### 3.1 VLM 검증 — Anthropic → Gemini
`motion_b_vlm.py`를 Claude vision에서 Gemini 2.5 Flash로 migration.
이유:
- 어차피 Veo로 i2v 하니까 GOOGLE_API_KEY 하나로 통일
- 비용 ~10× 절감 ($0.01-0.02 → $0.001-0.003 per call)
- thinking-mode footgun: `thinkingConfig.thinkingBudget: 0` 안 박으면 출력
  토큰 다 먹고 JSON truncate됨 → 반드시 명시

Mac Python urllib SSL: `certifi` 번들 강제. 코드에 fallback 박혀 있음.

### 3.2 ffmpeg / 자막 렌더
경로 매우 험난했음:

1. ASS + libass + subtitles 필터 → Homebrew ffmpeg 8.1.x가 libass 빠진 슬림
   빌드를 default로 깔아둠 → "No such filter: 'subtitles'"
2. drawtext + libfreetype → 같은 슬림 빌드에서 libfreetype도 빠짐
3. Homebrew tap (`homebrew-ffmpeg/ffmpeg/ffmpeg`)으로 빌드 시도 → CLT 너무
   오래돼서 컴파일 실패
4. evermeet.cx static binary → x86_64만 줘서 Apple Silicon Mac에서
   "bad CPU type in executable"
5. Rosetta 2 설치 → evermeet x86_64 ffmpeg 가 Rosetta 번역 통해 동작
6. 폰트 매칭 (한국어 tofu) — libass family-name 매칭 실패 + Mac TTC 핸들링
   문제. drawtext + freetype + fontfile 직접 박는 게 가장 안정적.

**다음에 새 Mac에서 셋업할 때:**
```bash
softwareupdate --install-rosetta --agree-to-license
curl -L -o /tmp/ffmpeg.zip https://evermeet.cx/ffmpeg/getrelease/zip
unzip -o /tmp/ffmpeg.zip -d /opt/homebrew/bin/
chmod +x /opt/homebrew/bin/ffmpeg
xattr -d com.apple.quarantine /opt/homebrew/bin/ffmpeg 2>/dev/null || true
# ffprobe도 똑같이
brew install --cask font-pretendard
```

### 3.3 폰트 선택
- 시도: Apple SD Gothic Neo (Mac 기본) → libass family-match 실패 + Korean tofu
- 시도: NotoSansKR variable font → 작동했지만 weight 약하고 분위기 약함
- 채택: **Pretendard** (Bold for KO, Medium for EN) — 모던/깔끔, 한국 콘텐츠 표준

## 4. Reusable knobs (Episode 02 부터는 이것만 바꾸면 됨)

| 파일 | 무엇이 바뀜 |
| ---- | ----------- |
| `scripts/prompts/captions_bilingual.json` | 컷별 KO/EN 캡션 |
| `data/output/animated/cutN_*.mp4` | 새 cuts (animate_all_cuts.sh 재실행) |
| `assets/bgm/<곡>.mp3` | 곡 교체 |
| `assemble_episode.py --volume / --fadeout` | 믹스 조정 |

스크립트 자체는 더 이상 안 건드림. 컨셉마다 새 manifest + 새 cuts + 새 곡.

## 5. Episode 02 후보 — 부처님 오신날 (2026-05-24, 5일 뒤)

- 톤: lucy_bday 레퍼런스의 vtuber-esque AI sticker/decoration overlay
- 단, 부처님 오신날 맥락에 맞춰 변환:
  - 디스코볼 → 연등 (lotus paper lanterns)
  - 풍선 → 연꽃잎 floating
  - 핑크 네온 → 핑크+골드 (불교 색)
  - 케이크 → 작은 부처상 또는 한국식 떡
  - 댄스 모션 → 평화로운 동작 (꼬리 흔들기, 가벼운 head tilt)
- 사진 셀렉션 진행 중 — 자세한 storyboard는 후속 카드에서 결정
