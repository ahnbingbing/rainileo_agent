#!/usr/bin/env python3
"""Stage 4.5 — RF clip matcher (the backbone of an rf_heavy MV).

The Director routes most beats of a real-story MV to RF cuts, each carrying a
`clip_query` ("the real moment this beat is"). This stage turns that query into an
actual library clip + a trim window landed on the matching action — reusing the
existing RF infra, NO new retrieval engine (PD: 기존 재사용):

  find_similar(query, kind='video', subject)      # semantic nearest real clips
    → cameraman._ensure_local(file_path)          # GCS-fetch the original on demand
    → video_actions.actions(local)                # Gemini action moments [{start,end,actor,action,desc}]
    → _best_window(...)                           # land a target-dur window on the matching action

The chosen clip is NOT pre-rendered: make_mv (stage 7 assemble) already tone-maps
HLG→SDR, crops to 1080×1920 CFR30, stamps bt709, and trims per-clip via ss/dur. So
a matched RF cut just stores {rf_clip (local path), ss, duration, rf_asset_id,
rf_action, rf_candidates} and flows straight into assemble.

Beat sync: cut durations are set from the karaoke line each cut's lyric lands on,
so the RF body tiles the song instead of a flat 5s-per-cut guess.
"""
from __future__ import annotations

import difflib
import logging
import math
from pathlib import Path

from agents import asset_embed as ae

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("agents.mv_rf_matcher")

# Only ground (fetch + Gemini actions) this many top semantic candidates per cut —
# grounding costs one Gemini call + one GCS fetch each. Accept early on a strong match.
GROUND_TOPN = 3
ACCEPT_SCORE = 0.48          # embed-cosine(query, action desc) good enough to stop early
MIN_WINDOW = 1.2             # never trim an RF cut shorter than this (sync slack)


# ─────────────────────────── beat timing (cut ← karaoke) ───────────────────────────

def set_cut_durations(m: dict) -> dict:
    """Set each cut's duration from the karaoke line its lyric lands on, so the body
    tiles the song. Cuts are in lyric order; karaoke may have more lines (a lyric split
    across two sung windows) — align monotonically by fuzzy text match."""
    kl = m.get("karaoke_lines") or []
    cuts = m.get("cuts") or []
    if not kl or not cuts:
        return m
    song_end = float((m.get("song") or {}).get("duration") or (kl[-1].get("end") or 0.0))

    # Cuts legitimately outnumber karaoke lines — the Director adds instrumental /
    # ad-lib / ending cuts beyond the sung lines. Those trailing cuts have no line to
    # land on (ptr runs off the end), so collect them and spread the remaining song
    # time across them evenly instead of indexing past kl.
    n = len(cuts)
    starts: list[float] = [0.0] * n
    ptr, last, last_end = 0, 0.0, 0.0
    tail: list[int] = []
    for i, cut in enumerate(cuts):
        if ptr >= len(kl):
            tail.append(i)
            continue
        lyric = (cut.get("lyric") or "").strip()
        best_i, best_r = ptr, -1.0
        for j in range(ptr, len(kl)):
            r = difflib.SequenceMatcher(None, lyric, (kl[j].get("text") or "").strip()).ratio()
            if r > best_r:
                best_r, best_i = r, j
            # a near-exact hit ends the search (monotonic)
            if r > 0.85:
                break
        starts[i] = float(kl[best_i].get("start") or last)
        last = starts[i]
        last_end = max(last_end, float(kl[best_i].get("end") or last))
        ptr = best_i + 1
    if tail:
        # The last SUNG cut must cover its karaoke line FULLY — so the trailing instrumental /
        # outro cuts begin at the last line's END, not its start. (Spreading from `last` = the last
        # line's START stole that line's remaining sung time, leaving the final lyric cut ~one step
        # long while an outro cut already played under its still-singing caption → the sync gate.)
        span = max(song_end - last_end, MIN_WINDOW * len(tail))
        step = span / len(tail)
        for pos, i in enumerate(tail):
            starts[i] = last_end + step * pos
    # The body is CONCATENATED from video-0, so a cut's on-screen time = sum of prior durations,
    # not its absolute karaoke start. The first sung line begins AFTER the song's instrumental intro
    # (e.g. 3s in); if cut 0 also "starts" there, the whole sequence plays that intro-gap EARLY and
    # every visual leads its caption by the intro length. Anchor cut 0 at 0 so it absorbs the intro —
    # then each later cut telescopes to start exactly at its karaoke line (visual ↔ caption aligned).
    if starts:
        starts[0] = 0.0
    # duration = gap to the next cut's start (last cut runs to song end)
    for i, cut in enumerate(cuts):
        nxt = starts[i + 1] if i + 1 < len(starts) else song_end
        dur = max(MIN_WINDOW, round(nxt - starts[i], 2))
        cut["duration"] = dur
        cut["beat_start"] = starts[i]
    log.info("set_cut_durations: %d cuts, body=%.1fs (song=%.1fs)",
             len(cuts), sum(c["duration"] for c in cuts), song_end)
    return m


# ─────────────────────────── clip + window matching ───────────────────────────

def _uuid_from(asset_id: str) -> str | None:
    """Photos UUID fragment is the tail after `_icloud_` / `_slack_` etc. _ensure_local
    tries GCS by path first, so this is only a fallback — return None when unsure."""
    for marker in ("_icloud_", "_petlabel_", "_slack_"):
        if marker in asset_id:
            return None  # the path-based GCS fetch is authoritative; no real UUID here
    return None


def _resolve_hint(hint: str) -> str | None:
    """A per-cut rf_clip_hint is a PD/human-verified clip (asset_id stem or path) that
    bypasses the semantic index — needed because the LOCAL embedding index is stale and
    blind to recently-ingested never-used footage (the VM index is the ground truth). Resolve
    it to a real local path (glob by stem), else GCS-fetch by the stored path if we have one."""
    if not hint:
        return None
    p = Path(hint)
    if p.is_absolute() and p.exists():
        return str(p)
    rp = ROOT / hint
    if rp.exists():
        return str(rp)
    # treat as an asset_id / filename stem → glob the clips tree
    from pathlib import Path as _P
    stem = _P(hint).name
    for base in (ROOT / "data" / "assets" / "clips", ROOT / "data" / "assets" / "photos"):
        hits = sorted(base.glob(f"**/{stem}*"))
        if hits:
            return str(hits[0])
    return None


def _clip_duration(local: str) -> float:
    try:
        from scripts.video_actions import _probe_duration
        return float(_probe_duration(local) or 0.0)
    except Exception:
        return 0.0


def _best_window(actions: list, query_vec, subject: str, target_dur: float,
                 clip_dur: float) -> tuple[float, float, dict, float]:
    """Pick the trim window (ss, dur) landed on the action best matching the query.
    Returns (ss, dur, action, score). Falls back to a centred window if no actions."""
    if clip_dur <= 0:
        clip_dur = target_dur
    want = min(target_dur, clip_dur)

    if not actions:
        ss = max(0.0, (clip_dur - want) / 2.0)
        return round(ss, 2), round(want, 2), {}, 0.0

    descs = [(a.get("desc") or a.get("action") or "") for a in actions]
    vecs = ae.embed_texts(descs)
    import numpy as np

    def cos(u, v):
        u, v = np.asarray(u, "float32"), np.asarray(v, "float32")
        return float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-8))

    qv = np.asarray(query_vec, "float32")
    best = (0, -1.0)
    for i, a in enumerate(actions):
        s = cos(qv, vecs[i])
        actor = (a.get("actor") or "both").lower()
        # reward the clip's actor matching the cut's subject (both-cut accepts anything)
        if subject in ("ryani", "leo") and actor in (subject, "both"):
            s += 0.06
        elif subject in ("ryani", "leo") and actor not in (subject, "both"):
            s -= 0.10
        if s > best[1]:
            best = (i, s)
    act = actions[best[0]]
    a_start = float(act.get("start") or 0.0)
    a_end = float(act.get("end") or (a_start + want))
    centre = (a_start + a_end) / 2.0
    ss = max(0.0, min(centre - want / 2.0, clip_dur - want))
    return round(ss, 2), round(want, 2), act, round(best[1], 3)


def match_cut(cut: dict, *, k: int = 6, ground: bool = True, exclude_assets: set | None = None) -> dict:
    """Resolve one RF cut's clip_query → a real clip + trim window. Mutates & returns the cut.
    Stores rf_clip (local path), ss, duration (kept), rf_asset_id, rf_action, rf_score,
    rf_candidates. On total failure leaves rf_clip=None (caller decides: reroute to AV / drop).

    `exclude_assets` = asset_ids already used by OTHER cuts this MV. Clip DIVERSITY: a montage that
    staples the same clip onto 4 different lyric lines reads as broken, so we prefer candidates not
    already used and only fall back to reuse when no distinct clip exists."""
    exclude_assets = exclude_assets or set()
    query = (cut.get("clip_query") or "").strip()
    subject = (cut.get("subject") or "both").lower()
    target_dur = float(cut.get("duration") or 5.0)
    subj_filter = None if subject == "both" else subject

    # PD/human-verified clip override — bypasses the (stale local) semantic index.
    hint = cut.get("rf_clip_hint")
    if hint:
        local = _resolve_hint(hint)
        if local and Path(local).exists():
            clip_dur = _clip_duration(local)
            acts = []
            if ground:
                try:
                    from scripts.video_actions import actions as detect_actions
                    acts = detect_actions(local)
                except Exception as e:
                    log.warning("match_cut %s: hint actions() failed: %s", cut.get("tag"), e)
            qv_h = ae.embed_texts([query])[0] if query else None
            ss, dur, act, ascore = _best_window(acts, qv_h, subject, target_dur, clip_dur) if qv_h \
                else (max(0.0, (clip_dur - target_dur) / 2.0), min(target_dur, clip_dur), {}, 0.0)
            cut.update({"rf_clip": local, "rf_asset_id": Path(local).stem, "ss": ss,
                        "duration": dur, "clip_dur": round(clip_dur, 2), "rf_action": act,
                        "rf_score": round(ascore, 3), "rf_source": "hint",
                        "rf_candidates": cut.get("rf_candidates", [])})
            log.info("match_cut %s → HINT %s ss=%.1f dur=%.1f", cut.get("tag"), Path(local).name, ss, dur)
            return cut
        log.warning("match_cut %s: rf_clip_hint %r did not resolve — falling back to retrieval", cut.get("tag"), hint)

    cands = ae.find_similar(query, k=k, kind="video", subject=subj_filter)
    cut["rf_candidates"] = [{"asset_id": c["asset_id"], "score": round(c["score"], 3),
                             "scene": c["scene_description"]} for c in cands]
    if not cands:
        cut["rf_clip"] = None
        log.warning("match_cut %s: no candidates for %r (subj=%s)", cut.get("tag"), query, subject)
        return cut

    qv = ae.embed_texts([query])[0]
    from agents.cameraman import _ensure_local
    try:
        from scripts.video_actions import actions as detect_actions
    except Exception:
        detect_actions = None

    # Clip diversity: try candidates NOT yet used by another cut first; only reuse if no distinct
    # option grounds. (The grounding loop below already stops early on a strong match.)
    fresh = [c for c in cands if c["asset_id"] not in exclude_assets]
    ranked = (fresh or cands)

    best = None  # (score, dict-of-fields)
    for cand in ranked[:GROUND_TOPN if ground else 1]:
        local = _ensure_local(cand["file_path"], _uuid_from(cand["asset_id"]))
        if not local or not Path(local).exists():
            log.info("match_cut %s: fetch miss %s", cut.get("tag"), cand["asset_id"])
            continue
        clip_dur = _clip_duration(local)
        acts = []
        if ground and detect_actions is not None:
            try:
                acts = detect_actions(local)
            except Exception as e:
                log.warning("match_cut %s: actions() failed on %s: %s", cut.get("tag"), cand["asset_id"], e)
        ss, dur, act, ascore = _best_window(acts, qv, subject, target_dur, clip_dur)
        # overall score blends the retrieval score and the grounded action match
        score = 0.5 * float(cand["score"]) + 0.5 * (ascore if acts else float(cand["score"]))
        if best is None or score > best[0]:
            best = (score, {"rf_clip": local, "rf_asset_id": cand["asset_id"],
                            "ss": ss, "duration": dur, "clip_dur": round(clip_dur, 2),
                            "rf_action": act, "rf_score": round(score, 3)})
        if acts and ascore >= ACCEPT_SCORE:
            break  # strong grounded match — stop paying for more Gemini calls

    if best is None:
        cut["rf_clip"] = None
        log.warning("match_cut %s: all candidates failed to fetch", cut.get("tag"))
        return cut
    cut.update(best[1])
    cut["rf_source"] = "retrieval"
    # Grounding is what tells us the real clip's ACTION ≠ the beat — flag weak matches so the
    # board surfaces "this beat has no real footage" instead of silently shipping a substitute.
    cut["rf_weak"] = best[0] < ACCEPT_SCORE
    log.info("match_cut %s → %s  ss=%.1f dur=%.1f score=%.2f  (%s)",
             cut.get("tag"), best[1]["rf_asset_id"], best[1]["ss"], best[1]["duration"],
             best[1]["rf_score"], (best[1]["rf_action"] or {}).get("desc", "")[:50])
    return cut


def match_manifest(m: dict, *, progress_cb=None, ground: bool = True) -> dict:
    """Set beat durations, then match every RF cut. AV cuts keep their still-prompt path."""
    set_cut_durations(m)
    rf_cuts = [c for c in m.get("cuts", []) if c.get("cut_type") == "rf"]
    matched = 0
    used_assets: set = set()   # clip diversity across the whole MV
    for i, cut in enumerate(rf_cuts):
        if progress_cb:
            progress_cb(f":mag: [rf-match] {cut.get('tag')} ({i+1}/{len(rf_cuts)}) — {cut.get('clip_query','')[:48]}")
        match_cut(cut, ground=ground, exclude_assets=used_assets)
        if cut.get("rf_clip"):
            matched += 1
            if cut.get("rf_asset_id"):
                used_assets.add(cut["rf_asset_id"])
    if progress_cb:
        progress_cb(f":white_check_mark: [rf-match] {matched}/{len(rf_cuts)} RF cuts matched to real clips")
    return m


if __name__ == "__main__":
    import argparse
    from agents.mv_producer import load_manifest, save_manifest
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--no-ground", action="store_true", help="skip Gemini action grounding (retrieval only)")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    m = load_manifest(a.slug)
    match_manifest(m, progress_cb=print, ground=not a.no_ground)
    if a.save:
        save_manifest(m)
        print("saved", a.slug)
    for c in m["cuts"]:
        if c.get("cut_type") != "rf":
            print(f"[{c['tag']}] AV  dur={c.get('duration')}")
            continue
        print(f"[{c['tag']}] RF  dur={c.get('duration')}  clip={c.get('rf_asset_id')}  "
              f"ss={c.get('ss')}  score={c.get('rf_score')}")
        print(f"    q: {c.get('clip_query')}")
        print(f"    a: {(c.get('rf_action') or {}).get('desc','')}")
