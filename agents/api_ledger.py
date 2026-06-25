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

# Rough per-unit USD estimates. Counts are exact; these turn counts into $ guesses.
# Override any via env API_PRICE_<KEY_UPPER> (e.g. API_PRICE_SEEDANCE_I2V=0.4).
# Sources: CLAUDE.md (Veo lite $0.60, Gemini img $0.04), gen_still_multiref header,
# cameraman cost-guard comments. Seedance unit is unpublished → conservative guess.
_DEFAULT_PRICES = {
    "seedance_i2v": 0.50,        # BytePlus dreamina-seedance-2-0 per cut (estimate)
    "seedance_fast": 0.30,       # -fast variant
    "veo_gemini": 0.60,          # Veo 3 lite (Gemini API) per cut — CLAUDE.md
    "veo_vertex": 1.20,          # Veo 3 standard (Vertex) per cut (estimate, richer)
    "gemini_image": 0.04,        # nano-banana / imagen still per image — CLAUDE.md
    "gpt_image": 0.10,           # OpenAI gpt-image per image
    "openai_text": 0.01,         # per gpt-4.1 text call (rough; small)
    "gemini_text": 0.004,        # per Gemini text/vision call (rough; small)
    "anthropic_text": 0.02,      # per Anthropic last-resort call (rough)
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
        "card_id TEXT, meta TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_api_calls_ts ON api_calls(ts)")


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
        if est_cost is None:
            est_cost = _price(price_key or "") * max(1, units)
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            _ensure(con)
            con.execute(
                "INSERT INTO api_calls (provider, service, model, stage, units, "
                "est_cost_usd, card_id, meta) VALUES (?,?,?,?,?,?,?,?)",
                (provider, service, model, stage, units, round(est_cost, 4),
                 card_id, json.dumps(meta, ensure_ascii=False) if meta else None))
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
