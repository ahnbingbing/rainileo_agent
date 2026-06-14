---
name: prompt-authoring
description: Use when WRITING or EDITING any agent prompt / instruction .md (agents/prompts/*.md, director_shots.md, character_sheets.md, CLAUDE.md sections, review-agent specs). Turns PD feedback into developed, principle-first guidance instead of accreting dated rule band-aids. Invoke BEFORE touching a prompt file.
---

# Prompt authoring — write briefs that teach judgment, not rule lists

## The problem this fixes

Agent prompts here decayed into **sedimentary rule bases**: every piece of PD
feedback got appended as a dated, verbatim band-aid (`PD 2026-06-14: "..."`),
so the file became a long imperative DO/DON'T list with contradictions
(e.g. "short = 3-5컷×5초" sitting next to "컷 수 제한 없음, 가변 길이"). Two costs:
- The LLM can't **generalize** — it only has rules, so every unseen case needs a
  new rule. PD has to keep pointing out gaps. The list grows; quality doesn't.
- Contradictions confuse the model and silently cancel earlier intent.

A prompt is **onboarding a smart writer**, not a compliance checklist. Teach the
*intent and judgment*; trust the model to apply it to cases you didn't enumerate.

## The model: principle → why → how → one example

Write each concept as a short coherent unit, in this order:

1. **Principle** — the thing we actually want, stated as intent ("Story richness is
   independent of video length").
2. **Why** — the reason / failure it prevents (one line). This is what lets the
   model generalize to new cases.
3. **How** — the concrete mechanics / numbers / field names (`duration_seconds`
   2.5–6s; readability floor 2.5s → ~8 video cuts).
4. **One example** — a single vivid instance, ideally from a real episode. Not five.

Prose and tight bullets, read top-to-bottom as a brief. A new reader should
understand *why* each rule exists, not just *what* it forbids.

## Editing methodology (every edit)

- **Integrate, don't append.** Find where the new guidance belongs and rewrite that
  section to absorb it. Never bolt a new dated block onto the end.
- **Supersede contradictions.** When new intent conflicts with old text, DELETE the
  old text — don't leave both. After editing, the file must not contradict itself.
- **No provenance in the body.** Drop `PD <date>: "<quote>"`, `(PD anger)`, "폐기한다"
  changelog narration. Git history holds who/when/why. The prompt states the rule as
  current truth.
- **Deduplicate.** If a point is made in three places, make it once, well.
- **Keep the mechanics.** Field names, numeric thresholds, pipeline facts (`ref mode`,
  `chain_from_prev`, validator BLOCK consequences) are load-bearing — preserve them,
  just fold them under the right principle.

## Refactor procedure (when a file has decayed, or when an edit touches a messy area)

1. Read the **whole** file, not just the target lines.
2. List the distinct *intents* present (incl. the new change), ignoring how they're
   currently worded. Note contradictions and duplicates.
3. Regroup by concept; order concepts logically (identity → format → mechanics → checks).
4. Rewrite each concept as principle→why→how→example, superseding stale/contradictory text.
5. Delete redundancy and dated narration.
6. Final pass: read straight through — does it flow as one coherent brief? Any
   remaining contradiction or rule-without-reason? Fix before saving.

## Before-save checklist

- [ ] Could a new reader infer the unstated cases from the principles? (generalizes)
- [ ] No verbatim quotes / dates / "폐기"/"anger" changelog narration in the body.
- [ ] No two passages contradict each other.
- [ ] Every rule has a visible *why* (or sits under a principle that supplies it).
- [ ] No point repeated in 3 places.
- [ ] Load-bearing mechanics (field names, numbers, pipeline facts) preserved.
- [ ] Reads top-to-bottom as a brief, not a flat checklist.

## Scope note

This is about *how* prompt text is written. It does NOT change the rules' meaning —
preserve every real constraint; only restructure how it's expressed. When in doubt
about whether a constraint still applies, ask PD rather than silently dropping it.
