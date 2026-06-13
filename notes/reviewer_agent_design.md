# Design — Reviewer agent (macro, audience-aware) + Writer rewrite loop

> Status: DESIGN (not built). PD 2026-06-13. Separate from Giri (single-episode,
> post-render, tactical QC) — Giri stays as-is. The Reviewer is a MACRO, strategic,
> audience-informed second opinion that shapes the STORY before render and feeds back
> to Writer/Director.

## Why
Giri reviews ONE episode in isolation (no cross-episode / audience awareness), so it
missed: the near-duplicate-of-yesterday clip, and broader "is this what the audience
responds to" judgment. The Reviewer fills that macro gap.

## What the Reviewer sees (macro context, fetched ONCE per run, cached)
- **Real YouTube comments** on recent uploads (audience sentiment, requests, what
  landed) — needs a comments fetch (YouTube Data API commentThreads).
- **Popularity / performance** — views, retention, CTR from `youtube/analytics.py` →
  `video_performance` (already exists; bandit consumes it).
- **Last ~7 days of videos** — themes/clips/tones already used (cross-episode dedup &
  variety: "patty clip again", "3rd vlog in a row", what over/under-performed).
Cache the bundle per producer run; do NOT re-fetch per rewrite (slow + API quota).

## Flow (PD 2026-06-13)
```
[macro context: YT comments + performance + last-7-days]  ← fetched once, cached
   → Writer initial draft (macro context injected from the START)
   → Reviewer reviews (macro perspective) ──pass──→ Director
                                          └─fail──→ Writer rewrite (Reviewer feedback
                                                     injected), loop ≤ REVIEWER_MAX_REWRITES (5)
```
- **Stop early**: Reviewer passes as soon as the story is good → usually 0–1 rewrites;
  5 is a HARD ceiling for bad cases only (Writer is 3-pass Opus — don't always burn 5).
- Reviewer feedback flows to the **Writer** (rewrite) and its macro guidance also informs
  the **Director** (and is available at INITIAL generation, not only on retry).

## Reviewer's verdict (macro lens — NOT Giri's per-frame QC)
- Audience fit: does this match what comments/performance say resonates?
- Freshness / variety: too similar to a recent episode (clip/theme/tone repetition)?
- Strategic value: does it advance the channel (series arc, requested content)?
- Returns {pass: bool, rewrite_directive: str, macro_notes: str}.

## Build sketch
- `agents/prompts/reviewer_agent.md` — macro persona + the verdict schema.
- `agents/reviewer_macro.py` (or in writer_director): `fetch_macro_context()` (comments
  + analytics + last-7-days), `run_reviewer(draft, macro_ctx)`.
- YT comments: add to `youtube/` (commentThreads.list per recent video_id).
- Wire into `writer_director.propose_concepts_v2`: fetch macro ctx → inject into Writer
  draft input → after Writer, run Reviewer → ≤5 rewrite loop → Director.
- Env: `REVIEWER_AGENT=1`, `REVIEWER_MAX_REWRITES=5`, cache TTL for macro ctx.

## Open questions
- Comments volume/cost: cap to top-N recent videos × top-M comments.
- Does the Reviewer also gate real_footage (single-pass writer), or AV-only first?
  (RF uses `_propose_realfootage_singlepass`, not propose_concepts_v2 — would need its
  own hook.)
- Cold start (no uploads yet / launch week): Reviewer runs with empty macro ctx → should
  pass-through (no audience data → don't block).
