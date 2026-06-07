"""youtube/analytics.py — per-video performance pull for the av-vs-rf bandit.

YouTube Analytics API v2 reports per CALENDAR DAY with a 1-2 day processing lag,
so "48h views" is approximated as [publish_date, publish_date + 2 days]. retention
= averageViewPercentage (0-100). Falls back to Data API lifetime viewCount when
Analytics has no row yet (very fresh / zero-traffic video).

See notes/first_month_plan.md §4-5 and agents/bandit.py.
"""
from __future__ import annotations

import datetime as dt
import logging

log = logging.getLogger("youtube.analytics")


def _analytics():
    from .oauth import get_analytics
    return get_analytics()


def _data_api():
    from .oauth import get_youtube
    return get_youtube()


def video_metrics(video_id: str, start_date: str, end_date: str) -> dict:
    """Query Analytics for one video over [start_date, end_date] (YYYY-MM-DD,
    inclusive). Returns {views, retention_pct, est_minutes, source}. Never
    raises — on failure returns zeros with source='error'."""
    try:
        ya = _analytics()
        resp = ya.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views,estimatedMinutesWatched,averageViewPercentage",
            dimensions="video",
            filters=f"video=={video_id}",
        ).execute()
        rows = resp.get("rows") or []
        if rows:
            # columns in the order of `metrics` after the `video` dimension
            r = rows[0]
            # r = [videoId, views, estimatedMinutesWatched, averageViewPercentage]
            return {
                "views": int(r[1] or 0),
                "est_minutes": float(r[2] or 0.0),
                "retention_pct": float(r[3] or 0.0),
                "source": "analytics",
            }
    except Exception as e:
        log.warning("analytics query failed for %s: %s", video_id, e)

    # Fallback: lifetime viewCount via Data API (no retention available)
    try:
        yt = _data_api()
        resp = yt.videos().list(part="statistics", id=video_id).execute()
        items = resp.get("items") or []
        if items:
            views = int(items[0]["statistics"].get("viewCount", 0))
            return {"views": views, "est_minutes": 0.0,
                    "retention_pct": 0.0, "source": "data_api"}
    except Exception as e:
        log.warning("data-api fallback failed for %s: %s", video_id, e)

    return {"views": 0, "est_minutes": 0.0, "retention_pct": 0.0, "source": "error"}


def window_48h(publish_iso_utc: str) -> tuple[str, str]:
    """[start_date, end_date] (YYYY-MM-DD) covering ~48h from a publishAt ISO-UTC
    timestamp. end is publish_date + 2 days (inclusive day granularity)."""
    try:
        d0 = dt.datetime.strptime(publish_iso_utc[:10], "%Y-%m-%d").date()
    except Exception:
        d0 = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=2)
    return d0.isoformat(), (d0 + dt.timedelta(days=2)).isoformat()


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level="INFO")
    ap = argparse.ArgumentParser(description="pull one video's 48h metrics")
    ap.add_argument("video_id")
    ap.add_argument("--publish", help="publishAt ISO-UTC (default: 3 days ago)")
    args = ap.parse_args()
    pub = args.publish or (
        (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3))
        .strftime("%Y-%m-%dT%H:%M:%SZ"))
    s, e = window_48h(pub)
    print(f"window {s} .. {e}")
    print(video_metrics(args.video_id, s, e))
