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

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

log = logging.getLogger("agents.launch")
KST = ZoneInfo("Asia/Seoul")

# 4 daily timeslots (KST). PD-confirmed 2026-06-07.
TIMESLOTS: list[str] = os.getenv(
    "LAUNCH_TIMESLOTS", "08:00,12:30,18:00,21:00"
).split(",")

# The two lanes under A/B test.
LANES = ("ai_vtuber", "real_footage")


# ── Per-video Slack thread ↔ video mapping (PD 2026-06-09) ────────────────
# Each launch video gets its OWN Slack thread. `/veto` inside a thread → veto
# that thread's video; `/veto <video_id>` from the main channel works too. We
# persist the mapping so the (separate-process) slack listener can resolve it.
def _ensure_launch_threads_table(con) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS launch_threads ("
        " thread_ts TEXT PRIMARY KEY, channel TEXT, video_id TEXT, lane TEXT,"
        " slot TEXT, target TEXT, title TEXT, vetoed INTEGER DEFAULT 0)")
    con.commit()


def record_launch_thread(con, *, thread_ts, channel, video_id, lane, slot,
                         target, title) -> None:
    _ensure_launch_threads_table(con)
    con.execute(
        "INSERT INTO launch_threads (thread_ts, channel, video_id, lane, slot, "
        "target, title, vetoed) VALUES (?,?,?,?,?,?,?,0) "
        "ON CONFLICT(thread_ts) DO UPDATE SET video_id=excluded.video_id, "
        "title=excluded.title",
        (thread_ts, channel, video_id, lane, slot, str(target), title))
    con.commit()


def video_id_for_thread(con, thread_ts: str) -> str | None:
    try:
        _ensure_launch_threads_table(con)
        row = con.execute(
            "SELECT video_id FROM launch_threads WHERE thread_ts=?",
            (thread_ts,)).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


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
                    dry_run: bool = False,
                    max_slots: int | None = None,
                    ask_cb: Callable[[list], dict] | None = None,
                    slack_client=None,
                    slack_channel: str | None = None,
                    lane_filter: str | None = None) -> list[dict]:
    """Produce the day's 4 launch episodes per the Latin-square assignment.

    Returns a list of slot result dicts: {lane, slot, output, video_id,
    publish_at}. Giri gating lives inside produce_and_render's per-lane retry
    loops — a slot that fails to render is left empty (no junk upload).
    """
    from agents.producer import (_db, _gather_context, produce_and_render,
                                  _auto_upload_episode)
    con = _db()
    assignments = day_assignments(target)
    if lane_filter:  # PD 2026-06-09: re-render only one lane's slots (e.g. AV redo)
        assignments = [(ln, hh) for ln, hh in assignments if ln == lane_filter]
    if max_slots is not None:
        assignments = assignments[:max_slots]  # shakedown: render a subset

    if progress_cb:
        plan = "  ".join(f"{hh}:{'AV' if ln=='ai_vtuber' else 'RF'}"
                         for ln, hh in assignments)
        progress_cb(f":rocket: 런칭 4슬롯 — {target.isoformat()}\n  {plan}")

    context = _gather_context(con, target)
    # WAL so parallel slots (each its own connection) don't lock each other.
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    from agents.producer import propose_concepts, resolve_knowledge_questions

    concurrency = max(1, int(os.getenv("LAUNCH_CONCURRENCY", "3")))
    sequential = concurrency == 1 or len(assignments) <= 1
    # In parallel mode the knowledge Q&A is non-blocking — concurrent threads can't
    # each block on a PD reply. (Cron is non-blocking anyway; questions still post
    # + persist for /answer. For week-1 blocking, run /launch with LAUNCH_CONCURRENCY=1.)
    slot_ask = ask_cb if sequential else None

    def _slot_pipeline(lane: str, hhmm: str) -> dict | None:
        # PD 2026-06-08: each slot proposes ITS OWN concept and renders it in one
        # thread, so the slow av Writer/Director proposals overlap each other AND
        # the rf renders (was: propose-all-then-render, serial → ~45min for 4).
        # PD 2026-06-09: each slot also gets its OWN Slack thread — all of this
        # slot's progress + its result mp4 land there, and `/veto` in that thread
        # vetoes THIS video.
        lane_lbl = "AV" if lane == "ai_vtuber" else "RF"
        slot_ts = None
        if slack_client and slack_channel:
            try:
                r = slack_client.chat_postMessage(
                    channel=slack_channel,
                    text=(f":clapper: *{hhmm} {lane_lbl}* — {target.isoformat()} "
                          f"제작 시작 (이 쓰레드에 진행상황·결과영상. 취소하려면 "
                          f"이 쓰레드에 `veto` 라고 답글)"))
                slot_ts = r.get("ts")
            except Exception as e:
                log.warning("slot thread open failed (%s %s): %s", hhmm, lane, e)

        def sp(m: str):
            print(m, flush=True)
            if slack_client and slack_channel and slot_ts:
                try:
                    slack_client.chat_postMessage(channel=slack_channel, text=m,
                                                  thread_ts=slot_ts)
                except Exception:
                    pass
            elif progress_cb:
                progress_cb(m)

        def sv(p):
            if slack_client and slack_channel and slot_ts:
                try:
                    slack_client.files_upload_v2(
                        channel=slack_channel, thread_ts=slot_ts, file=str(p),
                        title=Path(p).name,
                        initial_comment=":movie_camera: 결과 — 취소하려면 이 쓰레드에 `veto` 라고 답글")
                    return
                except Exception as e:
                    log.warning("slot video upload failed: %s", e)
            if video_cb:
                video_cb(p)

        sp(f":bulb: {hhmm} {lane_lbl} 컨셉 생성 중...")
        try:
            batch = propose_concepts(target, dict(context),
                                     style_filter=lane, progress_cb=sp)
        except Exception as e:
            log.warning("propose %s failed: %s", lane, e)
            sp(f":x: {hhmm} {lane_lbl} 컨셉 실패: {str(e)[:140]}")
            return None
        if not batch:
            sp(f":warning: {hhmm} {lane_lbl}: 컨셉 없음 — 슬롯 비움")
            return None
        concept = batch[0]
        concept["render_style"] = lane
        try:
            concept = (resolve_knowledge_questions(
                [concept], target, ask_cb=slot_ask, progress_cb=sp)
                or [concept])[0]
        except Exception as e:
            log.warning("knowledge Q resolve failed (%s): %s", lane, e)
        if dry_run:
            return {"lane": lane, "slot": hhmm, "title": concept.get("title"),
                    "publish_at": publish_at_for(target, hhmm), "thread_ts": slot_ts}
        # render (each thread its own db connection — sqlite isn't shareable)
        sp(f":factory: {hhmm} {lane_lbl} 렌더: {concept.get('title','?')}")
        try:
            outs = produce_and_render([concept], target, progress_cb=sp)
        except Exception as e:
            log.exception("launch render failed (%s %s): %s", hhmm, lane, e)
            sp(f":x: {hhmm} {lane_lbl} 렌더 실패: {str(e)[:140]}")
            return None
        out = outs[0] if outs else None
        if not out:
            sp(f":x: {hhmm} {lane_lbl}: 기리 미통과/렌더 실패 — 슬롯 비움(junk 금지)")
            return None
        sv(out)
        vid = None
        publish_at = publish_at_for(target, hhmm)
        if do_upload and os.getenv("YOUTUBE_AUTO_UPLOAD", "1") == "1":
            try:
                tcon = _db()
                vid = _auto_upload_episode(tcon, out, target, sp,
                                           publish_at_iso=publish_at)
                # PD 2026-06-09: map this slot's thread → video_id so /veto works.
                if vid and slot_ts and slack_channel:
                    try:
                        record_launch_thread(
                            tcon, thread_ts=slot_ts, channel=slack_channel,
                            video_id=vid, lane=lane, slot=hhmm, target=target,
                            title=concept.get("title", ""))
                    except Exception as e:
                        log.warning("record_launch_thread failed: %s", e)
                tcon.close()
            except Exception as e:
                log.warning("launch upload failed (%s %s): %s", hhmm, lane, e)
        if vid:
            sp(f":white_check_mark: {hhmm} {lane_lbl} 예약완료 — video_id=`{vid}` "
               f"공개예정 {publish_at}. 취소: 이 쓰레드에 `veto` 답글 (또는 메인에서 "
               f"`/veto {vid}`)")
        return {"lane": lane, "slot": hhmm, "output": str(out),
                "video_id": vid, "publish_at": publish_at, "thread_ts": slot_ts}

    results: list[dict] = []
    # PD 2026-06-08: run ONE pipeline PER LANE (rf ∥ av) in parallel, but slots
    # WITHIN a lane sequentially. So at most 2 run at once (1 rf + 1 av) — they use
    # different engines (rf=ffmpeg+Gemini, av=Seedance) so they overlap well, and
    # the smaller concurrency avoids the DNS-query burst that flaky resolvers throttle.
    by_lane: dict[str, list[str]] = {}
    for lane, hhmm in assignments:
        by_lane.setdefault(lane, []).append(hhmm)

    def _run_lane(lane: str, slots: list[str]) -> list[dict]:
        out = []
        for hhmm in slots:           # sequential within the lane
            r = _slot_pipeline(lane, hhmm)
            if r:
                out.append(r)
        return out

    if sequential or len(by_lane) <= 1:
        for lane, slots in by_lane.items():
            results.extend(_run_lane(lane, slots))
    else:
        from concurrent.futures import ThreadPoolExecutor
        if progress_cb:
            progress_cb(f":fast_forward: {len(by_lane)}개 레인 파이프라인 병렬 "
                        f"(rf ∥ av, 레인 내부는 순차)")
        with ThreadPoolExecutor(max_workers=len(by_lane)) as ex:
            for lane_results in ex.map(lambda kv: _run_lane(*kv), list(by_lane.items())):
                results.extend(lane_results)

    if dry_run and progress_cb:
        for r in results:
            progress_cb(f"  [dry] {r['slot']} {r['lane']}: {r.get('title')}")

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
    p.add_argument("--max-slots", type=int, default=None,
                   help="shakedown: only run the first N slots (e.g. 1)")
    p.add_argument("--lane", choices=["ai_vtuber", "real_footage"], default=None,
                   help="re-render only this lane's slots (e.g. AV redo)")
    args = p.parse_args()
    # default to TOMORROW (consistent with /daily/test; PD 2026-06-07)
    target = (dt.date.fromisoformat(args.date) if args.date
              else (dt.datetime.now(KST) + dt.timedelta(days=1)).date())
    if args.dry_run:
        for lane, hhmm in day_assignments(target):
            print(f"  {hhmm}  {lane}  → publish_at {publish_at_for(target, hhmm)}")
        return 0

    # Slack wiring (PD 2026-06-09): post a day-level header to the workroom
    # channel, then let each slot open its OWN thread (4 threads/day) for its
    # progress + result mp4 + `/veto`. Day-level progress_cb posts to the channel
    # (un-threaded summary); per-slot detail lives in each slot's thread. Falls
    # back to stdout-only when Slack isn't available.
    progress_cb = lambda m: print(m, flush=True)
    client = ch = None
    try:
        ch = os.environ.get("SLACK_WORKROOM_CHANNEL")
        tok = os.environ.get("SLACK_BOT_TOKEN")
        if ch and tok:
            from slack_sdk import WebClient
            client = WebClient(token=tok)
            client.chat_postMessage(
                channel=ch,
                text=(f":rocket: *런칭 데이* {target.isoformat()} — 4슬롯 생산 시작 "
                      f"(슬롯별 쓰레드에서 진행상황·결과영상 확인 + `/veto`)"))

            def progress_cb(m, _c=client, _ch=ch):  # noqa — day-level summary only
                print(m, flush=True)
                try:
                    _c.chat_postMessage(channel=_ch, text=m)
                except Exception:
                    pass
    except Exception as e:
        log.warning("slack wiring failed (stdout only): %s", e)

    launch_pipeline(target, progress_cb=progress_cb, video_cb=None,
                    do_upload=not args.no_upload, max_slots=args.max_slots,
                    slack_client=client, slack_channel=ch, lane_filter=args.lane)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
