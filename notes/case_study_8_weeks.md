# How I Built a Human-in-the-Loop Multi-Agent Video Studio in 8 Weeks

*An 8-week case study in AI-assisted short-form video production: orchestration, review, launch optimization, and human-in-the-loop quality control.*

**TL;DR** — Over eight weeks, a small pet channel I run stopped being a channel and became a human-in-the-loop, multi-agent studio that produces, measures, and improves four videos a day, with me reviewing, steering, and correcting the system at the seams. Along the way I built 34 agent modules (~25,000 LOC), shipped 118 videos, made 343 commits across 27 days, and pushed through a modern image-to-video stack — early i2v experiments → Google Veo → BytePlus Seedance 2.0 — to get motion I could trust. This is the story of the system, the model journey, and the failures I chose to keep.

## The problem

It began with a false sense of simplicity. I used GPT image editing to generate character stills and thought, oh — this could actually work. And it could, for stills. But motion is a different, much harder pipeline. A still is one API call; a moving shot is a chain of models, hard constraints, and failure modes.

The moment I tried to scale past one-clip-at-a-time, four problems surfaced at once: the real quality bottleneck was upstream writing, not the video engine; AI-looking footage was ignored by viewers; the LLM reviewer rubber-stamped flawed output; and my Mac infrastructure kept falling over — not in dramatic ways, but in the slow, grinding way where a media library outgrows the disk it lives on. Fixing any one of them in isolation changed nothing. The system didn't need a better model. It needed to become an organization.

## From stickers to two lanes — when the concept isn't in the source

At its core, this channel was a process of generating infinite content from **two fixed characters** — Ryani, an 11-year-old French bulldog, and Leo, a roughly 8-month-old tabby cat. Everything was a way to keep pulling content out of those two: seasonal concepts, and, when I wanted, flights of pure imagination.

My original plan was different. I wanted to lay **cartoon-cut stickers over real footage** — hand-drawn-style overlays on top of genuine clips. But once I saw firsthand how hard that is to pull off with 100% image-gen or video-gen APIs, I split in a different direction: either **edit real footage (RF)** or **produce it as an AI vtuber (AV)**.

The reason for the split came down to one thing: **the concept a director sets may not exist anywhere in the actual footage.** Real footage (RF) is genuine but bound to what was already filmed — you can't shoot a scene that never happened. The AI-vtuber lane (AV) can generate a concept that was never filmed, but pays for it by having to generate every frame (and most of the failures later in this piece are born right there). So the two lanes aren't rivals; they're complementary — RF carries truth, AV carries imagination.

## The model journey: early i2v → Veo → Seedance

Stills were cheap and easy (GPT `images.edit`, ~$0.04 each). Motion was neither.

I started with early image-to-video experiments, A/B/C-tested them against Google Veo, and moved to Veo for stability. Then, chasing richer, more dynamic motion, I moved again — to BytePlus Seedance 2.0 i2v, which I run in three modes:

* **i2v** — the standard shot (GPT still → Seedance).
* **interp** — first-and-last-frame gap-fill for real footage (capped at 4s).
* **ref** — up to nine reference images to fight character drift across cuts.

Getting to Seedance meant learning exactly where a state-of-the-art video model breaks, and building the scaffolding that makes it reliable. Veo's first-and-last-frame interpolation simply wasn't available to me — every endpoint returned 400/404, so I fell back to Vertex Veo 3.0. Seedance 2.0 can't mix first/last-frame with reference images in a single call, and has no scene-chaining — so episode continuity is assembled cut-by-cut (concat + interp gap-fill), and every hero still is chosen best-of-5.

The economics lesson, learned the hard way: a full render costs ~$50, and Seedance renders were 84% of my weekly cloud spend. A $50 render is not a $5 iteration. Validate on $0.04 stills first, then pay for motion.

## Two shape changes — one take to cuts, text to references

The episode format was ~20 seconds, and my first instinct was to generate the whole thing as a **single take**. It broke immediately: one long Seedance call (with 4x slow-motion layered on) produced motion lag and captions that drifted out of sync with the picture. So the episode split into **4-5 cuts** — and, more importantly, the cut count stopped being a fixed number and became **one cut per story beat** (within a readability limit). Continuity across cuts is stitched with concat + interp gap-fill.

The generation *method* wandered just as much. I started with **text-to-video** — pure prompts — but a text prompt **invents** the location, so the room teleported between shots. I moved to **still→i2v** (generate a first frame, then animate it), and increasingly to **reference-image-based generation** (Seedance's ref mode, up to nine images). The lesson underneath all of it: **when the look, the set, or a prop matters, the reference image beats the prompt.** Prompt text saying "photoreal" or "no collar" loses to whatever the reference shows. So the fix was never better prompt wording — it was feeding the right image.

## The architecture

The core move was to stop treating the LLM as a mind and start treating the pipeline as a studio. A single call became a division of labor:

* **Writer** (Opus, 3-pass: draft → self-critique → revise) — story only: beats, captions, transitions.
* **Conte / cue-sheet agent** — shot design only: shot size, angle, camera, blocking, first→last delta. It deliberately doesn't read the caption or prompt strings — a storyboard discipline.
* **Director** — realizes the cue-sheet into a prompt and picks the Seedance mode per cut.
* **Cameraman** — AV: still → Seedance i2v (best-of-5); real footage: clip trim + interp gap-fill.
* **Assembly** — caption burn-in → normalize + bumpers + BGM.
* **Giri (reviewer)** — an upload / revise / discard gate, backed by deterministic gates (below).
* **Launch orchestrator + Thompson bandit** — schedules 2 AV + 2 RF per day on a lane×timeslot Latin square, measures 48-hour views + retention, and feeds a 3-level Thompson bandit.
* **Board Executor** — a Slack-based coding agent that edits code, commits, and pushes under smoke gates, kill switches, and a shared progress log, coordinating with my interactive sessions.

Two content lanes run in parallel — AV (AI-generated) and RF (real footage) — grounded by a three-layer knowledge system (VLM observation / human-authored facts / ask-when-unknown) that exists because the pipeline once hallucinated a character trait and I never wanted that to happen twice.

## Orchestration was the bottleneck

The biggest lesson wasn't about any single agent. It was that the real bottleneck of automation is orchestration — the division of labor and the seams between the parts, not the tuning of any one model. Improving the i2v engine could never fix bad upstream writing, so writing had to be isolated into its own three-pass agent. A blindly-rendering director had to grow an editor's eye on the actual footage. A rubber-stamping reviewer had to be replaced with signals computed in code. Every hard problem, in the end, was an interface problem.

## Reliability and cost, engineered

* **Deterministic gates.** When I couldn't trust the LLM reviewer, I stopped trying to fix it with prompt text and started computing signals in code and feeding it booleans — temporal grounding, "preachy caption" detection, era/existence coherence, character-marking checks. Prompt text alone will not fix a reviewer; compute the signal and feed it.
* **Failure-signature triage.** Immediate ENOSPC = disk; a 600-second run = resource contention; an infinite block = a held lock. Naming the signature turned days of debugging into minutes — the ENOSPC one wasn't hypothetical (see the sync trap below).
* **Cost ledger + caps.** Every provider call is logged; retries are capped; the expensive render step sits behind cheap validation. Across the codebase, `fix` commits (110) outnumbered `feat` (89) — the honest shape of firefighting at the edge of a new stack.

## The spine: failures I kept

The part I'm proudest of is the one most people delete. I keep a running log of everything I rolled back, because deleted failures repeat. A few:

* **3D-modeling the room** (VLM → Blender) to lock background consistency — built, then abandoned for real-photo references, which were simply more photoreal. Sophisticated modeling is not the shortcut to realism; real source is.
* **The rubber-stamp reviewer** passing a batch of obvious defects 9/8/8/9 and auto-scheduling them — which forced the whole shift to deterministic gates. Once, an entire day's batch was scrapped.
* **The single hardest creative problem of the project:** getting my dog's exact white markings right in every generated frame, without hallucination — a problem a real pet channel never has, because it films instead of generating. Fourteen-plus commits, balanced only by advisory caps + reference comparison + per-cut self-healing.
* **The sync-and-disk-staging trap — the infrastructure problem I fought longest and never fully closed.** My Mac had only ~32 GB of free disk, while the source archive was tens of gigabytes and still growing. A naive bulk export once ignored its date scope and pulled *every* pruned original at once — 37 GB — and filled the disk to 100% (ENOSPC). The obvious fix — a watermark that only synced "new" items — quietly created a worse one: ~8,000 older clips were permanently excluded from ingest, so the Writer was drafting stories from a recency-biased pool and the oldest, most nostalgic footage never surfaced (and, because the iCloud shared album was never cleared after ingest, the sync kept re-seeing the same items and the cloud upload never triggered). The real fix was a chunked backlog sync: pull ~10 GB of un-ingested items, tag them, delete the staging copy, repeat until the diff is empty — bounded by disk, not compute. That got the full library (16,478 assets) tagged. But that same constraint — I can never stage the whole archive at once — is exactly why the clean structural fix, moving everything to the cloud, still isn't done: the migration is written and ready but unexecuted, blocked by the very disk ceiling it's meant to remove. **The real ceiling of a local-first system wasn't CPU. It was disk staging capacity.**
* **Additive guardrails degrading the whole.** A night of well-meant "improvements," stacked, made the real-footage lane worse. New gates are kept only after they're proven to help.

## What it actually shows

Strip away the pets, and this is an end-to-end multimodal AI production system: ambiguous inputs, model constraints, human review, deterministic gates, cost controls, launch scheduling, and feedback loops — all held together as a repeatable operating system. That is the work I want to keep doing: taking an ambiguous, moving problem and building the structure that lets it hold together.

The studio is still running. You can watch what it makes here → [@ryani_n_loe](https://www.youtube.com/@ryani_n_loe).

**By the numbers:** 8 weeks · 34 agent modules · ~25,000 LOC · 343 commits / 27 days · 118 videos shipped · 4 videos/day · 12+ documented rollbacks · early i2v → Veo → Seedance 2.0.

*Evidence available upon request: commit log, module map, architecture diagram, and production ledger.*
