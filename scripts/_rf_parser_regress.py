#!/usr/bin/env python3
"""Regression: _robust_json_parse must extract the concept, not a prose trim-range.

9/23 empty-batch root (PD 2026-09-21): the grounding-heavy clip-reading prose writes
usable trim ranges like "[0,8] only, 155s long", and the parser returned the FIRST
parseable balanced slice → json.loads("[0,8]") → [0, 8] → dict-filter → 0 concepts →
no_concept → empty RF slot → slot_topup/self-heal runaway. Every one of the 15 saved
9/23 artifacts held a full, valid 5–6 cut concept the parser threw away.

A concept payload is ALWAYS an object or an array containing objects — never a bare
[int,int]. This locks that in. Run: .venv/bin/python -m scripts._rf_parser_regress
(exits non-zero on any failure). Also sweeps data/output/artifacts/realfootage_*.json
when present so a live re-run proves the real payloads recover.
"""
import glob
import json
import sys

from agents.producer import _robust_json_parse as P

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ── 1. The exact 9/23 shape: trim-range prose BEFORE a top-level concept array ──
RAW = """Looking at the available assets, I need a fresh concept.

**STEP 1 — Clip Reading:**
- `med_2026_08_21_001044`: 랴니+레오 play_bow — usable [0,8] only, 155s long!
- `med_2026_08_13_123931`: 카페 — [0,42] only, 69s
- `med_2026_08_25_001657`: 레오 발라당 [0,29] all

Chosen arc below.

```json
[{"title": {"ko": "오늘도 무사히"}, "tone": "warm", "cuts": [
  {"tag": "cut1", "action": "랴니 산책", "captions": [{"start":0.2,"end":4.0,"ko":"가자","en":"Go"}]},
  {"tag": "cut2", "action": "레오 발라당", "captions": [{"start":0.2,"end":4.0,"ko":"발라당","en":"flop"}]}
]}]
```"""
r = P(RAW, allow_llm_repair=False)
check("singlepass-array picks concept over [0,8]",
      isinstance(r, list) and len(r) == 1 and isinstance(r[0], dict)
      and len(r[0].get("cuts", [])) == 2,
      detail=f"cuts={len(r[0].get('cuts', [])) if isinstance(r, list) and r else 'n/a'}")

# ── 2. bare single-concept dict with cuts, prose range in front ──
r2 = P('prose [0,5] blah\n{"cuts":[{"tag":"a"}], "tone":"x"}', allow_llm_repair=False)
check("single concept dict recovered", isinstance(r2, dict) and len(r2.get("cuts", [])) == 1)

# ── 3. generic non-concept JSON still parses (fallback preserved) ──
r3 = P('here: {"a": 1, "b": 2}', allow_llm_repair=False)
check("generic dict still parses", r3 == {"a": 1, "b": 2})

# ── 4. bare range only → no crash (legacy first-parseable fallback) ──
try:
    P("trim [0,8]", allow_llm_repair=False)
    check("bare range no crash", True)
except Exception as e:  # noqa: BLE001
    check("bare range no crash", False, detail=str(e))

# ── 5. live sweep: every saved artifact must now recover its concept ──
arts = sorted(glob.glob("data/output/artifacts/realfootage_*.json"))
if arts:
    print(f"  -- live artifact sweep ({len(arts)} files) --")
    bad = 0
    for f in arts[-40:]:
        try:
            raw = (json.load(open(f)).get("raw_llm_text") or "")
        except Exception:
            continue
        if not raw.strip():
            continue
        try:
            v = P(raw, allow_llm_repair=False)
        except Exception:
            v = None
        concept = (v[0] if isinstance(v, list) and v and isinstance(v[0], dict)
                   else (v if isinstance(v, dict) else None))
        n = len(concept.get("cuts", [])) if concept else 0
        if n == 0:
            bad += 1
            print(f"     ZERO-CUTS {f.split('/')[-1]}")
    check("all recent artifacts recover a concept", bad == 0, detail=f"{bad} zero-cuts")
else:
    print("  -- no local artifacts (run on VM to sweep the real 9/23 batch) --")

print()
if FAILS:
    print(f"REGRESS FAILED: {FAILS}")
    sys.exit(1)
print("RF PARSER REGRESS: ALL PASS")
