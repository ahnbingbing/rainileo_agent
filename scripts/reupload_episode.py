"""
scripts/reupload_episode.py — take down a card's current YouTube upload and
re-upload a freshly re-rendered mp4 on the SAME schedule, then point the card at
the new video. Use after a manual re-render (render_card) that fixes a defect on
an already-scheduled launch episode.

Unlike scripts/swap_bgm.py (audio-only re-mux + claimed-track ledger), this does
NOT touch BGM and does NOT mark anything claimed — it just swaps the whole video.

    python scripts/reupload_episode.py --card 47629c67 --video data/output/episodes/EP.mp4
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"


def reupload_episode(card_prefix: str, video_path: str, dry_run: bool = False) -> dict:
    from youtube.upload import upload_short, veto_video

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM cards WHERE card_id LIKE ? OR youtube_video_id = ?",
        (card_prefix + "%", card_prefix)).fetchone()
    con.close()
    if not row:
        raise SystemExit(f"no card matching {card_prefix!r}")

    payload = json.loads(row["payload_json"])
    draft = payload.get("draft", {})
    title = draft.get("title") or payload.get("title") or payload.get("theme")
    description = draft.get("description") or payload.get("narrative_oneliner") or ""
    tags = [t.lstrip("#") for t in (draft.get("hashtags") or payload.get("hashtag_slate") or [])]
    publish_at = row["youtube_publish_at"]
    old_vid = row["youtube_video_id"]

    summary = {"card_id": row["card_id"], "old_video_id": old_vid,
               "video": video_path, "publish_at": publish_at,
               "title": title}
    if dry_run:
        summary["dry_run"] = True
        return summary

    if old_vid:
        veto_video(old_vid, delete=True)
    resp = upload_short(video_path, title, description, tags=tags,
                        publish_at_iso=publish_at)
    new_vid = resp["id"]

    con = sqlite3.connect(DB_PATH)
    con.execute(
        "UPDATE cards SET youtube_video_id=?, output_video_path=?, uploaded=1, "
        "state='published', updated_at=? WHERE card_id=?",
        (new_vid, str(video_path), datetime.now(timezone.utc).isoformat(), row["card_id"]))
    con.commit()
    con.close()
    summary["new_video_id"] = new_vid
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", required=True, help="card_id prefix or old youtube video_id")
    ap.add_argument("--video", required=True, help="path to the re-rendered mp4")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(json.dumps(reupload_episode(a.card, a.video, dry_run=a.dry_run),
                     ensure_ascii=False, indent=2))
