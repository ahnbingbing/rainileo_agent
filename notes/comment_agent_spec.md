# Comment Agent — spec (PD 2026-11-12)

> PD: "댓글 남기는 에이전트 필요해. 내가 댓글을 다 못달고 있어." Engagement (replies → watch-time
> + algorithm + audience loyalty) is a real growth lever the channel is leaving on the table
> because PD can't keep up manually.

## What it does (confirmed scope)

**Find the UN-REPLIED viewer comments and reply to them** (PD 2026-11-12: "내가 댓글을 못단
것들을 찾고 거기에 다는 거") — the backlog PD can't keep up with, NOT every new comment. A comment
is a target iff **none of its replies are from our channel** (check each top-level comment's
replies for our `authorChannelId`; if PD/channel already answered it → SKIP). Reply in the
channel's pet-parent / 함미하비 character voice, grounded in the video the comment is on.

- NOT commenting on other channels (growth/outreach) — explicitly out for now.
- NOT the pinned first-comment on our uploads — **TODO, later** (PD: "아직은 필요 없을 듯").

## Approval flow (public-facing → the load-bearing decision)

**Safe categories auto-post; everything else drafts for PD approval** (PD: "안전 범주만 자동,
나머지 승인"). So the agent MUST classify before acting:

- **Auto-post (safe):** praise ("너무 귀여워요"), emoji-only, simple thanks, obvious positive
  affection. Low risk of a wrong/controversial reply.
- **Draft → PD approval (everything else):** questions (factual — could hallucinate a pet fact),
  anything emotional/sensitive, criticism/complaints, ambiguous, anything mentioning real people
  (함미하비/엄마), monetization/health/advice. When unsure → approval, never auto. (Mirror the
  caption anti-fabrication discipline: a reply that invents a pet fact is the same failure class.)

## Build components (when implemented — a focused session, not a rushed add-on)

1. **Fetch + filter to UN-REPLIED** — YouTube Data API `commentThreads.list` (part=`snippet,replies`)
   per recent channel video. ⚠️ needs the `youtube.force-ssl` OAuth scope (current token may only
   have upload/manage — verify + re-auth if missing). Keep a comment ONLY if **no reply under it is
   from our channel** (our `authorChannelId`) — that's PD's backlog; drop ones already answered.
   Also track already-handled comment IDs in the DB so the agent never replies twice.
2. **Classify** — safe-vs-needs-approval (cheap LLM or rules + LLM). Err toward approval.
3. **Generate reply** — character voice, grounded in THAT video's concept/captions (reuse the
   caption grounding + character facts so it can't invent traits). KO for KO comments, EN for EN.
4. **Act** — auto-post safe via `comments.insert`; queue the rest to Slack (board channel) with
   an approve/edit affordance (mirror the pd_reviewer veto-queue pattern).
5. **Schedule = BATCH poll, not real-time** (PD 2026-11-12: "답글은 배치로 돌던지"). A periodic
   cron (run_job.sh wrapper, same pattern as every other VM job), ~1–2×/day — frequent enough that
   fans aren't left waiting, rare enough to dodge spam/rate-limit. Each run processes the whole
   un-replied backlog in one pass: collect → classify → batch-post the safe ones → batch-append the
   rest to the approval queue. **Approval is batched too** — PD reviews the queued drafts together
   in Slack and approves in bulk (don't ping per comment). Per-run cap on auto-posts + queued items;
   log auto-posted vs queued counts. Kill-switch env (OFF until smoke + PD spot-check).

## Guardrails (carry the project's hard-won rules)

- **No fabrication** — a reply must not assert a pet fact not in CHARACTER_FACTS / the video.
  Unknown → approval or a safe non-committal reply, never a guess.
- **Idempotent** — never reply to the same comment twice (seen-set in DB).
- **Kill-switch** + OFF-by-default until smoke + PD spot-check (v2-rollback discipline).
- **Rate/cost caps** — bound replies per run; log what was auto-posted vs queued.

## Status

Spec only — NOT built. Next focused effort. cf [[harness_pairing_and_fantasy_gate]] (anti-
fabrication), pd_reviewer (approval-queue pattern), youtube/upload.py + youtube/oauth.py (API/scope).
