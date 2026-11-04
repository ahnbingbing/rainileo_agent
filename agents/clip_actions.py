"""agents/clip_actions.py — per-clip ACTION layer (Gemini native video) wired into production.

WHY: frame-sampled VLM tagging (openai_vision / tag_assets_vlm) samples 6-10 stills, so a 1-2s
action BETWEEN frames (e.g. "Leo walks out the door", "Leo climbs on Ryani's back") is never
described. That blinds two stages: CLIP SELECTION (can't find a clip by what actually happens)
and CAPTION authoring (the caption is guessed from static frames → wrong action/subject/timing,
then Giri flags it). scripts/video_actions.py watches the WHOLE clip natively and returns
[{start,end,actor,action,desc}]. This module is the production front door to that signal:

  • ensure_column / store / actions_for — persist action moments on assets.action_moments (JSON)
  • extract()  — run Gemini native video on a local clip (source clip OR a rendered cut)
  • format_actions() — compact timeline string for prompts + the embedding doc

Consumers: agents/asset_embed.py (selection search doc), agents/cameraman.py (RF caption
grounding), scripts/tag_assets_vlm.py (ingest-time populate), scripts/backfill_actions.py.

Kill-switch: USE_CLIP_ACTIONS (default "0" = off; flip to "1" after the backfill). Every call
is fail-silent so a Gemini hiccup never breaks a render.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def enabled() -> bool:
    """Master kill-switch. Default ON (PD 2026-11-04 "지금 넣어라"): the CAPTION path grounds
    natively at render time so it works immediately; the SELECTION path is harmless until the
    backfill + embedding rebuild populate action_moments. Set USE_CLIP_ACTIONS=0 to roll back —
    every call is fail-silent so a Gemini hiccup never breaks a render."""
    return os.getenv("USE_CLIP_ACTIONS", "1") == "1"


def ensure_column(con: sqlite3.Connection) -> None:
    """Idempotent — add assets.action_moments if it isn't there yet (same idiom as visual_hash)."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(assets)").fetchall()}
    if "action_moments" not in cols:
        con.execute("ALTER TABLE assets ADD COLUMN action_moments TEXT")
        con.commit()


def extract(file_path: str, *, model: str | None = None) -> list:
    """Gemini native video → temporal action moments [{start,end,actor,action,desc}].
    Works on a SOURCE clip (ingest/backfill) or a rendered CUT mp4 (caption grounding)."""
    import video_actions as va  # scripts/video_actions.py (on sys.path above)
    return va.actions(str(file_path), model=model)


def store(con: sqlite3.Connection, asset_id: str, moments: list) -> None:
    ensure_column(con)
    con.execute("UPDATE assets SET action_moments=? WHERE asset_id=?",
                (json.dumps(moments, ensure_ascii=False), str(asset_id)))
    con.commit()


def actions_for(con: sqlite3.Connection, asset_id: str) -> list:
    """Stored action moments for an asset ([] if none / column absent)."""
    try:
        row = con.execute("SELECT action_moments FROM assets WHERE asset_id=?",
                          (str(asset_id),)).fetchone()
    except sqlite3.OperationalError:
        return []
    if not row or not row[0]:
        return []
    try:
        v = json.loads(row[0])
        return v if isinstance(v, list) else []
    except Exception:
        return []


def format_actions(moments, *, max_n: int = 14) -> str:
    """Compact one-line timeline for prompts + the embedding doc, e.g.
    '0.5-1.2s leo: stalks the tail; 1.3-2.1s leo: climbs onto ryani's back; ...'."""
    if not moments:
        return ""
    if isinstance(moments, str):
        try:
            moments = json.loads(moments)
        except Exception:
            return ""
    out = []
    for m in (moments or [])[:max_n]:
        if not isinstance(m, dict):
            continue
        desc = (m.get("desc") or m.get("action") or "").strip()
        if not desc:
            continue
        who = (m.get("actor") or "").strip()
        s, e = m.get("start"), m.get("end")
        span = (f"{float(s):.1f}-{float(e):.1f}s "
                if isinstance(s, (int, float)) and isinstance(e, (int, float)) else "")
        out.append((span + (f"{who}: " if who else "") + desc).strip())
    return "; ".join(out)
