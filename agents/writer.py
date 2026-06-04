"""
agents/writer.py — Writer Agent runner (v1.1).

Pipeline:
    1. Resolve target date (default = tomorrow KST).
    2. Build context: today's milestones, recent tone history, recent backgrounds, asset pool.
    3. Call Anthropic API with system prompt + structured user prompt.
    4. Validate output JSON against concept_card_schema (v2).
    5. Persist to cards table; log run.
    6. Echo card_id summary to stdout (Slack /writer-run will surface this).

Run:
    python -m agents.writer                       # tomorrow
    python -m agents.writer --date 2026-05-10     # specific KST date
    python -m agents.writer --dry-run             # don't persist
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("agents.writer")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
SCHEMA_PATH = ROOT / "data" / "concept_card_schema.json"
SYSTEM_PROMPT_PATH = ROOT / "prompts" / "writer_system.md"
KST = ZoneInfo("Asia/Seoul")


# ──────────────────────────────────────────────────────────────────────
# Context gathering
# ──────────────────────────────────────────────────────────────────────
def kst_today() -> dt.date:
    return dt.datetime.now(KST).date()


def find_milestones(con: sqlite3.Connection, day: dt.date) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT * FROM milestones WHERE month=? AND day=?",
        (day.month, day.day),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_tone(con: sqlite3.Connection, days: int = 7) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT date, tone_primary, intensity FROM tone_history
        WHERE date >= date('now', ?)
        ORDER BY date DESC
        """,
        (f"-{days} days",),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_backgrounds(con: sqlite3.Connection, days: int = 7) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT date, background_id, phash FROM background_history
        WHERE date >= date('now', ?)
        ORDER BY date DESC
        """,
        (f"-{days} days",),
    ).fetchall()
    return [dict(r) for r in rows]


def asset_pool(con: sqlite3.Connection, day: dt.date, lookback_days: int = 3) -> list[dict[str, Any]]:
    """Recent regular pool: last 3 days of assets (Slack + iCloud)."""
    cutoff = (day - dt.timedelta(days=lookback_days)).isoformat()
    rows = con.execute(
        """
        SELECT asset_id, source, kind, captured_iso, duration_sec, phash, subjects_csv, age_tag, location_tag, notes
        FROM assets
        WHERE captured_iso >= ?
        ORDER BY captured_iso DESC
        LIMIT 80
        """,
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def youth_archive_pool(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT asset_id, source, kind, captured_iso, subjects_csv, location_tag
        FROM assets
        WHERE age_tag='youth'
        ORDER BY captured_iso DESC
        LIMIT 40
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def active_trends(con: sqlite3.Connection, day: dt.date) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT trend_id, source, category, title, fit_score, expiry_date, notes
        FROM trends
        WHERE date(expiry_date) >= date(?)
        ORDER BY fit_score DESC
        LIMIT 10
        """,
        (day.isoformat(),),
    ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────
# LLM call
# ──────────────────────────────────────────────────────────────────────
def call_llm(system: str, user: str, model: str | None = None) -> str:
    """PD 2026-06-02: LLM cascade (OpenAI → Gemini → Anthropic). The `model`
    arg only matters on the Anthropic last-resort hop."""
    from agents.llm_cascade import call_text_cascade
    max_tokens = int(os.getenv("WRITER_MAX_TOKENS", "4096"))
    return call_text_cascade(system, user, max_tokens=max_tokens,
                                anthropic_model=model or "claude-opus-4-7")


def strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()


# ──────────────────────────────────────────────────────────────────────
# Validate
# ──────────────────────────────────────────────────────────────────────
def validate_card(card: dict) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    return [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in validator.iter_errors(card)]


# ──────────────────────────────────────────────────────────────────────
# Persist
# ──────────────────────────────────────────────────────────────────────
def persist_card(con: sqlite3.Connection, card: dict, run_id: int) -> None:
    payload = json.dumps(card, ensure_ascii=False)
    bg = card.get("background_plan", {}) or {}
    tone = card.get("tone", {}) or {}
    lane = card.get("memory_lane") or {}
    con.execute(
        """
        INSERT OR REPLACE INTO cards
            (card_id, date, created_at, author, card_type, theme,
             tone_primary, tone_intensity, seasonal_tag, trend_id,
             memory_lane_variant, memory_lane_milestone, illustration_style,
             background_id, background_phash, duration_target_sec,
             writer_confidence, ask_pd, ask_reason, state, payload_json,
             render_style, updated_at)
        VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?, ?,datetime('now'))
        """,
        (
            card["card_id"], card["date"], card["created_at"], card["author"],
            card["card_type"], card.get("theme"),
            tone.get("primary"), tone.get("intensity"),
            (card.get("seasonal") or {}).get("tag"),
            (card.get("trend") or {}).get("trend_id"),
            lane.get("variant"), lane.get("milestone"), lane.get("illustration_style"),
            bg.get("target_background_id"), bg.get("perceptual_hash"),
            card.get("duration_target_sec"),
            card.get("writer_confidence"),
            1 if card.get("ask_pd") else 0,
            card.get("ask_reason"),
            "pd_review" if card.get("ask_pd") else "draft",
            payload,
            card.get("render_style"),
        ),
    )
    con.execute(
        "UPDATE runs SET finished_at=datetime('now'), status='ok', card_id=?, output_snapshot=? WHERE id=?",
        (card["card_id"], payload[:8000], run_id),
    )


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
def build_user_prompt(target_date: dt.date, ctx: dict) -> str:
    return json.dumps(
        {
            "instructions": "Produce one Concept Card v2 JSON object for the given target_date.",
            "target_date": target_date.isoformat(),
            "today_kst": kst_today().isoformat(),
            "milestones_today": ctx["milestones"],
            "is_milestone_today": bool(ctx["milestones"]),
            "tone_history_7d": ctx["tone"],
            "background_history_7d": ctx["bgs"],
            "asset_pool_recent": ctx["assets"],
            "youth_archive_pool": ctx["youth"],
            "active_trends": ctx["trends"],
            "subjects": {
                "ryani": {"born": "2015-05-05", "species": "french_bulldog"},
                "leo":   {"born": "2025-09-25", "species": "cat", "born_estimate": True},
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="target date YYYY-MM-DD KST (default: tomorrow)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print-prompt", action="store_true")
    args = ap.parse_args()

    target = (
        dt.date.fromisoformat(args.date)
        if args.date else
        kst_today() + dt.timedelta(days=1)
    )

    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row

    # context
    ctx = {
        "milestones": find_milestones(con, target),
        "tone":       recent_tone(con),
        "bgs":        recent_backgrounds(con),
        "assets":     asset_pool(con, target),
        "youth":      youth_archive_pool(con),
        "trends":     active_trends(con, target),
    }
    user_prompt = build_user_prompt(target, ctx)
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    if args.print_prompt:
        print("=== SYSTEM ===\n", system_prompt)
        print("=== USER ===\n", user_prompt)
        return 0

    # log run
    with con:
        cur = con.execute(
            "INSERT INTO runs (agent, status, input_snapshot) VALUES ('writer','running',?)",
            (user_prompt[:8000],),
        )
        run_id = cur.lastrowid

    # call LLM
    log.info("calling LLM model=%s target=%s milestones=%d",
             os.getenv("WRITER_MODEL"), target, len(ctx["milestones"]))
    try:
        raw = call_llm(system_prompt, user_prompt)
    except Exception as e:
        with con:
            con.execute(
                "UPDATE runs SET finished_at=datetime('now'), status='error', error_message=? WHERE id=?",
                (str(e), run_id),
            )
        log.exception("LLM call failed")
        return 2

    body = strip_fences(raw)
    try:
        card = json.loads(body)
    except json.JSONDecodeError as e:
        with con:
            con.execute(
                "UPDATE runs SET finished_at=datetime('now'), status='error', error_message=? WHERE id=?",
                (f"json decode: {e}\n--- raw ---\n{body[:2000]}", run_id),
            )
        log.error("LLM returned non-JSON. First 400 chars:\n%s", body[:400])
        return 3

    # ensure required server-side fields
    card.setdefault("card_id", str(uuid.uuid4()))
    card.setdefault("created_at", dt.datetime.utcnow().isoformat(timespec="seconds") + "Z")
    card.setdefault("author", "writer_agent")
    card["date"] = target.isoformat()

    errs = validate_card(card)
    if errs:
        with con:
            con.execute(
                "UPDATE runs SET finished_at=datetime('now'), status='error', error_message=? WHERE id=?",
                ("schema errors:\n" + "\n".join(errs), run_id),
            )
        log.error("schema validation failed:\n%s", "\n".join(errs))
        return 4

    if args.dry_run:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        log.info("dry-run ok — not persisted")
        return 0

    with con:
        persist_card(con, card, run_id)

    print(f"[ok] card {card['card_id'][:8]} for {card['date']} "
          f"type={card['card_type']} tone={card.get('tone',{}).get('primary')} "
          f"ask_pd={'Y' if card.get('ask_pd') else 'N'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
