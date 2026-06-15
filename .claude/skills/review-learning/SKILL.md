---
name: review-learning
description: Use when PD gives manual review feedback on any output (a rendered episode, stills, captions, a clip choice) that should make the PIPELINE smarter — not just fix this one item. Turns a one-off correction into a durable, generalized rule, integrated into the right prompt/gate/stage across both lanes, AFTER auditing it for consistency and contradictions with everything already there. Prevents PD from giving the same note twice.
---

# Review-learning — turn PD's manual review into pipeline learning, safely

## The problem this fixes

Every session PD spot-reviews output and gives sharp notes ("captions too old-timey",
"why is a human the subject?", "two RF episodes are the same", "grass clip but caption
says splash"). If we only fix THIS episode, the pipeline re-makes the same mistake
tomorrow and PD has to repeat the note. The cost is exactly what PD complained about:
"오케스트라 안 됨, 각자 퀄 낮음" — fixes don't compound.

This skill makes the **pipeline learn** from each review: extract the principle, put it
where the pipeline will apply it next time, and — critically — do that WITHOUT
contradicting what's already there. A learned rule that conflicts with an existing one
silently cancels both and confuses the model. So learning is gated on a consistency
audit, every time.

## The loop (run for every piece of review feedback worth keeping)

1. **Capture the note + the evidence.** What did PD see, and in which artifact (episode
   id / cut / still)? Keep the concrete failure — it becomes the worked example.

2. **Classify: durable rule vs situational call vs date-aware.** Not every note is a
   universal law.
   - *Durable* → institutionalize (e.g. "AV character ref must be a real photo").
   - *Situational* (a one-off taste call for this episode) → just fix the item, do NOT
     bake a rule. Forcing a situational note into a prompt over-constrains future work.
   - *Date/context-aware* → the rule is conditional, and the condition must be written
     in (e.g. topic-dedup is *same publish window*, not a forever ban on "첫"). State the
     condition explicitly so the model doesn't over-apply it.
   When unsure which, ask PD rather than guessing.

3. **Generalize to a principle.** Restate the note as intent + why, so it covers cases
   PD didn't enumerate. "Don't say 첨벙 on this grass clip" → principle "footage-first:
   never write an event the clip doesn't show; the arc serves the clips."

4. **Locate every consumer.** Which prompt / gate / validator / reviewer / schema, in
   BOTH lanes (ai_vtuber + real_footage), would enforce or violate this? (Compose
   `pipeline-change-impact` — grep the behavior, triage read/enforce/render.)

5. **CONSISTENCY AUDIT (mandatory — the gate before writing anything).**
   - Grep the target prompt(s) AND memory (`MEMORY.md` + the memory files) for rules on
     the same topic. List every related/overlapping/contradicting passage.
   - For each: does the new principle AGREE, REFINE, or CONTRADICT it?
   - If CONTRADICT → you must SUPERSEDE: rewrite/delete the old text so only one rule
     stands. Never leave both (the recurring "단일 장소 절대 금지" vs "멀티-로케 OK" trap).
   - If a memory says the opposite of the new note, the memory is now stale → update or
     delete it.
   - Output the audit: "found X related rules; 2 agree, 1 contradicts (superseded)."

6. **Integrate, principle-first.** Edit the prompt via `prompt-authoring` (integrate into
   the right section, no dated band-aid blocks). Add/loosen a programmatic gate if the
   rule needs enforcement code (e.g. duplicate-subject guard, subject-prominence).

7. **Verify end-to-end.** Re-render or dry-run one affected episode and confirm the
   learned behavior actually appears downstream — and that nothing it superseded broke.

8. **Record.** Write/update one memory file (principle + why + how-to-apply + the worked
   example), link related memories with [[name]], and add the MEMORY.md pointer. This is
   how the lesson survives the session.

## The non-negotiables PD called out

- **정합성 무조건.** Before institutionalizing, you MUST reconcile with existing content.
  No new rule ships until the consistency audit (step 5) is done and the file/memory set
  has zero internal contradictions.
- **모순 철저 확인.** After editing, read the whole target file straight through (not just
  the diff) and confirm no two passages disagree. Same for memory: a new memory must not
  assert the opposite of an existing one — supersede the stale one.
- **날짜를 보라.** Time-relative notes (freshness, topic-dedup, "recently did X") encode
  the *date/window* as part of the rule, never as an absolute.

## Before-done checklist

- [ ] Classified durable / situational / date-aware (situational → fixed item only, no rule).
- [ ] Note generalized to a principle with a visible *why*.
- [ ] Consistency audit done: every related rule in prompts + memory found and triaged.
- [ ] Contradictions SUPERSEDED (old text rewritten/deleted; not left alongside).
- [ ] Stale memory updated/deleted if it now disagrees.
- [ ] Integrated principle-first (prompt-authoring), propagated across both lanes
      (pipeline-change-impact), gate code added if enforcement needed.
- [ ] One end-to-end render/dry-run confirms the behavior + no regression.
- [ ] Memory written + MEMORY.md pointer added; related memories linked.
- [ ] Report to PD: what was learned, where it went, what it superseded, what was verified.

## Scope note

Pairs with `prompt-authoring` (how to write the edit) and `pipeline-change-impact` (where
the edit must ripple). This skill is the wrapper that decides *whether* a note becomes a
rule, *audits it against the existing corpus*, and *closes the loop into memory* — so the
pipeline gets monotonically smarter instead of accreting contradictions.
