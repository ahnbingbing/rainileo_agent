"""scripts/intake_report.py — channel INTAKE recovery tracker.

The Phase 3 gauge for the algorithmic-suppression recovery (PD 2026-10-26). Channel
intake collapsed ~95% after the v2 flood (9mo 3-6.5k → ~200 views/day) and did NOT
recover after the 10/12 rollback — the Shorts algorithm throttled channel-wide
distribution and a code rollback does not undo that. See the C_v2rollback 10/26 entry
in notes/retrospective_2026-05_to_07.md.

Unlike packaging_report (per-ARM proxy table), this reads the TRUE intake directly from
the YouTube Analytics API at the CHANNEL level — daily channel views (what we're trying
to climb back), plus the traffic-source mix (95.6% Shorts feed = the structural fragility
we're watching) and the per-publish-day per-video reach from video_performance.

Success = daily channel views climbing toward 4 digits, OR one breakout (>1k). Tripwire =
flat at ~200 after ~3 weeks of genuine RF → escalate to the harder-reset fork.

  python -m scripts.intake_report              # print (last 30d)
  python -m scripts.intake_report --days 45    # wider window
  python -m scripts.intake_report --slack      # + post to the board channel
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "agent.db"


def _channel_daily(days: int) -> list[tuple[str, int, int, int]]:
    """(day, views, estMinutes, subsGained) per day from the Analytics API — the real intake."""
    from youtube.oauth import get_analytics
    yt = get_analytics()
    end = dt.date.today()
    start = end - dt.timedelta(days=days - 1)
    r = yt.reports().query(
        ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
        metrics="views,estimatedMinutesWatched,subscribersGained", dimensions="day",
    ).execute()
    return [(row[0], int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
            for row in r.get("rows", [])]


def _traffic_mix(days: int) -> list[tuple[str, int, float]]:
    """(source, views, pct) — Shorts-feed dependence is the fragility we watch."""
    from youtube.oauth import get_analytics
    yt = get_analytics()
    end = dt.date.today()
    start = end - dt.timedelta(days=days - 1)
    r = yt.reports().query(
        ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
        metrics="views", dimensions="insightTrafficSourceType", sort="-views",
    ).execute()
    rows = r.get("rows", [])
    tot = sum(int(x[1] or 0) for x in rows) or 1
    return [(row[0], int(row[1] or 0), 100.0 * int(row[1] or 0) / tot) for row in rows]


def _pervideo_daily(days: int) -> list[tuple[str, int, int, int]]:
    """(publish_day, n_videos, avg_48h, max_48h) from video_performance — per-video reach."""
    con = sqlite3.connect(str(DB)); con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT substr(publish_at,1,10) day, count(*) n,
                  cast(avg(views_48h) as int) avg_v, max(views_48h) max_v
           FROM video_performance
           WHERE publish_at >= date('now', ?) AND views_48h IS NOT NULL
           GROUP BY day ORDER BY day""", (f"-{days} day",)).fetchall()
    return [(r["day"], r["n"], r["avg_v"], r["max_v"]) for r in rows]


def report(days: int = 30) -> str:
    parts = [f":chart_with_downwards_trend: *채널 인입 회복 트래커* (최근 {days}일 · 진짜 인입="
             "채널 일일 조회, API 직접). 목표=4자리 등반 or 브레이크아웃>1k · 트립와이어=~3주 후 ~200 평탄"]

    try:
        daily = _channel_daily(days)
        peak = max((v for _, v, _, _ in daily), default=0)
        last = daily[-1][1] if daily else 0
        lines = [f"*채널 일일 조회* (peak {peak} · 최신 {last})"]
        for day, v, mins, subs in daily:
            bar = "█" * min(40, round(v / max(peak, 1) * 40))
            lines.append(f"  {day}  {v:>5}  {bar}  subs+{subs}")
        parts.append("\n".join(lines))
    except Exception as e:  # noqa: BLE001
        parts.append(f"*채널 일일 조회* — API 조회 실패: {e}")

    try:
        mix = _traffic_mix(days)
        lines = ["*트래픽 소스* (Shorts 의존도 = 구조 취약성)"]
        for src, v, pct in mix[:6]:
            lines.append(f"  {src:28} {v:>6}  {pct:4.1f}%")
        parts.append("\n".join(lines))
    except Exception as e:  # noqa: BLE001
        parts.append(f"*트래픽 소스* — API 조회 실패: {e}")

    try:
        pv = _pervideo_daily(days)
        lines = ["*발행일별 per-video 48h 조회* (video_performance)"]
        for day, n, avg_v, max_v in pv:
            lines.append(f"  {day}  n={n}  avg={avg_v:>5}  max={max_v:>5}")
        parts.append("\n".join(lines))
    except Exception as e:  # noqa: BLE001
        parts.append(f"*per-video* — 조회 실패: {e}")

    return "\n\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="channel intake recovery tracker")
    ap.add_argument("--days", type=int, default=30, help="window size (default 30)")
    ap.add_argument("--slack", action="store_true", help="post to the board channel")
    args = ap.parse_args()
    text = report(args.days)
    print(text)
    if args.slack:
        try:
            from agents import notify
            notify.board(text) if hasattr(notify, "board") else notify.warn(text)
        except Exception as e:  # noqa: BLE001
            print(f"(slack post failed: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
