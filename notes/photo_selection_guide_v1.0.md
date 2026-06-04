# Photo & Clip Selection Guide

> CLAUDE.md says WHAT scripts to run. This file says HOW to PICK the inputs.
> The pipeline is mechanical; the taste is what makes the episode work.

---

## Core selection principles (channel-wide)

### 1. Subject fidelity always wins over "interesting shot"

- **Ryani** must be recognizable. Her signature is the **white markings**:
  white blaze on chin/muzzle, white chest patch, white toes. If those aren't
  clearly visible in the source photo, **the AI regen will paint over them**
  and she'll come out as a solid-black blob. **Reject** photos where her
  face is in shadow or her white parts are obscured.
- **Leo** must show his **orange tabby stripes + amber eyes + white
  whiskers**. Cropped Leo without face = not usable for hook/intro cuts.

### 2. Match the photo to the cut's narrative beat

| beat | what to look for |
|------|------------------|
| Hook (cut1) | direct camera contact, eyes open, alert/curious expression. **Front-facing > profile**. Single subject preferred (cleaner read). |
| Spring/intro (cut2) | mid-action OR a setting that establishes mood (flowers, sunlight, traditional architecture). |
| Bonding/action (cut3) | both subjects in frame, with at least one of them ACTIVELY engaging (sniffing, licking, mouthing, wrestling). Static side-by-side reads weaker. |
| Closer (cut4) | calm/peaceful pose. Sleeping cuddle is the strongest template. Or "looking off into distance" together. |

### 3. Reject from these categories

- **People-heavy frames** (caretaker in front, pet small in back) — unless
  the framing is intentional and the human is cropped to a hand/arm.
- **Outdoors with cars/storefronts** in background — modern noise breaks
  the cozy domestic tone (except Lane 3 일상 where outdoor is OK and even
  preferred).
- **Already-used photos** from a previous episode — viewers notice and the
  channel feels repetitive. Always check `notes/episode_NN_*.md` to see
  what's been spent.
- **Bad lighting** (yellow indoor flash, harsh sodium-vapor street lamp) —
  AI regen can't fix bad source.
- **Tilted/blurry** — center crop + 9:16 rotation will amplify the problem.

### 4. Background variety across cuts

Don't pick 4 photos from the same room with the same blanket. Even if each
photo is great alone, the assembled video feels static. **Aim for 3+
different settings** across the 4 cuts.

Example (EP03 Ryani판 worked):
- cut1: cafe vintage warm (indoor brown)
- cut2: blue tile cushion (indoor blue)
- cut3: cream floor (indoor cream)
- cut4: vintage chair window (indoor with outside light)

---

## Lane-specific picks

### Lane 1 (AI vtuber / sumukhwa) — `data/assets/photos/<year>/`

**Photo size**: 720×1280 after preprocess. Source can be anywhere from
1440×1081 (older iPhone landscape) to 5712×4284 (recent portrait).
Preprocess auto-handles EXIF rotation.

**What works**:
- Indoor "still life" shots — the AI fills the background with stylized
  elements anyway, so a busy background just gets painted over. The pet
  should be the photographic anchor.
- **Solo close-up** (face + shoulders fills 60-80% of frame) — best regen
  fidelity. AI doesn't have to invent fur detail at small subject scale.
- **Profile or 3/4 turn** — adds visual depth, AI handles depth better than
  flat side views.

**What fails**:
- Group shots with both pets very small in frame — AI regen tends to
  simplify them, losing detail.
- Pets with accessories (cat hat, costume) — AI may interpret and modify
  the accessory unpredictably.
- High-contrast lighting (one side blown out) — AI tries to match style and
  the highlight gets weird.

### Lane 2 (decorate_photo PIL overlay) — same source dir

**Critical difference from Lane 1**: the photo pixels are **NOT touched**.
decorate_photo overlays PIL vectors (halo, sparkles, hearts, paws) in the
NEGATIVE SPACE around the pet. So:
- **Subject bbox matters**: pet should occupy a clear central region with
  empty edges. Sticker placement goes "around" the subject.
- Wider/full-body shots work well (more negative space for stickers).
- Backgrounds can be busy — they get sparkle-overlayed but stay visible.

### Lane 3 (일상 video) — `data/assets/clips/<year>/`

**Different criteria — looking for MOMENT, not POSE**:
- **Short candid clips (4-12s)** are easier to trim than long clips. 36s
  clips are usable but pick the best 4s window.
- **Genuine micro-actions** beat static cute (a paw twitch, a head turn, a
  yawn, a stretch — better than a held pose).
- **Camera somewhat steady** — handheld with a tiny wobble is fine and adds
  documentary feel, but heavy shake reads amateur. (Tripod cleanliness is
  *too* clean for 일상 vibe.)
- **Original lighting/color** kept — no AI gen means what you see is what
  ships. Pick clips where the lighting is already cinematic-enough.

**Anti-patterns** (specific to video clips):
- Long boring stretches where neither pet moves for >2s — picks need 4s of
  watchable motion within them.
- Voice-over from the recorder ("oh my god so cute") — we strip audio
  anyway but it's a signal that the recorder broke the moment.
- Mirror reflections or visible recording device — pulls viewer out of
  observational frame.

---

## Past picks reference (what worked, what didn't)

### EP01 첫 만남 (Lane 2)
- Photos: `data/output/decorated/cut{1-5}_*.png` (look at to see picks)
- Worked: face-forward Leo solo (cut2), wrestle pose Leo+Ryani (cut3)
- Lessons: chose photos with clean negative space for sticker placement

### EP02 Leo판 부처님 (Lane 1, vtuber pink kawaii)
Final picks (all 2026):
1. **cut1_peony_greeting**: `med_2026_05_06_203116_*.jpeg` — Leo + 작약 peony
   bouquet on table. Pink flowers became core motif for AI regen palette. ✓
2. **cut2_sunbathe_meditate**: `med_2026_04_18_163637_*_a61bf9ca.jpeg` —
   Leo stretched out on tile floor reaching for plant. Solo. Outdoor light
   = meditation parallel. ✓
3. **cut3_dance_party**: `med_2026_05_06_153947_*_32f780a4.jpeg` — Leo paws-
   up wrestling with Ryani. Multi-subject. Used Vertex Veo 3.0 standard for
   richer motion than lite. ⚠ Ryani's white markings got mostly painted
   over in AI regen (known issue).
4. **cut4_cuddle_peace**: `med_2026_04_20_123613_*_16ea2825.jpeg` — both
   sleeping close. Pink pillow background fit pink kawaii palette. ✓

What we **rejected** during selection:
- `med_2026_02_13_*` outdoor street shots (human in frame, no pet)
- `med_2026_01_18_*` forest walk (no pet visible clearly)
- `med_2026_01_25_*` (63 photos — all Leo solo, no variety)

### EP03 Ryani판 부처님 (Lane 1, sumukhwa)
Final picks:
1. **cut1_ryani_greeting**: `med_2026_01_01_141833_*.jpg` — Ryani cafe
   vintage close-up. **Best Ryani solo of 2026** — white markings perfectly
   visible. Alert pose. ★★★★
2. **cut2_ryani_contemplate**: `med_2026_02_07_100934_*.jpeg` — Ryani
   profile on blue tile cushion looking up. Hidden gem — blue tile fit
   sumukhwa palette unexpectedly. ★★★★★
3. **cut3_ryani_with_leo**: `med_2026_05_06_203421_*.jpeg` — Ryani sitting
   stable + Leo passing behind/beside. Ryani as anchor, Leo as movement.
4. **cut4_ryani_peaceful**: `med_2026_01_01_150036_*.jpg` — Ryani vintage
   chair sleep. Cohesive "1/1 cafe day" thread with cut1.

Why this worked: chose photos where Ryani's face is **clearly recognizable
with white markings**, then designed the regen aesthetic around the photos
(rather than picking photos to fit a pre-decided aesthetic).

### EP04 일상 (Lane 3)
Final clips:
1. **cut1_sit_together**: `2026/med_2026_03_04_105836_*.mp4` (5.2s) — both
   facing camera, robot vacuum in background = pure 일상 signal
2. **cut2_leo_munching**: `2026/med_2026_05_05_124151_*.mov` (4.6s) — Leo
   eating catgrass close-up
3. **cut3_ryani_sleeping**: `2026/med_2026_01_01_204650_*.mov` (36s) —
   trim 8-12s window for stable sleeping shot
4. **cut4_cuddle_together**: `2026/med_2026_04_11_110559_*.mov` (8.1s) —
   Leo + Ryani face-touching cuddle

Why these worked: each is **a moment**, not a pose. The robot vacuum in
cut1 is the kind of incidental detail that makes the 일상 lane feel real.

---

## Photo-finding workflow (when starting fresh)

1. **Inventory by date density**: `ls data/tmp/photos_2026_jpeg/ |
   awk -F_ '{print $2"-"$3"-"$4}' | sort | uniq -c | sort -rn`
   Days with 5+ photos = focused shoots. Days with 1-2 photos = incidental,
   skip unless investigating a specific moment.

2. **Sample one photo per cluster** — `Read` 5-10 photos across different
   dates. Build a mental map of subjects/settings.

3. **Score against the 4 narrative beats** (hook / mid / action / closer).
   For each beat, mark 2-3 candidates.

4. **Cross-check Ryani white-markings** for any photo where she appears.
   Hold up the candidate against her reference look (see "Subject fidelity"
   above).

5. **Verify background variety** across the 4 chosen photos. Different
   rooms / lighting / context.

6. **Present candidates to PD (user) with rationale** — don't pre-commit.
   Show 5-8 candidates per cut, recommend top pick, explain why.

7. **After PD approves**, freeze the picks. Don't second-guess.

## Direction-catching pattern

The user's preferences emerge over the conversation. Capture them as you go:

- **Tone preferences**: "동양적 매력 + 신나고 힙" (EP02) ≠ "단정 시적"
  (EP03) ≠ "(괄호) 재잘재잘" (EP04). Each lane has a distinct voice.
- **Visual line-in-the-sand decisions** the user makes:
  - **No black bars** on landscape video (EP04 — use crop not pad)
  - **No bumper caption** (EP04 — caption only on content cuts)
  - **Bumper carries its own audio** (different from main BGM)
  - **Ryani's white markings** absolute requirement
  - **No proper nouns in Veo prompts** (moderation-blocks "Leo"/"Ryani")
- **Cost preferences**: User OK'd Vertex Veo 3.0 standard ($4) for richer
  motion on dance cuts. NOT OK with running Gemini Imagen 4 times at $0.16
  total if Ryani's face fidelity is at risk — would rather re-regen 3x.
- **Iteration pattern**: User generally prefers "do option A, see result,
  iterate" over "guess what I want then build all of it". When stuck or
  unsure, **stop and ask** with `AskUserQuestion` (1-3 focused options) —
  don't burn cycles on a wrong-direction build.

### Common direction-catching mistakes (avoid these)

1. **Building the whole pipeline before showing intermediate output**. If
   you regen 4 photos then animate 4 cuts then assemble — you might have
   wasted $5 on a wrong-direction look. **Show the first cut's regen to
   the user before committing the other three.**

2. **Picking based on technical convenience instead of taste**. The closest
   photo that's "good enough" isn't the right answer when 30 minutes of
   browsing might find a perfect one.

3. **Ignoring rejection signals**. User says "별로야" — that's a hard
   stop. Don't try to re-justify the previous direction. Pivot.

4. **Over-engineering the next iteration**. User says "동물이 정적이야" —
   try Vertex standard before building ffmpeg overlay scaffolding. Try the
   cheap incremental fix first.

5. **Forgetting cross-episode consistency**. Bumpers, CTA, channel handle,
   default font choices — these should stay the same. Don't reinvent each
   episode's brand layer.
