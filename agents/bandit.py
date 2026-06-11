"""agents/bandit.py — av-vs-rf launch A/B: collection + factorial Thompson.

Arms = lane × timeslot (the launch factorial, agents/launch.py). We collect each
uploaded episode's ~48h performance (youtube/analytics.py), compute a population-
normalized reward, and maintain Thompson-Sampling posteriors at three levels:
  • per arm (lane × timeslot)
  • marginal lane   (av vs rf)        ← the 1차 결정
  • marginal timeslot (08/12:30/18/21) ← 최적 시각

reward = 0.6 · sigmoid(z-norm(log views_48h)) + 0.4 · (retention% / 100), in (0,1).
Population-normalized at compute time (not stored) so it self-calibrates as data
arrives. See notes/first_month_plan.md §2/§4.

Decision is MONTH-END (PD 2026-06-07): launch keeps shipping 2 av + 2 rf/day
(explore); the bandit's choose_* drives NEXT month's allocation.

CLI:
  python -m agents.bandit --collect     # pull 48h metrics for eligible uploads
  python -m agents.bandit --report      # posteriors / win probs
  python -m agents.bandit --choose      # Thompson-sample next lane + timeslot
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import os
import sqlite3
from pathlib import Path

log = logging.getLogger("agents.bandit")
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"

# Min age before a video's 48h window is considered settled enough to collect.
MIN_AGE_HOURS = int(os.getenv("BANDIT_MIN_AGE_HOURS", "48"))
# Re-pull a row if it's younger than this many days (numbers still moving).
RESTAT_WITHIN_DAYS = int(os.getenv("BANDIT_RESTAT_DAYS", "7"))
# Monte-Carlo draws for P(best).
_MC = 4000


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS video_performance (
            video_id      TEXT PRIMARY KEY,
            card_id       TEXT,
            lane          TEXT,
            timeslot      TEXT,
            publish_at    TEXT,
            views_48h     INTEGER,
            retention_pct REAL,
            est_minutes   REAL,
            source        TEXT,
            fetched_at    TEXT
        )
        """
    )
    con.commit()


def _timeslot_of(publish_at_iso: str | None) -> str:
    """Map a publishAt (ISO-UTC) to the nearest launch timeslot label (KST)."""
    try:
        from agents.launch import TIMESLOTS, KST
        slots = [s.strip() for s in TIMESLOTS if s.strip()]
        t = dt.datetime.strptime(publish_at_iso[:19], "%Y-%m-%dT%H:%M:%S")
        t = t.replace(tzinfo=dt.timezone.utc).astimezone(KST)
        mins = t.hour * 60 + t.minute
        def slot_min(s):
            h, m = (int(x) for x in s.split(":"))
            return h * 60 + m
        return min(slots, key=lambda s: abs(slot_min(s) - mins))
    except Exception:
        return "?"


def _hours_since(publish_at_iso: str | None) -> float:
    try:
        t = dt.datetime.strptime(publish_at_iso[:19], "%Y-%m-%dT%H:%M:%S")
        t = t.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600.0
    except Exception:
        return 1e9


# ──────────────────────────────────────────────────────────────────────
# Collection
# ──────────────────────────────────────────────────────────────────────
def collect(con: sqlite3.Connection | None = None) -> list[dict]:
    """Pull ~48h metrics for every uploaded episode whose publish window has
    settled (age ≥ MIN_AGE_HOURS) and that is either uncollected or still young
    enough to re-stat. Upserts into video_performance. Returns collected rows."""
    own = con is None
    con = con or _db()
    ensure_table(con)
    # cards upload columns are created lazily on first upload — ensure they exist
    # so collect() can run on a channel that hasn't uploaded yet (returns []).
    try:
        from agents.producer import _ensure_upload_columns
        _ensure_upload_columns(con)
    except Exception as e:
        log.warning("ensure upload columns failed: %s", e)
    from youtube.analytics import video_metrics, window_48h

    rows = con.execute(
        "SELECT card_id, render_style, youtube_video_id, youtube_publish_at "
        "FROM cards WHERE uploaded=1 AND youtube_video_id IS NOT NULL"
    ).fetchall()

    collected: list[dict] = []
    for r in rows:
        vid = r["youtube_video_id"]
        pub = r["youtube_publish_at"]
        age = _hours_since(pub)
        if age < MIN_AGE_HOURS:
            continue
        existing = con.execute(
            "SELECT fetched_at, publish_at FROM video_performance WHERE video_id=?",
            (vid,),
        ).fetchone()
        if existing:
            # skip if already settled (older than the restat window)
            if _hours_since(existing["publish_at"]) > RESTAT_WITHIN_DAYS * 24:
                continue
        s, e = window_48h(pub or "")
        m = video_metrics(vid, s, e)
        lane = (r["render_style"] or "").lower()
        slot = _timeslot_of(pub)
        con.execute(
            """
            INSERT INTO video_performance
              (video_id, card_id, lane, timeslot, publish_at, views_48h,
               retention_pct, est_minutes, source, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(video_id) DO UPDATE SET
              views_48h=excluded.views_48h,
              retention_pct=excluded.retention_pct,
              est_minutes=excluded.est_minutes,
              source=excluded.source,
              fetched_at=excluded.fetched_at
            """,
            (vid, r["card_id"], lane, slot, pub, m["views"],
             m["retention_pct"], m["est_minutes"], m["source"]),
        )
        collected.append({"video_id": vid, "lane": lane, "timeslot": slot,
                          **m})
    con.commit()
    if own:
        con.close()
    return collected


# ──────────────────────────────────────────────────────────────────────
# Reward (population-normalized)
# ──────────────────────────────────────────────────────────────────────
def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _rows_with_reward(con: sqlite3.Connection) -> list[dict]:
    """Load all performance rows, attaching a population-normalized reward."""
    raw = [dict(r) for r in con.execute(
        "SELECT * FROM video_performance").fetchall()]
    if not raw:
        return []
    logv = [math.log1p(max(0, x["views_48h"] or 0)) for x in raw]
    mean = sum(logv) / len(logv)
    var = sum((v - mean) ** 2 for v in logv) / len(logv)
    std = math.sqrt(var) if var > 1e-9 else 1.0
    for x, v in zip(raw, logv):
        z = (v - mean) / std
        views_score = _sigmoid(z)
        ret_score = max(0.0, min(1.0, (x["retention_pct"] or 0.0) / 100.0))
        x["reward"] = round(0.6 * views_score + 0.4 * ret_score, 4)
    return raw


# ──────────────────────────────────────────────────────────────────────
# Thompson posteriors
# ──────────────────────────────────────────────────────────────────────
def _posterior(rewards: list[float]) -> dict:
    """Gaussian posterior over the mean reward of one arm. Weak prior
    N(0.5, 0.25) so a 0-sample arm explores. Returns posterior mean/std + n."""
    n = len(rewards)
    prior_mu, prior_var = 0.5, 0.25
    if n == 0:
        return {"mu": prior_mu, "sd": math.sqrt(prior_var), "n": 0,
                "mean_reward": None}
    sample_mean = sum(rewards) / n
    sample_var = (sum((r - sample_mean) ** 2 for r in rewards) / n
                  if n > 1 else 0.15)
    sample_var = max(sample_var, 0.02)  # floor so tiny-n arms keep exploring
    # Normal-Normal conjugate update (known obs variance ≈ sample_var)
    obs_var = sample_var
    post_var = 1.0 / (1.0 / prior_var + n / obs_var)
    post_mu = post_var * (prior_mu / prior_var + n * sample_mean / obs_var)
    return {"mu": post_mu, "sd": math.sqrt(post_var), "n": n,
            "mean_reward": round(sample_mean, 4)}


def _group(rows: list[dict], key: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for r in rows:
        out.setdefault(r.get(key) or "?", []).append(r["reward"])
    return out


def _gauss():
    # deterministic-ish RNG without Math.random ban concerns (pure python)
    import random
    return random.gauss


def _p_best(posteriors: dict[str, dict]) -> dict[str, float]:
    """Monte-Carlo P(arm has the highest mean) per arm."""
    import random
    keys = list(posteriors.keys())
    if not keys:
        return {}
    wins = {k: 0 for k in keys}
    for _ in range(_MC):
        best, bk = -1e9, None
        for k in keys:
            p = posteriors[k]
            draw = random.gauss(p["mu"], p["sd"])
            if draw > best:
                best, bk = draw, k
        wins[bk] += 1
    return {k: round(wins[k] / _MC, 3) for k in keys}


def analyze(con: sqlite3.Connection | None = None) -> dict:
    own = con is None
    con = con or _db()
    ensure_table(con)
    rows = _rows_with_reward(con)
    out = {"n_total": len(rows), "levels": {}}
    for level, key in (("lane", "lane"), ("timeslot", "timeslot"),
                       ("arm", None)):
        if key is None:
            groups: dict[str, list[float]] = {}
            for r in rows:
                groups.setdefault(f"{r.get('lane')}@{r.get('timeslot')}",
                                  []).append(r["reward"])
        else:
            groups = _group(rows, key)
        post = {k: _posterior(v) for k, v in groups.items()}
        pbest = _p_best(post) if post else {}
        out["levels"][level] = {
            k: {**post[k], "p_best": pbest.get(k, 0.0)} for k in post}
    if own:
        con.close()
    return out


def report(con: sqlite3.Connection | None = None) -> str:
    a = analyze(con)
    lines = [f"*av vs rf 밴딧* — 표본 {a['n_total']}편"]
    for level in ("lane", "timeslot", "arm"):
        lvl = a["levels"].get(level, {})
        if not lvl:
            continue
        lines.append(f"\n— {level} —")
        for k, v in sorted(lvl.items(), key=lambda kv: -(kv[1]['p_best'])):
            mr = v["mean_reward"]
            lines.append(
                f"  {k}: n={v['n']} reward={mr if mr is not None else '—'} "
                f"P(best)={v['p_best']}")
    return "\n".join(lines)


def _card_title(con: sqlite3.Connection, card_id: str) -> str:
    """Human-readable episode title for the digest (cards has no `title` column —
    it lives in payload_json or falls back to theme)."""
    try:
        r = con.execute("SELECT theme, payload_json FROM cards WHERE card_id=?",
                        (card_id,)).fetchone()
    except Exception:
        r = None
    if not r:
        return "(제목 없음)"
    theme = r[0] if not hasattr(r, "keys") else r["theme"]
    payload = r[1] if not hasattr(r, "keys") else r["payload_json"]
    try:
        d = json.loads(payload or "{}")
        t = d.get("title")
        if isinstance(t, dict):
            t = t.get("ko") or t.get("en")
        if t:
            return str(t)[:48]
    except Exception:
        pass
    return (str(theme)[:48] if theme else "(제목 없음)")


def daily_digest(con: sqlite3.Connection | None = None, *, days: int = 4) -> str:
    """PD 2026-06-11: a DAILY per-video metrics digest (not the weekly strategy
    posteriors). Lists every published episode from the last `days` days with its
    48h views + retention, newest first, plus a one-line lane leader. Built to be
    glanceable every morning."""
    own = con is None
    if own:
        con = _db()
    ensure_table(con)
    try:
        rows = con.execute(
            """SELECT video_id, card_id, lane, timeslot, publish_at, views_48h,
                      retention_pct, source, fetched_at
               FROM video_performance
               WHERE publish_at >= datetime('now', ?)
               ORDER BY publish_at DESC""",
            (f"-{int(days)} days",)).fetchall()
    except Exception as e:
        log.warning("daily_digest query failed: %s", e)
        rows = []
    lines = [f":bar_chart: *일일 지표* — 최근 {days}일 공개 영상"]
    if not rows:
        lines.append("\n아직 집계된 공개 영상 지표가 없어요. (YouTube는 공개 후 "
                     "~48시간 뒤부터 조회수·시청유지율이 집계됩니다 — 첫 공개 6/10이라 "
                     "곧 채워집니다.)")
        return "\n".join(lines)
    cur_day = None
    tot_views = 0
    for r in rows:
        get = (lambda k: r[k]) if hasattr(r, "keys") else \
            (lambda k, _r=r, _i=["video_id", "card_id", "lane", "timeslot",
                                 "publish_at", "views_48h", "retention_pct",
                                 "source", "fetched_at"]: _r[_i.index(k)])
        pub = (get("publish_at") or "")[:16].replace("T", " ")
        day = pub[:10]
        if day != cur_day:
            cur_day = day
            lines.append(f"\n*{day}*")
        lane = "AV" if (get("lane") or "").lower() == "ai_vtuber" else "RF"
        slot = get("timeslot") or "--:--"
        views = int(get("views_48h") or 0)
        ret = float(get("retention_pct") or 0.0)
        src = get("source") or ""
        tot_views += views
        title = _card_title(con, get("card_id") or "")
        flag = "" if src not in ("error", "") else " ⚠️집계대기"
        lines.append(f"  {slot} {lane} · 👁 {views:,} · ⏯ {ret:.0f}% · {title}{flag}")
    lines.append(f"\n합계 조회수(최근 {days}일): {tot_views:,}")
    # compact lane leader from the posteriors (skip if not enough data)
    try:
        a = analyze(con)
        lane_lvl = a["levels"].get("lane", {})
        if lane_lvl:
            best = max(lane_lvl.items(), key=lambda kv: kv[1]["p_best"])
            lines.append(f"우세 레인: {best[0]} (P(best)={best[1]['p_best']}, "
                         f"표본 {a['n_total']}편)")
    except Exception:
        pass
    return "\n".join(lines)


def choose_lane(con: sqlite3.Connection | None = None) -> str:
    """Thompson-sample the marginal lane posteriors → the lane to favor next."""
    import random
    a = analyze(con)
    lanes = a["levels"].get("lane", {})
    if not lanes:
        return "ai_vtuber"
    draws = {k: random.gauss(v["mu"], v["sd"]) for k, v in lanes.items()}
    return max(draws, key=draws.get)


def choose_timeslot(con: sqlite3.Connection | None = None) -> str:
    import random
    a = analyze(con)
    slots = a["levels"].get("timeslot", {})
    if not slots:
        from agents.launch import TIMESLOTS
        return TIMESLOTS[0].strip()
    draws = {k: random.gauss(v["mu"], v["sd"]) for k, v in slots.items()}
    return max(draws, key=draws.get)


def main() -> int:
    import argparse
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="av-vs-rf launch bandit")
    ap.add_argument("--collect", action="store_true",
                    help="pull 48h metrics for eligible uploads")
    ap.add_argument("--report", action="store_true", help="print posteriors")
    ap.add_argument("--choose", action="store_true",
                    help="Thompson-sample next lane + timeslot")
    ap.add_argument("--slack", action="store_true",
                    help="post the report to SLACK_WORKROOM_CHANNEL (weekly job)")
    ap.add_argument("--daily", action="store_true",
                    help="DAILY per-video metrics digest (use with --slack to post)")
    args = ap.parse_args()
    did = False
    if args.collect:
        got = collect()
        print(f"collected/updated {len(got)} rows")
        did = True
    # PD 2026-06-11: --daily = per-video digest (every morning). Without --daily,
    # --slack still posts the weekly strategy posteriors (Monday job, unchanged).
    if args.daily:
        digest = daily_digest()
        print(digest)
        did = True
        if args.slack:
            try:
                from slack_sdk import WebClient
                ch = os.environ.get("SLACK_WORKROOM_CHANNEL")
                if ch:
                    WebClient(token=os.environ["SLACK_BOT_TOKEN"]).chat_postMessage(
                        channel=ch, text=digest)
                    print("posted daily digest to slack")
            except Exception as e:
                log.warning("slack daily post failed: %s", e)
        return 0
    rep = None
    if args.report or args.slack or not did:
        rep = report()
        print(rep)
        did = True
    if args.choose:
        print(f"choose lane     → {choose_lane()}")
        print(f"choose timeslot → {choose_timeslot()}")
    if args.slack and rep:
        try:
            from slack_sdk import WebClient
            ch = os.environ.get("SLACK_WORKROOM_CHANNEL")
            if ch:
                WebClient(token=os.environ["SLACK_BOT_TOKEN"]).chat_postMessage(
                    channel=ch, text=":bar_chart: *주간 av-vs-rf 리포트*\n" + rep)
                print("posted to slack")
        except Exception as e:
            log.warning("slack post failed: %s", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
