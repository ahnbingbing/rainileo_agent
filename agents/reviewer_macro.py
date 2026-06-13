"""Macro Reviewer agent (PD 2026-06-13).

A SECOND opinion that complements Giri (single-episode, post-render, tactical QC).
The Reviewer is MACRO + audience-aware: it reads real YouTube comments, recent
performance, and the LAST ~7 days of episodes, and judges a Writer DRAFT for
audience-fit and FRESHNESS (is this too similar to what we already shipped?). Its
feedback flows back to the Writer for up to N rewrites, and its macro guidance is
also injected into the Writer's initial draft.

Public API:
  fetch_macro_context(con, days=7) -> dict          # once per run; cache it
  macro_context_text(ctx) -> str                    # compact text for prompts
  run_reviewer(drafts, ctx, lane) -> dict           # {pass, rewrite_directive, macro_notes}

All sources are best-effort: on a cold start (no uploads / no comments) the context
is empty and the Reviewer passes (never blocks the very first episodes).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import datetime as dt
from pathlib import Path

log = logging.getLogger("agents.reviewer_macro")
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()

_CACHE: dict = {}  # keyed by (date, days) so a launch run fetches once


def _recent_episodes(con, days: int) -> list[dict]:
    """Title/theme/asset_ids of RF+AV episodes touched in the last `days` days — for
    freshness / overlap judgement."""
    out: list[dict] = []
    try:
        rows = con.execute(
            "SELECT created_at, render_style, theme, payload_json, uploaded "
            "FROM cards WHERE created_at >= datetime('now', ?) "
            "ORDER BY created_at DESC LIMIT 60", (f"-{days} days",)).fetchall()
        for created, style, theme, pj, uploaded in rows:
            try:
                pl = json.loads(pj or "{}")
            except Exception:
                pl = {}
            title = pl.get("title") or theme or ""
            if isinstance(title, dict):
                title = title.get("ko") or ""
            aids = [c.get("asset_id") for c in (pl.get("cuts") or []) if c.get("asset_id")]
            out.append({
                "date": (created or "")[:10],
                "lane": style or "",
                "title": title,
                "oneliner": (pl.get("narrative_oneliner") or "")[:80],
                "asset_ids": aids,
                "uploaded": bool(uploaded),
            })
    except Exception as e:
        log.warning("recent episodes fetch failed: %s", e)
    return out


def _performance(con, limit: int = 12) -> list[dict]:
    try:
        rows = con.execute(
            "SELECT video_id, lane, timeslot, views_48h, retention_pct "
            "FROM video_performance ORDER BY publish_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"video_id": v, "lane": l, "timeslot": t,
                 "views_48h": vi, "retention_pct": r} for v, l, t, vi, r in rows]
    except Exception as e:
        log.info("performance fetch skipped: %s", e)
        return []


def _comments(con, max_videos: int = 6, per_video: int = 8) -> list[str]:
    """Top recent YouTube comments on our last few uploads (audience voice)."""
    if os.getenv("REVIEWER_FETCH_COMMENTS", "1") == "0":
        return []
    vids = []
    try:
        vids = [r[0] for r in con.execute(
            "SELECT youtube_video_id FROM cards WHERE youtube_video_id IS NOT NULL "
            "AND uploaded=1 ORDER BY youtube_publish_at DESC LIMIT ?", (max_videos,)).fetchall()
            if r[0]]
    except Exception:
        vids = []
    if not vids:
        return []
    out: list[str] = []
    try:
        from youtube.oauth import get_youtube
        yt = get_youtube()
        for vid in vids:
            try:
                resp = yt.commentThreads().list(
                    part="snippet", videoId=vid, maxResults=per_video,
                    order="relevance", textFormat="plainText").execute()
                for it in resp.get("items", []):
                    txt = (it["snippet"]["topLevelComment"]["snippet"]
                           .get("textDisplay") or "").strip().replace("\n", " ")
                    if txt:
                        out.append(txt[:160])
            except Exception as e:
                log.info("comments for %s skipped: %s", vid, e)
    except Exception as e:
        log.info("YouTube comments unavailable: %s", e)
    return out[:40]


def fetch_macro_context(con, days: int = 7) -> dict:
    """Fetch (once, cached) the macro context: recent episodes + performance +
    audience comments. Cold-start safe (empty pieces)."""
    key = (dt.date.today().isoformat() if False else "_run", days)  # no Date.now in tests
    if key in _CACHE:
        return _CACHE[key]
    ctx = {
        "recent_episodes": _recent_episodes(con, days),
        "performance": _performance(con),
        "comments": _comments(con),
        "days": days,
    }
    _CACHE[key] = ctx
    log.info("macro context: %d recent eps, %d perf rows, %d comments",
             len(ctx["recent_episodes"]), len(ctx["performance"]), len(ctx["comments"]))
    return ctx


def macro_context_text(ctx: dict) -> str:
    """Compact human-readable macro context for the Writer/Reviewer prompts."""
    if not ctx:
        return ""
    lines: list[str] = []
    eps = ctx.get("recent_episodes") or []
    if eps:
        lines.append(f"[최근 {ctx.get('days', 7)}일 에피소드 — 중복 회피용]")
        for e in eps[:20]:
            lines.append(f"- {e['date']} {e['lane']}: {e['title']} | {e['oneliner']}")
    perf = ctx.get("performance") or []
    if perf:
        lines.append("\n[최근 성과 (views_48h / retention%)]")
        for p in perf[:10]:
            lines.append(f"- {p['lane']} {p['timeslot']}: {p['views_48h']} views, "
                         f"{p['retention_pct']}% ret")
    cm = ctx.get("comments") or []
    if cm:
        lines.append("\n[시청자 댓글 (오디언스 목소리)]")
        for c in cm[:20]:
            lines.append(f"- {c}")
    return "\n".join(lines).strip()


def is_empty(ctx: dict) -> bool:
    return not (ctx and (ctx.get("recent_episodes") or ctx.get("performance")
                         or ctx.get("comments")))


_REVIEWER_PROMPT = ROOT / "agents" / "prompts" / "reviewer_agent.md"


# ──────────────────────────────────────────────────────────────────────
# Quantitative visual-overlap (PD 2026-06-13)
# ──────────────────────────────────────────────────────────────────────
# PD: "리뷰어가 볼 것은 컨셉이 아니고 동영상 사이의 유사성이야. 유사한 컷이 얼마나 많은지를
# 기준으로." The concept-text reviewer can't see that the FOOTAGE repeats — count it.
#
# Calibration finding: perceptual hashing CANNOT reliably tell a same-scene
# clip from a random one on our moving-pet footage (same-moment median 110 vs random
# 114 / 256; a loose threshold false-positives ~17%). So pixel phash is used only at
# a TIGHT threshold (high precision) as confirmation. The PRIMARY signal is SEMANTIC
# OVER-REPRESENTATION: a cut "repeats" if its (location_type, activity) is something
# recent uploads already lean on heavily (e.g. (home, resting) = 32% of last-week cuts
# — the literal "집/실내 휴식" complaint). Mere membership is too coarse (6 locations ×
# ~15 activities saturate over 50+ episodes); the FREQUENCY share is the real signal.
# VLM tags cover ~all assets, so this has no iCloud coverage gap.
_VIS_TIGHT = int(os.getenv("REVIEWER_VHASH_TIGHT", "96"))
# A (loc, act) pair counts as over-represented if it is ≥ this share of recent cuts.
# ~15% ≈ 4–5× a uniform baseline (~30 realistic combos), so only genuinely over-used
# pairs flag; (outdoor, walking) at ~10% passes, (home, resting) at ~32% does not.
_OVERUSE_FRAC = float(os.getenv("REVIEWER_OVERUSE_FRAC", "0.15"))
# A LOCATION alone is over-represented if it is ≥ this share of recent cuts. Catches
# "전부 집안" monotony that the (loc,act) check misses (home is ~56% of recent cuts →
# over-used; outdoor ~28% → fine). This is the literal "집/실내" half of PD's complaint.
_LOC_OVERUSE_FRAC = float(os.getenv("REVIEWER_LOC_OVERUSE_FRAC", "0.40"))


def _cut_asset_ids(draft: dict) -> list[str]:
    return [c.get("asset_id") for c in (draft.get("cuts") or []) if c.get("asset_id")]


def visual_overlap(drafts: list, ctx: dict, *, tight: int | None = None) -> dict:
    """Count draft cuts that repeat recently-used footage.

    Returns {comparable, repeats, repeat_ratio, pixel_dups, examples, recent_cuts,
    overused}.
      comparable — draft cuts with a known (location, activity) to judge
      repeats    — of those, how many reuse an OVER-REPRESENTED (location, activity)
                   from recent uploads (drives the override) OR are a tight pixel dup
      pixel_dups — draft cuts confirmed near-IDENTICAL to a recent clip (tight phash)
      overused   — the over-represented (loc, act) pairs and their recent share
    Fail-safe → zeros on any error."""
    empty = {"comparable": 0, "repeats": 0, "repeat_ratio": 0.0, "pixel_dups": 0,
             "examples": [], "recent_cuts": 0, "overused": [], "overused_loc": []}
    try:
        from collections import Counter
        from agents.visual_hash import hamming as _ham
        tt = _VIS_TIGHT if tight is None else tight
        recent_ids = [aid for e in (ctx.get("recent_episodes") or [])
                      for aid in (e.get("asset_ids") or [])]
        draft_ids: list[str] = []
        for d in (drafts or []):
            draft_ids += _cut_asset_ids(d)
        draft_ids = list(dict.fromkeys(draft_ids))  # de-dup, keep order
        if not recent_ids or not draft_ids:
            return empty
        want = set(recent_ids) | set(draft_ids)
        con = sqlite3.connect(str(DB_PATH), timeout=30)
        try:
            ph = "(" + ",".join("?" for _ in want) + ")"
            rows = con.execute(
                f"SELECT asset_id, location_type, activity, vis_phash "
                f"FROM assets WHERE asset_id IN {ph}", list(want)).fetchall()
        finally:
            con.close()
        meta = {a: {"loc": loc, "act": act, "h": h} for a, loc, act, h in rows}
        # OVER-REPRESENTATION: count recent cut (loc,act) frequency; a pair is overused
        # if its share ≥ _OVERUSE_FRAC. (frequency, not membership — see module note.)
        recent_la = [(meta[a]["loc"], meta[a]["act"]) for a in recent_ids
                     if a in meta and meta[a]["loc"] and meta[a]["act"]]
        freq = Counter(recent_la)
        tot_la = sum(freq.values()) or 1
        overused = {la for la, n in freq.items() if n / tot_la >= _OVERUSE_FRAC}
        # location-level over-representation (catches all-one-place monotony)
        loc_freq = Counter(loc for loc, _ in recent_la)
        overused_loc = {loc for loc, n in loc_freq.items()
                        if n / tot_la >= _LOC_OVERUSE_FRAC}
        recent_h = [meta[a]["h"] for a in set(recent_ids) if a in meta and meta[a]["h"]]
        comparable = repeats = pixel_dups = 0
        examples: list[dict] = []
        for a in draft_ids:
            m = meta.get(a)
            if not m or not (m["loc"] and m["act"]):
                continue
            comparable += 1
            sem = (m["loc"], m["act"]) in overused or m["loc"] in overused_loc
            pix = None
            if m["h"] and recent_h:
                pix = min((d for d in (_ham(m["h"], rh) for rh in recent_h)
                           if d is not None), default=None)
            is_pixdup = pix is not None and pix <= tt
            if is_pixdup:
                pixel_dups += 1
            if sem or is_pixdup:
                repeats += 1
                if len(examples) < 4:
                    examples.append({"asset_id": a, "loc": m["loc"], "act": m["act"],
                                     "semantic": sem, "pixel_dist": pix})
        return {
            "comparable": comparable,
            "repeats": repeats,
            "repeat_ratio": round(repeats / comparable, 2) if comparable else 0.0,
            "pixel_dups": pixel_dups,
            "examples": examples,
            "recent_cuts": len(recent_ids),
            "overused": sorted(((f"{loc}/{act}", round(freq[(loc, act)] / tot_la, 2))
                                for (loc, act) in overused),
                               key=lambda x: -x[1]),
            "overused_loc": sorted(((loc, round(loc_freq[loc] / tot_la, 2))
                                    for loc in overused_loc), key=lambda x: -x[1]),
        }
    except Exception as e:
        log.warning("visual_overlap failed: %s", e)
        return empty


def run_reviewer(drafts: list, ctx: dict, lane: str, progress_cb=None) -> dict:
    """Macro review of the Writer's draft concept(s). Returns
    {pass: bool, rewrite_directive: str, macro_notes: str}. Cold-start (empty macro
    context) → pass. Fail-safe → pass (never block on a Reviewer error)."""
    if os.getenv("REVIEWER_AGENT", "1") == "0" or is_empty(ctx):
        return {"pass": True, "rewrite_directive": "", "macro_notes": "(macro context empty — pass)"}
    try:
        system = _REVIEWER_PROMPT.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("reviewer prompt unreadable: %s", e)
        return {"pass": True, "rewrite_directive": "", "macro_notes": ""}
    # Compact the drafts to titles + onelines + beats so the Reviewer judges direction.
    def _d(c):
        t = c.get("title")
        t = t.get("ko") if isinstance(t, dict) else (t or "")
        beats = " / ".join((cut.get("beat") or cut.get("action") or "")[:40]
                            for cut in (c.get("cuts") or [])[:6])
        return f"제목: {t}\n한줄: {c.get('narrative_oneliner','')}\n비트: {beats}"
    # PD 2026-06-13: objective visual-overlap — how many draft cuts LOOK like recently
    # used footage (best-frame perceptual-hash distance), independent of the concept text.
    vo = visual_overlap(drafts, ctx)
    vo_line = ""
    if vo["comparable"]:
        vo_line = (f"\n\n[footage 중복 — 객관 지표]\n"
                   f"- 비교가능 컷 {vo['comparable']}개 중 {vo['repeats']}개가 최근 업로드와 "
                   f"같은 (장소+활동)을 재사용(중복률 {vo['repeat_ratio']}). "
                   f"그중 {vo['pixel_dups']}개는 화면까지 거의 동일(pixel near-dup).\n"
                   f"- 이건 '컨셉'이 아니라 실제 footage 반복이다. 중복률이 높으면 다른 장소/활동의 클립을 쓰라.")
    user = ("[이번 후보 초안]\n" + "\n---\n".join(_d(c) for c in (drafts or [])) +
            "\n\n[채널 거시 컨텍스트]\n" + macro_context_text(ctx) + vo_line +
            "\n\nlane: " + lane + "\n\n위 초안을 거시 관점에서 검수하고 JSON으로 답하라.")
    try:
        from agents.llm_cascade import call_text_cascade
        import re as _re
        txt = call_text_cascade(system, user, max_tokens=500).strip()
        txt = _re.sub(r"^```(?:json)?\s*", "", txt)
        txt = _re.sub(r"\s*```$", "", txt)
        d = json.loads(txt)
        if isinstance(d, list):
            d = next((x for x in d if isinstance(x, dict)), {})
        verdict = {
            "pass": bool(d.get("pass", True)),
            "rewrite_directive": (d.get("rewrite_directive") or "").strip(),
            "macro_notes": (d.get("macro_notes") or "").strip(),
        }
        # PD 2026-06-13: the DECISION criterion is footage similarity, not concept text.
        # So the objective footage-overlap number is decisive at both ends, and the LLM's
        # thematic read only breaks ties in the middle:
        #   • ratio ≥ FAIL_RATIO  → FAIL even if the LLM passed (footage too repetitive)
        #   • ratio ≤ PASS_RATIO  → PASS even if the LLM failed (footage is FRESH — don't
        #     let the LLM churn on "테마가 비슷" when the actual video is different)
        # Both need enough comparable cuts to trust the number.
        min_cmp = int(os.getenv("REVIEWER_VHASH_MIN_COMPARABLE", "2"))
        fail_ratio = float(os.getenv("REVIEWER_VHASH_FAIL_RATIO", "0.5"))
        pass_ratio = float(os.getenv("REVIEWER_VHASH_PASS_RATIO", "0.34"))
        pass_min_cmp = int(os.getenv("REVIEWER_VHASH_PASS_MIN_COMPARABLE", "3"))
        if (verdict["pass"] and vo["comparable"] >= min_cmp
                and vo["repeat_ratio"] >= fail_ratio):
            verdict["pass"] = False
            od = (f"footage 중복 과다: 비교 {vo['comparable']}컷 중 {vo['repeats']}컷이 최근 "
                  f"업로드와 같은 (장소+활동) 재사용(중복률 {vo['repeat_ratio']}, "
                  f"화면동일 {vo['pixel_dups']}컷). 해당 컷을 다른 장소/활동/시기의 클립으로 교체하라.")
            verdict["rewrite_directive"] = (
                (verdict["rewrite_directive"] + " " if verdict["rewrite_directive"] else "") + od)
            verdict["macro_notes"] = (verdict.get("macro_notes") or "") + " [vhash-override:fail]"
            log.info("reviewer vhash override → fail (ratio=%s comparable=%d)",
                     vo["repeat_ratio"], vo["comparable"])
        elif (not verdict["pass"] and vo["comparable"] >= pass_min_cmp
                and vo["repeat_ratio"] <= pass_ratio):
            verdict["pass"] = True
            verdict["rewrite_directive"] = ""
            verdict["macro_notes"] = ((verdict.get("macro_notes") or "")
                                      + f" [vhash-override:pass — footage fresh, "
                                        f"중복률 {vo['repeat_ratio']}]")
            log.info("reviewer vhash override → pass (fresh footage ratio=%s comparable=%d; "
                     "LLM theme-reject demoted)", vo["repeat_ratio"], vo["comparable"])
        if progress_cb:
            extra = ""
            if vo["comparable"]:
                extra = f" · footage중복 {vo['repeats']}/{vo['comparable']}"
            progress_cb(f":satellite: Reviewer(거시): {'통과' if verdict['pass'] else '재작성'}"
                        + (f" — {verdict['rewrite_directive'][:50]}" if not verdict['pass'] else "")
                        + extra)
        log.info("reviewer verdict: pass=%s — %s (vo=%s)", verdict["pass"],
                 (verdict["rewrite_directive"] or verdict["macro_notes"])[:120],
                 {k: vo[k] for k in ("comparable", "repeats", "repeat_ratio")})
        return verdict
    except Exception as e:
        log.warning("reviewer failed (%s) → pass", e)
        return {"pass": True, "rewrite_directive": "", "macro_notes": ""}
