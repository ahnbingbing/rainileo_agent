"""
scripts/review_bgm.py — Listen-and-tag CLI for bgm_tracks.

Why this exists
---------------
index_bgm.py auto-tags from filename keywords; that's good for ~85% of
tracks but a chunk needs a human ear. This script plays each track via
macOS `open` (default audio app — Music or QuickTime) and prompts you
for tone + energy. Saves with manual_review=1 so the next index_bgm
run won't overwrite your call.

Filters
-------
    --filter unsorted    only tone_tag='unsorted'        (default)
    --filter low-conf    auto_tag_confidence < 0.4
    --filter all-auto    every row where manual_review=0
    --filter tone:warm   only tone_tag='warm' (sub-spot-check)

Keys
----
At each prompt:
    w / f / c / t / u    set tone (warm/fun/calm/trends/unsorted)
    1 / 2 / 3            set energy (low/mid/high)
    p                    re-play current track
    n  or  Enter         next without saving (skip)
    s                    save & next
    q                    save & quit

Usage
-----
    python -m scripts.review_bgm                          # default: unsorted
    python -m scripts.review_bgm --filter low-conf
    python -m scripts.review_bgm --filter tone:fun --limit 10
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
log = logging.getLogger("review_bgm")

TONE_KEYS = {"w": "warm", "f": "fun", "c": "calm", "t": "trends", "u": "unsorted"}
ENERGY_KEYS = {"1": "low", "2": "mid", "3": "high"}


def build_query(filter_arg: str) -> tuple[str, list]:
    base = """
        SELECT track_id, file_path, filename, title, artist,
               duration_sec, tone_tag, energy_tag, instrument_csv,
               vibe_csv, auto_tag_confidence, manual_review
        FROM bgm_tracks
    """
    if filter_arg == "unsorted":
        return base + " WHERE tone_tag='unsorted' ORDER BY title", []
    if filter_arg == "low-conf":
        return base + " WHERE auto_tag_confidence < 0.4 AND manual_review=0 ORDER BY auto_tag_confidence, title", []
    if filter_arg == "all-auto":
        return base + " WHERE manual_review=0 ORDER BY tone_tag, title", []
    if filter_arg.startswith("tone:"):
        tone = filter_arg.split(":", 1)[1]
        return base + " WHERE tone_tag=? AND manual_review=0 ORDER BY title", [tone]
    raise SystemExit(f"unknown filter: {filter_arg!r}")


def play(file_path: Path) -> None:
    if sys.platform == "darwin":
        # default audio app on macOS — usually Music.app for mp3
        subprocess.run(["open", str(file_path)], check=False)
    else:
        log.warning("playback only on macOS; file: %s", file_path)


def stop_playback() -> None:
    """Best-effort stop — quits Music.app's current track."""
    if sys.platform == "darwin":
        subprocess.run(
            ["osascript", "-e", 'tell application "Music" to pause'],
            capture_output=True, check=False,
        )


def show(idx: int, total: int, r: sqlite3.Row) -> None:
    dur = f"{r['duration_sec']:.0f}s" if r['duration_sec'] else "?"
    insts = r['instrument_csv'] or "-"
    vibes = r['vibe_csv'] or "-"
    print()
    print("─" * 72)
    print(f"  [{idx}/{total}]  {r['title']}")
    print(f"     artist={r['artist']}  duration={dur}")
    print(f"     auto:  tone={r['tone_tag']}  energy={r['energy_tag']}  "
          f"conf={r['auto_tag_confidence']:.2f}")
    print(f"     insts={insts}  vibes={vibes}")
    print(f"     file: {r['filename']}")
    print()
    print("  keys: [w]arm [f]un [c]alm [t]rends [u]nsorted   "
          "[1]low [2]mid [3]high")
    print("        [p]lay  [s]ave  [n]ext (no save)  [q]uit")


def prompt_loop(con: sqlite3.Connection, rows: list[sqlite3.Row]) -> None:
    total = len(rows)
    if not total:
        print("nothing to review under this filter.")
        return

    saved = 0
    for i, r in enumerate(rows, 1):
        path = ROOT / r["file_path"]
        if not path.exists():
            log.warning("missing file: %s — skipping", path)
            continue

        # Working copy of the row's tags — committed only on 's'
        new_tone = r["tone_tag"]
        new_energy = r["energy_tag"]

        play(path)
        show(i, total, r)
        print(f"     ▶ now: tone={new_tone} energy={new_energy}")

        while True:
            try:
                choice = input("  > ").strip().lower()
            except EOFError:
                choice = "q"
            if not choice:
                # bare Enter == next, no save
                break
            if choice in TONE_KEYS:
                new_tone = TONE_KEYS[choice]
                print(f"     tone -> {new_tone}")
                continue
            if choice in ENERGY_KEYS:
                new_energy = ENERGY_KEYS[choice]
                print(f"     energy -> {new_energy}")
                continue
            if choice == "p":
                play(path)
                continue
            if choice == "n":
                break
            if choice == "s":
                con.execute(
                    """UPDATE bgm_tracks
                       SET tone_tag=?, energy_tag=?, manual_review=1
                       WHERE track_id=?""",
                    (new_tone, new_energy, r["track_id"]),
                )
                con.commit()
                saved += 1
                print(f"     ✓ saved ({new_tone}/{new_energy}, "
                      f"manual_review=1)")
                break
            if choice == "q":
                # save current, then quit
                con.execute(
                    """UPDATE bgm_tracks
                       SET tone_tag=?, energy_tag=?, manual_review=1
                       WHERE track_id=?""",
                    (new_tone, new_energy, r["track_id"]),
                )
                con.commit()
                saved += 1
                stop_playback()
                print(f"\n  saved {saved} row(s); quitting at {i}/{total}.")
                return
            print(f"     unknown key: {choice!r}")

        stop_playback()

    print(f"\n  done — saved {saved}/{total} row(s).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Review and re-tag BGM tracks.")
    ap.add_argument("--filter", default="unsorted",
                    help="unsorted | low-conf | all-auto | tone:<warm|fun|calm|trends>")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N rows (0 = no limit)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    sql, params = build_query(args.filter)
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, params).fetchall()
        log.info("filter=%s -> %d row(s)", args.filter, len(rows))
        prompt_loop(con, rows)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
