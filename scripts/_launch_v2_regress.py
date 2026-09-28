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

# ── orchestrator wiring (PD 2026-09-27) ──
import os

# helpers pick the right slots per role
check("_rf_slots(produce, fresh) = 5", launch_v2._rf_slots(p0, "rf_fresh") == sorted(rf0))
check("_av_slots(produce) = [20:00]", launch_v2._av_slots(p0) == ["20:00"])
check("_rf_slots(carry, carry) = 4", launch_v2._rf_slots(p1, "rf_carry") == sorted(rf1))
check("_av_slots(carry) = 2", launch_v2._av_slots(p1) == ["08:00", "20:00"])

# produce day's 4 carry pins align with the NEXT day's 4 carry slots
carry_slots_next = launch_v2._rf_slots(launch_v2.day_plan(d0 + dt.timedelta(days=1)), "rf_carry")
check("produce pins (5..9) map onto next-day carry slots",
      len(carry_slots_next) == 4, detail=f"carry_slots={carry_slots_next}")

# per-source filename tag is unique + filesystem-safe (no collisions across 3 sources)
tags = [launch_v2._safe_tag(sid, i) for i, sid in enumerate(["A", "B", "C"])]
check("_safe_tag unique across sources", len(set(tags)) == 3, detail=str(tags))
check("_safe_tag alnum-safe", all(t.replace("src", "").isalnum() for t in tags))
check("_safe_tag handles empty source_id", bool(launch_v2._safe_tag("", 7)))

# effective_assignments: flag OFF = live 4-slot; flag ON = v2 6-slot (both days)
from agents import launch as _launch
_prev = os.environ.pop("LAUNCH_MODEL", None)
try:
    off = _launch.effective_assignments(d0)
    check("flag OFF → 4-slot live grid", len(off) == 4, detail=f"{len(off)} slots")
    check("flag OFF → 12:30 present (legacy)", any(hh == "12:30" for _, hh in off))
    os.environ["LAUNCH_MODEL"] = "v2"
    os.environ["V2_START_DATE"] = "2020-01-01"   # test the STRUCTURE regardless of the flip date
    on_p = _launch.effective_assignments(d0)
    on_c = _launch.effective_assignments(d1)
    check("flag ON produce → 6 slots (5RF+1AV)",
          len(on_p) == 6 and sum(1 for l, _ in on_p if l == "real_footage") == 5,
          detail=str(on_p))
    check("flag ON carry → 6 slots (4RF+2AV)",
          len(on_c) == 6 and sum(1 for l, _ in on_c if l == "ai_vtuber") == 2,
          detail=str(on_c))
    check("flag ON → no legacy 12:30", not any(hh == "12:30" for _, hh in on_p))
finally:
    os.environ.pop("LAUNCH_MODEL", None)
    os.environ.pop("V2_START_DATE", None)
    if _prev is not None:
        os.environ["LAUNCH_MODEL"] = _prev

# enabled() reflects the flag
check("enabled() False without flag", launch_v2.enabled() is False)

# ── date-gating: v2 governs only dates >= V2_START_DATE (no transition phantom gaps) ──
_pm = os.environ.pop("LAUNCH_MODEL", None)
_ps = os.environ.pop("V2_START_DATE", None)
try:
    os.environ["LAUNCH_MODEL"] = "v2"
    os.environ["V2_START_DATE"] = "2026-09-30"
    check("active_for(before start) False", launch_v2.active_for(dt.date(2026, 9, 29)) is False)
    check("active_for(on start) True", launch_v2.active_for(dt.date(2026, 9, 30)) is True)
    check("active_for(after start) True", launch_v2.active_for(dt.date(2026, 10, 1)) is True)
    from agents import launch as _l2
    pre = _l2.effective_assignments(dt.date(2026, 9, 29))   # pre-flip day → 4-slot, NOT 6
    post = _l2.effective_assignments(dt.date(2026, 9, 30))  # v2 day → 6-slot
    check("pre-start day stays 4-slot (no phantom v2 slots)", len(pre) == 4, detail=f"{len(pre)}")
    check("start day is v2 6-slot", len(post) == 6, detail=f"{len(post)}")
    os.environ.pop("V2_START_DATE", None)  # unset → falls back to the built-in default (flip date)
    check("default start gates pre-flip dates", launch_v2.active_for(dt.date(2026, 9, 1)) is False)
    check("default start admits post-flip dates", launch_v2.active_for(dt.date(2026, 10, 5)) is True)
finally:
    os.environ.pop("LAUNCH_MODEL", None)
    os.environ.pop("V2_START_DATE", None)
    if _pm is not None:
        os.environ["LAUNCH_MODEL"] = _pm
    if _ps is not None:
        os.environ["V2_START_DATE"] = _ps

# ── AV role-swap gate: the 9/30 leak (a role-swap premise reached Seedance render) ──
from agents.producer import _av_role_swap_hit as _swap
_SWAP_HITS = [
    {"title": "자다가 우리 리듬이 바뀌어버렸다고요?!"},                    # 9/30 leak: 리듬(=루틴) 동의어
    {"cuts": [{"captions": [{"ko": "서로 바뀐 밥 그릇 앞, 랴니도 레오"}]}]},  # 9/30 leak: 수동 '바뀐'+물건 스왑
    {"title": "생활 패턴이 뒤바뀐 하루"}, {"title": "역할 반전 도전"},
    {"theme": "루틴 체인지"}, {"title": "서로의 역할을 바꿔보기"},
]
_SWAP_MISS = [  # innocent — must NOT trip (incl. the real 9/30 RF concepts)
    {"title": "레오가 낮잠 리듬을 탄다"}, {"title": "산책 루틴 브이로그"},
    {"cuts": [{"action": "밥 그릇 앞에서 기다린다"}]}, {"title": "풀숲 속 레오를 찾아라"},
    {"title": "같은 쇼파 다른 꿈"}, {"title": "뽀뽀는 싫어 근데 도망은 못 가"},
]
check("role-swap gate catches all swap premises (incl. 9/30 leak)",
      all(_swap(c) for c in _SWAP_HITS), detail=str([_swap(c) for c in _SWAP_HITS]))
check("role-swap gate no false-positive on innocent concepts",
      all(_swap(c) is None for c in _SWAP_MISS))

print()
if FAILS:
    print(f"REGRESS FAILED: {FAILS}")
    sys.exit(1)
print("LAUNCH V2 REGRESS: ALL PASS")
