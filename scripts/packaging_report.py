"""scripts/packaging_report.py — packaging + bumper experiment dashboard.

The gauge PD asked for (2026-10-12). YouTube does NOT expose impressions or
impressionClickThroughRate via the Analytics API (Studio-only), so we can't measure
feed-push or hook-stop CTR directly. The available breakout proxies — collected into
video_performance by agents.bandit.collect — are:

  reach        = mean 48h views            (how far the feed pushed it)
  retention%   = averageViewPercentage     (did the hook hold)
  avg_view_dur = seconds watched           (absolute hold)
  eng/1k       = (likes+comments+shares)/views × 1000   (how strongly it landed)
  subs/1k      = subscribersGained/views × 1000         (conversion)

Grouped by packaging_arm (hook_search / hook_strong / search_strong) and bumper_arm
(intro_outro / outro_only) so each live experiment can be read off one table.

  python -m scripts.packaging_report            # print
  python -m scripts.packaging_report --slack     # + post to the board channel
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "agent.db"


def _agg(con: sqlite3.Connection, dim: str) -> list[dict]:
    """Per-arm aggregate of the proxy metrics for one experiment dimension."""
    rows = con.execute(
        f"""SELECT {dim} AS arm, count(*) n,
                   avg(views_48h) reach,
                   avg(retention_pct) ret,
                   avg(avg_view_dur) dur,
                   sum(likes+comments+shares)*1000.0/nullif(sum(views_48h),0) eng_k,
                   sum(subs_gained)*1000.0/nullif(sum(views_48h),0) subs_k
            FROM video_performance
            WHERE views_48h IS NOT NULL AND {dim} IS NOT NULL
            GROUP BY {dim} ORDER BY reach DESC""").fetchall()
    return [dict(r) for r in rows]


def _fmt(title: str, rows: list[dict]) -> str:
    if not rows:
        return f"*{title}*\n  (데이터 없음)"
    out = [f"*{title}*",
           "  arm             n   reach  ret%  dur(s)  eng/1k  subs/1k"]
    for r in rows:
        out.append(f"  {str(r['arm'])[:14]:14} {r['n']:3} {(r['reach'] or 0):6.0f} "
                   f"{(r['ret'] or 0):5.0f} {(r['dur'] or 0):6.1f} "
                   f"{(r['eng_k'] or 0):7.1f} {(r['subs_k'] or 0):7.2f}")
    return "\n".join(out)


def report() -> str:
    con = sqlite3.connect(str(DB)); con.row_factory = sqlite3.Row
    parts = [
        ":bar_chart: *패키징/범퍼 실험 대시보드* (impressions/CTR은 API 미제공 — reach·retention·"
        "engagement가 가용 프록시)",
        _fmt("패키징 arm (도달 우선: hook_search 핀 중)", _agg(con, "packaging_arm")),
        _fmt("범퍼 A/B (outro_only = 인트로 제거)", _agg(con, "bumper_arm")),
    ]
    return "\n\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="packaging + bumper experiment dashboard")
    ap.add_argument("--slack", action="store_true", help="post to the board channel")
    args = ap.parse_args()
    text = report()
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
