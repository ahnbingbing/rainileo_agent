"""slot_ab_report — settle the 6-slot vs 5-slot question with CHANNEL-level data.

The weak-slot down-weight (agents/launch_v2.day_plan, V2_SLOT_MODE=ab) alternates 6-slot (arm0)
and 5-slot (arm1) by 2-day block, phase-aligned to the produce/carry cycle so each arm sees both
day types. The open question is whether cutting a slot ADDS channel reach (Shorts feed looks
throttled to ~3 videos/day → extra slots cannibalize) or just drops a breakout lottery ticket.

Per-VIDEO 48h views can't answer it (fewer videos trivially means higher per-video) — the decision
metric is CHANNEL DAILY TOTAL reach. This groups channel daily totals (YouTube Analytics, day
dimension) by A/B arm and reports avg/median, plus the actual published count per day so arm
contamination (an arm1 day where no clear laggard existed → effectively 6 slots) is visible.

    .venv/bin/python -m scripts.slot_ab_report            # last 16 days
    .venv/bin/python -m scripts.slot_ab_report --days 21
"""
import argparse, os, sqlite3, statistics as st, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)


def _arm(d: dt.date) -> int:
    """A/B arm for date d — mirrors launch_v2.day_plan's block parity (deterministic, data-free)."""
    anchor = int(os.getenv("V2_CYCLE_ANCHOR_ORDINAL", str(dt.date(2026, 1, 1).toordinal())))
    return ((d.toordinal() - anchor) // 2) % 2        # 0 = full 6-slot, 1 = 5-slot (laggard dropped)


def _produce(d: dt.date) -> bool:
    anchor = int(os.getenv("V2_CYCLE_ANCHOR_ORDINAL", str(dt.date(2026, 1, 1).toordinal())))
    return (d.toordinal() - anchor) % 2 == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=16)
    args = ap.parse_args()

    from youtube import analytics as A
    ya = A._analytics()
    today = dt.date.today()
    start = today - dt.timedelta(days=args.days)
    r = ya.reports().query(ids="channel==MINE", startDate=start.isoformat(), endDate=today.isoformat(),
                           metrics="views,subscribersGained", dimensions="day", sort="day").execute()
    daily = {row[0]: (int(row[1]), int(row[2])) for row in r.get("rows", [])}

    con = sqlite3.connect(str(ROOT / "data" / "agent.db"))
    pub_count: dict[str, int] = {}
    for d10, n in con.execute(
            "SELECT substr(publish_at,1,10), COUNT(*) FROM video_performance "
            "WHERE publish_at >= ? GROUP BY 1", (start.isoformat(),)):
        pub_count[d10] = n

    print(f"{'date':<12} {'arm':<16} {'pub':>3} {'ch_views':>9} {'subs':>5}")
    arms: dict[int, list[int]] = {0: [], 1: []}
    for k in sorted(daily):
        d = dt.date.fromisoformat(k)
        arm = _arm(d)
        v, sg = daily[k]
        pc = pub_count.get(k, 0)
        label = f"arm{arm} {'6slot' if arm == 0 else '5slot'}/{'P' if _produce(d) else 'C'}"
        print(f"  {k:<10} {label:<16} {pc:>3} {v:>9} {sg:>5}")
        arms[arm].append(v)

    print("\n=== ARM SUMMARY (channel daily total reach) ===")
    for arm in (0, 1):
        vs = arms[arm]
        if not vs:
            print(f"  arm{arm} ({'6slot' if arm==0 else '5slot'}): no days yet"); continue
        print(f"  arm{arm} ({'6slot' if arm==0 else '5slot'}): n={len(vs)} "
              f"avg={sum(vs)/len(vs):.0f} median={st.median(vs):.0f}")
    if arms[0] and arms[1]:
        a0, a1 = sum(arms[0]) / len(arms[0]), sum(arms[1]) / len(arms[1])
        verdict = ("5-slot WINS (cutting a slot RAISED channel reach → concentrate)" if a1 > a0 * 1.05
                   else "6-slot WINS (more videos ADD reach → keep volume)" if a0 > a1 * 1.05
                   else "TIE within 5% (reach ~conserved → keep volume for breakout tickets)")
        print(f"\n  Δ 5slot−6slot = {a1 - a0:+.0f}/day ({(a1/a0 - 1)*100:+.0f}%) → {verdict}")
        print("  (need ~2 weeks & balanced produce/carry per arm before trusting this)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
