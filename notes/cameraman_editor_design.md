# Design — #3: Cameraman → professional Editor + upstream feedback loop

> Status: BUILT & E2E-verified 2026-06-13. PD decisions: separate LLM agent / RF+AV /
> ≤2 re-render loop / editor may reorder & drop. Companion to memory
> `rf_onetake_editing_spec`.
>
> **BUILT & committed (2c2210b RF, c20aed7 AV):**
> - `agents/prompts/editor.md` — Editor agent persona + both guides injected.
> - `cameraman._run_editor` → EditPlan (per-cut technique/tempo/trim, reorder, drop,
>   `intent_mismatch{what_intent_said, what_footage_shows, suggestion}`).
> - `cameraman._apply_edit_plan(allow_structural)` — drop/reorder/trim/tempo on
>   manifests + captions JSON; never empties the episode; mirrors _rf_face_gate.
> - RF: editor runs PRE-trim in run_real_footage_pipeline (full authority).
> - AV: editor runs POST-render (judges vlm_actual_action), CONSERVATIVE — tempo +
>   mismatch only; reorder/drop only if NOT chained AND EDITOR_AV_STRUCTURAL=1.
> - Verified: a "savoring the meal" intent over can't-eat footage (RF21 class) is
>   auto-flagged with a recaption suggestion, cut kept.
>
> **FEEDBACK LOOP — BUILT & E2E-verified (49899db, 449ad78):**
> - `recaption` → `_apply_edit_plan` stamps `editor_footage_truth` on the cut;
>   `_vlm_post_render_caption_rewrite` PREFERS it over the raw VLM AND embeds a hard
>   constraint (forbid captions that invent an outcome / contradict the screen, incl.
>   the closer). E2E: a 100%-wrong "finishes the meal" intent over the RF21 clip
>   auto-rewrites to an honest "why isn't it shrinking… too hard" with NO false ending.
> - `different_technique` → the editor applies it in its own EditPlan.
> - `different_clip` → render_meta carries `_edit_plan`; `_render_realfootage_direct`
>   surfaces it on the report; `_render_realfootage_with_retry` excludes that asset_id
>   and re-proposes with the editor note, bounded to EDITOR_MAX_LOOPS (2).
>
> Hardening: editor output unwraps list-wrapped JSON + normalizes intent_mismatch/
> per_cut/dropped; _apply_edit_plan guards the captions path + never empties the
> episode. Env: EDITOR_AGENT=1, EDITOR_AV_STRUCTURAL=0, EDITOR_MAX_LOOPS=2.
>
> **Possible future polish (not blocking):** age-marker on old clips ("아기 랴니" for a
> 6-years-ago = ~5yo dog) still only WARNs; a Claim⊥Evidence verifier could double-
> check consumption/outcome claims against first↔last frames as a belt-and-suspenders.

## Why
The RF21 failure (card ea14a010): Writer/Director formed an intent ("느긋한 식사")
from a TEXT scene-description and the Cameraman **mechanically trimmed + rendered**
with no editorial judgment. Result: captions described the opposite of what the
clip shows (랴니 = can't eat the hard cheese patty), the trim cut off the real
payoff, the tempo (blanket 1.3×) shrank reading time, the ending hard-cut.

**The Cameraman is the ONLY stage that actually sees the footage / the render.**
So editorial CRAFT (technique, tempo, trim, pacing, 여운) belongs with it — serving
the Writer/Director's INTENT, and feeding back upstream when the footage can't
deliver that intent. PD's architecture call: **"둘 다 강화 + 루프"** — W/D own intent,
Cameraman owns execution + can push back; agents iterate (no silent override).

## Scope split (already decided)
- **Writer/Director** = intent + arc (NOT editing mechanics; they can't see render).
- **Cameraman = editor**: from the REAL footage + intent + the two guides
  (`editing_direction.md` judgment, `editing_techniques.md` palette), it (a) proposes
  the edit and (b) escalates a mismatch upstream.

## Already-built primitives to reuse
- `_should_onetake` (#1) — agent picks format from clip content. Generalize to a
  full "choose technique" decision.
- `_fit_caption_reading_time` + `_tempo_factors` plumbing (#4) — reading-time → cut
  length; tempo per cut. The editor sets `tempo_factor` here.
- `_fade_out_ending` (#4 f/o) — 여운.
- `_free_trim_start` / per-segment cooldown (#2) — trim-window selection.
- `_vlm_post_render_caption_rewrite` + `_cut_scene_ok` (scene gate) + `caption_salvage`
  — existing post-render checks; fold into the editor/feedback role.
- Guides injected into Writer/Director/RF-writer already; ALSO inject into the editor.

## Proposed design

### A. Editorial pass (Cameraman, per episode)
A new step that runs once the assets are resolved and VLM-described, BEFORE the
expensive trim/render commits. Inputs: concept (intent, per-cut beats/captions),
the ACTUAL footage descriptions (VLM timeline per candidate clip — reuse
`_vlm_clip_timeline`), the two guides. Output (structured):
```
{
  per_cut: [{ tag, asset_id, technique, tempo_factor, trim_start, trim_dur, order }],
  episode_technique,           # one-take / montage / compilation / ...
  notes,                       # editor's rationale
  intent_mismatch: null | {    # ← feedback trigger (B)
     cut/asset, what_intent_said, what_footage_shows, suggestion }
}
```
The editor chooses from the PALETTE to best deliver intent (speed-ramp / day-
compilation / smart-trim-to-include-payoff / etc.) — NOT a default. Writes
`tempo_factor` (so #4/assemble honor it), trim windows (segment-aware via #2).

### B. Upstream feedback loop
If `intent_mismatch` is set (footage can't deliver the stated intent — e.g.
"caption says 'finishes the meal' but the clip shows she never eats"):
1. Send the editor's note BACK to the Writer/Caption-Agent (and Director for AV)
   with the footage ground-truth, asking them to rewrite the intent/captions to
   match the footage OR pick a different clip/technique.
2. Re-run the affected upstream stage with that note appended (bounded: ≤2 loops).
3. If still mismatched → fall back to the safest honest option (caption the trim
   truthfully via `caption_salvage`, or refuse the slot — never ship the inversion).
Prevents the "silent downstream override" anti-pattern (editing_direction §5):
the loop makes the disagreement explicit and resolves it on the INTENT.

### C. Where to wire
- RF: inside `run_real_footage_pipeline` (footage exists upfront → editorial pass
  is pre-trim). Likely replaces/augments the ad-hoc trim logic.
- AV: footage is generated, so the editor runs post-generation (it sees the rendered
  cuts) and can request a re-gen from the chain anchor (reuse av_surreal action-gate).
- New prompt: `agents/prompts/editor.md` (the editor persona + the two guides).
- New module fn (cameraman): `_editorial_pass(concept, assets, lane) -> EditPlan`
  and `_apply_edit_plan(...)`; feedback via the existing producer retry loop.

## Open questions for PD before building
1. Editorial pass as a **separate LLM agent** (own prompt, like Caption Agent) vs
   **fold into Director**? (Leaning: separate — it needs the footage, Director doesn't.)
2. Feedback loop max iterations + cost ceiling (Seedance is expensive for AV).
3. For AV, is a re-gen-on-mismatch acceptable cost, or prefer caption-salvage only?
4. Does the editor get to REORDER cuts / DROP cuts, or only set technique/tempo/trim?

## Verification plan
- RF21-type case: editor sees "licks but can't eat hard patty" → either picks
  smart-trim/speed to include the eventual eating (if intent = "eats"), or flags
  mismatch → captions rewritten to the struggle. No inversion ships.
- Distribution: across N RF episodes, technique variety (not all one-take/vlog).
- AV: a failed surreal beat → editor requests re-gen from chain anchor (existing).
