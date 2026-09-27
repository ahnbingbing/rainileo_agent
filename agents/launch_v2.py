"""Launch v2 — senior-director batch model (PD 2026-09-21). FLAG-GATED, off by default.

Every batch makes 9 RF (3 senior-director sources × 3 edit grammars) + AV, drained over a
2-day cycle so daily volume is 6 with the RF casting amortised (one source → 3 grammars):

  Day 1 (produce day): senior director → 3 sources → 9 RF + 1 AV.
                       schedule 5 RF + 1 AV(20:00); carry the other 4 RF to Day 2.
  Day 2 (carry day):   schedule the 4 carried RF + 2 AV.
                       the 2 AV re-imagine Day-1's popular video(s) as fantasy (시의성).

6 slots (KST), evening-peak weighted (viewers peak 18–21; 12:30 was our weakest → 13:00):
  08:00 / 09:00 / 13:00 / 18:00 / 20:00 / 21:00   (AV lives at 20:00; Day 2 also 08:00)

LAUNCH_MODEL=v2 turns it on; anything else keeps the live 4-slot pipeline untouched.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent

SLOTS_V2: list[str] = os.getenv("LAUNCH_TIMESLOTS_V2",
                                "08:00,09:00,13:00,18:00,20:00,21:00").split(",")
GRAMMARS = ("velocity", "meme", "story")
# Which of the 9 produced RF air on Day 1 (the rest carry to Day 2). 5 today, 4 tomorrow.
DAY1_RF_COUNT = int(os.getenv("V2_DAY1_RF", "5"))
# Day-2 AV feedback: a Day-1 winner is "dominant" when its early views ≥ this × the runner-up.
AV_DOMINANCE_RATIO = float(os.getenv("V2_AV_DOMINANCE", "2.0"))


def enabled() -> bool:
    return os.getenv("LAUNCH_MODEL", "").lower() == "v2"


# ── 2-day cycle ────────────────────────────────────────────────────────────

def is_produce_day(target: dt.date) -> bool:
    """Day 1 (produce 9 RF) vs Day 2 (carry). Even ordinal = produce day; a fixed anchor
    keeps the phase stable across restarts (no Date.now dependence)."""
    anchor = int(os.getenv("V2_CYCLE_ANCHOR_ORDINAL", str(dt.date(2026, 1, 1).toordinal())))
    return (target.toordinal() - anchor) % 2 == 0


def day_plan(target: dt.date) -> dict:
    """Deterministic slot → (lane, role) map for `target`. No LLM/DB — pure structure.

    Day 1: 5 RF (grammar) + 1 AV(20:00).           Day 2: 4 RF (carried) + 2 AV(08:00, 20:00).
    role ∈ {"rf_fresh" (produced today), "rf_carry" (from Day 1), "av", "av_timely"}."""
    produce = is_produce_day(target)
    slots = list(SLOTS_V2)
    plan: dict[str, dict] = {}
    if produce:
        for hh in slots:
            if hh == "20:00":
                plan[hh] = {"lane": "ai_vtuber", "role": "av"}
            else:
                plan[hh] = {"lane": "real_footage", "role": "rf_fresh"}
    else:
        for hh in slots:
            if hh in ("08:00", "20:00"):
                plan[hh] = {"lane": "ai_vtuber", "role": "av_timely"}
            else:
                plan[hh] = {"lane": "real_footage", "role": "rf_carry"}
    return {"target": target.isoformat(), "produce_day": produce, "slots": plan}


# ── source cast → grammar pool ─────────────────────────────────────────────

def _pool_from_cast(cast: list) -> list[dict]:
    """Turn a source bible's cast (asset_ids) into _fresh_pool-shaped rows so the grammar
    Writer casts from EXACTLY this source's footage (keeps the 3 sources' footage distinct)."""
    ids = [c.get("asset_id") for c in (cast or []) if c.get("asset_id")]
    if not ids:
        return []
    db = sqlite3.connect(str(ROOT / "data" / "agent.db"))
    db.row_factory = sqlite3.Row
    q = ("SELECT asset_id, scene_description, activity, subjects_csv, duration_sec, "
         "location_type, quality_score FROM assets WHERE asset_id IN (%s)"
         % ",".join("?" * len(ids)))
    rows = {r["asset_id"]: dict(r) for r in db.execute(q, ids).fetchall()}
    db.close()
    return [rows[i] for i in ids if i in rows]     # preserve the source's cast order


def plan_sources_to_grammar(target: dt.date, progress_cb=None) -> dict:
    """Cheap dry-run of the produce-day core: senior director → 3 sources → each source cast
    into the 3 grammars (cast + copy, NO render). Proves the source→9-video wiring and that
    the 3 sources keep distinct footage. Returns {sources, episodes:[{source,grammar,clips,...}]}."""
    from agents import senior_director
    from agents.edit_grammar_writer import propose_grammar_copy

    def _sp(m):
        if progress_cb:
            progress_cb(m)

    sources = senior_director.propose_sources(target, progress_cb=progress_cb)
    episodes: list[dict] = []
    for s in sources:
        pool = _pool_from_cast(s.get("cast") or [])
        if len(pool) < 4:
            _sp(f":warning: source {s.get('source_id')} 캐스트<4 → 이 소스 스킵")
            continue
        base = propose_grammar_copy("story", pool, slot_hhmm=None)
        shared = base["clips"]
        for g in GRAMMARS:
            copy = (base["copy"] if g == "story"
                    else propose_grammar_copy(g, pool, fixed_clips=shared)["copy"])
            episodes.append({
                "source_id": s.get("source_id"), "origination": s.get("origination"),
                "grammar": g, "title": s.get("title"),
                "clips": list(dict.fromkeys(shared.values())),
                "caption_sample": _first_caption(copy),
            })
        _sp(f":clapper: source {s.get('source_id')} → {len(GRAMMARS)} grammars cast")
    return {"sources": sources, "episodes": episodes}


def _first_caption(copy: dict) -> str:
    caps = copy.get("captions") or []
    if caps:
        return caps[0].get("ko", "") if isinstance(caps[0], dict) else str(caps[0])
    beats = copy.get("beats") or []
    if beats and isinstance(beats[0], dict):
        return beats[0].get("ko") or beats[0].get("text") or ""
    return ""


# ── Day-2 AV timeliness feedback ───────────────────────────────────────────

def day1_winners(target_day2: dt.date) -> dict:
    """Rank Day-1's published videos by early views, decide the Day-2 AV amplification shape.

    Returns {"mode": "dominant"|"spread"|"none", "winners": [row, ...]}:
      dominant → 2 fantasy takes on the single top video (top1 ≥ RATIO × top2).
      spread   → 1 fantasy take each on top-1 and top-2 (views were similar).
      none     → no measurable Day-1 signal → caller falls back to arc-originated AV."""
    day1 = (target_day2 - dt.timedelta(days=1)).isoformat()
    db = sqlite3.connect(str(ROOT / "data" / "agent.db"))
    db.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in db.execute(
            "SELECT video_id, card_id, lane, timeslot, publish_at, views_48h "
            "FROM video_performance WHERE substr(publish_at,1,10)=? "
            "AND views_48h IS NOT NULL ORDER BY views_48h DESC", (day1,)).fetchall()]
    except Exception as e:  # noqa: BLE001
        log.warning("day1_winners: %s", e)
        rows = []
    finally:
        db.close()
    if not rows:
        return {"mode": "none", "winners": []}
    top1 = rows[0]
    top2 = rows[1] if len(rows) > 1 else None
    v1 = top1["views_48h"] or 0
    v2 = (top2["views_48h"] or 0) if top2 else 0
    if not top2 or v1 >= AV_DOMINANCE_RATIO * max(v2, 1):
        return {"mode": "dominant", "winners": [top1]}
    return {"mode": "spread", "winners": [top1, top2]}


# ── production batch (render + schedule + carry) ─────────────────────────────

def _rf_slots(plan: dict, role: str) -> list[str]:
    """Sorted HH:MM of the plan's RF slots with the given role."""
    return sorted(hh for hh, s in plan["slots"].items()
                  if s["lane"] == "real_footage" and s["role"] == role)


def _av_slots(plan: dict) -> list[str]:
    return sorted(hh for hh, s in plan["slots"].items() if s["lane"] == "ai_vtuber")


def _safe_tag(source_id: str, idx: int) -> str:
    """A filesystem-safe, per-source filename tag so 3 sources rendering the same grammar
    don't collide on episode_rf_<grammar>_<ts>_<tag>.mp4."""
    base = "".join(c for c in str(source_id or "") if c.isalnum()) or f"s{idx}"
    return f"src{idx}{base[:8]}"


def _occupied_slots(target: dt.date) -> set:
    """Slots already filled on the LIVE YouTube schedule for `target` (schedule = ground
    truth — never double-book). Best-effort; a lookup failure means 'fill everything'."""
    try:
        from agents.slot_topup import slot_occupancy
        occ = slot_occupancy({target.isoformat()})
        return {slot for (d, slot), _ in occ.items() if d == target.isoformat()}
    except Exception as e:  # noqa: BLE001
        log.warning("v2 occupancy check failed (filling all slots): %s", e)
        return set()


def _render_av_slot(target: dt.date, hhmm: str, *, timely: bool, do_upload: bool,
                    progress_cb=None, slack_client=None, slack_channel=None) -> dict | None:
    """Render one AV slot by reusing the live per-slot pipeline via assignments_override.
    `timely=False` (produce-day AV) suppresses the forced-timely hook; `timely=True`
    (carry-day AV) keeps it. Returns the slot-result dict or None (empty slot)."""
    from agents.launch import launch_pipeline
    prev = os.environ.get("SELFHEAL_DROP_TIMELY")
    if not timely:
        os.environ["SELFHEAL_DROP_TIMELY"] = "1"   # launch_pipeline honors this to skip timely-force
    try:
        res = launch_pipeline(target, progress_cb=progress_cb, do_upload=do_upload,
                              lane_filter="ai_vtuber", slot_filter=hhmm,
                              assignments_override=[("ai_vtuber", hhmm)],
                              slack_client=slack_client, slack_channel=slack_channel,
                              consolidate_videos=True)
    finally:
        if prev is None:
            os.environ.pop("SELFHEAL_DROP_TIMELY", None)
        else:
            os.environ["SELFHEAL_DROP_TIMELY"] = prev
    return res[0] if res else None


def _schedule_rf(target: dt.date, hhmm: str, mp4, do_upload: bool, progress_cb=None) -> dict:
    """Schedule a freshly-rendered grammar RF mp4 into a Day-1 slot (public at that slot).
    The card was persisted by produce_grammar_episodes_shared; _auto_upload_episode looks
    it up by output_video_path. Returns a slot-result dict (video_id None if upload skipped
    /failed → surfaced as an orphan by the caller)."""
    from agents.launch import publish_at_for
    from agents.producer import _db, _auto_upload_episode
    publish_at = publish_at_for(target, hhmm)
    vid = None
    if do_upload and os.getenv("YOUTUBE_AUTO_UPLOAD", "1") == "1":
        try:
            con = _db()
            vid = _auto_upload_episode(con, mp4, target, progress_cb, publish_at_iso=publish_at)
            con.close()
        except Exception as e:  # noqa: BLE001
            log.warning("v2 RF schedule failed (%s %s): %s", target, hhmm, e)
    return {"lane": "real_footage", "slot": hhmm, "output": str(mp4),
            "video_id": vid, "publish_at": publish_at,
            "fname": f"{target.strftime('%y%m%d')}_RF{hhmm.replace(':', '')}"}


def _render_9_rf(target: dt.date, progress_cb=None) -> list[tuple]:
    """Senior director → 3 sources → render each source's cast into the 3 grammars.
    Returns up to 9 (mp4_path, concept) pairs (a source with a thin cast or a failed
    grammar render is skipped — the caller leaves those slots empty / falls back)."""
    from agents import senior_director, grammar_slot

    def _sp(m):
        if progress_cb:
            progress_cb(m)

    sources = senior_director.propose_sources(target, progress_cb=progress_cb)
    if not sources:
        _sp(":warning: senior director가 소스를 못 냈어요 — RF 0편(다음 배치/수동)")
        return []
    episodes: list[tuple] = []
    for idx, s in enumerate(sources[:3]):
        pool = _pool_from_cast(s.get("cast") or [])
        if len(pool) < 4:
            _sp(f":warning: source {s.get('source_id')} 캐스트<4({len(pool)}) → 스킵")
            continue
        tag = _safe_tag(s.get("source_id"), idx)
        try:
            results = grammar_slot.produce_grammar_episodes_shared(
                list(GRAMMARS), target, hhmm_by_grammar={}, progress_cb=progress_cb,
                pool=pool, tag=tag)
        except Exception as e:  # noqa: BLE001
            _sp(f":warning: source {s.get('source_id')} 공유 렌더 실패 → 스킵: {str(e)[:120]}")
            continue
        for g in GRAMMARS:
            if g in results:
                mp4, concept = results[g]
                episodes.append((mp4, concept))
        _sp(f":clapper: source {s.get('source_id')} → {sum(1 for g in GRAMMARS if g in results)}/3 grammar 렌더")
    return episodes


def run_v2_batch(target: dt.date, *, do_upload: bool = True, dry_run: bool = False,
                 progress_cb=None, slack_client=None, slack_channel=None) -> dict:
    """v2 senior-director 6-slot batch. Same return shape as launch_selfheal.run_with_selfheal
    ({done, failed, diagnoses}) so the cron/summary path is unchanged.

    Produce day: render 9 RF (3 sources × 3 grammars); schedule 5 to today's Day-1 RF slots,
      PIN 4 to tomorrow's carry slots (the carry-day batch schedules them), render 1 AV@20:00.
    Carry day: schedule the 4 pinned RF (via launch's _pinned_episode_for) + render 2 timely AV.
    """
    from agents import grammar_slot
    from agents.launch import launch_pipeline, publish_at_for

    def _sp(m):
        if progress_cb:
            progress_cb(m)
        if slack_client and slack_channel:
            try:
                slack_client.chat_postMessage(channel=slack_channel, text=m)
            except Exception:
                pass

    plan = day_plan(target)
    produce = plan["produce_day"]
    occupied = _occupied_slots(target) if (do_upload and not dry_run) else set()
    done: dict = {}
    pinned: list = []
    failed: list = []
    _sp(f":rocket: *런칭 v2* {target.isoformat()} — "
        f"{'생산일(9RF+1AV)' if produce else '이월일(4RF+2AV)'} · "
        f"슬롯 {','.join(SLOTS_V2)}"
        + (f" · 이미 채워진 슬롯 {sorted(occupied)} 건너뜀" if occupied else ""))

    if dry_run:
        # plan-only: prove the structure without rendering/uploading. Grammar cast preview
        # is exercised by plan_sources_to_grammar / the CLI --plan-grammar path.
        for hh, s in sorted(plan["slots"].items()):
            _sp(f"  {hh} {('AV' if s['lane']=='ai_vtuber' else 'RF')} {s['role']}"
                + (" [occupied]" if hh in occupied else ""))
        return {"done": {}, "failed": [], "diagnoses": [], "dry_run": True, "plan": plan}

    # ── RF ────────────────────────────────────────────────────────────────
    if produce:
        from agents.launch import _pinned_episode_for
        day1_slots = [hh for hh in _rf_slots(plan, "rf_fresh") if hh not in occupied]
        carry_date = target + dt.timedelta(days=1)
        # Only pin carry slots that AREN'T already pinned — so a re-run of a produce day (or a
        # manual run followed by the cron re-targeting the same date) doesn't double-pin tomorrow.
        carry_slots = [hh for hh in _rf_slots(day_plan(carry_date), "rf_carry")
                       if not _pinned_episode_for(carry_date, "real_footage", hh)]
        if not day1_slots and not carry_slots:
            # Idempotent produce day: every fresh RF slot is already filled AND tomorrow is fully
            # pinned → this batch already ran (or a manual run beat it). Skip the 9-RF render + pin
            # entirely (rendering to schedule/pin nothing is pure waste); still handle AV below.
            _sp(":information_source: 생산일 RF가 이미 완료(슬롯 채워짐+이월 핀 존재) — RF 렌더/이월 스킵. AV만 확인.")
            episodes = []
        else:
            episodes = _render_9_rf(target, progress_cb=progress_cb)
        # Day-1: schedule the first N into today's fresh RF slots.
        for (mp4, _concept), hh in zip(episodes[:len(day1_slots)], day1_slots):
            r = _schedule_rf(target, hh, mp4, do_upload, progress_cb=_sp)
            if r.get("video_id"):
                done[("real_footage", hh)] = r
                _sp(f":white_check_mark: {hh} RF 예약완료 — `{r['video_id']}` (공개 {r['publish_at']})")
            else:
                failed.append(("real_footage", hh))
                _sp(f":rotating_light: {hh} RF 렌더OK·예약실패(고아) — `[ORPHAN-SKIP]` 참조")
        # Carry: PIN the rest to tomorrow's carry slots for the next batch to schedule.
        for (mp4, concept), hh in zip(episodes[len(day1_slots):len(day1_slots) + len(carry_slots)],
                                      carry_slots):
            try:
                cid = grammar_slot._pin_grammar_card(target + dt.timedelta(days=1), hh, concept, mp4)
                pinned.append({"slot": hh, "date": (target + dt.timedelta(days=1)).isoformat(),
                               "output": str(mp4), "card_id": cid})
                _sp(f":pushpin: {hh} RF 이월 핀 → {(target + dt.timedelta(days=1)).isoformat()} "
                    f"(내일 배치가 예약) {Path(mp4).name}")
            except Exception as e:  # noqa: BLE001
                log.warning("v2 carry pin failed (%s): %s", hh, e)
                _sp(f":warning: {hh} RF 이월 핀 실패: {str(e)[:120]}")
        _unused = episodes[len(day1_slots) + len(carry_slots):]
        if _unused:
            _sp(f":information_source: 잉여 RF {len(_unused)}편(슬롯보다 많음) — 미사용")
    else:
        # Carry day: schedule the 4 RF pinned yesterday. launch's _pinned_episode_for finds
        # each pin (date+slot+publish_at) and schedules the file — no propose/render.
        for hh in _rf_slots(plan, "rf_carry"):
            if hh in occupied:
                continue
            try:
                res = launch_pipeline(target, progress_cb=_sp, do_upload=do_upload,
                                      lane_filter="real_footage", slot_filter=hh,
                                      assignments_override=[("real_footage", hh)],
                                      slack_client=slack_client, slack_channel=slack_channel,
                                      consolidate_videos=True)
                r = res[0] if res else None
            except Exception as e:  # noqa: BLE001
                log.exception("v2 carry schedule failed (%s)", hh)
                r = None
            if r and r.get("video_id"):
                done[("real_footage", hh)] = r
                _sp(f":white_check_mark: {hh} RF(이월) 예약완료 — `{r['video_id']}`")
            else:
                failed.append(("real_footage", hh))
                _sp(f":rotating_light: {hh} RF(이월) 핀 없음/예약실패 — 손수정 필요")

    # ── AV ────────────────────────────────────────────────────────────────
    if not produce:
        # Day-2 timeliness signal (logged only for now). day1_winners ranks Day-1's early
        # views to shape a fantasy re-imagining, but with LAUNCH_LEAD_DAYS=2 the produce day
        # hasn't aired when this batch runs → no 48h data → 'none'. Wiring the winner into the
        # AV concept is DEFERRED until the lead-time model is resolved; carry AVs render as
        # normal timely concepts meanwhile.
        try:
            w = day1_winners(target)
            if w["mode"] != "none" and w["winners"]:
                _sp(f":crystal_ball: Day-1 인기 신호 감지({w['mode']}) — "
                    "판타지 재해석 배선은 리드타임 모델 확정 후(현재 일반 시의성 AV로 렌더)")
        except Exception as e:  # noqa: BLE001
            log.warning("v2 day1_winners failed: %s", e)
    # One fresh-concept reroll on AV failure (PD 2026-09-27): a single AV attempt via
    # launch_pipeline has produce_and_render's INNER caption-salvage but no OUTER reroll, so one
    # bad concept (e.g. a role-swap premise Giri caps, 9/30) shipped the slot empty for the whole
    # batch. Mirror the 4-slot self-heal's SELFHEAL_REROLL: retry once with a fresh concept (the
    # failed one is now a recent card → AV_DEDUP_GATE/role-swap gate steer the re-propose away).
    # One extra Seedance render — bounded, PD's call. V2_AV_REROLL=0 disables.
    _av_tries = 1 + max(0, int(os.getenv("V2_AV_REROLL", "1")))
    for hh in _av_slots(plan):
        if hh in occupied:
            continue
        r = None
        for _att in range(_av_tries):
            r = _render_av_slot(target, hh, timely=not produce, do_upload=do_upload,
                                progress_cb=_sp,
                                slack_client=slack_client, slack_channel=slack_channel)
            if r and r.get("video_id"):
                break
            if _att + 1 < _av_tries:
                _sp(f":game_die: {hh} AV 미통과 — 완전히 새 컨셉으로 재롤 "
                    f"({_att + 2}/{_av_tries})")
        if r and r.get("video_id"):
            done[("ai_vtuber", hh)] = r
            _sp(f":white_check_mark: {hh} AV 예약완료 — `{r['video_id']}`")
        else:
            failed.append(("ai_vtuber", hh))
            _sp(f":x: {hh} AV 실패({_av_tries}회 시도) — 슬롯 비움(junk 금지)")

    # ── summary ─────────────────────────────────────────────────────────────
    n_live = len(done)
    n_slots = len([hh for hh in SLOTS_V2 if hh not in occupied])
    lines = [f":checkered_flag: *배치 v2 써머리* {target.isoformat()} — 예약 {n_live}/{n_slots}"
             + (f" · 이월 핀 {len(pinned)}편(내일)" if pinned else "")]
    if failed:
        lines.append(":rotating_light: *빈 슬롯 " + str(len(failed)) + "개*: "
                     + ", ".join(f"{h} {'AV' if l=='ai_vtuber' else 'RF'}" for l, h in failed))
    for (l, h), v in sorted(done.items(), key=lambda kv: kv[0][1]):
        lines.append(f"  ✅ {h} {'AV' if l=='ai_vtuber' else 'RF'} — `{v.get('video_id','-')}` "
                     f"(공개 {v.get('publish_at','?')})")
    _sp("\n".join(lines))
    return {
        "done": {f"{l}/{h}": v for (l, h), v in done.items()},
        "failed": [f"{h} {l}" for l, h in failed],
        "pinned": pinned,
        "diagnoses": [],
    }


def _cli() -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Launch v2 — dry-run plan")
    ap.add_argument("--date", default=None)
    ap.add_argument("--plan-grammar", action="store_true", help="run senior director → 9 grammar cast (dry)")
    ap.add_argument("--run-dry", action="store_true", help="run_v2_batch(dry_run=True) — plan, no render/upload")
    args = ap.parse_args()
    target = dt.date.fromisoformat(args.date) if args.date else dt.date.today() + dt.timedelta(days=2)

    dp = day_plan(target)
    print(f"=== day_plan {target} (produce_day={dp['produce_day']}) ===")
    for hh in SLOTS_V2:
        s = dp["slots"][hh]
        print(f"  {hh}  {s['lane']:>12}  {s['role']}")

    if args.run_dry:
        print("\n=== run_v2_batch(dry_run=True) ===")
        r = run_v2_batch(target, dry_run=True, progress_cb=lambda m: print(m))
        print(json.dumps({k: v for k, v in r.items() if k != "plan"}, ensure_ascii=False, indent=2))

    if args.plan_grammar:
        print("\n=== senior director → grammar plan (dry, no render) ===")
        res = plan_sources_to_grammar(target, progress_cb=lambda m: print(m))
        eps = res["episodes"]
        print(f"\n{len(eps)} RF episodes from {len(res['sources'])} sources:")
        for e in eps:
            print(f"  [{e['source_id']}/{e['grammar']:8}] clips={len(e['clips'])} "
                  f"cap='{e['caption_sample'][:36]}' — {e['title'][:40]}")
        from agents import footage_diversity
        print(f"\nfootage-diversity across sources: {footage_diversity.violations(res['sources']) or 'NONE ✓'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
