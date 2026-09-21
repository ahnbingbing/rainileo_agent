"""B2/B3 — edit_grammar production wiring (Phase B).

`edit_grammar_for_slot` assigns velocity/meme/story to the day's 3 RF slots (fixed for the
launch month) behind the **EDIT_GRAMMAR_MODE kill-switch** (default ON for the launch-month A/B;
EDIT_GRAMMAR_MODE=0 → standard RF). When on, `produce_grammar_episodes_shared` casts ONE clip set and renders all three grammars from
that SAME footage in parallel — a controlled A/B where footage is the control and the edit is
the only variable (the B4 grammar-copy Writer writes grammar-specific grounded copy per arm).
The launch slot pipeline builds this once, then each RF slot pulls its grammar's pre-rendered
mp4; on ANY failure a slot falls back to the standard RF produce (never an empty slot).
(`produce_grammar_episode` — the older per-slot cast+render — is retained for dry-run/standalone.)

Reversibility (the D_lanemix lesson): EDIT_GRAMMAR_MODE=0 reverts every RF slot to
standard trim→burn→assemble on the next batch — no redeploy, no in-flight impact.
"""
from __future__ import annotations
import logging
import os
import sqlite3
import datetime as dt
from pathlib import Path

log = logging.getLogger("agents.grammar_slot")

# Fixed grammar order across the day's RF slots (launch-month; then edit_grammar becomes a
# bandit arm — B5). Index = the slot's position among the day's sorted RF slots.
_GRAMMAR_CYCLE = ["velocity", "meme", "story"]


def _grammar_mode_on() -> bool:
    # PD 2026-09-20: rolling-window shipped, so the feature is RE-ACTIVATED (default ON). The
    # 55eb300 OFF-hold existed ONLY because the old A/B put all 3 grammars on the SAME day
    # (a viewer saw the same clip 3× that day); rolling-window fixes that by spreading one
    # variant per day across D/D+1/D+2. The earlier 8h self-heal runaway had SEPARATE roots
    # that are already fixed (face-gate corroboration 6fe509d + wall-clock caps b378348), not
    # grammar itself. Kill-switch: EDIT_GRAMMAR_MODE=0 → standard RF everywhere, next batch,
    # no redeploy.
    return os.getenv("EDIT_GRAMMAR_MODE", "1") == "1"


def _day_grammar_slot(d: dt.date) -> tuple[str | None, str | None]:
    """The ONE (grammar, RF-slot) this day airs under the rolling window, or (None, None).

    Rolling window (PD 2026-09-20): exactly ONE grammar variant airs per day (not 3 same-day).
    A 3-day window casts once and spreads velocity/meme/story across D/D+1/D+2. So per day we
    pick a single grammar + a single RF slot. Both rotate by (day-in-window + window-id) so, over
    windows, each grammar visits every RF slot — timeslot averages out across grammars and the
    bandit reads a clean grammar marginal (the A/B-validity reason the old per-slot map rotated).
    Deterministic per date, so a pin created 2 days early (on the window-start batch) lands on the
    exact (date, slot) this day's own pin-lookup queries."""
    import agents.launch as _launch
    rf = sorted(hh for ln, hh in _launch.day_assignments(d) if ln == "real_footage")
    if not rf:
        return None, None
    wid = d.toordinal() // len(_GRAMMAR_CYCLE)
    k = d.toordinal() % len(_GRAMMAR_CYCLE)
    grammar = _GRAMMAR_CYCLE[(k + wid) % len(_GRAMMAR_CYCLE)]
    slot = rf[(k + wid) % len(rf)]
    return grammar, slot


def edit_grammar_for_slot(target: dt.date, hhmm: str, assignments: list) -> str | None:
    """The grammar this RF slot airs, or None (→ standard RF). Off unless EDIT_GRAMMAR_MODE=1.

    Rolling window: only the day's ONE designated grammar slot returns a grammar; the other RF
    slots return None (standard RF). See _day_grammar_slot. (assignments kept for signature
    compat; the slot is computed from day_assignments(target) directly so a filtered self-heal
    call still resolves the true day-wide slot.)"""
    if not _grammar_mode_on():
        return None
    grammar, slot = _day_grammar_slot(target)
    return grammar if (slot and hhmm == slot) else None


def _fresh_pool(exclude: set | None = None, limit: int = 12) -> list[dict]:
    """Recent, unused, clean RF video clips with grounded VLM fields — the candidate pool the
    grammar Writer casts from. Kept simple/coherent: recent first, pets present, decent quality."""
    exclude = exclude or set()
    db = sqlite3.connect(str(Path("data/agent.db")))
    db.row_factory = sqlite3.Row
    used = set(exclude)
    for r in db.execute("SELECT payload_json FROM cards WHERE render_style='real_footage' AND uploaded=1"):
        import json as _j
        try:
            for c in (_j.loads(r[0] or "{}").get("cuts") or []):
                if c.get("asset_id"):
                    used.add(c["asset_id"])
        except Exception:
            pass
    rows = db.execute(
        "SELECT asset_id, scene_description, activity, subjects_csv, duration_sec, "
        "location_type, quality_score FROM assets WHERE kind='video' "
        "AND duration_sec >= 8 AND captured_iso >= '2026-03-01' "
        "AND (quality_score IS NULL OR quality_score >= 0.6) "
        "ORDER BY captured_iso DESC LIMIT 120").fetchall()
    db.close()
    pool = []
    for r in rows:
        aid = r["asset_id"]
        subj = (r["subjects_csv"] or "").lower()
        if aid in used or not ("ryani" in subj or "leo" in subj):
            continue
        pool.append(dict(r))
        if len(pool) >= limit:
            break
    return pool


def _ensure_local(aid: str):
    from icloud import gcs
    db = sqlite3.connect(str(Path("data/agent.db")))
    fp = db.execute("SELECT file_path FROM assets WHERE asset_id=?", (aid,)).fetchone()[0]
    db.close()
    lp = gcs.local_path(fp)
    if not os.path.exists(lp):
        gcs.download_to(fp)


# ── Footage-fit gate (PD 2026-09-20 quality) ────────────────────────────────
# A grammar imposes a FORM (velocity = kinetic beat-cut montage; meme = reaction beats). If the
# cast footage lacks the energy that form needs — the fresh RF pool trends calm (sniffing/walking/
# napping) after the 9/20 fresh-pool fix — the form fights the footage: velocity hue-strobes a dog
# sniffing plants, meme claims "텐션 미쳤다" over a cat sitting still. That's the deepest root of the
# "허접" club/meme edits (same lesson as story B4: form is only strong when the footage carries it).
# So before rendering a grammar we check the shared cast clears that grammar's motion floor; if not,
# skip it → standard RF fallback (never an empty slot). Story is cinematic and reads fine on calm
# footage, so it is ungated. Floors calibrated on scripts.impact_edit.clip_motion_peak (running/
# swimming/playing pet ≈ 23-30, sniff/walk ≈ 8-14, nap ≈ 6).
_VELOCITY_MOTION_FLOOR = float(os.getenv("VELOCITY_MOTION_FLOOR", "16.0"))
_VELOCITY_BUILD_FLOOR = float(os.getenv("VELOCITY_BUILD_FLOOR", "11.0"))
_MEME_MOTION_FLOOR = float(os.getenv("MEME_MOTION_FLOOR", "11.0"))


def _cast_motion_peaks(clips: dict) -> dict:
    """asset_id → peak-window motion for each distinct cast clip (computed once, reused across the
    3 grammars that share the cast). Clips must already be local (_ensure_local run first)."""
    from scripts.impact_edit import clip_motion_peak
    peaks: dict = {}
    con = sqlite3.connect(str(Path("data/agent.db")))
    try:
        from icloud import gcs
        for aid in dict.fromkeys(clips.values()):
            row = con.execute("SELECT file_path FROM assets WHERE asset_id=?", (aid,)).fetchone()
            if not row:
                continue
            try:
                peaks[aid] = clip_motion_peak(gcs.local_path(row[0]))
            except Exception as e:
                log.warning("motion peak %s: %s", aid, str(e)[:80])
    finally:
        con.close()
    return peaks


def _footage_fit(grammar: str, peaks: dict) -> str | None:
    """Does the cast carry the ENERGY this grammar's form needs? Returns a reason to SKIP
    (→ standard RF) or None to proceed. velocity needs a genuine kinetic climax + a non-calm
    build; meme needs at least one reaction-level motion beat; story is ungated."""
    vals = sorted(peaks.values(), reverse=True) if peaks else [0.0]
    top = vals[0] if vals else 0.0
    if grammar == "velocity":
        if top < _VELOCITY_MOTION_FLOOR:
            return (f"velocity needs a kinetic climax but cast peak motion {top:.1f} "
                    f"< {_VELOCITY_MOTION_FLOOR:.0f} (all-calm footage → hue-strobing a sniff)")
        if sum(1 for v in vals if v >= _VELOCITY_BUILD_FLOOR) < 2:
            return (f"velocity needs a moving build but only {sum(1 for v in vals if v >= _VELOCITY_BUILD_FLOOR)} "
                    f"cast clip(s) ≥ {_VELOCITY_BUILD_FLOOR:.0f}")
    elif grammar == "meme":
        if top < _MEME_MOTION_FLOOR:
            return (f"meme needs a reaction beat but cast peak motion {top:.1f} "
                    f"< {_MEME_MOTION_FLOOR:.0f} (calm footage → claimed chaos never shown)")
    return None


def _title_from_copy(grammar: str, copy: dict) -> str:
    if grammar == "story":
        for b in copy.get("beats", []):
            if b.get("ko"):
                return b["ko"]
    for cp in copy.get("captions", []):
        if cp.get("ko"):
            return cp["ko"]
    return {"velocity": "우리집 국가대표", "meme": "우리집 텐션", "story": "랴니와 레오"}[grammar]


def produce_grammar_episode(grammar: str, target: dt.date, hhmm: str,
                            progress_cb=None, exclude_asset_ids=None):
    """Cast + copy + render one grammar episode. Returns (mp4_path, concept_dict). Raises on any
    failure so the launch caller can fall back to standard RF. NOTE: grammar edits use a different
    aesthetic than the RF Giri rubric, so this path does NOT Giri-gate — the Writer grounds the
    copy and PD spot-checks the batch summary (flag-gated + dry-run-verified before live)."""
    from scripts.impact_edit import render_grammar
    from agents.edit_grammar_writer import propose_grammar_copy

    def _sp(m):
        if progress_cb:
            progress_cb(m)

    pool = _fresh_pool(set(exclude_asset_ids or []))
    if len(pool) < 4:
        raise RuntimeError(f"grammar pool too thin ({len(pool)} clips)")
    _sp(f":art: {hhmm} grammar={grammar} — 클립 {len(pool)}개서 캐스팅+카피 생성 중")
    res = propose_grammar_copy(grammar, pool, slot_hhmm=hhmm)
    clips, copy = res["clips"], res["copy"]
    for aid in dict.fromkeys(clips.values()):
        _ensure_local(aid)
    fit = _footage_fit(grammar, _cast_motion_peaks(clips))   # form must match footage energy
    if fit:
        raise RuntimeError(f"footage-fit: {fit}")
    ts = target.strftime("%Y%m%d")
    out = Path(f"data/output/episodes/episode_rf_{grammar}_{ts}_{hhmm.replace(':', '')}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    render_grammar(grammar, out, clips=clips, copy=copy)
    if not out.exists():
        raise RuntimeError("grammar render produced no file")
    title = _title_from_copy(grammar, copy)
    concept = {
        "render_style": "real_footage", "edit_grammar": grammar,
        "title": title, "theme": title, "narrative_oneliner": title,
        "subjects": ["ryani", "leo"],
        "cuts": [{"asset_id": aid} for aid in dict.fromkeys(clips.values())],
    }
    _sp(f":white_check_mark: {hhmm} grammar={grammar} 렌더 완료 → {out.name}")
    return out, concept


def _persist_grammar_card(target: dt.date, concept: dict, out_path) -> str:
    """Register a card row for a rendered grammar episode + link the mp4 via output_video_path.
    Without this the launch scheduler's _auto_upload_episode (which looks a card up BY
    output_video_path) hits [ORPHAN-SKIP] and the slot never schedules — the grammar path
    bypasses produce_and_render, which is what normally creates the card. Returns the card_id."""
    import uuid as _uuid
    import datetime as _dt
    from agents.producer import _db
    from agents.writer import persist_card
    title = concept.get("title") or "랴니와 레오"
    con = _db()
    try:
        card = {
            "card_id": str(_uuid.uuid4()),
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "author": "grammar_slot",
            "card_type": "daily",
            "date": target.isoformat(),
            "theme": concept.get("theme") or title,
            "title": title,
            "narrative_oneliner": concept.get("narrative_oneliner") or title,
            "render_style": "real_footage",
            "edit_grammar": concept.get("edit_grammar"),
            "episode_format": "short",
            "tone": {"primary": "warm", "intensity": 0.6},  # cards.tone_primary is NOT NULL
            "subjects": concept.get("subjects", ["ryani", "leo"]),
            "duration_target_sec": 20,
            "writer_confidence": 0.85,
            "ask_pd": False,
            "cuts": concept.get("cuts", []),
            "draft": {"title": title, "description": title,
                      "hashtags": ["#랴니", "#레오", "#일상"], "caption_burnin": title},
        }
        # runs.agent has a CHECK constraint (writer/pd/cameraman/memory/scheduler) — the grammar
        # render is a cameraman-lane render, so log it as 'cameraman'.
        run_cur = con.execute("INSERT INTO runs (agent, status) VALUES ('cameraman', 'ok')")
        con.commit()
        persist_card(con, card, run_cur.lastrowid)
        con.execute("UPDATE cards SET state='approved', output_video_path=?, "
                    "updated_at=datetime('now') WHERE card_id=?",
                    (str(out_path), card["card_id"]))
        con.commit()
        return card["card_id"]
    finally:
        con.close()


def _concept_for(grammar: str, clips: dict, copy: dict) -> dict:
    title = _title_from_copy(grammar, copy)
    return {
        "render_style": "real_footage", "edit_grammar": grammar,
        "title": title, "theme": title, "narrative_oneliner": title,
        "subjects": ["ryani", "leo"],
        "cuts": [{"asset_id": aid} for aid in dict.fromkeys(clips.values())],
    }


def produce_grammar_episodes_shared(grammars: list, target: dt.date, hhmm_by_grammar: dict,
                                    progress_cb=None, exclude_asset_ids=None,
                                    pool=None) -> dict:
    """Controlled edit_grammar A/B: cast ONE clip set, then render every grammar from the SAME
    footage IN PARALLEL — footage is the control, the edit is the only variable. Returns
    {grammar: (mp4_path, concept)}. Raises if the shared cast itself fails (caller falls back to
    standard RF for all slots); a single grammar's render failure just omits that grammar from the
    result (its slot falls back to standard RF). This is why the 3 daily RF slots share footage and
    run together instead of each casting its own clips sequentially."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from scripts.impact_edit import render_grammar
    from agents.edit_grammar_writer import propose_grammar_copy

    def _sp(m):
        if progress_cb:
            progress_cb(m)

    # PD 2026-09-21 (v2): when the senior director supplies a source's cast, render the 3
    # grammars from THAT footage (pool override) instead of self-casting from _fresh_pool. Each
    # of the batch's 3 sources thus keeps its own distinct footage (the footage-diversity gate
    # already guaranteed <60% overlap across sources). No override → legacy self-cast, unchanged.
    if pool is None:
        pool = _fresh_pool(set(exclude_asset_ids or []))
    if len(pool) < 4:
        raise RuntimeError(f"grammar pool too thin ({len(pool)} clips)")
    # Cast ONCE via story — its beat structure references all 5 roles, so it produces the
    # fullest cast for the other grammars to reuse.
    _sp(f":art: RF grammar A/B — 클립 {len(pool)}개서 공유 캐스팅(1회) 중")
    base = propose_grammar_copy("story", pool, slot_hhmm=hhmm_by_grammar.get("story"))
    shared_clips = base["clips"]
    base_copy = base["copy"]
    for aid in dict.fromkeys(shared_clips.values()):
        _ensure_local(aid)
    peaks = _cast_motion_peaks(shared_clips)          # footage-fit: does the shared cast carry each form?
    ts = target.strftime("%Y%m%d")

    def _one(grammar: str):
        hh = hhmm_by_grammar.get(grammar, "")
        fit = _footage_fit(grammar, peaks)            # skip a grammar the footage can't carry → standard RF
        if fit:
            raise RuntimeError(f"footage-fit: {fit}")
        copy = (base_copy if grammar == "story"
                else propose_grammar_copy(grammar, pool, slot_hhmm=hh,
                                          fixed_clips=shared_clips)["copy"])
        out = Path(f"data/output/episodes/episode_rf_{grammar}_{ts}_{hh.replace(':', '')}.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        render_grammar(grammar, out, clips=shared_clips, copy=copy)
        if not out.exists():
            raise RuntimeError("grammar render produced no file")
        concept = _concept_for(grammar, shared_clips, copy)
        # Register a card + link the mp4 so the launch scheduler can find and schedule it
        # (otherwise [ORPHAN-SKIP] → slot never fills). Non-fatal: a card failure shouldn't
        # discard a good render — the slot would just need a manual schedule.
        try:
            concept["card_id"] = _persist_grammar_card(target, concept, out)
        except Exception as e:
            log.warning("grammar %s card persist failed (slot will orphan): %s", grammar, e)
        _sp(f":white_check_mark: {hh} grammar={grammar} 렌더 완료(공유 footage) → {out.name}")
        return out, concept

    # Cap concurrency: the VM has 2 vCPUs, so 3 simultaneous CPU-bound renders thrash (loadavg ~7,
    # slower wall-clock than 2-wide). 2-wide keeps it parallel without starving. GRAMMAR_RENDER_
    # CONCURRENCY tunes it (a bigger VM can raise it).
    _cc = min(len(grammars), max(1, int(os.getenv("GRAMMAR_RENDER_CONCURRENCY", "2"))))
    results: dict = {}
    with ThreadPoolExecutor(max_workers=_cc) as ex:
        futs = {ex.submit(_one, g): g for g in grammars}
        for fut in as_completed(futs):
            g = futs[fut]
            try:
                results[g] = fut.result()
            except Exception as e:
                log.warning("grammar %s failed in shared render → standard RF: %s", g, e)
                _sp(f":warning: grammar={g} 렌더 실패 → 표준 RF 폴백: {str(e)[:120]}")
    return results


# ── Rolling window (PD 2026-09-20) ──────────────────────────────────────────
def _ground_cast_clips(clips: dict) -> dict:
    """Authoritative subject-union + location grounding for the cast clips
    (pd_notes + gpt-4o-mini multi-frame). Returns asset_id → grounding dict. Fed to the copy
    Writer so grammar copy stops erasing a present pet / mislabelling an outdoor outing, and
    attached to the pinned concept so Giri caps against the same truth."""
    try:
        from agents import openai_vision
    except Exception:
        return {}
    out: dict = {}
    con = sqlite3.connect(str(Path("data/agent.db")))
    try:
        for aid in dict.fromkeys(clips.values()):
            row = con.execute(
                "SELECT file_path, kind, pd_notes, captured_iso FROM assets WHERE asset_id=?",
                (aid,)).fetchone()
            if not row:
                continue
            fp, kind, pdn, cap_iso = row
            try:
                g = openai_vision.ground_asset(fp, kind=kind or "video",
                                               pd_notes=pdn, captured_iso=cap_iso)
            except Exception as e:
                log.warning("grammar grounding %s: %s", aid, str(e)[:100])
                g = None
            if g and g.get("subjects"):
                out[aid] = g
    finally:
        con.close()
    return out


def _grounding_union(grounding: dict) -> dict:
    subj: set = set()
    any_outdoor = False
    for g in grounding.values():
        subj.update(g.get("subjects") or [])
        if g.get("indoor_outdoor") == "outdoor" or g.get("location_type") in ("outdoor", "cafe"):
            any_outdoor = True
    return {"subjects": sorted(subj), "any_outdoor": any_outdoor}


def _copy_text(copy: dict, grammar: str) -> str:
    bits = []
    for cp in copy.get("captions", []) or []:
        bits += [str(cp.get("ko") or ""), str(cp.get("en") or "")]
    for b in copy.get("beats", []) or []:
        bits += [str(b.get("ko") or ""), str(b.get("ko2") or ""), str(b.get("narration") or "")]
    return " ".join(bits)


def _grounding_violation(copy: dict, grammar: str, union: dict) -> str | None:
    """Backstop: does the grounded copy erase a present pet or mislabel location? Returns a
    reason string (→ skip pin, fall back to standard RF) or None. Copy is grounded at source so
    this rarely fires — it's the m1AFJiWzGx0 (story-grammar erased Ryani + outdoor→집) guard."""
    text = _copy_text(copy, grammar).lower()
    if not text.strip():
        return None
    subs = set(union.get("subjects") or [])
    if {"ryani", "leo"} <= subs:
        has_leo = ("레오" in text) or ("leo" in text)
        has_ry = ("랴니" in text) or ("ryani" in text) or ("라니" in text)
        if has_leo != has_ry:  # names exactly one pet while both are present
            return "subject-erasure: 둘 다 나오는데 캡션이 한 마리만 언급"
    if union.get("any_outdoor"):
        says_home = any(w in text for w in ("집에서", "우리 집", "우리집", "실내", "방 안", "거실"))
        says_out = any(w in text for w in ("밖", "실외", "야외", "카페", "공원", "산책", "테라스",
                                           "outdoor", "outside", "cafe", "park"))
        if says_home and not says_out:
            return "location: 실외 나들이인데 캡션이 '집/실내'"
    return None


def _pin_grammar_card(air_day: dt.date, slot_hhmm: str, concept: dict, out_path) -> str:
    """Pin a rendered grammar mp4 to a FUTURE (date, slot) as a `cards` row the launch
    pin-lookup (_pinned_episode_for) honors: state='rendered', uploaded=0, date=air_day,
    render_style='real_footage', youtube_publish_at=publish_at_for(air_day, slot). On that day
    launch skips propose+render and just schedules this file. Returns card_id."""
    import uuid as _uuid
    import datetime as _dt
    from agents.producer import _db
    from agents.writer import persist_card
    from agents.launch import publish_at_for
    title = concept.get("title") or "랴니와 레오"
    con = _db()
    try:
        card = {
            "card_id": str(_uuid.uuid4()),
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "author": "grammar_slot",
            "card_type": "daily",
            "date": air_day.isoformat(),
            "theme": concept.get("theme") or title,
            "title": title,
            "narrative_oneliner": concept.get("narrative_oneliner") or title,
            "render_style": "real_footage",
            "edit_grammar": concept.get("edit_grammar"),
            "episode_format": "short",
            "tone": {"primary": "warm", "intensity": 0.6},
            "subjects": concept.get("subjects", ["ryani", "leo"]),
            "duration_target_sec": 20,
            "writer_confidence": 0.85,
            "ask_pd": False,
            "cuts": concept.get("cuts", []),
            "draft": {"title": title, "description": title,
                      "hashtags": ["#랴니", "#레오", "#일상"], "caption_burnin": title},
        }
        run_cur = con.execute("INSERT INTO runs (agent, status) VALUES ('cameraman', 'ok')")
        con.commit()
        persist_card(con, card, run_cur.lastrowid)
        con.execute(
            "UPDATE cards SET state='rendered', output_video_path=?, "
            "youtube_publish_at=?, uploaded=0, updated_at=datetime('now') WHERE card_id=?",
            (str(out_path), publish_at_for(air_day, slot_hhmm), card["card_id"]))
        con.commit()
        return card["card_id"]
    finally:
        con.close()


def _window_already_pinned(target: dt.date) -> bool:
    """Idempotency: are this window's grammar variants already pinned? (a re-run of the
    window-start batch must not re-cast + double-pin)."""
    con = sqlite3.connect(str(Path("data/agent.db")))
    try:
        days = [(target + dt.timedelta(days=j)).isoformat() for j in range(len(_GRAMMAR_CYCLE))]
        qs = ",".join("?" * len(days))
        n = con.execute(
            f"SELECT COUNT(*) FROM cards WHERE author='grammar_slot' AND state='rendered' "
            f"AND uploaded=0 AND date IN ({qs})", days).fetchone()[0]
        return n >= len(_GRAMMAR_CYCLE)
    except Exception:
        return False
    finally:
        con.close()


def ensure_rolling_window(target: dt.date, progress_cb=None, exclude_asset_ids=None):
    """Rolling-window grammar (PD 2026-09-20). On a WINDOW-START day, cast ONE clip set, ground
    it, render velocity/meme/story with grounded copy, and PIN each variant to a DIFFERENT day
    (D/D+1/D+2) at that day's designated RF slot — so a viewer sees the same footage at most once
    per day, spread over 3 days (not 3× same-day). Returns (mp4, concept) for the CURRENT day's
    variant to fill this slot, or None (→ standard RF). Idempotent; any failure per-day falls back
    to standard RF for that day. Non-window-start days are served by the pins made here (their own
    _pinned_episode_for finds them) — this returns None for them."""
    if not _grammar_mode_on():
        return None
    if target.toordinal() % len(_GRAMMAR_CYCLE) != 0:
        return None  # not a window-start; this day's variant was pinned on the window-start batch
    if _window_already_pinned(target):
        log.info("rolling-window %s already pinned — skip re-cast", target.isoformat())
        # still return today's variant if its file is on disk
        return _today_pinned_variant(target)

    from agents.edit_grammar_writer import propose_grammar_copy

    def _sp(m):
        if progress_cb:
            progress_cb(m)

    pool = _fresh_pool(set(exclude_asset_ids or []))
    if len(pool) < 4:
        raise RuntimeError(f"grammar pool too thin ({len(pool)} clips)")
    _sp(f":art: RF grammar 롤링윈도우 — 클립 {len(pool)}개서 공유 캐스팅(1회, 3일 분산)")
    base = propose_grammar_copy("story", pool)
    shared_clips = base["clips"]
    for aid in dict.fromkeys(shared_clips.values()):
        _ensure_local(aid)
    grounding = _ground_cast_clips(shared_clips)
    union = _grounding_union(grounding)
    peaks = _cast_motion_peaks(shared_clips)          # footage-fit: does the cast carry each form?
    _sp(f":mag: 캐스트 그라운딩 — subjects={union['subjects']} outdoor={union['any_outdoor']} "
        f"peak_motion={max(peaks.values()) if peaks else 0:.1f}")

    from scripts.impact_edit import render_grammar
    ts0 = target.strftime("%Y%m%d")
    today_hit = None
    for j in range(len(_GRAMMAR_CYCLE)):
        air_day = target + dt.timedelta(days=j)
        grammar, slot = _day_grammar_slot(air_day)
        if not grammar or not slot:
            continue
        try:
            fit = _footage_fit(grammar, peaks)         # form must match the footage's energy
            if fit:
                log.warning("rolling-window %s %s footage-fit skip → standard RF: %s",
                            air_day, grammar, fit)
                _sp(f":warning: {air_day.isoformat()} grammar={grammar} footage 부적합({fit}) → 표준 RF")
                continue
            copy = propose_grammar_copy(grammar, pool, slot_hhmm=slot,
                                        fixed_clips=shared_clips, grounding=grounding)["copy"]
            viol = _grounding_violation(copy, grammar, union)
            if viol:
                log.warning("rolling-window %s %s grounding violation → standard RF: %s",
                            air_day, grammar, viol)
                _sp(f":warning: {air_day.isoformat()} grammar={grammar} 그라운딩 위반({viol}) → 표준 RF")
                continue
            out = Path(f"data/output/episodes/episode_rf_{grammar}_{ts0}p{j}_"
                       f"{slot.replace(':', '')}.mp4")
            out.parent.mkdir(parents=True, exist_ok=True)
            render_grammar(grammar, out, clips=shared_clips, copy=copy)
            if not out.exists():
                raise RuntimeError("grammar render produced no file")
            concept = _concept_for(grammar, shared_clips, copy)
            concept["_grounding_union"] = union
            for c in concept.get("cuts", []):
                g = grounding.get(c.get("asset_id"))
                if g:
                    c["grounding"] = {"subjects": g.get("subjects"),
                                      "location_type": g.get("location_type"),
                                      "indoor_outdoor": g.get("indoor_outdoor")}
            concept["card_id"] = _pin_grammar_card(air_day, slot, concept, out)
            _sp(f":pushpin: {air_day.isoformat()} {slot} grammar={grammar} 렌더+핀 완료 → {out.name}")
            if j == 0:
                today_hit = (out, concept)
        except Exception as e:
            log.warning("rolling-window %s %s failed → standard RF: %s", air_day, grammar, e)
            _sp(f":warning: {air_day.isoformat()} grammar={grammar} 실패 → 표준 RF: {str(e)[:120]}")
    return today_hit


def _today_pinned_variant(target: dt.date):
    """Return (mp4, concept) for target's already-pinned grammar variant, if its file exists."""
    grammar, slot = _day_grammar_slot(target)
    if not grammar or not slot:
        return None
    try:
        from agents.launch import publish_at_for
        con = sqlite3.connect(str(Path("data/agent.db")))
        row = con.execute(
            "SELECT output_video_path, title, theme FROM cards WHERE author='grammar_slot' "
            "AND state='rendered' AND uploaded=0 AND date=? AND youtube_publish_at=? "
            "ORDER BY updated_at DESC LIMIT 1",
            (target.isoformat(), publish_at_for(target, slot))).fetchone()
        con.close()
        if row and row[0] and Path(row[0]).exists():
            return Path(row[0]), {"render_style": "real_footage", "edit_grammar": grammar,
                                  "title": row[1] or row[2] or "랴니와 레오",
                                  "subjects": ["ryani", "leo"], "cuts": []}
    except Exception as e:
        log.warning("today pinned variant lookup failed: %s", e)
    return None
