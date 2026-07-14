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


# Batch-summary review (PD 2026-07-04): the 4 result mp4s are consolidated into ONE
# summary thread, so a thread↔video map (above, 1:1) can't disambiguate which video a
# `veto` reply means. This map is (thread, label)→video, where label is the friendly
# schedule name (260705_RF2100); an in-thread `veto <label>` resolves THAT video.
def _ensure_batch_videos_table(con) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS launch_batch_videos ("
        " thread_ts TEXT, fname TEXT, channel TEXT, video_id TEXT, lane TEXT,"
        " slot TEXT, target TEXT, publish_at TEXT, vetoed INTEGER DEFAULT 0,"
        " PRIMARY KEY(thread_ts, fname))")
    con.commit()


def record_batch_video(con, *, thread_ts, fname, channel, video_id, lane, slot,
                       target, publish_at) -> None:
    _ensure_batch_videos_table(con)
    con.execute(
        "INSERT INTO launch_batch_videos (thread_ts, fname, channel, video_id, lane,"
        " slot, target, publish_at, vetoed) VALUES (?,?,?,?,?,?,?,?,0) "
        "ON CONFLICT(thread_ts, fname) DO UPDATE SET video_id=excluded.video_id",
        (thread_ts, fname, channel, video_id, lane, slot, str(target), publish_at))
    con.commit()


def batch_videos_for_thread(con, thread_ts: str) -> list[dict]:
    """All (label, video_id) pairs uploaded under a batch-summary thread."""
    try:
        _ensure_batch_videos_table(con)
        rows = con.execute(
            "SELECT fname, video_id, slot, lane FROM launch_batch_videos "
            "WHERE thread_ts=? AND video_id IS NOT NULL", (thread_ts,)).fetchall()
        return [{"fname": r[0], "video_id": r[1], "slot": r[2], "lane": r[3]} for r in rows]
    except Exception:
        return []


def video_id_for_fname(con, fname: str) -> str | None:
    """The most recent scheduled video for a schedule name (260705_RF2100). Used to
    unlist the currently-scheduled video when a slot is re-rendered (replace, not add)."""
    try:
        _ensure_batch_videos_table(con)
        row = con.execute(
            "SELECT video_id FROM launch_batch_videos WHERE fname=? AND video_id IS NOT NULL "
            "ORDER BY rowid DESC LIMIT 1", (fname,)).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def resolve_batch_veto(con, thread_ts: str, text: str) -> tuple[str | None, list[dict]]:
    """Resolve which video a `veto <label>` reply in a batch-summary thread targets.
    Returns (video_id, all_videos). video_id is None if the label is missing/ambiguous
    (caller lists `all_videos` so PD can pick). Matches the friendly name (260705_RF2100),
    the lane+slot token (RF2100 / rf 2100 / 2100), or a bare video_id."""
    vids = batch_videos_for_thread(con, thread_ts)
    if not vids:
        return None, []
    t = (text or "").lower().replace(":", "")
    # bare video_id typed directly
    for v in vids:
        if v["video_id"] and v["video_id"].lower() in t:
            return v["video_id"], vids
    for v in vids:
        fn = v["fname"].lower()
        lane_lbl = ("av" if v["lane"] == "ai_vtuber" else "rf")
        slot = v["slot"].replace(":", "")
        # full name, "rf2100", or lane + slot appearing separately
        if fn in t or f"{lane_lbl}{slot}" in t or (lane_lbl in t and slot in t):
            return v["video_id"], vids
    # single video in the thread → unambiguous even without a label
    if len(vids) == 1:
        return vids[0]["video_id"], vids
    return None, vids


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
    # Bandit loop-closure (Channel Manager Phase 2): the Latin square is the EXPLORATION
    # backbone (balanced 2av+2rf → clean marginals). Once a lane has clearly WON
    # (bandit.stabilized: P(best)≥θ & enough n), tilt the mix 2-2 → 3-1 toward it, but
    # KEEP one slot of the other lane so exploration never fully stops (drift detection).
    # Until a lane stabilizes (sparse launch data) this is a NO-OP — the balanced square
    # stands, so closing the loop changes nothing until the data earns it. Disable: BANDIT_STEER=0.
    if os.getenv("BANDIT_STEER", "1") == "1":
        try:
            from agents import bandit
            win = bandit.stabilized("lane")
            if win in LANES:
                lose = "real_footage" if win == "ai_vtuber" else "ai_vtuber"
                lose_slots = [i for i, (ln, _h) in enumerate(out) if ln == lose]
                for i in lose_slots[1:]:          # convert all but one loser slot → winner
                    out[i] = (win, out[i][1])
                if len(lose_slots) > 1:
                    log.info("bandit steer: lane '%s' stabilized → tilt to %s",
                             win, [f"{h}:{l}" for l, h in out])
        except Exception as e:
            log.warning("bandit steer skipped (Latin square stands): %s", e)
    # Pause a lane's auto-fill WITHOUT unloading the whole batch: LAUNCH_PAUSE_LANES is a
    # comma-sep list of lanes to SKIP (e.g. "ai_vtuber" while the AV still-gen — which
    # collapsed every cut of a multi-space concept into one identical two-shot — is being
    # fixed). Paused slots are simply left empty: no junk, no Seedance spend.
    paused = {s.strip() for s in os.getenv("LAUNCH_PAUSE_LANES", "").split(",") if s.strip()}
    if paused:
        out = [(lane, hhmm) for lane, hhmm in out if lane not in paused]
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


def _pinned_episode_for(target: dt.date, lane: str, hhmm: str) -> dict | None:
    """PD 2026-06-12: a pre-rendered episode PINNED to this (date, lane, slot) —
    e.g. PD promoted a great test render to a future slot via
    `scripts/pin_episode.py`. A pin is a `cards` row with state='rendered',
    matching date + render_style(lane), an on-disk output_video_path, NOT yet
    uploaded, whose youtube_publish_at equals this slot's publish time. When one
    exists, launch SKIPS propose+render and just schedules that file. Returns
    {"output": Path, "title": str} or None."""
    try:
        from agents.producer import _db
        con = _db()
        pa = publish_at_for(target, hhmm)
        row = con.execute(
            "SELECT output_video_path, theme FROM cards WHERE date=? "
            "AND render_style=? AND youtube_publish_at=? AND uploaded=0 "
            "AND state='rendered' AND output_video_path IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT 1",
            (target.isoformat(), lane, pa)).fetchone()
        con.close()
        if row and row[0] and Path(row[0]).exists():
            return {"output": Path(row[0]), "title": row[1] or ""}
    except Exception as e:
        log.warning("pin lookup failed (%s %s %s): %s", target, lane, hhmm, e)
    return None


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
                    slot_log_cb: Callable[[str], None] | None = None,
                    video_cb: Callable[[Path], None] | None = None,
                    do_upload: bool = True,
                    dry_run: bool = False,
                    max_slots: int | None = None,
                    ask_cb: Callable[[list], dict] | None = None,
                    slack_client=None,
                    slack_channel: str | None = None,
                    lane_filter: str | None = None,
                    slot_filter: str | None = None,
                    exclude_asset_ids: list | None = None,
                    consolidate_videos: bool = False) -> list[dict]:
    """Produce the day's 4 launch episodes per the Latin-square assignment.

    Returns a list of slot result dicts: {lane, slot, output, video_id,
    publish_at}. Giri gating lives inside produce_and_render's per-lane retry
    loops — a slot that fails to render is left empty (no junk upload).
    """
    from agents.producer import (_db, _gather_context, produce_and_render,
                                  _auto_upload_episode)
    con = _db()
    # PD 2026-06-24: refresh timely hooks (calendar events + live memes/challenges) BEFORE
    # concepts are generated, so the brainstorm can ride what's hot now (World Cup, Halloween,
    # viral pet challenges). Best-effort — the trends table feeds arc + concept_brainstorm.
    if not dry_run and not lane_filter and not slot_filter:  # full daily batch only
        try:
            import scripts.trend_feed as _tf
            res = _tf.refresh(con, target)
            if progress_cb:
                progress_cb(f":satellite: 시의성 훅 갱신 — 캘린더 {res['calendar_active']} / "
                            f"발견 {res['discovered']}")
            # PD 2026-07-01: a frozen trend feed = the AV engine keeps re-using the same memes
            # (Plan ABC 재탕). Live discovery silently returns 0 on a key/network failure, so a
            # stale feed used to be invisible. WARN loudly when nothing fresh landed AND the
            # newest live trend is already days old — so a non-learning feed is caught, not
            # silently riding week-old memes.
            try:
                newest = con.execute(
                    "SELECT MAX(discovered_at) FROM trends WHERE source='discovery'").fetchone()[0]
                stale = bool(newest) and str(newest)[:10] < (target - dt.timedelta(days=2)).isoformat()
                if res["discovered"] == 0 and stale:
                    msg = (f":rotating_light: 시의성 피드 STALE — 새 트렌드 0개, 최신이 {str(newest)[:10]} "
                           "(라이브 학습 실패 의심: GOOGLE_API_KEY/네트워크). AV가 같은 밈 재탕 위험.")
                    log.warning(msg)
                    if progress_cb:
                        progress_cb(msg)
            except Exception:
                pass
        except Exception as _e:
            log.warning("trend_feed refresh failed (non-fatal): %s", _e)
    assignments = day_assignments(target)
    # PD 2026-06-25: GUARANTEE that one of the two daily AVs rides a timely (시의성) hook —
    # World Cup, Halloween, a viral pet challenge — instead of leaving it to chance. The
    # trends were only SUGGESTED to the brainstorm, so a day could ship zero timely AVs.
    # Designate the EARLIEST AV slot of the day as the 시의성 slot (stable identity, computed
    # from the UNFILTERED day plan so a single-slot re-render still knows which slot it is);
    # that slot's concept proposal is forced timely (agents.concept_brainstorm require_timely
    # → every candidate built on a live hook). The other AV stays free-form. RF never forces
    # (footage-bound). Best-effort: if the trends table is dry, it degrades to a normal AV.
    timely_av_slot = next((hh for ln, hh in assignments if ln == "ai_vtuber"), None)
    if lane_filter:  # PD 2026-06-09: re-render only one lane's slots (e.g. AV redo)
        assignments = [(ln, hh) for ln, hh in assignments if ln == lane_filter]
    if slot_filter:  # re-render only one timeslot (e.g. a single failed slot)
        assignments = [(ln, hh) for ln, hh in assignments if hh == slot_filter]
    if max_slots is not None:
        assignments = assignments[:max_slots]  # shakedown: render a subset

    if progress_cb:
        plan = "  ".join(f"{hh}:{'AV' if ln=='ai_vtuber' else 'RF'}"
                         for ln, hh in assignments)
        progress_cb(f":rocket: 런칭 4슬롯 — {target.isoformat()}\n  {plan}")

    # PD 2026-06-30: drain untagged clips BEFORE building the clip pool. Slack-synced
    # grandmompapa clips ingest continuously (every 15 min) but only get VLM-tagged by the
    # 01:30 daily pass — so clips that arrive in the evening are still vlm_analyzed_at IS NULL
    # when this overnight batch selects, and best_videos (which requires VLM analysis) silently
    # skips them → the batch reuses OLD footage and ignores fresh grandmompapa content (PD:
    # "새로운게 많은데 왜 주입이 안된거야"). Tag them now so this batch sees them. Bounded +
    # best-effort; a tagging failure never blocks the batch.
    if os.getenv("LAUNCH_PREBATCH_VLM", "1") != "0":
        try:
            n_untagged = con.execute(
                "SELECT COUNT(*) FROM assets WHERE vlm_analyzed_at IS NULL "
                "AND kind IN ('video','photo')").fetchone()[0]
            if n_untagged:
                if progress_cb:
                    progress_cb(f":mag: 배치 전 VLM 태깅 — 미태깅 {n_untagged}개(신선 클립 확보)…")
                import subprocess as _sp
                import sys as _sys
                _root = str(Path(__file__).resolve().parent.parent)
                _sp.run([_sys.executable, "-m", "scripts.tag_assets_vlm", "--limit", "80"],
                        cwd=_root, env={**os.environ, "PYTHONPATH": _root},
                        timeout=int(os.getenv("LAUNCH_PREBATCH_VLM_TIMEOUT", "1200")))
        except Exception as e:
            log.warning("pre-batch VLM drain skipped: %s", e)

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

    # PD 2026-06-10: shared across this batch's slots — asset_ids already used by
    # an earlier slot, so later slots avoid re-using them (the 6/11 two-RF-identical-
    # clips bug). set.add across threads is GIL-safe for our best-effort diversity.
    # PD 2026-06-11: pre-seed with caller-supplied exclusions so a SEPARATE single-
    # slot run (e.g. RF 18:00 rendered after RF 08:00, when test renders aren't
    # uploaded so the cooldown is inert) still avoids the other slot's clips.
    batch_used_assets: set = set(exclude_asset_ids or [])

    # PD 2026-07-07: clip-level cooldown across BATCHES. batch_used_assets only avoids the
    # OTHER slots in THIS run — so a single-slot re-render (no siblings) had an empty set
    # and could re-pick a clip that's already live in a recently-published episode (the
    # 7/7 root: an RF re-render grabbed a clip from that day's public video). Seed from the
    # clips used by recently-published/scheduled episodes (last 7d) so no render re-uses a
    # currently-live clip. Truly old memory-lane clips are unaffected — only recently-USED
    # ones are blocked; log the count so the constraint is never a silent over-cap.
    try:
        import json as _json
        _seeded = 0
        with _db() as _con:
            for _r in _con.execute(
                "SELECT payload_json FROM cards WHERE date >= ? AND youtube_video_id IS NOT NULL "
                "AND state!='archived' ORDER BY date DESC LIMIT 60",
                ((target - dt.timedelta(days=7)).isoformat(),)).fetchall():
                try:
                    for _cut in (_json.loads(_r[0] or "{}").get("cuts") or []):
                        _aid = _cut.get("asset_id")
                        if _aid and _aid not in batch_used_assets:
                            batch_used_assets.add(_aid); _seeded += 1
                except Exception:
                    continue
        if _seeded:
            log.info("clip cooldown: seeded %d recently-published clips into exclude set", _seeded)
    except Exception as e:
        log.warning("recent-published clip cooldown seed failed: %s", e)

    # PD 2026-06-18: same-batch CONCEPT dedup (not just clip dedup). Two AV slots on
    # 6/19 shipped near-identical concepts (both "꼬리" theme, same set, same wink) —
    # batch_used_assets only blocks reusing the same CLIP, and the macro reviewer judges
    # freshness vs PAST public uploads, not vs the sibling slot. So accumulate each
    # slot's concept descriptor and feed it forward as exclude_concepts. Seed from any
    # sibling ALREADY scheduled/live for this date AND from recently-published episodes
    # (last 14 days) so we avoid not just the day's other concepts but a near-repeat of a
    # past public episode (PD 2026-06-27: an AV 낮잠 shipped that duplicated a prior 낮잠
    # upload — same-date dedup alone never caught it). state!=archived, has a video_id.
    # PD 2026-07-14 ("기존에 쓴 거 재탕하지 말라니까 — 컨셉!"): a 워터밤 concept shipped twice
    # despite the seed. Two reasons the old 14-day/published-only window missed it: (1) an
    # older repeat or a concept that was PRODUCED but not published (rendered/approved) never
    # entered the exclude set, (2) the lexical gate needs a full-history exclude to compare
    # against. So seed from ALL produced concepts (any non-archived card with a theme), most-
    # recent first, capped — semantic dedup (concept_brainstorm) then judges specific-concept
    # repeats while still allowing seasonal THEME revisits.
    batch_concepts: list = []
    try:
        import json as _json
        with _db() as _con:
            for _r in _con.execute(
                "SELECT theme, payload_json FROM cards WHERE state!='archived' "
                "AND theme IS NOT NULL ORDER BY date DESC LIMIT 80").fetchall():
                if _r[0]:
                    _ll = ""
                    try:
                        _p = _json.loads(_r[1] or "{}")
                        _ll = (_p.get("draft", {}) or {}).get("logline") or _p.get("narrative_oneliner") or ""
                    except Exception:
                        pass
                    batch_concepts.append({"theme": _r[0], "logline": _ll})
    except Exception as e:
        log.warning("batch_concepts seed failed: %s", e)

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
                          f"제작 시작 (이 쓰레드에 진행상황·결과영상."
                          + (" 취소하려면 이 쓰레드에 `veto` 라고 답글)" if do_upload
                             else " ⚠️ 검수용 — 자동 공개 안 함, PD 확인 후 예약)")))
                slot_ts = r.get("ts")
            except Exception as e:
                log.warning("slot thread open failed (%s %s): %s", hhmm, lane, e)

        def sp(m: str):
            print(m, flush=True)
            # PURE capture sink (PD 2026-07-05): self-heal classifies a failed slot by
            # regex-scanning its log, but when a Slack slot-thread exists every sp() goes
            # to the thread and NEVER to progress_cb (the elif below) — so self-heal's buf
            # stayed empty and real render/giri failures were misclassified "unknown" →
            # blind retry + no early diagnosis. slot_log_cb is ALWAYS called (no Slack
            # side-effect, no double-post) so the caller can capture the full slot log.
            if slot_log_cb:
                slot_log_cb(m)
            if slack_client and slack_channel and slot_ts:
                try:
                    slack_client.chat_postMessage(channel=slack_channel, text=m,
                                                  thread_ts=slot_ts)
                except Exception:
                    pass
            elif progress_cb:
                progress_cb(m)

        def sv(p):
            # Mirror every produced episode to GCS first (PD 2026-07-04): after the GCP
            # cutover the render runs on the VM, so its disk is the only local copy and Slack
            # sometimes drops the file. `gs://<bucket>/output/episodes/` is the ONE reliable
            # place PD reviews all outputs; include the URI in the Slack note as a fallback.
            _gcs = None
            try:
                from icloud import gcs
                # Communication-friendly name keyed on the upload SCHEDULE (PD 2026-07-04):
                # YYMMDD_<LANE><HHMM> — e.g. 260705_RF2100 for the 7/5 21:00 real_footage slot.
                _fname = f"{target.strftime('%y%m%d')}_{lane_lbl}{hhmm.replace(':', '')}"
                _gcs = gcs.upload_episode(str(p), name=_fname)
            except Exception as e:
                log.debug("episode GCS mirror skipped: %s", e)
            # Consolidated review (PD 2026-07-04): don't scatter the 4 result mp4s across
            # per-slot threads — the caller (self-heal) collects every slot's `output`
            # path and uploads them all into ONE batch-summary thread, where PD reviews
            # the whole day at a glance and vetoes by label. Here we only mirror to GCS
            # and drop a one-line pointer in the slot thread.
            if consolidate_videos:
                if slack_client and slack_channel and slot_ts:
                    try:
                        _p = f":movie_camera: `{_fname}` 완성 — 영상은 오늘 배치 써머리 쓰레드에 모아 올려요."
                        if _gcs:
                            _p += f"\n:cloud: {_gcs}"
                        slack_client.chat_postMessage(channel=slack_channel,
                                                      thread_ts=slot_ts, text=_p)
                    except Exception:
                        pass
                return
            _note = (":movie_camera: 결과 — 취소하려면 이 쓰레드에 `veto` 라고 답글"
                     if do_upload else
                     ":movie_camera: 결과 (검수용 — 자동 공개 안 함, PD 확인 후 예약)")
            if _gcs:
                _note += f"\n:cloud: {_gcs}"
            if slack_client and slack_channel and slot_ts:
                try:
                    slack_client.files_upload_v2(
                        channel=slack_channel, thread_ts=slot_ts, file=str(p),
                        title=Path(p).name, initial_comment=_note)
                    return
                except Exception as e:
                    log.warning("slot video upload failed: %s", e)
            if video_cb:
                video_cb(p)

        # PD 2026-06-10: AV resilience. A Validator BLOCK is a pre-render (LLM-only,
        # cheap) rejection — instead of emptying the slot on one bad concept (av went
        # 0/2 on 6/11: one slot dup-beat-blocked, one photo-failed), RE-PROPOSE a
        # fresh AV concept up to N×. This never spends Seedance (produce_and_render
        # skips blocked concepts). Render-fail retry (×AV_MAX_RETRIES=3) is separate
        # and lives INSIDE produce_and_render. RF keeps 1 propose (it has its own Giri
        # retry loop). Pass batch_used_assets so each slot avoids the others' clips.
        # PD 2026-06-12: a pre-rendered episode pinned to THIS slot? If so, skip
        # propose+render entirely and just publish that file at this slot's time.
        pin = _pinned_episode_for(target, lane, hhmm) if not dry_run else None
        max_repropose = int(os.getenv("AV_BLOCK_REPROPOSE", "5")) if lane == "ai_vtuber" else 1
        concept = None
        outs: list = []
        if pin:
            sp(f":pushpin: {hhmm} {lane_lbl} — 예약된 렌더 사용(재렌더 생략): "
               f"{Path(pin['output']).name}")
            concept = {"title": pin.get("title") or "예약 에피소드", "cuts": []}
            outs = [pin["output"]]
        for _att in range(1, max_repropose + 1):
            if pin:
                break
            suffix = f" (재제안 {_att}/{max_repropose})" if _att > 1 else ""
            sp(f":bulb: {hhmm} {lane_lbl} 컨셉 생성 중...{suffix}")
            ctx = dict(context)
            # The day's designated 시의성 AV slot: force a timely concept (see timely_av_slot
            # above). Exactly one AV/day carries this; the other AV proposes free-form.
            if lane == "ai_vtuber" and hhmm == timely_av_slot:
                ctx["require_timely"] = True
                if _att == 1:
                    sp(f":calendar: {hhmm} {lane_lbl} — 오늘의 '시의성 AV' 슬롯 "
                       "(월드컵/시즌 이벤트/밈 등 시의성 훅 강제)")
            # PD 2026-07-09: asset_id cooldown is an ON-SCREEN clip constraint — real_footage
            # only. For ai_vtuber asset_id is a pose/generation reference (reused legitimately
            # across episodes; the render is a fresh Seedance still), so excluding recently-used
            # refs just starves AV. Same-slot divergence for AV is handled by concept-dedup below.
            if lane == "real_footage" and batch_used_assets:
                ctx["exclude_asset_ids"] = sorted(batch_used_assets)
            if batch_concepts:
                ctx["exclude_concepts"] = list(batch_concepts)
            try:
                batch = propose_concepts(target, ctx, style_filter=lane, progress_cb=sp)
            except Exception as e:
                log.warning("propose %s failed: %s", lane, e)
                sp(f":x: {hhmm} {lane_lbl} 컨셉 실패: {str(e)[:140]}")
                continue
            if not batch:
                sp(f":warning: {hhmm} {lane_lbl}: 컨셉 없음{suffix}")
                continue
            cand = batch[0]
            cand["render_style"] = lane
            try:
                cand = (resolve_knowledge_questions(
                    [cand], target, ask_cb=slot_ask, progress_cb=sp) or [cand])[0]
            except Exception as e:
                log.warning("knowledge Q resolve failed (%s): %s", lane, e)
            concept = cand
            if dry_run:
                return {"lane": lane, "slot": hhmm, "title": concept.get("title"),
                        "publish_at": publish_at_for(target, hhmm), "thread_ts": slot_ts}
            _v = (cand.get("cameraman_validation") or {}).get("verdict", "")
            if _v == "blocked" and _att < max_repropose:
                sp(f":arrows_counterclockwise: {hhmm} {lane_lbl} Validator 블록 "
                   f"({(cand.get('cameraman_validation') or {}).get('summary','')[:60]}) "
                   f"— 재제안 ({_att}/{max_repropose})")
                continue
            # render (each thread its own db connection — sqlite isn't shareable)
            sp(f":factory: {hhmm} {lane_lbl} 렌더: {concept.get('title','?')}")
            # PD 2026-06-11: stamp the batch exclusions onto the concept so the RF
            # Giri-retry's RE-propose (which rebuilds a fresh context inside
            # produce_and_render) still avoids the other slot's clips. Without this
            # the first propose excluded them but a retry re-picked them (재탕).
            if lane == "real_footage" and batch_used_assets:
                concept["_batch_exclude_asset_ids"] = sorted(batch_used_assets)
            # Register this concept so LATER slots (and self-heal rounds) diverge from it
            # — same-batch concept-dedup. theme carries the core motif ("내 꼬리는 어디에").
            _desc = {"title": concept.get("title"),
                     "theme": concept.get("theme") or concept.get("title"),
                     "logline": concept.get("logline") or ""}
            if _desc.get("theme") or _desc.get("title"):
                batch_concepts.append(_desc)
            # PD 2026-06-12: RESERVE this concept's clips NOW (before the slow render)
            # so a later slot's propose already excludes them. Two RF one-takes both
            # grabbed the longest clip for 6/13 because the mark only happened AFTER
            # render. (set.add is GIL-safe; the after-render mark below is a backstop.)
            try:
                for _c in (concept.get("cuts") or []):
                    if _c.get("asset_id"):
                        batch_used_assets.add(_c["asset_id"])
            except Exception:
                pass
            # PD 2026-06-17: TRANSIENT-error retry. A slot used to empty on ANY render
            # exception — incl. transient LLM/provider blips (timeout/504/unavailable)
            # that just need a re-try. Retry ONLY on transient signatures (NOT on real
            # failures — re-rendering those re-spends Seedance, the cost-runaway). Env
            # LAUNCH_TRANSIENT_RETRIES (default 2). SEEDANCE_MAX_CALLS still caps spend.
            import time as _time
            _tr = max(0, int(os.getenv("LAUNCH_TRANSIENT_RETRIES", "2")))
            _TRANSIENT = ("timeout", "timed out", "deadline_exceeded", "temporarily unavailable",
                          "unavailable", "connection", "econnreset", "reset by peer",
                          "rate limit", "429", "502", "503", "504", "overloaded")
            outs = []
            for _r in range(_tr + 1):
                try:
                    outs = produce_and_render([concept], target, progress_cb=sp)
                    break
                except Exception as e:
                    es = str(e)
                    if any(s in es.lower() for s in _TRANSIENT) and _r < _tr:
                        sp(f":repeat: {hhmm} {lane_lbl} 일시 오류 — 재시도 {_r+1}/{_tr}: {es[:100]}")
                        _time.sleep(min(30, 5 * (_r + 1)))
                        continue
                    log.exception("launch render failed (%s %s): %s", hhmm, lane, e)
                    sp(f":x: {hhmm} {lane_lbl} 렌더 실패: {es[:140]}")
                    outs = []
                    break
            break  # rendered (block-reproposes already 'continue'd above)
        if concept is None:
            sp(f":warning: {hhmm} {lane_lbl}: 컨셉 없음 — 슬롯 비움")
            return None
        out = outs[0] if outs else None
        if not out:
            sp(f":x: {hhmm} {lane_lbl}: 기리 미통과/렌더 실패 — 슬롯 비움(junk 금지)")
            return None
        # PD 2026-06-10: record this episode's clips so later slots in THIS batch
        # avoid re-using them (the 6/11 two-RF-identical-clips bug).
        try:
            for _c in (concept.get("cuts") or []):
                if _c.get("asset_id"):
                    batch_used_assets.add(_c["asset_id"])
        except Exception:
            pass
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
                "video_id": vid, "publish_at": publish_at, "thread_ts": slot_ts,
                "fname": f"{target.strftime('%y%m%d')}_{lane_lbl}{hhmm.replace(':', '')}"}

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
    p.add_argument("--slot", default=None, help="re-render only this HH:MM timeslot")
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
                    slack_client=client, slack_channel=ch, lane_filter=args.lane,
                    slot_filter=args.slot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
