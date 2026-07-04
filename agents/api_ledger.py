"""agents/api_ledger.py — per-call AI-API cost ledger (PD 2026-06-25).

Born from the "동영상 2개에 $50?" question: the pipeline had NO per-call cost/usage
log, so "어디서 돈이 샜는지" could only be guessed. This records every billable AI
call into `api_calls` so the morning report (scripts/api_cost_report.py) can attribute
spend by provider × stage × day and surface call-count multiplication (the real Seedance
cost driver = re-renders, not unit price).

Design:
  • log_call(...) is BEST-EFFORT and must NEVER break a render — every error is swallowed.
  • Counts are EXACT (one row per billable hop); dollar amounts are ESTIMATES from a
    price map (real provider billing APIs are coarse / need admin keys — see report).
  • Instrumented at the few near-chokepoints: cameraman._run (all video dispatch),
    llm_cascade (most text), the gpt-image sites. Not every scattered call — video is
    >90% of spend and flows through _run.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()

# Per-unit USD estimates. Counts are exact; these turn counts into $ estimates.
# Override any via env API_PRICE_<KEY_UPPER> (e.g. API_PRICE_SEEDANCE_FAST=0.9).
# ★CALIBRATED to real provider receipts (notes/case_study_8_weeks_ko.md, 2026-07-04):
# BytePlus (Seedance) June=$1,614 (~$54/day); over the ledger window 6/25–7/4 (~9 days,
# 509 seedance calls) the real spend ≈ $460 → ~$0.90/call — the old $0.30–0.50 undercounted
# ~2–3×, and since Seedance is ~84% of spend that alone skewed the whole report. OpenAI
# (gpt-image + gpt-4.1) June=$640; text is now token-priced below (the Writer/Director calls
# carry big prompts, so a flat per-call price was very wrong). AUTHORITATIVE $ = the provider
# receipts / OpenAI Costs API; this map is a per-call ESTIMATE for the daily trend only.
_DEFAULT_PRICES = {
    "seedance_i2v": 1.20,        # BytePlus seedance-2-0 standard per cut (receipt-calibrated)
    "seedance_fast": 0.90,       # -fast variant (the one actually used) — ~$0.90/call real
    "veo_gemini": 0.60,          # Veo 3 lite (Gemini API) per cut — CLAUDE.md (mostly retired)
    "veo_vertex": 1.20,          # Veo 3 standard (Vertex) per cut
    "gemini_image": 0.04,        # nano-banana / imagen still per image — CLAUDE.md
    "gpt_image": 0.17,           # OpenAI gpt-image per image (calibrated)
    "openai_text": 0.01,         # fallback ONLY when no token count (token-priced below)
    "gemini_text": 0.004,        # fallback ONLY when no token count
    "anthropic_text": 0.02,      # fallback ONLY when no token count
}

# Text is priced by TOKENS when a token count is present (the accurate way — a Writer/Director
# call with a 25KB system prompt costs 50× a tiny cascade ping). Blended $/1M tokens per
# provider (input+output, receipt-anchored): gpt-4.1 ~$4, Claude Opus ~$18, Gemini ~$0.5.
_TEXT_RATE_PER_MTOK = {
    "openai": 4.0,
    "anthropic": 18.0,
    "google": 0.5,
}


def _price(key: str) -> float:
    env = os.getenv(f"API_PRICE_{key.upper()}")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return _DEFAULT_PRICES.get(key, 0.0)


def _ensure(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS api_calls ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts TEXT DEFAULT (datetime('now')), "      # UTC
        "provider TEXT, service TEXT, model TEXT, stage TEXT, "
        "units INTEGER DEFAULT 1, est_cost_usd REAL DEFAULT 0, "
        "tokens INTEGER DEFAULT 0, "     # LLM total tokens (text calls); 0 for image/video
        "card_id TEXT, meta TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_api_calls_ts ON api_calls(ts)")
    # Promote tokens to a first-class column on pre-existing DBs (was only in meta JSON).
    try:
        con.execute("ALTER TABLE api_calls ADD COLUMN tokens INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # already added


def log_call(provider: str, service: str, *, price_key: str | None = None,
             model: str | None = None, stage: str | None = None, units: int = 1,
             est_cost: float | None = None, card_id: str | None = None,
             meta: dict | None = None) -> None:
    """Record one billable API hop. Best-effort: never raises.

    provider: 'byteplus' | 'google' | 'openai' | 'anthropic'
    service:  'seedance' | 'veo' | 'image' | 'text'
    price_key: key into the price map for $ estimate (falls back to est_cost or 0).
    """
    try:
        # tokens: callers pass meta={"tokens": N} for text calls — mirror into a column so
        # cost/token reports don't parse JSON, AND so text can be TOKEN-priced (accurate).
        _tok = 0
        if meta and isinstance(meta, dict):
            try:
                _tok = int(meta.get("tokens") or 0)
            except (TypeError, ValueError):
                _tok = 0
        if est_cost is None:
            if (service or "").lower() == "text" and _tok > 0:
                # token-priced: $/1M × tokens (falls back to flat per-call if provider unknown)
                _rate = _TEXT_RATE_PER_MTOK.get((provider or "").lower())
                est_cost = (_tok / 1_000_000.0) * _rate if _rate else _price(price_key or "") * max(1, units)
            else:
                est_cost = _price(price_key or "") * max(1, units)
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            _ensure(con)
            con.execute(
                "INSERT INTO api_calls (provider, service, model, stage, units, "
                "est_cost_usd, tokens, card_id, meta) VALUES (?,?,?,?,?,?,?,?,?)",
                (provider, service, model, stage, units, round(est_cost, 4),
                 _tok, card_id, json.dumps(meta, ensure_ascii=False) if meta else None))
            con.commit()
        finally:
            con.close()
    except Exception:
        pass  # cost logging must never break a render


# Map a video dispatch script → (provider, service, price_key, model-ish) for _run.
def classify_video_cmd(cmd: list[str]) -> tuple[str, str, str, str] | None:
    """Given a subprocess cmd, return (provider, service, price_key, script) if it is a
    billable video-generation dispatch, else None."""
    joined = " ".join(str(c) for c in cmd)
    if "animate_seedance_i2v.py" in joined:
        fast = "--fast" in joined or "seedance-2-0-fast" in joined
        return ("byteplus", "seedance", "seedance_fast" if fast else "seedance_i2v",
                "seedance-2-0")
    if "animate_hero_veo3_vertex.py" in joined:
        return ("google", "veo", "veo_vertex", "veo-3.0-vertex")
    if "animate_hero_veo3.py" in joined:
        return ("google", "veo", "veo_gemini", "veo-3.0-gemini")
    return None
