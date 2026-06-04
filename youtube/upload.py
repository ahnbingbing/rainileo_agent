"""
youtube/upload.py — upload a rendered Short to YouTube.

Phase 0: this is the integration boundary the Cameraman calls when a card is `approved`.
Default privacy = private (set via .env YOUTUBE_DEFAULT_PRIVACY) so nothing accidentally goes live
during the launch-day shakedown.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .oauth import get_youtube

log = logging.getLogger("youtube.upload")
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PRIVACY = os.getenv("YOUTUBE_DEFAULT_PRIVACY", "private")


def upload_short(
    video_path: Path | str,
    title: str,
    description: str,
    tags: list[str] | None = None,
    privacy: str | None = None,
    publish_at_iso: str | None = None,   # e.g. "2026-05-10T12:00:00Z" for scheduled-public
) -> dict:
    yt = get_youtube()
    body: dict = {
        "snippet": {
            "title": title[:100],                 # YouTube hard cap
            "description": description[:5000],
            "tags": (tags or [])[:30],
            "categoryId": "15",                   # Pets & Animals
        },
        "status": {
            "privacyStatus": (privacy or DEFAULT_PRIVACY),
            "selfDeclaredMadeForKids": False,
        },
    }
    if publish_at_iso:
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = publish_at_iso

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        try:
            status, response = req.next_chunk()
            if status:
                log.info("upload %.0f%%", status.progress() * 100)
        except HttpError as e:
            raise RuntimeError(f"upload failed: {e}") from e

    log.info("upload ok: video_id=%s", response["id"])
    return response


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="path to .mp4")
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--privacy", default=None)
    args = ap.parse_args()
    out = upload_short(args.video, args.title, args.description, privacy=args.privacy)
    print(out["id"])
