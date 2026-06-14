---
name: pipeline-change-impact
description: Use after deciding on ANY change to story/render behavior, a prompt rule, a schema field, or a pipeline stage. Before calling the change done, trace its ripple across the WHOLE AV + RF pipeline, update every affected consumer, and verify end-to-end. Prevents "fixed the Writer, forgot Director/Cameraman/RF/Validator."
---

# Pipeline change-impact — propagate one change across the whole pipeline

## Why

The pipeline is a chain of agents that hand off to each other across TWO lanes
(ai_vtuber / real_footage). Changing one stage in isolation leaves the others
enforcing the old contract → silent overrides, contradictions, half-applied fixes
(the recurring "각자 퀄 낮음 / 오케스트라 안 됨"). A change is not done when one file
is edited — it's done when every point that *reads, enforces, or renders* the changed
behavior agrees, in BOTH lanes.

## The pipeline map (trace along this)

Concept → render, by lane. A change usually enters at one node; follow the arrows
forward (consumers) AND sideways (the other lane) to find everything it touches.

**Shared upstream**
- `agents/producer.py` — builds context (episode_stories, assets, set knowledge), picks photos, routes to Writer/Director.
- `agents/arc.py` / `agents/launch.py` — directive priority, slot/length-fit, scheduling.
- `agents/reviewer_macro.py` — freshness / overlap gate.

**AV (ai_vtuber)**
Writer (`prompts/writer_story.md`, 3-pass) → Director (`prompts/director_shots.md`:
shot_size/camera_move/duration/seedance_mode/regen_prompt/motion_prompt) →
Caption Agent → Caption Polisher → Cameraman Validator → `cameraman.py` (regen →
Seedance i2v, chain_from_prev, scene_ref/SCENE LOCK) → `burn_captions.py` →
`assemble_episode.py` → Giri (`reviewer.py`).

**RF (real_footage)**
Writer (`prompts/writer_realfootage.md`, clip-first) → Caption Agent →
`cameraman.py` (clip extract / interp gap-fill / grounding gate / face-crop) →
`burn_captions.py` → `assemble_episode.py` → Giri.

**Cross-cutting enforcers** (often the forgotten ones): Cameraman Validator,
Giri reviewer rubric, programmatic signature checks, schema defaults/clamps in
`cameraman.py` (e.g. `duration_seconds` handling, cut-count, caption timing).

## Procedure (run for every change)

1. **Name the contract that changed.** Which field / behavior / rule, and its new
   value? (e.g. "cut duration is variable 2.5–6s, was fixed 5s"; "cut count uncapped
   within a 2.5s readability floor".)
2. **Find every consumer.** grep the field/behavior across `agents/` + `scripts/` +
   `prompts/`. For each hit ask: does it READ this (needs the new value), ENFORCE an
   old version (clamp/validator/Giri rule to relax), or RENDER it (cameraman/burn)?
3. **Check BOTH lanes.** If the change is in shared Writer/Director logic, confirm RF
   gets the matching treatment (or a deliberate lane-specific exception). If it's in
   one lane, confirm the other lane isn't silently broken by it.
4. **Update or confirm each point.** Edit the consumers that need it (prompts via the
   `prompt-authoring` skill). For ones that are already fine, note why.
5. **Hunt contradictions.** Does any prompt / validator / Giri rule still assert the
   OLD contract? Supersede it.
6. **Verify end-to-end.** Render or dry-run one AV and (if touched) one RF episode and
   confirm the new behavior actually appears downstream — not just in the prompt.
7. **Report the blast radius.** List what you checked, what you changed, what you
   intentionally left, so PD can see the propagation was complete.

## Checklist before calling a change done

- [ ] Every grep hit for the changed field/behavior triaged (read / enforce / render).
- [ ] Director updated if the Writer change needs new cinematography handling.
- [ ] `cameraman.py` honors the change (no stale clamp/default/cut-cap fighting it).
- [ ] Cameraman Validator + Giri rubric don't still enforce the OLD contract.
- [ ] RF lane checked: matching update or a deliberate, noted exception.
- [ ] burn_captions / assemble unaffected — or updated (timing, readability).
- [ ] One end-to-end render confirms the behavior downstream.
- [ ] Blast-radius report given to PD.

## Note

Pair with `prompt-authoring` (for the prompt edits) and the CLAUDE.md lane/stage
tables (keep them current when stages move). If a change is big enough that the map
above is stale, fix the map too.
