"""Regression for the title↔CAST subject gate (PD 2026-10-29).

Known-bad: the Halloween "박쥐 레오의 첫 임무" shipped over Ryani-only (black-dog-in-bat-
costume) footage — the title named Leo, the cat, who is not in the clip. The gate must
swap the wrong name to the pet actually in the cast, leave correct/both-pet/ambiguous
titles untouched, and never fire when it can't ground the cast.

  PYTHONPATH=. .venv/bin/python scripts/_title_cast_regress.py
"""
from __future__ import annotations

from agents.producer import _title_cast_subject_gate, _title_named_pets

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ── the known-bad case: title names Leo, footage is Ryani-only ──
t, d, act = _title_cast_subject_gate("박쥐 레오의 첫 임무: 풀 냄새 수사 🦇",
                                     "박쥐 레오가 카페로 출동!", {"ryani"})
check("KNOWN-BAD 박쥐 레오→랴니 (title)", t == "박쥐 랴니의 첫 임무: 풀 냄새 수사 🦇", detail=t)
check("KNOWN-BAD desc corrected too", d == "박쥐 랴니가 카페로 출동!", detail=d)
check("KNOWN-BAD action label", act == "leo→ryani", detail=str(act))

# ── English name, whole-word ──
t, d, act = _title_cast_subject_gate("Leo the bat is on patrol", None, {"ryani"})
check("EN Leo→Ryani", t == "Ryani the bat is on patrol", detail=t)

# ── correct title stays untouched ──
t, d, act = _title_cast_subject_gate("랴니 낮잠 자는 중", None, {"ryani"})
check("correct title unchanged", t == "랴니 낮잠 자는 중" and act is None, detail=f"{t}/{act}")

# ── leo-only footage, leo title: fine ──
t, d, act = _title_cast_subject_gate("레오 식탁 감시", None, {"leo"})
check("leo title + leo cast unchanged", act is None, detail=str(act))

# ── both pets in cast: never correct (title may name either) ──
t, d, act = _title_cast_subject_gate("박쥐 레오의 첫 임무", None, {"ryani", "leo"})
check("both-pet cast → no change", act is None, detail=str(act))

# ── cast unknown (empty) → cannot ground, no change ──
t, d, act = _title_cast_subject_gate("박쥐 레오의 첫 임무", None, set())
check("empty cast → no change", act is None, detail=str(act))

# ── ambiguous: names BOTH but cast is single pet → FLAG, don't rewrite ──
t, d, act = _title_cast_subject_gate("랴니 & 레오의 할로윈 데뷔", None, {"ryani"})
check("names-both, single cast → flag not rewrite",
      t == "랴니 & 레오의 할로윈 데뷔" and str(act).startswith("flag:"), detail=f"{t}/{act}")

# ── no false name hit: '레오' substring guard (there's no such word here) ──
check("named-pets detects KO+EN", _title_named_pets("Leo와 랴니") == {"leo", "ryani"})
check("named-pets empty when none", _title_named_pets("고양이와 강아지") == set())

print("\n" + ("TITLE-CAST REGRESS: ALL PASS" if not FAILS else f"FAILED: {FAILS}"))
raise SystemExit(1 if FAILS else 0)
