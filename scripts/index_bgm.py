"""
scripts/index_bgm.py — Index every mp3 under assets/bgm/ into bgm_tracks
with auto-tagging from filename keywords.

Why this exists
---------------
We dropped 93 royalty-free Pixabay tracks into assets/bgm/. The Cameraman
agent later picks one per card based on tone_primary. Manually tagging
93 files is tedious — but Pixabay filenames are descriptive enough
(e.g. "redproductions-charming-lofi-cozy-peaceful-warm-wonderful-music-196174.mp3")
that a keyword-scoring pass gets us a decent first-cut classification.
Humans verify the result via the review CLI, flipping manual_review=1.

Tagging strategy
----------------
1. Parse Pixabay filename:  {artist-slug}-{title-slug}-{numeric_id}.mp3
2. Lowercase, hyphens → spaces, decode 'x27' → "'".
3. Score each tone (warm | fun | calm | trends) by weighted keyword hits.
4. Pick max-scoring tone; confidence = winning_score / total_score.
5. Energy from a separate keyword list (high/mid/low).
6. instrument_csv & vibe_csv from simple keyword sets.
7. ffprobe → duration_sec & bitrate.

Idempotent
----------
- Applies db/migrations/*.sql on every run (CREATE IF NOT EXISTS only).
- ON CONFLICT(track_id) preserves manual_review=1 rows untouched.
- Re-running after the user listens-and-corrects will not overwrite their work.

Usage
-----
    python -m scripts.index_bgm                # index assets/bgm/
    python -m scripts.index_bgm --bgm-dir path # different folder
    python -m scripts.index_bgm --reset        # drop auto-tagged rows first
    python -m scripts.index_bgm --verbose
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
MIGRATIONS_DIR = ROOT / "db" / "migrations"

log = logging.getLogger("index_bgm")

# ────────────────────────────────────────────────────────────────────────
# Keyword dictionaries — tuned by reading the 93 Pixabay filenames once.
# Weights: 3 = strong signal, 2 = clear, 1 = soft.
# Multi-word keys (with spaces) are matched against the space-joined text.
# ────────────────────────────────────────────────────────────────────────
TONE_KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "warm": [
        ("warm", 3), ("cozy", 3), ("cute", 2), ("sweet", 2), ("gentle", 2),
        ("charming", 3), ("friendly", 2), ("peaceful", 2), ("delicate", 2),
        ("wonderful", 1), ("dream", 2), ("dreams", 2), ("lullaby", 3),
        ("sunny", 2), ("sunshine", 2), ("smile", 2), ("satisfied", 2),
        ("hope", 1), ("hopeful", 1), ("optimistic", 1), ("harmony", 2),
        ("harmonic", 2), ("sparkle", 1), ("holiday", 1),
        ("you and me", 3), ("carefree", 2), ("spring", 1), ("meadow", 2),
        ("sunset", 1), ("golden", 1), ("rainbow", 2), ("august", 1),
        ("sweet moments", 3), ("little moments", 3), ("pure bliss", 2),
        ("bliss", 2), ("playdate", 2), ("background", 1), ("daydream", 2),
        ("happy dreams", 3), ("free", 1), ("sweet", 2),
    ],
    "fun": [
        ("bounce", 3), ("bouncy", 3), ("bouncing", 3), ("giggly", 3),
        ("jolly", 3), ("jumpy", 3), ("jumps", 3), ("funny", 3), ("fun", 2),
        ("funshine", 3), ("joyride", 3), ("joyful", 2), ("joy", 1),
        ("happy", 1), ("jam", 1), ("jingles", 2), ("comedy", 3),
        ("silly", 3), ("kids", 2), ("childrens", 2), ("child", 2),
        ("quirky", 3), ("cheerful", 2), ("celebration", 2),
        ("laughing", 3), ("laugh", 2), ("drunk", 2), ("hoedown", 3),
        ("eat me", 2), ("rock your body", 2), ("go bounce", 3),
        ("piggy", 2), ("groove", 2), ("grooves", 2), ("upbeat", 1),
        ("playful", 2), ("hawaiian shuffle", 3), ("weekend", 1),
        ("picnic", 2), ("pop", 1), ("country", 1), ("banjo", 1),
        ("hawaiian", 1), ("bunny", 2),
    ],
    "calm": [
        ("lofi", 4), ("lo fi", 4), ("chill", 3), ("ambient", 4),
        ("intimate", 3), ("slow", 2), ("relaxing", 3), ("relax", 2),
        ("calm", 3), ("sleep", 3), ("quiet", 2), ("soft", 2),
        ("mellow", 3), ("dreamy", 2), ("soothing", 3), ("zen", 3),
        ("meditation", 4), ("moonless", 3), ("moonlight", 3),
        ("night", 2), ("futuristic", 2), ("escape", 1), ("ending", 2),
        ("beginning is the end", 4), ("dungeon", 3), ("spirit", 2),
        ("halloween", 2), ("spooky", 3), ("dramatic", 1),
        ("cinematic", 2), ("orchestral", 1), ("flute", 1),
        ("laid back", 1), ("piano strings", 1),
    ],
    "trends": [
        ("ibiza", 4), ("house", 4), ("hawaiian chill", 3),
        ("bossa", 3), ("dance", 3), ("electronic", 2),
        ("ambient electronic", 3), ("dj", 2), ("party", 3),
        ("club", 3), ("edm", 4), ("drop", 2), ("vibrant", 2),
        ("summer bossa", 4), ("summer", 1), ("ibiza chill", 4),
        ("shallow water", 1), ("fading summer", 2),
    ],
}

# Energy buckets — first match wins, in order high → low → mid default.
HIGH_ENERGY = {
    "bounce", "bouncy", "bouncing", "joyride", "jumps", "jumpy",
    "upbeat", "dance", "party", "ibiza", "house", "groove", "grooves",
    "hoedown", "rock", "comedy", "drunk", "celebration", "laughing",
    "eat", "go", "vibrant", "hawaiian", "shuffle", "edm", "dj",
}
LOW_ENERGY = {
    "lofi", "chill", "ambient", "intimate", "slow", "relaxing",
    "calm", "peaceful", "lullaby", "soft", "mellow", "dreamy",
    "soothing", "zen", "meditation", "moonless", "moonlight",
    "halloween", "dungeon", "spirit", "futuristic", "ending",
    "spooky", "sunset", "delicate", "laid", "cinematic",
}

INSTRUMENTS = {
    "ukulele", "whistle", "whistling", "piano", "guitar",
    "glockenspiel", "marimba", "banjo", "strings", "claps",
    "jazz", "bossa", "flute", "electronic", "orchestral",
    "acoustic", "rock", "harmonic", "keyboard", "lofi",
    "ambient", "folk",
}

VIBE_KEYS = {
    "sweet", "nostalgic", "dreamy", "dramatic", "comedic",
    "mysterious", "romantic", "playful", "cinematic", "dark",
    "spooky", "wholesome", "warm", "carefree", "cozy",
    "celebratory", "kids", "intimate", "futuristic",
}

NUMERIC_RE = re.compile(r"^\d+$")
APOSTROPHE_DECODE = re.compile(r"x27")


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────
def parse_filename(name: str) -> dict:
    """Pixabay convention: {artist}-{title}-{numeric_id}.mp3."""
    stem = Path(name).stem
    bits = stem.rsplit("-", 1)
    if len(bits) == 2 and NUMERIC_RE.match(bits[1]):
        descriptive, track_id = bits
    else:
        descriptive = stem
        track_id = "h_" + hashlib.md5(stem.encode()).hexdigest()[:10]

    tokens = descriptive.split("-")
    # Heuristic: first token is the artist slug. Best-effort, not perfect.
    artist_slug = tokens[0] if tokens else "unknown"
    title_slug = "-".join(tokens[1:]) if len(tokens) > 1 else descriptive

    title_h = APOSTROPHE_DECODE.sub("'", title_slug).replace("-", " ")
    descriptive_h = APOSTROPHE_DECODE.sub("'", descriptive).lower()

    return {
        "track_id": track_id,
        "artist": artist_slug,
        "title": title_h.strip(),
        "search_text": descriptive_h,
    }


def ffprobe_meta(path: Path) -> dict:
    """Extract duration + bitrate. Returns Nones on failure."""
    if not shutil.which("ffprobe"):
        return {"duration_sec": None, "bitrate": None}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(path)],
            capture_output=True, text=True, check=True, timeout=10,
        )
        d = json.loads(out.stdout).get("format", {})
        return {
            "duration_sec": float(d.get("duration")) if d.get("duration") else None,
            "bitrate": int(d.get("bit_rate")) if d.get("bit_rate") else None,
        }
    except Exception as exc:
        log.warning("ffprobe failed for %s: %s", path.name, exc)
        return {"duration_sec": None, "bitrate": None}


def score_tags(search_text: str) -> dict:
    """Score the descriptive filename slug against keyword dictionaries."""
    text_spaced = search_text.replace("-", " ")
    tokens = set(re.split(r"\s+", text_spaced))

    scores = {k: 0 for k in TONE_KEYWORDS}
    for tone, kws in TONE_KEYWORDS.items():
        for kw, weight in kws:
            if " " in kw:
                if kw in text_spaced:
                    scores[tone] += weight
            else:
                if kw in tokens:
                    scores[tone] += weight

    total = sum(scores.values())
    if total == 0:
        tone_tag = "unsorted"
        confidence = 0.0
    else:
        tone_tag = max(scores, key=scores.get)
        confidence = round(scores[tone_tag] / total, 2)

    # Energy
    if tokens & HIGH_ENERGY:
        energy = "high"
    elif tokens & LOW_ENERGY:
        energy = "low"
    else:
        energy = "mid"

    # Instruments + vibe
    insts = sorted(tokens & INSTRUMENTS)
    vibes = sorted(tokens & VIBE_KEYS)

    return {
        "tone_tag": tone_tag,
        "tone_scores": scores,
        "energy_tag": energy,
        "instrument_csv": ",".join(insts) if insts else None,
        "vibe_csv": ",".join(vibes) if vibes else None,
        "auto_tag_confidence": confidence,
    }


# ────────────────────────────────────────────────────────────────────────
# DB ops
# ────────────────────────────────────────────────────────────────────────
def apply_migrations(con: sqlite3.Connection) -> int:
    n = 0
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        con.executescript(sql_file.read_text(encoding="utf-8"))
        n += 1
    con.commit()
    log.info("applied %d migration(s) from %s", n, MIGRATIONS_DIR)
    return n


UPSERT_SQL = """
INSERT INTO bgm_tracks
    (track_id, file_path, filename, artist, title,
     source, license, duration_sec, bitrate,
     tone_tag, energy_tag, instrument_csv, vibe_csv,
     auto_tag_confidence)
VALUES (?, ?, ?, ?, ?, 'pixabay', 'pixabay_content_license',
        ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(track_id) DO UPDATE SET
    file_path           = excluded.file_path,
    filename            = excluded.filename,
    artist              = excluded.artist,
    title               = excluded.title,
    duration_sec        = excluded.duration_sec,
    bitrate             = excluded.bitrate,
    tone_tag            = CASE WHEN bgm_tracks.manual_review = 1
                              THEN bgm_tracks.tone_tag
                              ELSE excluded.tone_tag END,
    energy_tag          = CASE WHEN bgm_tracks.manual_review = 1
                              THEN bgm_tracks.energy_tag
                              ELSE excluded.energy_tag END,
    instrument_csv      = excluded.instrument_csv,
    vibe_csv            = excluded.vibe_csv,
    auto_tag_confidence = CASE WHEN bgm_tracks.manual_review = 1
                              THEN bgm_tracks.auto_tag_confidence
                              ELSE excluded.auto_tag_confidence END
"""


def index_one(con: sqlite3.Connection, path: Path) -> dict:
    parsed = parse_filename(path.name)
    meta = ffprobe_meta(path)
    tags = score_tags(parsed["search_text"])
    rel_path = str(path.resolve().relative_to(ROOT))
    con.execute(
        UPSERT_SQL,
        (
            parsed["track_id"],
            rel_path,
            path.name,
            parsed["artist"],
            parsed["title"],
            meta["duration_sec"],
            meta["bitrate"],
            tags["tone_tag"],
            tags["energy_tag"],
            tags["instrument_csv"],
            tags["vibe_csv"],
            tags["auto_tag_confidence"],
        ),
    )
    return {
        "track_id": parsed["track_id"],
        "title": parsed["title"],
        "tone": tags["tone_tag"],
        "energy": tags["energy_tag"],
        "duration": meta["duration_sec"],
        "scores": tags["tone_scores"],
        "confidence": tags["auto_tag_confidence"],
    }


# ────────────────────────────────────────────────────────────────────────
# Reporting
# ────────────────────────────────────────────────────────────────────────
def print_report(con: sqlite3.Connection) -> None:
    print("\n=== bgm_tracks distribution ===")
    rows = con.execute(
        """SELECT tone_tag, energy_tag, COUNT(*) AS c
           FROM bgm_tracks
           GROUP BY tone_tag, energy_tag
           ORDER BY tone_tag, energy_tag"""
    ).fetchall()
    print(f"  {'tone':10s} {'energy':6s}  count")
    for r in rows:
        print(f"  {r[0]:10s} {r[1]:6s}  {r[2]}")

    total = con.execute("SELECT COUNT(*) FROM bgm_tracks").fetchone()[0]
    reviewed = con.execute("SELECT COUNT(*) FROM bgm_tracks WHERE manual_review=1").fetchone()[0]
    low_conf = con.execute(
        "SELECT COUNT(*) FROM bgm_tracks WHERE auto_tag_confidence < 0.4"
    ).fetchone()[0]
    print(f"\n  total tracks:        {total}")
    print(f"  manually reviewed:   {reviewed}")
    print(f"  low-confidence (<0.4): {low_conf}  ← prioritize these in review")

    print("\n=== sample of low-confidence rows (top 8) ===")
    rows = con.execute(
        """SELECT title, tone_tag, energy_tag, instrument_csv,
                  printf('%.2f', auto_tag_confidence) AS conf
           FROM bgm_tracks
           WHERE manual_review = 0
           ORDER BY auto_tag_confidence ASC
           LIMIT 8"""
    ).fetchall()
    for r in rows:
        print(f"  conf={r[4]}  {r[1]:8s} {r[2]:5s}  {r[0][:70]}")


# ────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Index BGM library + auto-tag.")
    ap.add_argument("--bgm-dir", default=str(ROOT / "assets" / "bgm"))
    ap.add_argument("--reset", action="store_true",
                    help="DELETE auto-tagged rows (keeps manual_review=1) before re-indexing")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bgm_dir = Path(args.bgm_dir).resolve()
    if not bgm_dir.exists():
        log.error("bgm dir not found: %s", bgm_dir)
        return 1

    files = sorted(bgm_dir.glob("*.mp3"))
    log.info("scanning %s — %d mp3 file(s)", bgm_dir, len(files))
    if not files:
        log.warning("no mp3 files; nothing to do")
        return 0

    con = sqlite3.connect(str(DB_PATH))
    try:
        apply_migrations(con)

        if args.reset:
            n = con.execute(
                "DELETE FROM bgm_tracks WHERE manual_review = 0"
            ).rowcount
            con.commit()
            log.info("--reset: deleted %d auto-tagged rows", n)

        per_tone = Counter()
        per_energy = Counter()
        for f in files:
            res = index_one(con, f)
            per_tone[res["tone"]] += 1
            per_energy[res["energy"]] += 1
            if args.verbose:
                log.debug("%s -> %s/%s (conf=%.2f)",
                          f.name, res["tone"], res["energy"], res["confidence"])
        con.commit()
        log.info("indexed %d track(s)", len(files))
        log.info("by tone:    %s", dict(per_tone))
        log.info("by energy:  %s", dict(per_energy))

        print_report(con)
    finally:
        con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
