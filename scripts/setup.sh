#!/usr/bin/env bash
# scripts/setup.sh — Phase 0 one-shot setup for macOS.
#
# Run from the repo root:
#     bash scripts/setup.sh
#
# What it does (in order):
#   1. Verifies Homebrew, installs uv (Python package manager) if missing.
#   2. Creates a Python 3.12 venv at .venv/ (uv handles the Python install too).
#   3. Installs requirements.txt.
#   4. Copies .env.example -> .env if .env doesn't exist yet.
#   5. Initializes the SQLite DB and seeds milestones.
#   6. Validates the 2026-05-10 first card against the schema.
#
# Idempotent — safe to re-run.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

c_ok()    { printf "\033[32m[ok]\033[0m   %s\n" "$*"; }
c_step()  { printf "\033[36m[step]\033[0m %s\n" "$*"; }
c_warn()  { printf "\033[33m[warn]\033[0m %s\n" "$*"; }
c_die()   { printf "\033[31m[fail]\033[0m %s\n" "$*"; exit 1; }

# ──────────────────────────────────────────────────────────────────────
# 1. Homebrew + uv
# ──────────────────────────────────────────────────────────────────────
c_step "checking Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  c_warn "Homebrew not found. Install from https://brew.sh first, then re-run."
  c_die "missing brew"
fi
c_ok "brew $(brew --version | head -1)"

c_step "checking uv (single-binary Python+pip replacement)"
if ! command -v uv >/dev/null 2>&1; then
  c_step "installing uv via brew"
  brew install uv
fi
c_ok "uv $(uv --version)"

# ──────────────────────────────────────────────────────────────────────
# 2. venv (Python 3.12 — uv installs it if absent)
# ──────────────────────────────────────────────────────────────────────
if [[ -d .venv ]]; then
  PY_IN_VENV="$(./.venv/bin/python --version 2>&1 || echo none)"
  if [[ "$PY_IN_VENV" != *"3.12"* ]]; then
    c_warn "existing .venv is $PY_IN_VENV — recreating with Python 3.12"
    rm -rf .venv
  fi
fi

if [[ ! -d .venv ]]; then
  c_step "creating .venv with Python 3.12 (uv will fetch it if needed)"
  uv venv --python 3.12 .venv
fi
c_ok ".venv ready ($(./.venv/bin/python --version))"

# ──────────────────────────────────────────────────────────────────────
# 3. install deps (uv pip is ~10x faster than pip)
# ──────────────────────────────────────────────────────────────────────
c_step "installing requirements.txt (uv pip install)"
uv pip install --python ./.venv/bin/python -r requirements.txt
c_ok "deps installed"

# ──────────────────────────────────────────────────────────────────────
# 4. .env
# ──────────────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  cp .env.example .env
  c_ok "created .env (you'll fill secrets in Step 3)"
else
  c_ok ".env already exists — leaving it alone"
fi

# ──────────────────────────────────────────────────────────────────────
# 5. db init
# ──────────────────────────────────────────────────────────────────────
c_step "initializing SQLite DB"
./.venv/bin/python -m db.init_db

# ──────────────────────────────────────────────────────────────────────
# 6. validate first card
# ──────────────────────────────────────────────────────────────────────
c_step "validating 2026-05-10 first card"
./.venv/bin/python - <<'PY'
import json
from jsonschema import Draft7Validator
schema = json.load(open('data/concept_card_schema.json'))
card = json.load(open('data/concept_card_2026_05_10.json'))
errs = list(Draft7Validator(schema).iter_errors(card))
if errs:
    for e in errs:
        print("  FAIL:", "/".join(str(p) for p in e.absolute_path), "->", e.message)
    raise SystemExit(1)
print(f"  PASS: {card['theme']} - {card['narrative_oneliner']}")
print(f"  type={card['card_type']}/{card['memory_lane']['variant']} ask_pd={card['ask_pd']}")
PY

echo
c_ok "Step 1 complete. Next:  source .venv/bin/activate"
echo
