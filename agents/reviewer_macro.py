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
import datetime as dt
from pathlib import Path

log = logging.getLogger("agents.reviewer_macro")
ROOT = Path(__file__).resolve().parent.parent

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
    user = ("[이번 후보 초안]\n" + "\n---\n".join(_d(c) for c in (drafts or [])) +
            "\n\n[채널 거시 컨텍스트]\n" + macro_context_text(ctx) +
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
        if progress_cb:
            progress_cb(f":satellite: Reviewer(거시): {'통과' if verdict['pass'] else '재작성'}"
                        + (f" — {verdict['rewrite_directive'][:50]}" if not verdict['pass'] else ""))
        log.info("reviewer verdict: pass=%s — %s", verdict["pass"],
                 (verdict["rewrite_directive"] or verdict["macro_notes"])[:120])
        return verdict
    except Exception as e:
        log.warning("reviewer failed (%s) → pass", e)
        return {"pass": True, "rewrite_directive": "", "macro_notes": ""}
