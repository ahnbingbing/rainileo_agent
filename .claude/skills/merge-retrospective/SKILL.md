---
name: merge-retrospective
description: Use right after merging work to main (a commit/push of durable changes). Before calling the merge done, record what this merge SHIPPED and what it TAUGHT — especially failures, rollbacks, and root causes — into the project retrospective (notes/retrospective_2026-05_to_07.md), synthesized principle-first into the right existing section, NOT a dated changelog dump. Keeps the retrospective the living spine of the project so the same failures don't recur. Pairs with prompt-authoring (how to write it) and pipeline-change-impact (what the blast radius was).
---

# Merge-retrospective — every merge feeds the living memory

## The problem this fixes

The retrospective is this project's **durable memory**: "지운 실패는 반복된다 — 롤백한 것이
다음 사람의 지도가 된다." But memory only works if it's *fed*. Durable fixes get
committed and the lesson evaporates — the root cause lives only in a progress-log line
and a commit message, so months later the same trap gets re-hit because nobody wrote
down *why* it was a trap. A merge that changes behavior but leaves no trace in the
retrospective is a half-finished merge.

This skill makes "record what happened + what it taught" a **mandatory step of merging**,
not an optional afterthought — the same discipline as leaving failures in the log,
turned into a habit that fires on every merge.

## When it fires

After you **merge to main** — i.e. commit/push a shipped unit of durable work (a fix, a
gate, a schema change, a rollback). Not every scratch edit; a *merge*. If the work was
purely mechanical with nothing learned and nothing that changes the project's shape,
one line under the numbers is enough — but the default is: **it goes in.**

## What to record

Two things, and the second matters more:

1. **What shipped** — the durable change(s), one tight line each: the gate/fix, where it
   lives, which lane(s) it touches. This updates the architecture/numbers picture.
2. **What it taught** — the lesson, especially the **failure → root cause → fix spine**
   (the §4 material) and any new **rollback** (something tried then abandoned, and why).
   A merge that fixed a recurring bug should leave behind the *principle* that stops the
   next instance, not just "fixed X."

Prefer the lesson. A retrospective full of "shipped X, shipped Y" with no root causes is
a changelog; the value is in the spine of *why things broke and what generalizes*.

## How to write it (principle-first — same discipline as prompt-authoring)

- **Integrate into the right existing section, don't append a dump.** The retrospective
  has a shape: §1 numbers, §3 agent differentiation / deterministic gates, §4 failures &
  rollbacks (§4.2 AV, §4.3 Giri, §4.4 RF, §4.5 infra, §4.6 strategy), §5 architecture,
  §6 meta-lessons. Find where the lesson belongs and fold it in. A new deterministic gate
  → §3.3 + the failure it fixes → §4.x. A rollback → the §4.1 quick-ref table + prose.
- **Synthesize, don't transcribe.** No `PD <date>: "<quote>"`, no "폐기한다" changelog
  narration in the body. State the lesson as current truth with its *why*; git and the
  progress log hold provenance. (This is the prompt-authoring rule applied to the retro.)
- **Principle → why → how → one example.** Each entry: the principle that generalizes,
  the failure it prevents, the concrete mechanism/file, one vivid real instance.
- **Supersede, don't stack.** If this merge overturns an earlier lesson, edit that entry —
  don't leave two that disagree.
- **Numbers current.** If the merge changed a load-bearing number (agent count, a
  calibrated price, commit total), update §1 too.

## One example

This session recalibrated the cost ledger and fixed Giri over-penalizing photoreal AV.
The retrospective didn't get "PD said av looking like rf is fine (7/4)" appended — it got
**B5 in §4.3**: *"Giri가 photoreal AV를 reject — 러버스탬프의 역방향"* (an over-penalty is
also a checker failure), with the root (LOOK vs CONTENT conflation), the fix, and the
regression (2→9). The reader learns a generalizable principle about reviewers, not a
diary entry.

## Composition

- **prompt-authoring** — *how* to write the entry (this skill points at the retrospective;
  that skill governs the prose).
- **pipeline-change-impact** — run *before* the merge to know the blast radius; this skill
  records the result *after*.
- Progress log (`agents/progress_log.log_progress`) is the running ledger for board↔CLI
  handoff; the retrospective is the *synthesized* memory. Both, not either.
