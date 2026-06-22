---
name: giri-update
description: Use when PD gives review feedback that should change how GIRI (the post-render reviewer, agents/reviewer.py) judges — i.e. a quality bar Giri must catch but currently passes. Giri is meant to be PD's mirror: the synthesized total of PD's accumulated standards, applied CRITICALLY. It drifts into a rubber-stamp because review-learning updates the GENERATORS (Writer/Director/caption prompts) but never syncs the CHECKER. This skill keeps Giri a living, skeptical mirror — folds the standard into Giri's lens as a hard score-cap, prefers deterministic enforcement over trusting the LLM, and verifies with a known-bad regression. Pairs in lockstep with review-learning.
---

# Giri-update — keep the reviewer a critical mirror of PD, not a rubber-stamp

## The problem this fixes

Giri is supposed to BE PD: every opinion PD has ever given, synthesized into one
review judgment, applied autonomously and critically to each rendered episode. It
drifted into a confident rubber-stamp — giving 9-10/10 and glowing praise
("캡션과 영상의 조화가 훌륭") to episodes that violate PD's own rules (caption≠clip,
time-jump with no POV marker, static story, background drift, subject under-shown).

Two structural reasons it drifted:
1. **review-learning fed the generators, not the checker.** New PD standards went
   into the Writer/Director/caption prompts but Giri's lens (agents/reviewer.py)
   was never updated in lockstep — so Giri checks against an old, partial standard
   and cannot deduct for what was never put in its lens.
2. **LLM reviewers default to praise.** Given sparse frames and a multi-dimension
   score, the model pattern-matches "looks on-brand" → high score. A soft note
   ("captions should match") doesn't move the score; the model still gives 8-9 and
   the 8/10 auto-ships.

This skill makes every PD review-bar land in Giri as something that **forces the
score DOWN**, and prefers a deterministic gate when the LLM can't be trusted.

## The loop (for each review-bar PD gives)

1. **Capture the bar + the worked example.** Which rendered episode violated it,
   and what exactly did PD see? The failing episode is the regression test later.
2. **Lockstep check.** If review-learning just added a GENERATOR rule (e.g. "state
   the timeframe on big time-gaps"), Giri needs the MATCHING checker rule. A
   generator rule with no Giri check = Giri falls behind again.
3. **Translate to a CAP, not a note.** Phrase it as detect → consequence:
   "If <violation visible in frames/captions> → flag in `개선점` AND score MUST NOT
   exceed N / verdict MUST be 수정 필요." Caps are the only lever that beats the
   model's praise instinct. Pick N by severity (lie/mismatch ≤5; missing
   POV-marker / bg-drift / static ≤6-7).
4. **Scope the lane.** AV vs real_footage vs both. (e.g. caption-density is RF-only;
   marking pixel-cap is AI-rendered cuts only.) Mis-scoped caps cause false fails.
5. **Prefer deterministic enforcement.** If the LLM can't reliably judge it from
   sparse frames (motion, time-jump, background sameness, subject screen-time),
   compute it in code (a gate / heuristic, like the RF grounding gate or the
   marking pixel check) and FEED Giri the boolean — don't rely on the model to
   notice. The LLM rubric is the fallback, the gate is the guarantee.
6. **Consistency audit.** Read the whole reviewer prompt; no two caps/exceptions
   contradict (the recurring "penalize surreal" vs "reward intentional surreal
   hook" trap). Supersede stale text; one standard stands.
7. **Fix the ship gate too.** A great review is wasted if `GIRI_PASS_VERDICTS`
   auto-ships the flawed verdict. Ensure "소폭 수정 후 업로드" either applies the
   revision before shipping or does NOT auto-publish (PD spot-check). Clean
   "업로드" is the only no-touch auto-ship.
8. **Verify with a known-bad regression.** Re-run Giri on the episode from step 1
   and confirm it now CATCHES the violation and caps the score. Do not trust a
   rubric edit you didn't see bite. Also re-run one KNOWN-GOOD episode to confirm
   you didn't introduce a false fail.
9. **Record.** Memory: the bar + why + the cap + where it lives + the lane scope.

## Non-negotiables

- **Cap, don't suggest.** Every quality bar = a score cap / verdict floor. The LLM
  rubber-stamps; a soft note changes nothing.
- **Deterministic beats trusting the LLM.** When feasible, gate it in code and feed
  Giri the verdict. Reserve the LLM for genuinely subjective calls.
- **Lockstep with review-learning.** Touching a generator rule? Add/confirm the
  matching Giri check in the same pass. They are one change in two places.
- **Default skeptical.** Giri's prompt should instruct it to assume defects until
  verified per-cut, not to praise on-brand-looking output.
- **Regression-verified.** A known-bad episode must fail and a known-good must pass
  before the change is done.

## Scope note

Giri's lens lives in `agents/reviewer.py` (the review system prompt: CHECK 0,
scoring dimensions, the 판정/점수 verdict map) with appearance canon imported from
`agents/canon.py` (REVIEW_RYANI / REVIEW_LEO). The auto-ship gate is
`GIRI_PASS_VERDICTS` in `agents/producer.py`. Pairs with `review-learning` (the
generator side) and `prompt-authoring` (how to write the edit). This skill is the
reviewer-side half: it exists so the CHECKER never falls behind the standards the
generators are already being held to.
