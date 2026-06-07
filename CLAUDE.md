# CLAUDE.md — Ryani × Leo YouTube Shorts Pipeline

> **Start here.** This file is the single source of truth for the channel
> production pipeline. Any agent/CLI working on this repo should read this
> first, then dive into the per-episode notes/ docs as needed.

> **⛔ OUTPUT RULE (절대):** NEVER write tool-call syntax as visible text —
> no `<invoke>`, `<parameter>`, leading `count`, or XML function blocks in
> the reply body. Tool calls go ONLY through the real function-call channel.
> Reply text = plain prose only. When an episode renders, immediately
> deliver the mp4 via SendUserFile (don't wait to be asked). PD has been
> repeatedly frustrated by leaked tool-call text — this is a hard failure.

---

## The channel

- **Pets**:
  - **Leo** — orange tabby cat, juvenile/young adult, ~3kg, gold-amber eyes
  - **Ryani** — small black French bulldog, **no tail** (brachycephalic
    Frenchie). Mostly black BUT has **white markings on chin/muzzle, chest,
    and toes/paws** that MUST be preserved in any regen.
- **Channel handle**: `@ryani_n_loe`
- **Format**: YouTube Shorts, 9:16 vertical, ~20s, KO + EN burned-in captions

## Three content-style lanes

The channel runs three parallel aesthetic lanes. **Pick one per episode**
before writing any code.

| # | Style | EP example | Source | Look | Caption tone |
|---|-------|------------|--------|------|--------------|
| **1** | **AI vtuber** (김햄찌 스타일 포토리얼리스틱) | **EP01-03** | photos(포즈 레퍼런스) → GPT images.edit (캐릭터 생성) → Veo i2v | 실사에 가까운 캐릭터 일러스트, 컨셉별 스타일 변경 | 컨셉에 따라 유동적 |
| **2** | **일상 실사** (documentary) | **EP04** 일상 | real video clips → trim + caption (NO AI) | unchanged real footage, narrator captions | (괄호) 관찰자 narrator ("랴니는 쌔근쌔근 자네요") |

## Pipeline architecture by lane

Stage-by-stage script map. Each row = one stage. Cells marked with the
lane number are scripts used by that lane.

| Stage | Script | Lane 1 (AI vtuber) | Lane 2 (sticker) | Lane 3 (일상) |
|-------|--------|:---:|:---:|:---:|
| Photo preprocess (rotate, crop, 720×1280) | `scripts/preprocess_for_i2v.py` | ✓ | ✓ | – |
| AI regen (vtuber/sumukhwa) | `scripts/regen_vtuber_style.py` | ✓ | – | – |
| End-frame regen (abandoned — see Gotchas) | `scripts/regen_end_frame.py` | (off) | – | – |
| AI regen (cartoon style) | `scripts/regen_vtuber_style.py` (cartoon prompt) | – | ✓ | – |
| Veo i2v (Gemini API, lite) | `scripts/animate_hero_veo3.py` | ✓ | ✓ | – |
| Veo i2v (Vertex AI, standard — richer motion for dance-y cuts) | `scripts/animate_hero_veo3_vertex.py` | ✓ (EP02 cut3 only) | – | – |
| Video clip extract + caption (single ffmpeg pass) | `scripts/extract_clips_ep04.py` | – | – | ✓ |
| Caption burn (bottom KO/EN) | `scripts/burn_captions.py` | ✓ | ✓ | – |
| Bumper render (intro+outro w/ theme music + CTA) | `scripts/build_bumpers.py` | shared | shared | shared |
| Final assemble (concat + BGM) | `scripts/assemble_episode.py` | shared | shared | shared |

Per-episode bash wrappers tie it together:

| EP | Wrapper |
|----|---------|
| 01 | `scripts/animate_all_cuts.sh` + manual `burn_captions.py` + `assemble_episode.py` |
| 02 | `scripts/animate_episode_02.sh` (auto-routes cut3 to Vertex) |
| 03 | `scripts/animate_episode_03.sh` (all Veo lite gentle) + `scripts/run_episode_03.sh` (master) |
| 04 | `scripts/run_episode_04.sh` (master — no Veo, just ffmpeg) |

## Concept proposal stack (Writer → Director, Opus 4.7)

The daily `python -m agents.producer` pipeline used to call Sonnet 4.6 once
to produce the storyboard + veo_prompts + captions in a single shot. That was
the bottleneck — improving the i2v engine (e.g. Seedance) couldn't fix poor
upstream writing. As of 2026-05-30 the stack is split:

```
agents/producer.py:propose_concepts
  └→ agents/writer_director.py:propose_concepts_v2
       ├ Writer (Opus 4.7, 3-pass)
       │   draft (writer_story.md) → self-critique (writer_critique.md)
       │     → revise (writer_revise.md)
       │   Story-only output: beats, captions (TV동물농장/세나개 톤),
       │   transition_in (sensory bridges), character functional roles.
       │   NO veo_prompt at this stage.
       └ Director (Opus 4.7, 1-pass, director_shots.md)
           Adds shot_size, camera_move, angle, lighting, action_beats.
           Assembles veo_prompt (t2v) OR regen_prompt + motion_prompt (i2v).
           Embeds character_sheets + sora2_motion_lessons +
           proven_motion_prompts.json as static system context.
```

Few-shot exemplars: past concepts with Giri review score ≥ 8 are pulled from
`retry_log` joined with `cards`/`daily_proposals` and injected into the
Writer's draft input (up to 2, configurable).

Prompt caching: system prompts use `cache_control: ephemeral` — 5-min TTL,
~90% input cost reduction on retry-loop re-renders. Director system is
~25KB (refs included), so this matters.

Env knobs:
- `USE_WRITER_DIRECTOR=0` — disable the new flow, force legacy single-pass
  (`producer_propose.md` + `WRITER_MODEL_LEGACY`, default Sonnet 4.6)
- `WRITER_MODEL` / `DIRECTOR_MODEL` — default `claude-opus-4-7`
- `GIRI_FEWSHOT_MIN` (default 8.0) / `GIRI_FEWSHOT_N` (default 2)

On exception in the new flow, `propose_concepts` auto-falls-back to legacy.

Direct CLI for debugging:
```
.venv/bin/python -m agents.writer_director --date 2026-06-15 \
    --style ai_vtuber --out /tmp/concept.json
```

When story/연출 quality complaints come up, the bottleneck is HERE — not the
i2v scripts or BGM choice. Edit prompts in `agents/prompts/writer_*.md` /
`director_shots.md` first.

## Launch month system (PD 2026-06-07 — LIVE)

First month = explore-heavy **4 videos/day A/B** (av vs rf). Full design in
`notes/first_month_plan.md`; operations in README "런칭 자동화".

- **`agents/launch.py`** — `day_assignments()` = 2 av + 2 rf/day on a lane×timeslot
  **Latin square** (timeslots 08:00/12:30/18:00/21:00 KST, rotated daily so each
  lane×slot cell is balanced). `launch_pipeline()` proposes per lane → renders
  (existing per-lane Giri retry gates) → auto-schedules passing episodes public at
  their slot → leaves failed slots empty (no junk). `publish_at_for()` rolls a
  passed slot to next day.
- **launchd `com.rianileo.launch`** runs **00:00** producing TOMORROW's batch →
  a full day for PD spot-check/veto/answer before it goes public. Pause:
  `launchctl unload ~/Library/LaunchAgents/com.rianileo.launch.plist`.
- **Review = Giri-gate + PD spot-check** (no blocking per-episode approval). 4 mp4s
  post to a Slack thread; PD `/veto`s bad ones (`youtube.upload.veto_video` →
  private/delete + `uploaded=0` so cooldown/arc drop it).
- **Measurement**: `youtube/analytics.py` (48h views + retention) → `video_performance`
  → `agents/bandit.py` (population reward + 3-level Thompson: marginal lane /
  marginal timeslot / lane×timeslot arm + P(best) + `choose_*` for next month).
  launchd `bandit-collect` (06:30) + `bandit-report` (Mon 10:00 → Slack).
- Env: `ARC_ENABLED=1`, `LAUNCH_START_DATE=YYYY-MM-DD` (Day1 = first public day),
  `YOUTUBE_AUTO_UPLOAD=1`. cards gain `youtube_video_id` / `youtube_publish_at`.

### Concept directive priority (`agents/arc.py:next_directive` — what each episode does)

```
1. PD /concept <date> <text>   (pd_concept_directives table) — HIGHEST, even if ARC off
       ↓ none
2. Launch intro overlay (_launch_intro_directive, by LAUNCH_START_DATE)
   Day1 both-greet self-intro ("안녕! 나는 ~예요", rf+av; rf = real clip + 1st-person
   caption) / Day2 Leo solo / Day3 Ryani solo — past⇄present memory-lane + interaction
       ↓ not launch week
3. arc season-plan LLM directive (rolling ~1mo: season/holiday/trend/monthly re-intro)
```
`/concept`-free days run on arc by default. Character-fact + asset fidelity is
enforced at **every** layer (never invent).

## Character knowledge system v2 (anti-hallucination, 3 layers)

Born from the "랴니 물 공포" hallucination (the arc invented a false trait). Traits
are grounded, never invented — see memory `character_knowledge_v2`.

1. **VLM auto profile** (`agents/pet_profile.py`) — aggregates VLM tags per pet
   (activity / pet_intent / looking_at / micro_behaviors / props). Observed,
   supporting.
2. **PD facts** (`arc.CHARACTER_FACTS` + `character_sheets.md`) — what footage can't
   infer / corrections. Authoritative. ★ Ryani = water-MANIAC (barks at water,
   leaps into fountains), strong swimmer, loves snow/ice-sled; feared water only as
   a 2016 puppy. "물 공포" is FALSE. Leo (cat) avoids water.
3. **Ask-when-unknown** (`agents/knowledge.py` + `character_facts` table) — concept
   stage emits `knowledge_questions` when uncertain → `producer.resolve_knowledge_
   questions` asks PD (week-1 blocking via `/launch` ask_cb / else non-blocking) →
   answer stored permanently + injected. `/knowledge`, `/answer <id> <답>`.

All three inject into the arc plan/directive + rf concept prompt.

## Seedance 2.0 modes (per-cut)

`scripts/animate_seedance_i2v.py` exposes `--mode {i2v|interp|ref}`:

| mode | content[] roles | When |
|---|---|---|
| `i2v` (default) | `first_frame` only | Standard ai_vtuber cut. GPT regen still → Seedance. |
| `interp` | `first_frame` + `last_frame` | real_footage gap-fill. Cameraman extracts anchors from surrounding real clips via ffmpeg. Capped at 4s. |
| `ref` | up to 9 `reference_image` (no first_frame) | Omni Reference — skips GPT still. Used when character drift across cuts is the risk. |

BytePlus API rule: `first_frame`/`last_frame` and `reference_*` **cannot** be
mixed in one call. The legacy `--subject-ref` was a dead flag because of this.

Director picks the mode per cut via `seedance_mode` in its output. Cameraman
dispatches in `_run_i2v_pipeline` (ai_vtuber) and `_prerender_interp_fills`
(real_footage). Ref name resolution lives in `agents/cameraman.py:REF_LIBRARY`
— missing files fall back to `pair` (`assets/character_ref/official_ryani_leo.png`).

**Scene Chaining**: NOT supported in Seedance 2.0 (only 1.0 Pro). Don't bother
asking the API for multi-shot — our episode-level continuity is built via
cut-by-cut concat plus interp gap-fill.

**Audio**: Seedance generates audio with each cut. Our pipeline uses external
BGM and currently does not strip Seedance audio — known follow-up.

## Manifest layout (one set per episode)

| Type | Pattern | Purpose |
|------|---------|---------|
| Sources | `scripts/prompts/episode_NN_sources.json` | cut_tag → source photo/video + (for video) trim_start, trim_dur |
| Captions | `scripts/prompts/episode_NN_captions.json` | cut_tag → {ko, en} OR {scenes: [{start, end, ko, en}, ...]} for narrator-style |
| Regen prompts | `scripts/prompts/episode_NN_regen_prompts.json` | per-cut AI regen prompt (Lane 1 only) |
| Motion prompts | inline in `animate_episode_NN.sh` | Veo i2v text prompts |

**Cut order is derived from the captions manifest's dict iteration order**
(Python 3.7+ preserves insertion order). Keys starting with `_` are metadata
and skipped.

EP01 uses the legacy default name (`scripts/prompts/captions_bilingual.json`).

## Standard file paths

```
data/assets/
  photos/<year>/med_<date>_*.{heic,jpeg,jpg}      ← source photos
  clips/<year>/med_<date>_*.{mov,mp4}             ← source videos
data/tmp/
  photos_2026_jpeg/                               ← HEIC→JPEG (sips) thumbnails
  episode_NN_input/<tag>.jpg                      ← preprocessed 720×1280
  episode_NN_regen/<tag>.png                      ← AI regen output
  ep04_captions/<tag>_s<i>_{ko,en}.txt            ← textfile= sources for drawtext
data/output/
  decorated/<tag>.png                             ← decorate_photo output (Lane 2)
  animated/<tag>.mp4                              ← Veo i2v output
  animated_captioned/<tag>.mp4                    ← post burn_captions / Lane 3 extract output
  episodes/episode_<id>_<ts>.mp4                  ← final
assets/
  bgm/*.mp3                                       ← BGM library (93 tracks)
  branding/{intro,outro}_bumper.mp4               ← shared bumpers
  branding/channel_banner.png                     ← banner source for bumpers
notes/
  episode_NN_*.md                                 ← per-episode storyboard + lessons
  sora2_motion_lessons.md                         ← motion prompt rules
  proven_motion_prompts.json                      ← verified Veo prompt patterns
```

## Env / setup (Mac, one-time)

```bash
# ffmpeg (use evermeet.cx static — Homebrew default is slim, missing libass/libfreetype)
softwareupdate --install-rosetta --agree-to-license
curl -L -o /tmp/ffmpeg.zip https://evermeet.cx/ffmpeg/getrelease/zip
unzip -o /tmp/ffmpeg.zip -d /opt/homebrew/bin/
chmod +x /opt/homebrew/bin/ffmpeg
xattr -d com.apple.quarantine /opt/homebrew/bin/ffmpeg 2>/dev/null || true
# same for ffprobe
curl -L -o /tmp/ffprobe.zip https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip
unzip -o /tmp/ffprobe.zip -d /opt/homebrew/bin/
chmod +x /opt/homebrew/bin/ffprobe

# Fonts
brew install --cask font-pretendard font-nanum-pen-script
# Confirm filenames (cask naming varies):
#   ~/Library/Fonts/Pretendard-{Bold,Medium,ExtraBold,...}.otf
#   ~/Library/Fonts/NanumPenScript-Regular.ttf

# Python deps
pip3 install --break-system-packages certifi pillow python-dotenv

# Vertex AI (only needed for richer motion — EP02 cut3 dance-style)
gcloud auth application-default login
gcloud config set project <YOUR_PROJECT>           # NOT a gen-lang-client-* (those don't allow Vertex)
gcloud services enable aiplatform.googleapis.com

# .env file (repo root)
GOOGLE_API_KEY=<from https://aistudio.google.com/apikey>
GCP_PROJECT=<your billable project>
```

## How to make a new episode

### Lane 1 (AI vtuber / sumukhwa)
```bash
# 1. Copy a similar episode's manifests + edit
cp scripts/prompts/episode_03_{sources,captions,regen_prompts}.json scripts/prompts/episode_NN_*.json
# Edit each: swap photo paths, write KO/EN captions, per-cut regen style

# 2. Copy + edit motion prompt wrapper
cp scripts/animate_episode_03.sh scripts/animate_episode_NN.sh
# Edit motion prompts inside

# 3. Run end-to-end (mirror run_episode_03.sh)
cp scripts/run_episode_03.sh scripts/run_episode_NN.sh
# Edit to point at episode_NN_* manifests
bash scripts/run_episode_NN.sh

# Cost: ~$3 (Gemini Imagen $0.04×4 + Veo lite $0.60×4)
```

### Lane 2 (sticker overlay)
EP01 pattern. Less commonly used since EP02+ moved to Lane 1.
```bash
bash scripts/decorate_all_cuts.sh                  # PIL sticker overlay
bash scripts/animate_all_cuts.sh                   # Veo i2v on decorated PNGs
python3 scripts/burn_captions.py                   # default manifest = EP01
python3 scripts/assemble_episode.py --intro-bumper ... --outro-bumper ... --music ...
```

### Lane 3 (일상 video)
```bash
# 1. Pick clips + write manifests
cp scripts/prompts/episode_04_{sources,captions}.json scripts/prompts/episode_NN_*.json
# Edit sources: clip path + trim_start + trim_dur (in seconds)
# Edit captions: scenes array with start, end, ko, en (multiple per cut for narrator flow)

# 2. Run end-to-end ($0 cost — no API calls)
bash scripts/run_episode_04.sh                     # uses episode_04 by default
# OR with override:
python3 scripts/extract_clips_ep04.py \
    --sources scripts/prompts/episode_NN_sources.json \
    --captions scripts/prompts/episode_NN_captions.json
python3 scripts/assemble_episode.py \
    --captions scripts/prompts/episode_NN_captions.json \
    --intro-bumper assets/branding/intro_bumper.mp4 \
    --outro-bumper assets/branding/outro_bumper.mp4 \
    --music assets/bgm/<chosen>.mp3
```

## Bumpers (shared channel asset)

Rebuilt once with channel theme music + CTA, then reused for all episodes:
```bash
python3 scripts/build_bumpers.py \
  --intro-music assets/bgm/redproductions-whistling-bright-kids-education-positive-claps-music-187833.mp3 \
  --outro-music assets/bgm/redproductions-whistling-bright-kids-education-positive-claps-music-187833.mp3
# Defaults bake in:
#   handle    = @ryani_n_loe
#   KO CTA    = 구독 좋아요
#   EN CTA    = Like & Subscribe
#   Hearts ♥ wrap each CTA line in hot-pink (#FF6B9D).
```

Override via `--outro-handle / --outro-cta-ko / --outro-cta-en`.

## Review gate (after draft render)

Before publishing, always run the draft through the review agent:

- Read `notes/shorts_review_agent_giri_v1.md`.
- Decide one of: **upload now / minor revision then upload / revise before upload / rework concept / discard**.
- If the draft is for a cultural or seasonal concept (especially Buddha’s Birthday / lotus lantern / temple-night episodes), explicitly check **cultural fit** before proceeding.
- Do **not** continue iterating blindly. End each review pass with a clear next action and, if needed, a tool-ready revision request.

This review gate exists to reduce web→CLI drift: web may establish taste and direction, while CLI tends to over-focus on execution. The review agent re-aligns the draft with the intended hook, emotional clarity, and cultural fit before upload.

## Known gotchas (already solved — DO NOT re-debug from scratch)

1. **Korean tofu (□□□)** — libass family-name matching on Mac is flaky.
   - **Fix**: drawtext + `fontfile=full/path/to/font.ttf` (NOT font family
     names). Pretendard at `~/Library/Fonts/Pretendard-Bold.otf` works.
   - NanumPenScript file is `NanumPenScript-Regular.ttf` (NOT `NanumPen.ttf`).

2. **Homebrew default ffmpeg 8.1.x is slim** — missing libass AND libfreetype
   on some builds. Symptom: "No such filter: 'subtitles'" or "No such
   filter: 'drawtext'". **Fix**: use evermeet.cx static binary + Rosetta
   (see Setup above). Don't fight Homebrew taps.

3. **SSL CERTIFICATE_VERIFY_FAILED** (Mac Python urllib hitting googleapis):
   - **Fix**: `pip3 install --upgrade certifi --break-system-packages`. All
     API-calling scripts already use `ssl.create_default_context(cafile=
     certifi.where())`.

4. **Gemini 2.5 Flash thinking mode** truncates JSON output mid-stream.
   - **Fix**: pass `generationConfig.thinkingConfig.thinkingBudget = 0`.
     Already wired into `motion_b_vlm.py` and `regen_vtuber_style.py`.

5. **Veo `lastFrame` (first+last frame interpolation) NOT available to us**.
   - Tried: Gemini API Veo (lite/standard/preview) → 400 "use case not
     supported". Vertex AI Veo 3.0/3.0-fast/2.0/3.1 → 400 "request not
     supported by this model". Vertex Veo 3.1 → 404.
   - **Workaround**: use Vertex Veo 3.0 standard (no lastFrame, but richer
     baseline motion than lite). `scripts/regen_end_frame.py` retained but
     unused.

6. **drawtext `text='...'` apostrophe breakage** ("Buddha's Day" splits the
   value at the inner `'`).
   - **Fix**: write text to a file and use `textfile=` instead. All
     drawtext call sites in this repo do this.

7. **drawbox uses INPUT dims, not box-self dims**, in expressions.
   - `w-120` inside drawbox refers to the BOX's own w (which we're setting).
   - **Fix**: hardcode pixel ints, or use `in_w` / `in_h`. assemble_episode
     uses literal pixel ints derived from EPISODE_W/EPISODE_H constants.

8. **concat resolution / SAR mismatch** — silent bumpers were 1080×1920 but
   cuts were 720×1280, concat errors.
   - **Fix**: assemble_episode pre-normalizes each input with
     `scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:...,setsar=1`.

9. **Bumper audio mixing** — bumpers can carry their own channel-theme
   audio. assemble_episode probes each bumper with ffprobe and either:
   - (no bumper audio) main BGM covers everything, OR
   - (bumper has audio) concat: [intro_a] + [main_bgm_for_cuts] + [outro_a].

10. **gcloud + Vertex AI project gotcha** — Google-auto-provisioned
    `gen-lang-client-*` projects don't allow user-managed Vertex AI enablement
    (CONSUMER_INVALID 403). Need a **personal billable** GCP project.

11. **EP04 video crop vs pad** — landscape clips were getting black bars
    with `force_original_aspect_ratio=decrease,pad=...`. **Fix**: switched to
    `force_original_aspect_ratio=increase,crop=...` so subjects fill the
    frame; sides cropped instead of letterboxed.

## Motion prompt rules (Veo i2v — Lane 1, Lane 2)

From `notes/sora2_motion_lessons.md` §6:
- VERIFIED dual-motion pattern: `"An A and a B ... The A slowly Xs. At the
  same time the B Ys. Camera gently pushes in / holds still."`
- Cat tail MUST swish when cat in frame (mandatory primary motion).
- Mention "no tail" for Ryani (Veo hallucinates one otherwise).
- Avoid proper nouns ("Leo", "Ryani" trigger moderation intermittently —
  use breed/color descriptors).
- Avoid warp/animate/morph verbs (also moderation-prone).
- One modifier per action ("slowly", "gently") — don't stack emphases.
- Multi-subject cuts: smaller/darker subject may go static. Acceptable at
  Shorts pacing. Don't fight it for hours.

## BGM picks (current channel taxonomy)

- **Bumpers (channel theme — same every episode)**:
  `assets/bgm/redproductions-whistling-bright-kids-education-positive-claps-music-187833.mp3`
- **Lane 1 EP02 Leo판 (oriental luxury)**: `kuzu420-ambient-electronic-flute-bgm-431329.mp3`
- **Lane 1 EP03 Ryani판 (sumukhwa)**: `kuzu420-ambient-electronic-flute-bgm-431329.mp3` (or similar ambient)
- **Lane 3 EP04 일상 (chill daily)**: `redproductions-charming-lofi-cozy-peaceful-warm-wonderful-music-196174.mp3`

## When CLI gets confused

If picking up this repo cold:
1. Read **this file** end-to-end (you are here).
2. Read **`notes/photo_selection_guide.md`** for HOW TO PICK photos/clips
   (selection criteria, past picks reference, direction-catching pattern).
   **Most important after this file.**
3. Read **`notes/shorts_review_agent_giri_v1.md`** for the review gate and
   final upload / revision / discard criteria.
4. Read **`notes/episode_01_first_meeting.md`** for the full ffmpeg/font
   saga + technical lessons.
5. Identify which **lane** the current task belongs to.
6. Identify which **EP manifest** to copy/edit.
7. Run from the appropriate **wrapper script**.

If something breaks, **check the gotchas above before debugging**.

## Two ways CLI tends to underperform — and the fix

1. **Photo selection too mechanical** (picks first plausible photo
   instead of best one). → Always read
   `notes/photo_selection_guide.md` §"Photo-finding workflow" before picking.
   Recommend 5+ candidates per cut, not 1.

2. **Direction-catching too eager** (commits to an interpretation without
   confirming). → After understanding the request, restate the chosen
   tone/style and present a small sample (1 cut, 1 frame) BEFORE running
   the full pipeline. Use `AskUserQuestion` for ambiguous calls.


3. **It treats a pretty draft as a finished draft** (no explicit go/no-go review).
   → After generating or assembling any draft, run the review pass from
   `notes/shorts_review_agent_giri_v1.md` and end with a concrete decision:
   upload / revise once / regenerate / discard.

## Per-episode notes

| File | Content |
|------|---------|
| `notes/episode_01_first_meeting.md` | EP01 storyboard + toolchain lessons (ffmpeg/font/Anthropic→Gemini saga) |
| `notes/episode_02_buddha_birthday.md` | EP02 Leo판 storyboard |
| `notes/episode_03_buddha_birthday_ryani.md` | EP03 Ryani판 sumukhwa storyboard |
| `notes/episode_04_daily_life.md` | EP04 일상 storyboard |
| `notes/sora2_motion_lessons.md` | Veo/sora motion prompt verified patterns |
| `notes/proven_motion_prompts.json` | machine-readable prompt patterns |
