You are running the Giri review-learning pass for the Ryani & Leo Shorts pipeline,
fired every 3 DAYS (PD 2026-06-21, FULL AUTO with frequent re-check, temporary "until
the pipeline stabilizes"). You start with zero conversation context — this prompt is
self-contained.

GOAL: turn the past week's PD review feedback into durable improvements to the Giri
reviewer, so PD stops having to repeat the same notes. Then report what changed so PD
can veto/revert.

DO THIS:
1. Read CLAUDE.md and the `review-learning` skill (.claude/skills/review-learning/SKILL.md).
   Follow that skill's loop exactly — especially the mandatory CONSISTENCY AUDIT before
   editing anything.

2. Gather the LAST 3 DAYS of PD review feedback from these sources:
   - Slack `ryaleo-board` channel (SLACK_BOARD_CHANNEL in .env) — read recent messages via
     the Slack API (reuse slack/ helpers or slack_sdk + SLACK_BOT_TOKEN). PD corrections,
     complaints, taste notes.
   - DB `data/agent.db`: vetoes / `pd_selections` (agents/pd_taste.py), `board_escalations`,
     any review-related notes. Vetoes are the strongest signal of what PD rejects.
   - Recent git log / session handoff notes in notes/ for corrections already discussed.

3. For each recurring, DURABLE note (skip one-off situational taste calls): generalize it
   to a principle, run the consistency audit across agents/reviewer.py (REVIEW_PROMPT),
   agents/canon.py (REVIEW_RYANI / REVIEW_LEO), notes/shorts_review_agent_giri.md, and the
   memory files. SUPERSEDE contradictions (never leave two rules disagreeing). Then
   integrate principle-first (use the prompt-authoring skill). Add a programmatic gate only
   if enforcement truly needs code.

4. SAFETY (PD has been burned by additive fixes that net-degraded quality):
   - Be conservative. Only institutionalize notes that clearly recur or that PD stated as a
     rule. When in doubt, REPORT it as a candidate rather than applying it.
   - Make the SMALLEST principled edit. Do not rewrite working sections wholesale.
   - Leave all changes UNCOMMITTED (working tree) for PD review. Do NOT git commit/push.
   - After editing, read each touched file straight through and confirm no two passages
     contradict.

5. Update/add a memory file per durable rule learned (+ MEMORY.md pointer), linking related
   memories — same as the review-learning skill step 8.

6. REPORT to the `ryaleo-board` Slack channel (post via SLACK_BOT_TOKEN): a concise summary —
   (a) what feedback was found, (b) which durable rules you applied + where, (c) what you
   SUPERSEDED, (d) candidates you did NOT apply (and why), (e) `git status --short` of the
   touched files so PD can review/revert. Title it ":mag: Giri 학습(3일) — <date>".

7. If there was NO actionable feedback in the last 3 days, make NO edits and post a one-line
   "최근 3일 반영할 검수 피드백 없음" to the board.

Scope guard: only touch Giri-review files (agents/reviewer.py, agents/canon.py REVIEW_*,
notes/shorts_review_agent_giri.md) + memory. Do not change render/launch behavior here.
