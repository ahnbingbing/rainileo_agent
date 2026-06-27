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


def set_thumbnail(video_id: str, image_path: Path | str) -> None:
    """Set a custom channel thumbnail on a video (PD 2026-06-24). Needs the
    'youtube' OAuth scope (present in token.json). jpg/png, <2MB, ≥640px wide."""
    yt = get_youtube()
    media = MediaFileUpload(str(image_path), mimetype="image/jpeg")
    yt.thumbnails().set(videoId=video_id, media_body=media).execute()
    log.info("thumbnail set: %s ← %s", video_id, image_path)


def veto_video(video_id: str, delete: bool = False) -> str:
    """Take down an auto-published launch episode (PD /veto). Default = flip to
    private (reversible — the scheduled publishAt is cleared so it won't go
    public). delete=True removes it entirely. Returns the action taken."""
    yt = get_youtube()
    if delete:
        yt.videos().delete(id=video_id).execute()
        log.info("veto: deleted %s", video_id)
        return "deleted"
    # set private + clear any pending publishAt so it never auto-goes-public.
    # Sending status WITHOUT publishAt clears the schedule, but the API is
    # eventually-consistent — one observed video kept its publishAt after the
    # first update, which would let it auto-publish anyway. So VERIFY publishAt
    # is gone and retry once; a private video that still carries publishAt is the
    # dangerous half-state we must not return silently.
    body = {"id": video_id,
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
    for attempt in range(2):
        yt.videos().update(part="status", body=body).execute()
        st = yt.videos().list(part="status", id=video_id).execute()["items"][0]["status"]
        if not st.get("publishAt"):
            log.info("veto: set private %s (publishAt cleared)", video_id)
            return "private"
        log.warning("veto: %s still has publishAt after attempt %d — retrying",
                    video_id, attempt + 1)
    log.error("veto: %s publishAt persisted after retry — STILL SCHEDULED", video_id)
    return "private(publishAt 잔존 — 확인 필요)"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    up = sub.add_parser("upload")
    up.add_argument("video", help="path to .mp4")
    up.add_argument("--title", required=True)
    up.add_argument("--description", default="")
    up.add_argument("--privacy", default=None)
    vt = sub.add_parser("veto")
    vt.add_argument("video_id")
    vt.add_argument("--delete", action="store_true")
    args = ap.parse_args()
    if args.cmd == "veto":
        print(veto_video(args.video_id, delete=args.delete))
    else:  # default/upload (backward compatible)
        out = upload_short(args.video, args.title, args.description, privacy=args.privacy)
        print(out["id"])
