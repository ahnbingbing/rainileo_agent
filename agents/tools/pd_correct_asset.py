"""
agents/tools/pd_correct_asset.py — PD ground-truth override for a single asset.

VLM scene_description is best-effort but often wrong about spatial context
(e.g., calls a rooftop door "white drawer", or "wooden floor" for what is
actually tile near a hallway). PD knows what was actually filmed — this CLI
writes that ground truth to `assets.pd_notes`, which the Producer prefers
over the VLM's scene_description when feeding the Writer.

Usage:
    python -m agents.tools.pd_correct_asset <asset_id_prefix> "<ground truth>"
    python -m agents.tools.pd_correct_asset --list-recent  # show last 20 video assets
    python -m agents.tools.pd_correct_asset --show <asset_id_prefix>

Examples:
    python -m agents.tools.pd_correct_asset med_2026_05_09_175800 \\
        "옥상 올라가는 문 앞, 레오가 날벌레 잡다가 멈춰 stay한 모습. \\
         바닥은 타일, 우측에 회색 문 (옥상 입구). 왼쪽 벽엔 빨간 꽃 그림."

The note replaces what the Writer sees as scene_description, so write the
note as a 2-3 sentence factual description (no narrator embellishment).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "agent.db"


def _resolve_id(con: sqlite3.Connection, prefix: str) -> str | None:
    rows = con.execute(
        "SELECT asset_id FROM assets WHERE asset_id LIKE ? LIMIT 5",
        (f"{prefix}%",),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        print(f"Multiple matches for '{prefix}':", file=sys.stderr)
        for r in rows:
            print(f"  {r[0]}", file=sys.stderr)
        return None
    return rows[0][0]


def main() -> int:
    p = argparse.ArgumentParser(description="PD ground-truth override for an asset")
    p.add_argument("asset_id", nargs="?", help="asset_id or prefix")
    p.add_argument("note", nargs="?", help="ground-truth description (replaces VLM scene_description for Writer)")
    p.add_argument("--list-recent", action="store_true", help="list 20 most recent video assets")
    p.add_argument("--show", metavar="ID_PREFIX", help="show current pd_notes / sc for an asset")
    p.add_argument("--clear", action="store_true", help="clear pd_notes (revert to VLM)")
    args = p.parse_args()

    con = sqlite3.connect(DB_PATH)

    if args.list_recent:
        rows = con.execute(
            "SELECT asset_id, DATE(captured_iso) as d, focus_subject, activity, "
            "substr(COALESCE(pd_notes, scene_description, ''), 1, 60) as desc "
            "FROM assets WHERE kind='video' "
            "ORDER BY captured_iso DESC LIMIT 20"
        ).fetchall()
        for r in rows:
            mark = " [PD]" if con.execute("SELECT pd_notes FROM assets WHERE asset_id=?", (r[0],)).fetchone()[0] else ""
            print(f"{r[0]} | {r[1]} | {r[2]} | {r[3]}{mark}\n  {r[4]}")
        return 0

    if args.show:
        aid = _resolve_id(con, args.show)
        if not aid:
            return 1
        r = con.execute(
            "SELECT asset_id, file_path, scene_description, pd_notes, activity, focus_subject, location_type "
            "FROM assets WHERE asset_id=?", (aid,)
        ).fetchone()
        print(f"asset_id:       {r[0]}")
        print(f"file_path:      {r[1]}")
        print(f"activity:       {r[4]}")
        print(f"focus_subject:  {r[5]}")
        print(f"location_type:  {r[6]}")
        print(f"VLM sc:         {r[2]}")
        print(f"PD notes:       {r[3] or '(none)'}")
        return 0

    if not args.asset_id:
        p.print_help()
        return 1

    aid = _resolve_id(con, args.asset_id)
    if not aid:
        print(f"No asset matches '{args.asset_id}'", file=sys.stderr)
        return 1

    if args.clear:
        con.execute("UPDATE assets SET pd_notes=NULL WHERE asset_id=?", (aid,))
        con.commit()
        print(f"Cleared pd_notes for {aid}")
        return 0

    if not args.note:
        print("Note required (or use --clear / --show)", file=sys.stderr)
        return 1

    con.execute("UPDATE assets SET pd_notes=? WHERE asset_id=?", (args.note, aid))
    con.commit()
    print(f"Updated pd_notes for {aid}")
    print(f"  → {args.note[:100]}{'...' if len(args.note) > 100 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
