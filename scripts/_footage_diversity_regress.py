#!/usr/bin/env python3
"""Regression: footage-diversity gate judges CLIPS, not concept.

PD 2026-09-21: "산책 3개는 괜찮아 — 동영상이 똑같으면 안 돼." Diversity is about the
footage, not the theme. Run: .venv/bin/python -m scripts._footage_diversity_regress
(exits non-zero on any failure).
"""
import sys

from agents.footage_diversity import overlap_ratio, violations

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def src(sid, cast):
    return {"source_id": sid, "cast": cast}


def clip(aid, start=0, dur=8):
    return {"asset_id": aid, "trim_start": start, "trim_dur": dur}


# ── 1. Three walks, DIFFERENT clips → NO violation (theme overlap is fine) ──
walks = [
    src("A", [clip("med_ryani_walk_1"), clip("med_ryani_walk_2")]),
    src("B", [clip("med_leo_walk_1"), clip("med_leo_walk_2")]),
    src("C", [clip("med_both_walk_1"), clip("med_both_walk_2")]),
]
check("3 walks, different clips → pass", not violations(walks),
      detail=f"violations={violations(walks)}")

# ── 2. Three sources on the SAME clips → violation (reroll) ──
same = [
    src("A", [clip("med_X"), clip("med_Y")]),
    src("B", [clip("med_X"), clip("med_Y")]),
    src("C", [clip("med_X"), clip("med_Y")]),
]
v = violations(same)
check("3 identical casts → violations flagged", len(v) == 3 and all(r >= 0.99 for *_, r in v),
      detail=f"{v}")

# ── 3. Small shared segment (< 60%) → pass ──
# A = 16s across two clips; shares only 4s of one clip with B → 4/16 = 25% < 60%.
small = [
    src("A", [clip("med_shared", 0, 8), clip("med_A_only", 0, 8)]),
    src("B", [clip("med_shared", 4, 8), clip("med_B_only", 0, 8)]),  # shares 4s (4-8) of med_shared
]
r = overlap_ratio(small[0], small[1])
check("small shared segment → pass", not violations(small), detail=f"ratio={r:.2f} (want <0.6)")

# ── 4. Heavy overlap (≥ 60%) on shared clips → violation ──
heavy = [
    src("A", [clip("med_P", 0, 10), clip("med_Q", 0, 6)]),           # 16s
    src("B", [clip("med_P", 0, 10), clip("med_B_only", 0, 4)]),      # shares all 10s of med_P → 10/14
]
r = overlap_ratio(heavy[0], heavy[1])
check("heavy shared footage → violation", bool(violations(heavy)),
      detail=f"ratio={r:.2f} (want ≥0.6)")

# ── 5. Same clip, NON-overlapping trims → pass (different moments of one long clip) ──
splits = [
    src("A", [clip("med_long", 0, 10)]),
    src("B", [clip("med_long", 20, 10)]),   # different 10s window of the same source clip
]
r = overlap_ratio(splits[0], splits[1])
check("same clip, disjoint trims → pass", not violations(splits), detail=f"ratio={r:.2f} (want 0)")

# ── 6. Empty / unmeasurable cast → no crash, no false violation ──
empty = [src("A", []), src("B", [clip("med_Z")])]
check("empty cast → no crash/violation", not violations(empty), detail=f"ratio={overlap_ratio(*empty):.2f}")

print()
if FAILS:
    print(f"REGRESS FAILED: {FAILS}")
    sys.exit(1)
print("FOOTAGE DIVERSITY REGRESS: ALL PASS")
