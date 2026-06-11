#!/usr/bin/env python3
"""Canon guard — fails if a stale/wrong character fact reappears anywhere.

This is the safety net behind agents/canon.py (the single source of truth). Even
though Python consumers now import their canon blocks, the prompt .md files still
carry their own copies (Phase 1). This guard makes it impossible for a *wrong*
value to silently survive a correction: it scans the repo for affirmative stale
values (e.g. "amber eyes" for Leo, a swapped age) and exits non-zero if any
appears without a negation/correction marker on the same line.

Run: python3 scripts/check_canon.py    (exit 0 = clean, 1 = stale value found)
Wire into CI / pre-commit. When you correct a fact in agents/canon.py, run this
to catch every place the old value lingers.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files/dirs to scan for stale canon. The source of truth and this guard are
# exempt (they legitimately name the forbidden values).
SCAN = [
    "agents/cameraman.py", "agents/arc.py", "agents/producer.py",
    "agents/writer_director.py", "scripts/generate_character_scene.py",
    "CLAUDE.md",
]
SCAN_GLOBS = ["agents/prompts/*.md", "agents/*.py"]
EXEMPT = {"agents/canon.py", "agents/models.py", "scripts/check_canon.py"}

# A match is ALLOWED (not a violation) if any of these markers is on the line —
# i.e. the forbidden value is being negated or documented as a past mistake.
OK_MARKERS = re.compile(
    r"\bnot\b|\bno\b|\bnever\b|\bisn't\b|\bwrong\b|\bearlier\b|\bcorrect|"
    r"정정|금지|아님|아니|틀림|대신|❌|past|historical|old\b",
    re.IGNORECASE)

# Forbidden affirmative values: (regex, human description).
FORBIDDEN = [
    (r"\bgpt-5\b", "gpt-5 as a model (reasoning model → empty/timeout; use models.OPENAI_TEXT)"),
    (r"gold[- ]?amber",            "Leo eyes 'gold-amber' (wrong — chartreuse/yellow-green)"),
    (r"amber[- ]?gold",            "Leo eyes 'amber-gold' (wrong — chartreuse/yellow-green)"),
    (r"\bamber eyes\b",            "Leo eyes 'amber' (wrong — chartreuse/yellow-green)"),
    (r"\bgold(?:en)? eyes\b",      "Leo eyes 'gold' (wrong — chartreuse/yellow-green)"),
    (r"\b(?:wide|thick) (?:white )?blaze\b", "Ryani 'wide/thick blaze' (wrong — THIN narrow)"),
    (r"\brose ear|\bfolded ear|\bdrop ear", "Ryani 'rose/folded/drop ears' (wrong — UPRIGHT bat ears)"),
    # Age SWAP only — Leo directly tagged 11살, or Ryani directly tagged 8개월.
    # Direct adjacency (\s* only) so a correct "8개월 레오 vs 11살 랴니" comparison
    # — where the ages belong to the OTHER pet across a 'vs' — does not trip it.
    (r"11\s*살\s*레오|레오[는은가이]?\s*11\s*살", "Leo age 11 (wrong — Leo is 8개월)"),
    (r"8\s*개월\s*랴니|랴니[는은가이]?\s*8\s*개월", "Ryani age 8개월 (wrong — Ryani is 11살)"),
]

# canon.py must positively assert these (a bad 'correction' would drop them).
REQUIRED_IN_CANON = ["chartreuse", "SPAYED FEMALE", "NO TAIL", "THIN narrow"]


def _iter_files():
    seen = set()
    for rel in SCAN:
        p = ROOT / rel
        if p.exists():
            seen.add(p); yield p
    for g in SCAN_GLOBS:
        for p in sorted(ROOT.glob(g)):
            if p not in seen:
                seen.add(p); yield p


def main() -> int:
    violations: list[str] = []

    for path in _iter_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in EXEMPT:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            if OK_MARKERS.search(line):
                continue  # negated / documented-as-wrong → fine
            for pat, desc in FORBIDDEN:
                if re.search(pat, line, re.IGNORECASE):
                    violations.append(f"  {rel}:{i}  {desc}\n      › {line.strip()}")

    # Positive assertion: canon.py still holds the correct values.
    canon_txt = (ROOT / "agents" / "canon.py").read_text(encoding="utf-8")
    missing = [t for t in REQUIRED_IN_CANON if t not in canon_txt]
    if missing:
        violations.append(f"  agents/canon.py MISSING required canon tokens: {missing}")

    if violations:
        print("✗ CANON GUARD FAILED — stale/wrong character facts found:\n")
        print("\n".join(violations))
        print("\nFix: the truth lives in agents/canon.py. Correct it there, then "
              "update or remove the stale copy above (or negate it explicitly).")
        return 1

    print("✓ canon guard clean — no stale character facts found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
