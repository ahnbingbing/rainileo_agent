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


# Engagement metrics are the best available packaging/breakout proxy: YouTube does NOT expose
# impressions or impressionClickThroughRate via the Analytics API ("Unknown identifier" — they are
# Studio-dashboard-only), so we can't measure feed-push or hook-stop CTR directly. What IS queryable —
# engagement (likes/comments/shares) per view, subscriber conversion, and absolute avg_view_duration —
# captures "of the people reached, how strongly did it land", which is what drives the Shorts breakout
# push. These feed the packaging/bumper-arm dashboard (scripts/packaging_report.py). PD 2026-10-12.
_METRICS = ("views,likes,comments,shares,subscribersGained,"
            "averageViewDuration,averageViewPercentage,estimatedMinutesWatched")


def _zero_metrics(source: str) -> dict:
    return {"views": 0, "likes": 0, "comments": 0, "shares": 0, "subs_gained": 0,
            "avg_view_dur": 0.0, "retention_pct": 0.0, "est_minutes": 0.0, "source": source}


def video_metrics(video_id: str, start_date: str, end_date: str) -> dict:
    """Query Analytics for one video over [start_date, end_date] (YYYY-MM-DD, inclusive). Returns
    {views, likes, comments, shares, subs_gained, avg_view_dur, retention_pct, est_minutes, source}.
    Never raises — on failure returns zeros with source='error'. (impressions/CTR are not API-queryable
    — engagement + retention are the available breakout proxies.)"""
    try:
        ya = _analytics()
        resp = ya.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics=_METRICS,
            dimensions="video",
            filters=f"video=={video_id}",
        ).execute()
        rows = resp.get("rows") or []
        if rows:
            # columns after the `video` dimension, in _METRICS order:
            # [video, views, likes, comments, shares, subscribersGained,
            #  averageViewDuration, averageViewPercentage, estimatedMinutesWatched]
            r = rows[0]
            return {
                "views": int(r[1] or 0),
                "likes": int(r[2] or 0),
                "comments": int(r[3] or 0),
                "shares": int(r[4] or 0),
                "subs_gained": int(r[5] or 0),
                "avg_view_dur": float(r[6] or 0.0),
                "retention_pct": float(r[7] or 0.0),
                "est_minutes": float(r[8] or 0.0),
                "source": "analytics",
            }
    except Exception as e:
        log.warning("analytics query failed for %s: %s", video_id, e)

    # Fallback: lifetime viewCount via Data API (no retention/engagement available)
    try:
        yt = _data_api()
        resp = yt.videos().list(part="statistics", id=video_id).execute()
        items = resp.get("items") or []
        if items:
            st = items[0]["statistics"]
            out = _zero_metrics("data_api")
            out["views"] = int(st.get("viewCount", 0) or 0)
            out["likes"] = int(st.get("likeCount", 0) or 0)
            out["comments"] = int(st.get("commentCount", 0) or 0)
            return out
    except Exception as e:
        log.warning("data-api fallback failed for %s: %s", video_id, e)

    return _zero_metrics("error")


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
