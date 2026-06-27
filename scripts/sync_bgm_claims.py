"""
scripts/sync_bgm_claims.py — pull live YouTube Content-ID claim signals into the
BGM "claimed" ledger (data/bgm_claimed.json) so the picker
(agents/cameraman.py:_pick_bgm_track) never chooses a claimed track again.

Why this exists (PD 2026-06-27, "당근 해야 하는" 근본 방어책)
-----------------------------------------------------------
A claimed track only lands in the ledger today when the full copyright-recovery
flow runs (scripts/swap_bgm.py reupload → mark_claimed). If PD fixes a claim
manually, or uses scripts/reupload_episode.py (which does NOT mark claimed), the
offending track is never recorded — so the picker happily re-uses it and the
claim recurs. This script closes that hole: given the videos that got claimed, it
resolves each one's main BGM and marks it claimed, idempotently.

API feasibility (investigated 2026-06-27 — IMPORTANT)
-----------------------------------------------------
The public/owner **YouTube Data API v3 does NOT expose Content-ID claims.** A
non-MCN channel has no Content Owner / CMS, so the **youtubePartner** API
(claimSearch/claims — where claim data actually lives) returns 403
"insufficient authentication scopes"/permission, even with the youtubepartner
scope. videos().list(part="status,contentDetails,fileDetails,suggestions,
monetizationDetails") carries no per-claim field.

The ONE automatable signal the Data API gives us is `status.rejectionReason`:
when a Content-ID claim *blocks the upload outright* it surfaces as
rejectionReason ∈ {"claim","copyright"}. That is the worst case (video can't go
public) and `auto_detect_blocked()` catches it. But the common false-AdRev claim
leaves the video PUBLIC with monetization redirected and produces NO Data-API
signal — those can only be seen by a human in YouTube Studio (Content tab →
"저작권" / "Restrictions"). For those, PD passes the claimed video_id(s) here and
`sync_claims_from_videos()` records the BGM.

Durable BGM resolution
----------------------
A claim often lands hours/days after upload, by which point the cameraman scratch
dir (render_meta.json) has been pruned — so we can't always recover the track
post-hoc. To make resolution reliable, the producer snapshots each video's BGM at
upload time into data/bgm_by_video.json via record_bgm_for_video(). Resolution
order: that durable map → render_meta.json glob → card payload['bgm'].

CLI
---
    # PD saw these claimed in Studio → record their BGM as claimed
    python scripts/sync_bgm_claims.py --videos QlgbeyqkpDI jfyqT-7SqAU

    # auto-scan recent uploads for claim-BLOCKED videos (rejectionReason) + sync
    python scripts/sync_bgm_claims.py --auto

    # both, and show what each video resolved to (no-op safe / idempotent)
    python scripts/sync_bgm_claims.py --auto --videos QlgbeyqkpDI -v
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path

# reuse the single source of truth for the ledger + BGM resolution
from scripts.swap_bgm import (
    load_claimed,
    mark_claimed,
    _old_bgm_for_card,
    _card_row,
)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"
# durable video_id → bgm basename map, snapshotted at upload time (render_meta is
# ephemeral and gets pruned before a claim usually lands).
BGM_BY_VIDEO_PATH = ROOT / "data" / "bgm_by_video.json"

log = logging.getLogger("sync_bgm_claims")

# Data-API status.rejectionReason values that mean "a Content-ID / copyright
# claim blocked this upload" (the only claim signal the public API exposes).
_BLOCK_REASONS = {"claim", "copyright"}


# ── durable video_id → bgm ledger ──────────────────────────────────────────────
def _load_bgm_by_video() -> dict[str, str]:
    if BGM_BY_VIDEO_PATH.exists():
        try:
            return dict(json.loads(BGM_BY_VIDEO_PATH.read_text()))
        except Exception:
            return {}
    return {}


def record_bgm_for_video(video_id: str, card_id: str) -> str | None:
    """Snapshot a freshly-uploaded video's main BGM so a later claim-sync can
    always resolve it (render_meta gets pruned). Idempotent; call at upload time
    while the cameraman scratch dir is still warm. Returns the basename recorded
    (or None if it couldn't be resolved — non-fatal)."""
    if not video_id or not card_id:
        return None
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
        con.close()
        payload = json.loads(row["payload_json"]) if row else {}
    except Exception:
        payload = {}
    bgm = _old_bgm_for_card(card_id, payload)
    if not bgm:
        return None
    m = _load_bgm_by_video()
    if m.get(video_id) == bgm:
        return bgm
    m[video_id] = bgm
    BGM_BY_VIDEO_PATH.parent.mkdir(parents=True, exist_ok=True)
    BGM_BY_VIDEO_PATH.write_text(
        json.dumps(m, ensure_ascii=False, indent=2, sort_keys=True))
    log.info("recorded bgm for %s ← %s", video_id, bgm)
    return bgm


# ── resolve a video_id → its main BGM basename ─────────────────────────────────
def _bgm_for_video(video_id: str) -> str | None:
    """Resolve the main BGM filename for an uploaded video. Order: durable
    video→bgm map (most reliable) → render_meta glob / payload via card lookup."""
    durable = _load_bgm_by_video().get(video_id)
    if durable:
        return durable
    try:
        row = _card_row(video_id)            # matches youtube_video_id == video_id
    except SystemExit:
        return None
    payload = json.loads(row["payload_json"])
    return _old_bgm_for_card(row["card_id"], payload)


# ── core: mark the claimed videos' BGM ─────────────────────────────────────────
def sync_claims_from_videos(video_ids: list[str]) -> dict:
    """Resolve each claimed video's main BGM and mark it claimed. Idempotent.
    Returns a summary: per-video resolution + newly-added tracks."""
    before = load_claimed()
    resolved: dict[str, str | None] = {}
    for vid in video_ids:
        bgm = _bgm_for_video(vid)
        resolved[vid] = bgm
        if bgm:
            mark_claimed(bgm)          # also implicitly excludes the whole label
    after = load_claimed()
    return {
        "videos": resolved,
        "unresolved": sorted(v for v, b in resolved.items() if not b),
        "newly_claimed": sorted(after - before),
        "claimed_total": len(after),
    }


# ── auto-detect claim-BLOCKED uploads via the Data API ─────────────────────────
def auto_detect_blocked(limit: int = 60) -> list[str]:
    """Scan the most-recent uploaded videos for a Content-ID/copyright BLOCK
    (status.rejectionReason ∈ {claim, copyright}) — the only claim signal the
    Data API exposes. Returns the affected video_ids. (Most false-AdRev claims
    leave the video public and produce NO signal — see module docstring; those
    need PD-supplied ids via --videos.)"""
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT youtube_video_id FROM cards "
        "WHERE youtube_video_id IS NOT NULL AND youtube_video_id != '' "
        "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    vids = [r[0] for r in rows]
    if not vids:
        return []
    from youtube.oauth import get_youtube
    yt = get_youtube()
    flagged: list[str] = []
    for i in range(0, len(vids), 50):       # Data API caps id= at 50
        batch = vids[i:i + 50]
        try:
            resp = yt.videos().list(part="status", id=",".join(batch)).execute()
        except Exception as e:
            log.warning("videos.list failed for batch: %s", e)
            continue
        for it in resp.get("items", []):
            reason = (it.get("status") or {}).get("rejectionReason")
            if reason in _BLOCK_REASONS:
                flagged.append(it["id"])
                log.info("claim-blocked upload detected: %s (reason=%s)",
                         it["id"], reason)
    return flagged


# ── CLI ────────────────────────────────────────────────────────────────────────
def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos", nargs="*", default=[],
                    help="claimed youtube video_ids (from YouTube Studio)")
    ap.add_argument("--auto", action="store_true",
                    help="also scan recent uploads for claim-BLOCKED videos")
    ap.add_argument("--limit", type=int, default=60,
                    help="how many recent uploads to scan in --auto mode")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level="INFO" if args.verbose else "WARNING",
                        format="%(message)s")

    vids = list(dict.fromkeys(args.videos))       # de-dup, preserve order
    if args.auto:
        blocked = auto_detect_blocked(limit=args.limit)
        for b in blocked:
            if b not in vids:
                vids.append(b)
        print(f"[auto] claim-blocked uploads found: {blocked or 'none'}")

    if not vids:
        print("nothing to sync (pass --videos <id...> and/or --auto)")
        return

    summary = sync_claims_from_videos(vids)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["unresolved"]:
        print(f"\n⚠️  BGM 미해결 {len(summary['unresolved'])}건: "
              f"{summary['unresolved']}\n"
              "    render_meta/durable-map/payload 어디에도 트랙이 없습니다. "
              "이 회차들은 업로드 시점에 record_bgm_for_video가 안 돌았던 과거 건일 수 "
              "있습니다. 해당 트랙을 알면 직접 `swap_bgm.mark_claimed` 하거나 BGM을 "
              "알려주세요.")


if __name__ == "__main__":
    _main()
