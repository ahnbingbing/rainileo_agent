"""agents/launch.py — First-month launch scheduler (PD 2026-06-07).

Explore-heavy launch: 4 videos/day, lane×timeslot Latin square (2 ai_vtuber +
2 real_footage per day, timeslots rotated daily so lane and timeslot are
counterbalanced). Each episode is Giri-gated (the existing per-lane retry
loops), auto-scheduled-public at its assigned timeslot, and posted to Slack so
PD can /veto. No blocking per-episode PD approval — that's not feasible at 4/day.

See notes/first_month_plan.md §2/§2b/§3 and memory launch_month_experiment.

This module owns ONLY the launch cadence. The actual concept proposal + render
+ upload reuse agents.producer; arc reuses agents.arc.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

log = logging.getLogger("agents.launch")
KST = ZoneInfo("Asia/Seoul")

# 4 daily timeslots (KST). PD-confirmed 2026-06-07.
TIMESLOTS: list[str] = os.getenv(
    "LAUNCH_TIMESLOTS", "08:00,12:30,18:00,21:00"
).split(",")

# The two lanes under A/B test.
LANES = ("ai_vtuber", "real_footage")


def day_assignments(target: dt.date) -> list[tuple[str, str]]:
    """Return [(lane, "HH:MM"), ...] for the 4 daily slots, lane×timeslot
    counterbalanced via a 2-day Latin square.

    With 4 slots and 2 lanes we always ship 2 av + 2 rf. We rotate WHICH slots
    each lane occupies by day parity, so over any 2 consecutive days each lane
    appears in each timeslot exactly once → lane and timeslot are uncorrelated
    (clean marginal estimates for both factors).

        even day: av @ slots 0,2   rf @ slots 1,3
        odd  day: rf @ slots 0,2   av @ slots 1,3
    """
    slots = [s.strip() for s in TIMESLOTS if s.strip()]
    parity = target.toordinal() % 2
    out: list[tuple[str, str]] = []
    for idx, hhmm in enumerate(slots):
        # slots 0,2 → lane A ; slots 1,3 → lane B ; A/B swap each day
        first = (idx % 2 == 0)
        if parity == 0:
            lane = "ai_vtuber" if first else "real_footage"
        else:
            lane = "real_footage" if first else "ai_vtuber"
        out.append((lane, hhmm))
    return out


def publish_at_for(target: dt.date, hhmm: str) -> str:
    """ISO-UTC scheduled-public time for `hhmm` KST on `target`. YouTube requires
    publishAt > now; if the slot already passed (or is <1h away), roll it to the
    same slot on the next day."""
    h, m = (int(x) for x in hhmm.split(":"))
    when = dt.datetime.combine(target, dt.time(h, m), tzinfo=KST)
    now = dt.datetime.now(KST)
    while when <= now + dt.timedelta(hours=1):
        when += dt.timedelta(days=1)
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _propose_n_for_lane(target: dt.date, context: dict, lane: str, n: int,
                        progress_cb=None) -> list[dict]:
    """Get `n` distinct concepts for one lane. propose_concepts returns 1-2 per
    call, so loop until we have enough (re-proposing draws fresh ideas)."""
    from agents.producer import propose_concepts
    got: list[dict] = []
    for _ in range(n * 3):  # generous cap; each call yields 1-2
        if len(got) >= n:
            break
        try:
            batch = propose_concepts(target, dict(context), style_filter=lane,
                                     progress_cb=progress_cb)
        except Exception as e:
            log.warning("propose %s failed: %s", lane, e)
            break
        for c in batch or []:
            c["render_style"] = lane
            got.append(c)
            if len(got) >= n:
                break
    return got[:n]


def launch_pipeline(target: dt.date, *,
                    progress_cb: Callable[[str], None] | None = None,
                    video_cb: Callable[[Path], None] | None = None,
                    do_upload: bool = True,
                    dry_run: bool = False) -> list[dict]:
    """Produce the day's 4 launch episodes per the Latin-square assignment.

    Returns a list of slot result dicts: {lane, slot, output, video_id,
    publish_at}. Giri gating lives inside produce_and_render's per-lane retry
    loops — a slot that fails to render is left empty (no junk upload).
    """
    from agents.producer import (_db, _gather_context, produce_and_render,
                                  _auto_upload_episode)
    con = _db()
    assignments = day_assignments(target)

    if progress_cb:
        plan = "  ".join(f"{hh}:{'AV' if ln=='ai_vtuber' else 'RF'}"
                         for ln, hh in assignments)
        progress_cb(f":rocket: 런칭 4슬롯 — {target.isoformat()}\n  {plan}")

    # Group slots by lane so we propose the right COUNT per lane in one go.
    by_lane: dict[str, list[str]] = {}
    for lane, hhmm in assignments:
        by_lane.setdefault(lane, []).append(hhmm)

    context = _gather_context(con, target)
    lane_concepts: dict[str, list[dict]] = {}
    for lane, slots in by_lane.items():
        if progress_cb:
            progress_cb(f":bulb: {lane} {len(slots)}편 컨셉 생성 중...")
        lane_concepts[lane] = _propose_n_for_lane(
            target, context, lane, len(slots), progress_cb)

    if dry_run:
        results = []
        for lane, hhmm in assignments:
            queue = lane_concepts.get(lane, [])
            c = queue.pop(0) if queue else None
            results.append({"lane": lane, "slot": hhmm,
                            "title": (c or {}).get("title"),
                            "publish_at": publish_at_for(target, hhmm)})
        if progress_cb:
            for r in results:
                progress_cb(f"  [dry] {r['slot']} {r['lane']}: {r['title']}")
        return results

    # Render + upload each slot. Re-walk assignments in slot order so uploads
    # are scheduled per timeslot; pop one concept per lane as we go.
    results: list[dict] = []
    for lane, hhmm in assignments:
        queue = lane_concepts.get(lane, [])
        if not queue:
            if progress_cb:
                progress_cb(f":warning: {hhmm} {lane}: 컨셉 없음 — 슬롯 비움")
            continue
        concept = queue.pop(0)
        if progress_cb:
            progress_cb(f":factory: {hhmm} {lane} 생산: {concept.get('title','?')}")
        try:
            outs = produce_and_render([concept], target, progress_cb=progress_cb)
        except Exception as e:
            log.exception("launch render failed (%s %s): %s", hhmm, lane, e)
            if progress_cb:
                progress_cb(f":x: {hhmm} {lane} 렌더 실패: {str(e)[:140]}")
            continue
        out = outs[0] if outs else None
        if not out:
            if progress_cb:
                progress_cb(f":x: {hhmm} {lane}: 기리 미통과/렌더 실패 — 슬롯 비움(junk 금지)")
            continue
        if video_cb:
            try:
                video_cb(out)
            except Exception as e:
                log.warning("video_cb failed: %s", e)
        vid = None
        publish_at = publish_at_for(target, hhmm)
        if do_upload and os.getenv("YOUTUBE_AUTO_UPLOAD", "1") == "1":
            try:
                vid = _auto_upload_episode(con, out, target, progress_cb,
                                           publish_at_iso=publish_at)
            except Exception as e:
                log.warning("launch upload failed (%s %s): %s", hhmm, lane, e)
        results.append({"lane": lane, "slot": hhmm, "output": str(out),
                        "video_id": vid, "publish_at": publish_at})

    if progress_cb:
        ok = sum(1 for r in results if r.get("output"))
        up = sum(1 for r in results if r.get("video_id"))
        progress_cb(f":checkered_flag: 런칭 완료 — 렌더 {ok}/4, 예약업로드 {up}편")
    return results


def main() -> int:
    import argparse
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Launch-month 4-slot scheduler")
    p.add_argument("--date", default=None, help="target date YYYY-MM-DD (default: today KST)")
    p.add_argument("--dry-run", action="store_true", help="show assignments only")
    p.add_argument("--no-upload", action="store_true", help="render but do not upload")
    args = p.parse_args()
    target = (dt.date.fromisoformat(args.date) if args.date
              else dt.datetime.now(KST).date())
    if args.dry_run:
        for lane, hhmm in day_assignments(target):
            print(f"  {hhmm}  {lane}  → publish_at {publish_at_for(target, hhmm)}")
        return 0

    # Slack wiring: when a workroom is configured (e.g. the daily launchd job),
    # open a thread root and route progress + the 4 mp4s there. Falls back to
    # stdout-only when Slack isn't available.
    progress_cb = lambda m: print(m, flush=True)
    video_cb = None
    client = ch = root = None
    try:
        ch = os.environ.get("SLACK_WORKROOM_CHANNEL")
        tok = os.environ.get("SLACK_BOT_TOKEN")
        if ch and tok:
            from slack_sdk import WebClient
            client = WebClient(token=tok)
            r = client.chat_postMessage(
                channel=ch,
                text=f":clapper: *런칭 데이* {target.isoformat()} — 4슬롯 생산 시작")
            root = r.get("ts")

            def progress_cb(m, _c=client, _ch=ch, _root=root):  # noqa
                print(m, flush=True)
                try:
                    _c.chat_postMessage(channel=_ch, text=m, thread_ts=_root)
                except Exception:
                    pass

            def video_cb(p, _c=client, _ch=ch, _root=root):  # noqa
                try:
                    _c.files_upload_v2(
                        channel=_ch, thread_ts=_root, file=str(p),
                        title=Path(p).name,
                        initial_comment=f":movie_camera: {Path(p).name} — 문제 있으면 `/veto`")
                except Exception:
                    pass
    except Exception as e:
        log.warning("slack wiring failed (stdout only): %s", e)

    launch_pipeline(target, progress_cb=progress_cb, video_cb=video_cb,
                    do_upload=not args.no_upload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
