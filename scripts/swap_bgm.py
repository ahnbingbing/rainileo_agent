"""
scripts/swap_bgm.py — replace the main BGM of an already-assembled episode
without re-rendering, then (optionally) take down the claimed YouTube upload and
re-upload the clean version on the same schedule.

Why this exists
---------------
YouTube Content ID sometimes claims a track AFTER we upload — even royalty-free
library tracks get false/AdRev matches. A claim sits on the *main BGM* of one
specific episode (the shared intro/outro bumper theme is common to every video,
so a claim that hits only one or two episodes is never the bumper — it is that
episode's main BGM). The fix is therefore surgical: keep the bumper-region audio
exactly as-is and swap only the middle [intro_dur, total-outro_dur] section.

The assemble step (scripts/assemble_episode.py, case C) lays audio out as
    [intro_bumper_audio] + [main_bgm_over_cuts] + [outro_bumper_audio]
with the cut audio stripped, so the middle region is *pure BGM* and a clean
replacement target. We re-mux video losslessly (-c:v copy) and only rebuild the
audio track, loudness-matched to the original BGM so the mix level is unchanged.

CLI
---
    # just swap the audio (produces a new mp4)
    python scripts/swap_bgm.py swap --in EP.mp4 --bgm assets/bgm/NEW.mp3 --out OUT.mp4

    # swap + take down the claimed upload + re-upload on the same publish_at,
    # update the DB card, and record the old track as Content-ID-claimed so the
    # picker stops choosing it.
    python scripts/swap_bgm.py reupload --card 8395b4a6 [--bgm assets/bgm/NEW.mp3]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAIMED_PATH = ROOT / "data" / "bgm_claimed.json"
DB_PATH = ROOT / "data" / "agent.db"

INTRO_DUR_DEFAULT = 1.5   # assets/branding/intro_bumper.mp4
OUTRO_DUR_DEFAULT = 2.5   # assets/branding/outro_bumper.mp4


# ── ffmpeg helpers ────────────────────────────────────────────────────────────
def _duration(path: Path | str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def _integrated_loudness(path: Path | str, start: float, dur: float) -> float | None:
    """Measure integrated loudness (LUFS) of a segment via loudnorm analysis."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
         "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.DOTALL)
    if not m:
        return None
    try:
        return float(json.loads(m.group(0))["input_i"])
    except Exception:
        return None


def swap_bgm(
    in_path: Path | str,
    bgm_path: Path | str,
    out_path: Path | str,
    intro_dur: float = INTRO_DUR_DEFAULT,
    outro_dur: float = OUTRO_DUR_DEFAULT,
    bgm_start: float = 0.0,
    match_loudness: bool = True,
) -> Path:
    """Replace the main-BGM region of an assembled episode, preserving the
    intro/outro bumper audio. Returns the output path."""
    in_path, bgm_path, out_path = Path(in_path), Path(bgm_path), Path(out_path)
    total = _duration(in_path)
    mid = total - intro_dur - outro_dur
    if mid <= 0.5:
        raise ValueError(f"episode too short ({total:.2f}s) for intro+outro "
                         f"({intro_dur}+{outro_dur}); nothing to swap")

    gain_db = 0.0
    if match_loudness:
        orig_i = _integrated_loudness(in_path, intro_dur, mid)
        new_i = _integrated_loudness(bgm_path, bgm_start, mid)
        if orig_i is not None and new_i is not None:
            # clamp so a near-silent original can't blow up the new track
            gain_db = max(-12.0, min(12.0, orig_i - new_i))

    fade = 0.6
    fade_out_start = max(0.0, mid - 0.9)
    filt = (
        f"[0:a]atrim=start=0:end={intro_dur:.3f},asetpts=PTS-STARTPTS[ain];"
        f"[0:a]atrim=start={total - outro_dur:.3f},asetpts=PTS-STARTPTS[aout];"
        f"[1:a]atrim=start={bgm_start:.3f}:duration={mid:.3f},asetpts=PTS-STARTPTS,"
        f"volume={gain_db:.2f}dB,"
        f"afade=t=in:st=0:d={fade},afade=t=out:st={fade_out_start:.3f}:d=0.9[amid];"
        f"[ain][amid][aout]concat=n=3:v=0:a=1[aout_final]"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(in_path), "-i", str(bgm_path),
        "-filter_complex", filt,
        "-map", "0:v", "-map", "[aout_final]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path), "-y",
    ]
    subprocess.run(cmd, check=True)
    return out_path


# ── claimed-track ledger (so the picker stops choosing claimed tracks) ─────────
def load_claimed() -> set[str]:
    if CLAIMED_PATH.exists():
        try:
            return set(json.loads(CLAIMED_PATH.read_text()))
        except Exception:
            return set()
    return set()


def mark_claimed(track_basename: str) -> None:
    claimed = load_claimed()
    claimed.add(track_basename)
    CLAIMED_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLAIMED_PATH.write_text(json.dumps(sorted(claimed), ensure_ascii=False, indent=2))


def _label_of(basename: str) -> str:
    """Artist/label prefix of a library filename (e.g. 'lp-studio', 'musictown').
    Content-ID claims are often catalog-wide, so once one track from a label is
    claimed we avoid the whole label when picking a replacement."""
    # filenames are '<label>-<title>-<id>.mp3'; the label is everything up to the
    # first numeric-or-descriptive break — use the leading 1-2 hyphen tokens.
    stem = basename.rsplit(".", 1)[0]
    toks = stem.split("-")
    # most labels are a single leading token; "lp-studio" spans two hyphen tokens.
    return "lp-studio" if toks[:2] == ["lp", "studio"] else toks[0]


def claimed_labels() -> set[str]:
    return {_label_of(t) for t in load_claimed()}


# ── DB / card helpers ─────────────────────────────────────────────────────────
def _card_row(card_prefix: str) -> sqlite3.Row:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM cards WHERE card_id LIKE ? OR youtube_video_id = ?",
        (card_prefix + "%", card_prefix)).fetchone()
    con.close()
    if not row:
        raise SystemExit(f"no card matching {card_prefix!r}")
    return row


def _old_bgm_for_card(card_id: str, payload: dict) -> str | None:
    """Resolve the (claimed) main BGM filename for a card: render_meta.json in the
    cameraman scratch dir is authoritative; fall back to payload['bgm']."""
    for meta in sorted(ROOT.glob(f"data/tmp/cameraman_{card_id[:8]}*/render_meta.json")):
        m = re.search(r"assets/bgm/([A-Za-z0-9_.-]+\.mp3)", meta.read_text())
        if m:
            return m.group(1)
    bgm = payload.get("bgm")
    return Path(bgm).name if bgm else None


def reupload(card_prefix: str, new_bgm: str | None = None,
             fixed_file: str | None = None, dry_run: bool = False) -> dict:
    """Full copyright-recovery flow for one card: mark the claimed BGM, swap in a
    safe replacement, take down the claimed YouTube upload, and re-upload the
    clean cut on the SAME schedule. Updates the DB card to the new video_id."""
    from youtube.upload import upload_short, veto_video

    row = _card_row(card_prefix)
    card_id = row["card_id"]
    payload = json.loads(row["payload_json"])
    draft = payload.get("draft", {})
    title = draft.get("title") or payload.get("title") or payload.get("theme")
    description = draft.get("description") or payload.get("narrative_oneliner") or ""
    tags = [t.lstrip("#") for t in (draft.get("hashtags") or payload.get("hashtag_slate") or [])]
    publish_at = row["youtube_publish_at"]
    old_vid = row["youtube_video_id"]
    src = fixed_file or row["output_video_path"]

    # 1. record the claimed track so the picker never chooses it again
    old_bgm = _old_bgm_for_card(card_id, payload)
    if old_bgm:
        mark_claimed(old_bgm)

    # 2. pick a safe replacement (different label than any claimed one)
    if new_bgm is None:
        new_bgm = _pick_safe_replacement(payload.get("bgm_mood", ""), card_id)

    # 3. swap (unless a pre-swapped file was provided)
    if fixed_file:
        out_path = Path(fixed_file)
    else:
        out_path = Path(src).with_name(Path(src).stem + "_bgmfix.mp4")
        swap_bgm(src, ROOT / "assets" / "bgm" / Path(new_bgm).name, out_path)

    summary = {"card_id": card_id, "old_video_id": old_vid, "claimed_bgm": old_bgm,
               "new_bgm": Path(new_bgm).name if new_bgm else None,
               "fixed_file": str(out_path), "publish_at": publish_at}
    if dry_run:
        summary["dry_run"] = True
        return summary

    # 4. take down the claimed upload, then upload the clean cut on same schedule
    if old_vid:
        veto_video(old_vid, delete=True)
    resp = upload_short(out_path, title, description, tags=tags,
                        publish_at_iso=publish_at)
    new_vid = resp["id"]

    # 5. update the card to the new video + clean file
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "UPDATE cards SET youtube_video_id=?, output_video_path=?, uploaded=1, "
        "updated_at=? WHERE card_id=?",
        (new_vid, str(out_path), datetime.now(timezone.utc).isoformat(), card_id))
    con.commit()
    con.close()

    summary["new_video_id"] = new_vid
    return summary


def _pick_safe_replacement(bgm_mood: str, seed_key: str) -> str:
    """Deterministic replacement track for a mood, excluding any claimed track or
    claimed label. Imports the live mood map so it tracks the picker's pools."""
    import hashlib
    import importlib
    cam = importlib.import_module("agents.cameraman")
    pool = list(cam._BGM_MOOD_MAP.get(bgm_mood, []))  # type: ignore[attr-defined]
    if not pool:
        # flatten everything as a last resort
        pool = [t for v in cam._BGM_MOOD_MAP.values() for t in v]  # type: ignore[attr-defined]
    claimed = load_claimed()
    bad_labels = claimed_labels()
    safe = [t for t in pool if t not in claimed and _label_of(t) not in bad_labels]
    if not safe:
        safe = [t for t in pool if t not in claimed] or pool
    idx = int(hashlib.sha1(seed_key.encode()).hexdigest(), 16) % len(safe)
    return safe[idx]


# ── CLI ───────────────────────────────────────────────────────────────────────
def _main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("swap", help="swap BGM only, write new mp4")
    s.add_argument("--in", dest="in_path", required=True)
    s.add_argument("--bgm", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--intro", type=float, default=INTRO_DUR_DEFAULT)
    s.add_argument("--outro", type=float, default=OUTRO_DUR_DEFAULT)
    s.add_argument("--bgm-start", type=float, default=0.0)
    s.add_argument("--no-match-loudness", action="store_true")

    r = sub.add_parser("reupload", help="copyright recovery: swap BGM + take down "
                       "claimed upload + re-upload on same schedule + update DB")
    r.add_argument("--card", required=True, help="card_id prefix or old youtube video_id")
    r.add_argument("--bgm", default=None, help="replacement track (auto-picked if omitted)")
    r.add_argument("--fixed-file", default=None,
                   help="path to an already-swapped mp4 (skips the swap step)")
    r.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    if args.cmd == "swap":
        out = swap_bgm(args.in_path, args.bgm, args.out,
                       intro_dur=args.intro, outro_dur=args.outro,
                       bgm_start=args.bgm_start,
                       match_loudness=not args.no_match_loudness)
        print(out)
    elif args.cmd == "reupload":
        res = reupload(args.card, new_bgm=args.bgm,
                       fixed_file=args.fixed_file, dry_run=args.dry_run)
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
