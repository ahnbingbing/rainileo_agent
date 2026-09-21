#!/usr/bin/env python3
"""Regression: launch_v2 day_plan — 2-day cycle, 6 slots, Day2 AV timeliness shape.

Run: .venv/bin/python -m scripts._launch_v2_regress (exits non-zero on failure).
"""
import datetime as dt
import sys

from agents import launch_v2

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# Find a produce day and its following carry day.
d0 = dt.date(2026, 9, 28)
while not launch_v2.is_produce_day(d0):
    d0 += dt.timedelta(days=1)
d1 = d0 + dt.timedelta(days=1)

p0 = launch_v2.day_plan(d0)
p1 = launch_v2.day_plan(d1)


def roles(plan):
    return {hh: (s["lane"], s["role"]) for hh, s in plan["slots"].items()}


r0, r1 = roles(p0), roles(p1)

# ── 6 slots both days ──
check("6 slots", len(r0) == 6 and len(r1) == 6, detail=f"{len(r0)}/{len(r1)}")

# ── Day 1: 5 RF fresh + 1 AV @ 20:00 ──
rf0 = [hh for hh, (ln, ro) in r0.items() if ln == "real_footage"]
av0 = [hh for hh, (ln, ro) in r0.items() if ln == "ai_vtuber"]
check("Day1 = 5 RF + 1 AV", len(rf0) == 5 and len(av0) == 1, detail=f"rf={rf0} av={av0}")
check("Day1 AV @ 20:00", av0 == ["20:00"], detail=f"{av0}")
check("Day1 RF all fresh", all(r0[hh][1] == "rf_fresh" for hh in rf0))

# ── Day 2: 4 RF carried + 2 AV @ 08:00,20:00 ──
rf1 = [hh for hh, (ln, ro) in r1.items() if ln == "real_footage"]
av1 = sorted(hh for hh, (ln, ro) in r1.items() if ln == "ai_vtuber")
check("Day2 = 4 RF + 2 AV", len(rf1) == 4 and len(av1) == 2, detail=f"rf={rf1} av={av1}")
check("Day2 AV @ 08:00 & 20:00", av1 == ["08:00", "20:00"], detail=f"{av1}")
check("Day2 RF all carried", all(r1[hh][1] == "rf_carry" for hh in rf1))
check("Day2 AV timely", all(r1[hh][1] == "av_timely" for hh in av1))

# ── cycle alternates ──
check("consecutive days alternate produce/carry",
      launch_v2.is_produce_day(d0) and not launch_v2.is_produce_day(d1)
      and launch_v2.is_produce_day(d0 + dt.timedelta(days=2)))

# ── AV dominance branch math (top1 ≥ 2× top2 → dominant, else spread) ──
def _mode(v1, v2):
    # mirror day1_winners logic without DB
    if v2 is None:
        return "dominant"
    return "dominant" if v1 >= launch_v2.AV_DOMINANCE_RATIO * max(v2, 1) else "spread"


check("dominant when top1 ≥ 2× top2", _mode(400, 150) == "dominant", detail=_mode(400, 150))
check("spread when similar", _mode(300, 250) == "spread", detail=_mode(300, 250))
check("dominant when single winner", _mode(400, None) == "dominant")

print()
if FAILS:
    print(f"REGRESS FAILED: {FAILS}")
    sys.exit(1)
print("LAUNCH V2 REGRESS: ALL PASS")
