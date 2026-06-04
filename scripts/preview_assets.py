"""
scripts/preview_assets.py — Open candidate assets in macOS Preview/QuickTime
for visual review, side-by-side metadata table.

Why this exists
---------------
Curating a 4-cut Concept Card from hundreds of DB rows is faster when you
can arrow-key through the actual frames. macOS `open` with multiple files
opens them all in a single Preview window — arrow keys browse, photos
and videos both work (videos open in QuickTime).

Usage
-----
    # 1. By preset (1화 후보 풀: 입양 후 함께 등장한 mixed 사진들)
    python -m scripts.preview_assets --preset ep1

    # 2. By specific asset_ids (comma-separated)
    python -m scripts.preview_assets --ids med_2025_11_15_151126_icloud_7df20598,med_2025_11_16_090551_icloud_0029b86d

    # 3. By SQL WHERE clause
    python -m scripts.preview_assets --where "subjects_csv = 'leo,ryani' AND captured_iso >= '2025-11-15'"

    # 4. By date range + subjects filter
    python -m scripts.preview_assets --from 2025-11-15 --to 2025-11-25 --subjects "leo,ryani"

    # 5. Top-N by area (resolution)
    python -m scripts.preview_assets --where "subjects_csv='leo,ryani'" \
        --order "(width*height) DESC" --limit 10

    # 6. Print metadata only, don't open files
    python -m scripts.preview_assets --preset ep1 --print-only

Presets
-------
    ep1        first-episode pool: post-adoption + both subjects
    ryani_youth  랴니 영아기 archive (2015-2018)
    leo_first_week  레오 입양 첫 일주일
    high_res     5712×4284+ photos only
"""
from __future__ import annotations

import argparse
import logging
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
log = logging.getLogger("preview")

PRESETS = {
    "ep1": {
        "where": "subjects_csv = 'leo,ryani' AND captured_iso >= '2025-11-15'",
        "order": "captured_iso ASC",
        "desc": "1화 후보 풀: 입양 후 함께 등장한 사진/영상",
    },
    "ryani_youth": {
        "where": "subjects_csv = 'ryani' AND age_tag = 'youth'",
        "order": "captured_iso ASC",
        "desc": "랴니 영아기 (2015~2020) 단독 등장",
    },
    "leo_first_week": {
        "where": "subjects_csv LIKE '%leo%' "
                 "AND captured_iso >= '2025-11-15' "
                 "AND captured_iso < '2025-11-22'",
        "order": "captured_iso ASC",
        "desc": "레오 입양 첫 일주일 (혼자/함께 모두)",
    },
    "high_res": {
        "where": "subjects_csv = 'leo,ryani' "
                 "AND width >= 4000 AND height >= 4000",
        "order": "captured_iso ASC",
        "desc": "고해상도 mixed 사진만 (4K+)",
    },
}


def fetch_rows(args: argparse.Namespace) -> list[sqlite3.Row]:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row

    select = """
        SELECT asset_id, captured_iso, kind, subjects_csv, age_tag,
               width, height, duration_sec, file_path
        FROM assets
    """

    where_parts: list[str] = []
    params: list = []

    if args.preset:
        if args.preset not in PRESETS:
            raise SystemExit(f"unknown preset: {args.preset}. options: {', '.join(PRESETS)}")
        p = PRESETS[args.preset]
        where_parts.append(p["where"])
        order = p["order"]
    else:
        order = args.order or "captured_iso ASC"

    if args.ids:
        ids = [s.strip() for s in args.ids.split(",") if s.strip()]
        placeholders = ",".join("?" * len(ids))
        where_parts.append(f"asset_id IN ({placeholders})")
        params.extend(ids)

    if args.where:
        where_parts.append(f"({args.where})")

    if args.from_date:
        where_parts.append("captured_iso >= ?")
        params.append(args.from_date)
    if args.to_date:
        where_parts.append("captured_iso < ?")
        params.append(args.to_date)
    if args.subjects:
        where_parts.append("subjects_csv = ?")
        params.append(args.subjects)
    if args.kind:
        where_parts.append("kind = ?")
        params.append(args.kind)

    sql = select
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    sql += f" ORDER BY {order}"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"

    log.debug("SQL: %s | params=%s", sql, params)
    rows = con.execute(sql, params).fetchall()
    con.close()
    return rows


def print_table(rows: list[sqlite3.Row]) -> None:
    """Pretty table — uses rich if available, otherwise plain text."""
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        # Plain fallback
        print(f"{'idx':>3}  {'date':10}  {'kind':5}  {'subjects':12}  "
              f"{'age':6}  {'dims':12}  {'dur':>6}  asset_id")
        for i, r in enumerate(rows, 1):
            dims = f"{r['width']}x{r['height']}" if r['width'] else "-"
            dur = f"{r['duration_sec']:.1f}s" if r['duration_sec'] else "-"
            print(f"{i:>3}  {r['captured_iso'][:10]}  {r['kind']:5}  "
                  f"{(r['subjects_csv'] or '-'):12}  "
                  f"{(r['age_tag'] or '-'):6}  {dims:12}  {dur:>6}  "
                  f"{r['asset_id']}")
        return

    console = Console()
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("date", width=10)
    table.add_column("kind", width=5)
    table.add_column("subjects", width=12)
    table.add_column("age", width=6)
    table.add_column("dims", width=12)
    table.add_column("dur", justify="right", width=6)
    table.add_column("asset_id", style="dim")

    for i, r in enumerate(rows, 1):
        dims = f"{r['width']}x{r['height']}" if r['width'] else "-"
        dur = f"{r['duration_sec']:.1f}s" if r['duration_sec'] else "-"
        table.add_row(
            str(i),
            r['captured_iso'][:10] if r['captured_iso'] else "-",
            r['kind'],
            r['subjects_csv'] or "-",
            r['age_tag'] or "-",
            dims,
            dur,
            r['asset_id'],
        )
    console.print(table)


def open_in_finder(rows: list[sqlite3.Row], split_videos: bool = True) -> None:
    """
    Open all files at once. macOS `open` with multiple args:
      - photos -> single Preview window, arrow-key browse
      - videos -> each opens in QuickTime (one tab per video)
    Splitting them gives a calmer review flow.
    """
    if not rows:
        print("no rows to open.")
        return

    photos: list[str] = []
    videos: list[str] = []
    missing: list[str] = []
    for r in rows:
        p = Path(r["file_path"])
        if not p.exists():
            missing.append(r["asset_id"])
            continue
        if r["kind"] == "video":
            videos.append(str(p))
        else:
            photos.append(str(p))

    if missing:
        print(f"\n[warn] {len(missing)} files missing on disk: "
              f"{', '.join(missing[:3])}{'...' if len(missing) > 3 else ''}")

    if photos:
        print(f"\nopening {len(photos)} photo(s) in Preview...")
        # -a "Preview" forces Preview specifically (PNG/HEIC default mapping varies)
        subprocess.run(["open", "-a", "Preview", *photos], check=False)

    if videos:
        if split_videos:
            print(f"opening {len(videos)} video(s) in QuickTime Player...")
            subprocess.run(["open", "-a", "QuickTime Player", *videos], check=False)
        else:
            subprocess.run(["open", *videos], check=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Open Ryani & Leo asset files in macOS Preview/QuickTime.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="presets:\n  " + "\n  ".join(
            f"{k:14}  {v['desc']}" for k, v in PRESETS.items()
        ),
    )
    ap.add_argument("--preset", choices=list(PRESETS.keys()),
                    help="named candidate pool")
    ap.add_argument("--ids", help="comma-separated asset_ids")
    ap.add_argument("--where", help="raw SQL WHERE expression")
    ap.add_argument("--from", dest="from_date",
                    help="captured_iso >= YYYY-MM-DD")
    ap.add_argument("--to", dest="to_date",
                    help="captured_iso < YYYY-MM-DD")
    ap.add_argument("--subjects",
                    help="exact subjects_csv match, e.g. 'leo,ryani'")
    ap.add_argument("--kind", choices=["photo", "video"])
    ap.add_argument("--order", help="ORDER BY clause (default: captured_iso ASC)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--print-only", action="store_true",
                    help="print table, skip opening files")
    ap.add_argument("--no-split", action="store_true",
                    help="don't separate Preview/QuickTime — use default app")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not any([args.preset, args.ids, args.where, args.from_date,
                args.to_date, args.subjects, args.kind]):
        ap.error("specify at least one selector: --preset / --ids / --where / "
                 "--from+--to / --subjects / --kind")

    rows = fetch_rows(args)
    if not rows:
        print("no rows match the criteria.")
        return 0

    print(f"\n=== {len(rows)} asset(s) ===")
    if args.preset:
        print(f"preset: {args.preset} — {PRESETS[args.preset]['desc']}")
    print_table(rows)

    if args.print_only:
        return 0

    open_in_finder(rows, split_videos=not args.no_split)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
