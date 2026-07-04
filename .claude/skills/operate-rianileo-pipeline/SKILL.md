---
name: operate-rianileo-pipeline
description: >-
  Personal operational runbook for running the Ryani x Leo Shorts studio in THIS
  repo end to end. Use when the task is to produce/render an episode, run an E2E
  test, review a draft through the Giri gate, schedule or launch a batch, restart
  the Slack listener, or debug the pipeline (ffmpeg/caption/font, Seedance/Veo
  i2v, cost). This is the "how to actually run it here" companion to CLAUDE.md and
  the ryani-leo-shorts-guardrails skill. Private to this project.
---

# Operate the Ryani x Leo pipeline (this repo)

This is the runbook for *operating* the studio in this repository. It does not
restate the design — it points at the canonical docs and gives the order of
operations. **Read these first, every session:**

1. `ryani-leo-shorts-guardrails` skill — the constraints the code does NOT enforce.
   Consult it before touching agents, scripts, ffmpeg/caption steps, i2v calls,
   the Slack listener (`rianileo-bot.service` on the VM), or manifests.
2. `CLAUDE.md` — the single source of truth for lanes, pipeline map, and gotchas.
3. Shared progress log so you continue on top of the board bot's work:
   `from agents.progress_log import recent_progress; recent_progress(15)`
   plus the `board_escalations` queue.

When you finish a meaningful unit of work, log one line:
`from agents.progress_log import log_progress; log_progress('CLI', '<one line>')`.

## 1. Pick the lane

Decide the lane BEFORE writing any code (see CLAUDE.md "Three content-style lanes"):

- **Lane 1 — AI vtuber (photoreal):** photos -> GPT regen -> Seedance/Veo i2v.
- **Lane 2 — sticker overlay:** PIL sticker overlay -> i2v (EP01 pattern).
- **Lane 3 — daily real footage:** real clips -> trim + caption, no AI ($0).

## 2. Produce an episode

Mirror an existing episode's wrappers and manifests, then edit:

- Manifests live in `scripts/prompts/episode_NN_{sources,captions,regen_prompts}.json`.
  Cut order derives from the captions manifest's key order (keys starting with `_`
  are metadata, skipped).
- Copy a similar episode's manifests + wrapper (`scripts/animate_episode_NN.sh`,
  `scripts/run_episode_NN.sh`), swap photo/clip paths, write KO/EN captions and
  per-cut regen/motion prompts, then run the wrapper.
- Read `notes/photo_selection_guide.md` before picking photos — recommend 5+
  candidates per cut, not the first plausible one.

## 3. Cost discipline (do not skip)

- Validate on **~$0.04 stills** and $0 caption rewrites before any paid render.
- A full Seedance render is **~$50** and near-daily; kill a wrong run before the
  first paid call. Caps and the ledger are wired, but the human OK on the exact
  plan is the gate.

## 4. Review gate (Giri) — mandatory before publish

Run every draft through the review agent (`notes/shorts_review_agent_giri_v1.md`).
End each pass with ONE decision: upload / minor revision then upload / revise
before upload / rework concept / discard. Do not iterate blindly. For seasonal or
cultural concepts, check cultural fit explicitly.

## 5. Launch / schedule (runs on the GCP VM, not the Mac)

The brain lives on the always-on VM `rianileo-brain` (bot + cron + render + DB).
The Mac only does the dawn iCloud/osxphotos delta.

- **Batch:** `agents/launch_selfheal` on the VM **cron at 03:00 KST** produces
  TOMORROW's batch — `agents/launch.py` picks 2 AV + 2 RF on a lane x timeslot
  Latin square (08:00/12:30/18:00/21:00), renders with per-slot Giri retry, and
  self-heals failed slots. Passing episodes auto-schedule + auto-upload; failed
  slots stay empty (no junk). Pause = edit the VM crontab (`crontab -u rianileo -e`),
  not `launchctl`.
- **Review in ONE place:** the 4 episodes post into a single Slack **batch-summary
  thread** ("배치 써머리 …"), each labelled by schedule name. Veto by label there:
  `veto 260705_RF2100`. Every output is also mirrored to GCS
  `gs://rianileo-assets/output/episodes/` named `YYMMDD_<LANE>HHMM` (e.g.
  `260705_RF2100`) — the reliable browse surface when Slack drops a file.
- **board bot = top admin** (rayleo_board channel, natural language): re-render +
  replace a slot ("260705_RF2100 다시 만들어" -> immediate, no confirm), `현황` for
  batch status. The autonomous executor (auto mode) also self-fixes code + pushes;
  PD-authored escalations may render (Seedance-capped).
- **Deploy = `git push` to main** -> the VM deploy timer pulls, smoke-gates, and
  restarts the bot. Confirm it landed by checking the VM HEAD, not just the push
  (a silent smoke failure looks green — retro D16).

## 6. Common ops & debugging

- **Before debugging, check the gotchas in CLAUDE.md** (Korean tofu / font
  fontfile, slim ffmpeg, SSL certifi, Gemini thinkingBudget=0, Veo lastFrame
  unsupported, drawtext apostrophe, concat SAR mismatch, etc.).
- **Failure-signature triage:** immediate ENOSPC = disk; a 600s run = contention;
  an infinite block = a held lock. Suspect your last change before infrastructure.
- Deliver the rendered mp4 immediately when an episode finishes (do not wait to be
  asked). Reply text is plain prose only — NEVER leak tool-call syntax.

## 7. When you change anything

- After any change to story/render behavior, a prompt rule, a schema field, or a
  pipeline stage: invoke **`pipeline-change-impact`** and reconcile BOTH lanes.
- Before editing any prompt/instruction `.md`: invoke **`prompt-authoring`**
  (synthesize into principle->why->how->example; supersede; no dated band-aids).
- After merging durable change to main: invoke **`merge-retrospective`** and
  record what it SHIPPED and what it TAUGHT into `notes/retrospective_*.md`.
