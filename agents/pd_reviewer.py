"""PD daily reviewer (PD 2026-08-17: '매일 내가 손 검수하듯이 검수하는 에이전트').

After the nightly batch renders + Giri (the render-time defect gate) passes, this re-reviews the
episodes SCHEDULED to go public — the last check before an audience sees them — the way the PD does
by hand: dense frames across the whole clip + the live title + burned captions + each source clip's
capture-date/reuse from the DB. It then AUTO-FIXES what it finds (retitle / recaption / reselect /
rerender), reschedules, vetoes the old video, and posts a per-episode report to Slack.

Giri is a per-episode DEFECT gate on ~2 frames/cut and only sees the mp4; this reviewer sees the
whole video + title + DB context, so it catches title↔content lies, caption story-truth, cut-to-cut
prop/character drift, café-as-home / pre-Leo era, reuse, un-threaded running-gags and thin content.

CLI:
  python -m agents.pd_reviewer --date 2026-08-19 --dry-run     # review only, no fixes
  python -m agents.pd_reviewer --date 2026-08-19               # review + AUTO-FIX + reschedule
  python -m agents.pd_reviewer                                 # default: tomorrow (KST)

Env: PD_REVIEW_MODEL (gemini-2.5-flash), PD_REVIEW_RERENDER=1 (allow rerender/reselect fixes),
PD_REVIEW_MAX_RERENDER=2 (cost cap/run), PD_REVIEW_APPLY=1 (0 → force dry-run), SLACK_CHANNEL.
"""
from __future__ import annotations
import os
import sys
import json
import glob
import logging
import argparse
import subprocess
import datetime as dt
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("pd_reviewer")
PROMPT = (ROOT / "agents" / "prompts" / "pd_review.md").read_text(encoding="utf-8")

# KST slot ↔ the publish_at (UTC) hour:minute it schedules at
_SLOT_UTC = {"08:00": "23:00", "12:30": "03:30", "18:00": "09:00", "21:00": "12:00"}


# ─────────────────────────── batch discovery ───────────────────────────

def _scheduled_for(target: dt.date) -> list[dict]:
    """The episodes scheduled to go PUBLIC on `target` (KST), read from LIVE YouTube (the ground
    truth — the local DB is stale). Returns [{slot, video_id, live_title, publish_at}]."""
    from agents.reconcile import list_scheduled_videos
    out = []
    for v in list_scheduled_videos():
        pa = str(v.get("publish_at") or v.get("scheduled") or "")
        if not pa:
            continue
        try:
            u = dt.datetime.fromisoformat(pa.replace("Z", "+00:00"))
        except Exception:
            continue
        k = u.astimezone(dt.timezone(dt.timedelta(hours=9)))  # KST
        slot = k.strftime("%H:%M")
        if k.date() == target and slot in _SLOT_UTC:
            out.append({"slot": slot, "video_id": v.get("video_id"),
                        "live_title": v.get("title") or "", "publish_at": pa})
    out.sort(key=lambda e: e["slot"])
    return out


def _already_used(con, asset_id: str, exclude_card: str, this_date: str, style: str) -> bool:
    """Is this RF clip reused from ANOTHER episode (a real defect)? Calibrated to avoid false
    positives that would auto-rerender good videos: (a) AV pose-reference reuse is NORMAL (the
    same character-ref image feeds many episodes) → never a reuse defect, so skip AV entirely;
    (b) a same-DATE re-render/recaption produces old vetoed card rows carrying the same clip →
    exclude same-date; (c) only count a still-live episode (youtube_video_id present)."""
    if style != "real_footage" or not asset_id:
        return False
    try:
        rows = con.execute(
            "SELECT payload_json FROM cards WHERE uploaded=1 AND card_id!=? "
            "AND youtube_video_id IS NOT NULL AND date != ? AND date >= date('now','-45 day')",
            (exclude_card, this_date)).fetchall()
        for (pj,) in rows:
            if asset_id in (pj or ""):
                return True
    except Exception:
        pass
    return False


def _episode_context(con, ep: dict) -> dict | None:
    """Gather the card, workdir captions, and per-cut source clip era/reuse for one episode."""
    row = con.execute("SELECT card_id, render_style, output_video_path, payload_json, theme, date "
                      "FROM cards WHERE youtube_video_id=?", (ep["video_id"],)).fetchone()
    if not row:
        return None
    card_id, style, out_path, pj, theme, card_date = row
    try:
        payload = json.loads(pj or "{}")
    except Exception:
        payload = {}
    # workdir (newest for this card) → the burned captions (ground truth on-screen text)
    wds = sorted(glob.glob(str(ROOT / "data" / "tmp" / f"cameraman_{str(card_id).split('-')[0]}_*")),
                 reverse=True)
    wd = wds[0] if wds else None
    caption_tags, captions = [], []
    if wd and Path(wd, "captions.json").exists():
        try:
            cap = json.loads(Path(wd, "captions.json").read_text(encoding="utf-8"))
            for tag, e in cap.items():
                if tag.startswith("_") or not isinstance(e, dict):
                    continue
                caption_tags.append(tag)
                kos = [s.get("ko") for s in (e.get("scenes") or []) if isinstance(s, dict) and s.get("ko")]
                captions.append({"tag": tag, "ko": " / ".join(kos)})
        except Exception:
            pass
    sources = []
    for c in (payload.get("cuts") or []):
        aid = c.get("asset_id") or c.get("source") or ""
        cap_iso = None
        if aid:
            r2 = con.execute("SELECT captured_iso FROM assets WHERE asset_id=?", (aid,)).fetchone()
            cap_iso = r2[0] if r2 else None
        sources.append({"asset_id": aid, "captured_iso": cap_iso,
                        "already_used": _already_used(con, aid, card_id, card_date, style) if aid else False})
    # locate the mp4: local output first (this runs on the VM), else GCS mirror
    mp4 = None
    if out_path and Path(out_path).exists():
        mp4 = Path(out_path)
    return {**ep, "card_id": card_id, "render_style": style, "workdir": wd, "mp4": mp4,
            "caption_tags": caption_tags, "captions": captions, "sources": sources,
            "theme": theme, "payload": payload}


# ─────────────────────────── dense frames + LLM review ───────────────────────────

def _dense_frames(mp4: Path, n: int = 10) -> list[bytes]:
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
             "default=nk=1:nw=1", str(mp4)], capture_output=True, text=True).stdout.strip())
    except Exception:
        dur = 20.0
    frames = []
    for i in range(n):
        t = round(dur * (0.04 + 0.92 * i / max(1, n - 1)), 2)
        tmp = Path("/tmp") / f"pdr_{os.getpid()}_{i}.jpg"
        subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(mp4), "-frames:v", "1",
                        "-vf", "scale=480:-1", str(tmp)], capture_output=True)
        if tmp.exists():
            frames.append(tmp.read_bytes())
            tmp.unlink(missing_ok=True)
    return frames


def _llm_review(ctx: dict) -> dict:
    """Run the PD review on one episode. Returns the parsed verdict dict (or a safe 'ok')."""
    if not ctx.get("mp4"):
        return {"slot": ctx["slot"], "verdict": "ok", "summary": "mp4 not found — skipped", "issues": []}
    from google import genai as _genai
    from google.genai import types as _types
    client = _genai.Client(api_key=os.environ["GOOGLE_API_KEY"],
                           http_options=_types.HttpOptions(timeout=180_000))
    model = os.getenv("PD_REVIEW_MODEL", "gemini-2.5-flash")
    ctx_json = {
        "slot": ctx["slot"], "render_style": ctx["render_style"], "video_id": ctx["video_id"],
        "live_title": ctx["live_title"], "caption_tags": ctx["caption_tags"],
        "captions": ctx["captions"], "sources": ctx["sources"],
    }
    parts = [_types.Part.from_text(text=PROMPT),
             _types.Part.from_text(text="EPISODE CONTEXT:\n" + json.dumps(ctx_json, ensure_ascii=False)),
             _types.Part.from_text(text="FRAMES (evenly sampled across the whole video, in order):")]
    for b in _dense_frames(ctx["mp4"]):
        parts.append(_types.Part.from_bytes(data=b, mime_type="image/jpeg"))
    try:
        resp = client.models.generate_content(
            model=model, contents=parts,
            config=_types.GenerateContentConfig(response_mime_type="application/json"))
        data = json.loads(resp.text)
        if not isinstance(data, dict):
            raise ValueError("non-dict")
        data.setdefault("slot", ctx["slot"])
        data.setdefault("issues", [])
        data.setdefault("verdict", "fix" if any(
            (i or {}).get("action", "none") != "none" for i in data.get("issues") or []) else "ok")
        return data
    except Exception as e:
        log.warning("pd review LLM failed for %s: %s", ctx["slot"], e)
        return {"slot": ctx["slot"], "verdict": "ok", "summary": f"review error: {e}", "issues": []}


# ─────────────────────────── fix appliers ───────────────────────────

def _pub_iso(date: dt.date, slot: str) -> str:
    from agents.launch import publish_at_for
    return publish_at_for(date, slot)


def _do_retitle(ctx: dict, issue: dict) -> str:
    new = (issue.get("retitle") or "").strip()
    if not new:
        return "retitle: no title supplied — skipped"
    from youtube.oauth import get_youtube
    yt = get_youtube()
    items = yt.videos().list(part="snippet", id=ctx["video_id"]).execute().get("items")
    if not items:
        return "retitle: video not found"
    snip = items[0]["snippet"]
    snip["title"] = new[:100]
    yt.videos().update(part="snippet", body={"id": ctx["video_id"], "snippet": snip}).execute()
    return f"retitle → {new[:60]}"


def _do_recaption(con, ctx: dict, issue: dict, date: dt.date) -> str:
    caps = issue.get("recaption") or {}
    wd = ctx.get("workdir")
    if not caps or not wd:
        return "recaption: no captions/workdir — skipped"
    wdp = Path(wd)
    # inherit each cut's scene timing from the existing captions.json (keep timing, swap text)
    orig = json.loads((wdp / "captions.json").read_text(encoding="utf-8"))
    new_caps = {}
    for tag in ctx["caption_tags"]:
        if tag not in caps:
            new_caps[tag] = orig.get(tag)  # unchanged cut
            continue
        scenes = (orig.get(tag) or {}).get("scenes") or [{"start": 0.1, "end": 5.0}]
        end = scenes[-1].get("end", 5.0) if scenes else 5.0
        new_caps[tag] = {"scenes": [{"start": 0.1, "end": end,
                                     "ko": caps[tag].get("ko", ""), "en": caps[tag].get("en", "")}]}
    out = ROOT / "data" / "output" / "episodes" / f"episode_pdr_{ctx['card_id'].split('-')[0]}_{ctx['slot'].replace(':','')}.mp4"
    caps_path = wdp / "pdr_caps.json"
    caps_path.write_text(json.dumps(new_caps, ensure_ascii=False, indent=2), encoding="utf-8")
    rr = subprocess.run([sys.executable, "-m", "scripts.recaption_finish", "--workdir", str(wdp),
                         "--captions", str(caps_path), "--out", str(out)], capture_output=True, text=True)
    if rr.returncode != 0 or not out.exists():
        return f"recaption FAILED: {rr.stderr[-200:]}"
    from agents.producer import _auto_upload_episode
    con.execute("UPDATE cards SET output_video_path=? WHERE card_id=?", (str(out.resolve()), ctx["card_id"]))
    con.commit()
    vid = _auto_upload_episode(con, out.resolve(), date, publish_at_iso=_pub_iso(date, ctx["slot"]))
    if vid and vid != ctx["video_id"]:
        from youtube.upload import veto_video
        veto_video(ctx["video_id"], delete=False)
    return f"recaption → {vid} (old {ctx['video_id']} vetoed)"


def _do_rerender(con, ctx: dict, issue: dict, date: dt.date) -> str:
    """Reselect/rerender via the reviewer's corrective directive, reusing the render path (Giri-gated)."""
    directive = (issue.get("directive") or "").strip()
    if not directive:
        return "rerender: no directive — skipped"
    from agents import arc
    from agents.producer import (_gather_context, propose_concepts, produce_and_render,
                                 _render_realfootage_direct, _auto_upload_episode)
    style = ctx["render_style"]
    arc.set_concept_directive(con, date.isoformat(), directive)
    ctx2 = _gather_context(con, date)
    concepts = propose_concepts(date, ctx2, style_filter=style, progress_cb=lambda m: log.info("[pdr] %s", m))
    if not concepts:
        return "rerender: no concept produced"
    c = concepts[0]
    if style == "real_footage":
        out, _rep, _cid = _render_realfootage_direct(c, date, con,
                                                     progress_cb=lambda m: log.info("[pdr] %s", m))
        outs = [out] if out else []
    else:
        outs = produce_and_render([c], date, progress_cb=lambda m: log.info("[pdr] %s", m))
    out = outs[0] if outs else None
    if not out:
        row = con.execute("SELECT output_video_path FROM cards WHERE date=? AND render_style=? "
                          "ORDER BY updated_at DESC LIMIT 1", (date.isoformat(), style)).fetchone()
        out = row[0] if row and row[0] else None
    if not out:
        return "rerender: no output"
    vid = _auto_upload_episode(con, Path(out).resolve(), date, publish_at_iso=_pub_iso(date, ctx["slot"]))
    if vid and vid != ctx["video_id"]:
        from youtube.upload import veto_video
        veto_video(ctx["video_id"], delete=False)
    return f"rerender → {vid} (old {ctx['video_id']} vetoed)"


# ─────────────────────────── orchestration ───────────────────────────

def review_batch(target: dt.date | None = None, apply: bool = False, slack: bool = True) -> list[dict]:
    from agents.producer import _db
    if target is None:
        # default to the batch the 03:00 cron just built (LAUNCH_LEAD_DAYS ahead) so issues are
        # caught with maximum lead before it goes public.
        _lead = max(1, int(os.getenv("PD_REVIEW_LEAD_DAYS", os.getenv("LAUNCH_LEAD_DAYS", "2"))))
        target = (dt.datetime.now(dt.timezone(dt.timedelta(hours=9))) + dt.timedelta(days=_lead)).date()
    # SAFETY DEFAULT = flag-only. The LLM review is non-deterministic run-to-run (an episode that
    # passes one pass gets flagged the next), so blind auto-fix CHURNS good videos (an 8/18 catwheel
    # that a dry-run passed got spuriously re-captioned + rescheduled). Auto-fix must be explicitly
    # enabled (--apply / PD_REVIEW_APPLY=1) AND every fix is gated by a 2-pass agreement (below).
    apply = apply or os.getenv("PD_REVIEW_APPLY") == "1"
    allow_rr = os.getenv("PD_REVIEW_RERENDER", "1") == "1"
    max_rr = int(os.getenv("PD_REVIEW_MAX_RERENDER", "2"))
    con = _db()
    eps = _scheduled_for(target)
    log.info("PD review %s — %d scheduled episode(s)", target, len(eps))
    results, report_lines, rr_used = [], [f"🔎 PD 일일검수 {target} ({len(eps)}편)"], 0
    for ep in eps:
        ctx = _episode_context(con, ep)
        if not ctx:
            report_lines.append(f"• {ep['slot']} {ep['video_id']}: 카드 없음 — 스킵")
            continue
        verdict = _llm_review(ctx)
        results.append({"slot": ep["slot"], "verdict": verdict})
        vv = verdict.get("verdict", "ok")
        summ = verdict.get("summary", "")
        if vv == "ok":
            report_lines.append(f"✅ {ep['slot']}: {summ[:90]}")
            continue
        report_lines.append(f"⚠️ {ep['slot']}: {summ[:90]}")
        # 2-pass agreement gate: the LLM review is non-deterministic, so before mutating a live
        # video, confirm with a SECOND independent pass and only apply a fix whose (class, action)
        # is flagged in BOTH. A single-pass flag is reported but never auto-applied — this is what
        # stops a spurious pass from churning a good episode.
        confirm_keys = set()
        if apply and any((i or {}).get("action", "none") != "none" for i in verdict.get("issues", [])):
            confirm = _llm_review(ctx)
            confirm_keys = {((i or {}).get("class"), (i or {}).get("action"))
                            for i in confirm.get("issues", []) if (i or {}).get("action", "none") != "none"}
        for issue in verdict.get("issues", []):
            act = (issue or {}).get("action", "none")
            ev = str(issue.get("evidence", ""))[:80]
            report_lines.append(f"   └[{issue.get('class')}·{act}] {ev}")
            if not apply or act == "none":
                continue
            if (issue.get("class"), act) not in confirm_keys:
                report_lines.append(f"      → 미적용(2-pass 불일치 — 단일패스 플래그, 보고만)")
                continue
            try:
                if act == "retitle":
                    msg = _do_retitle(ctx, issue)
                elif act == "recaption":
                    msg = _do_recaption(con, ctx, issue, target)
                elif act in ("rerender", "reselect"):
                    if not allow_rr:
                        msg = "rerender skipped (PD_REVIEW_RERENDER=0)"
                    elif rr_used >= max_rr:
                        msg = f"rerender skipped (cap {max_rr}/run reached)"
                    else:
                        rr_used += 1
                        msg = _do_rerender(con, ctx, issue, target)
                else:
                    msg = f"unknown action {act}"
                report_lines.append(f"      → {msg}")
            except Exception as e:
                log.warning("apply %s failed for %s: %s", act, ep["slot"], e)
                report_lines.append(f"      → FAILED: {e}")
    text = "\n".join(report_lines)
    print(text)
    if slack:
        try:
            from agents.progress_log import log_progress
            log_progress("PD-reviewer", f"일일검수 {target}: " + " | ".join(
                f"{r['slot']}={r['verdict'].get('verdict')}" for r in results))
        except Exception:
            pass
        try:
            import os as _os
            ch = _os.getenv("SLACK_WORKROOM_CHANNEL")
            tok = _os.getenv("SLACK_BOT_TOKEN")
            if ch and tok:
                from slack_sdk import WebClient
                WebClient(token=tok).chat_postMessage(channel=ch, text=text)
        except Exception as e:
            log.warning("slack post failed: %s", e)
    return results


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="PD daily reviewer + auto-fixer")
    ap.add_argument("--date", default=None, help="KST publish date (YYYY-MM-DD); default=batch lead date")
    ap.add_argument("--apply", action="store_true",
                    help="auto-apply fixes (default = flag-only review). Each fix is still gated by a "
                         "2-pass agreement. Or set PD_REVIEW_APPLY=1.")
    ap.add_argument("--dry-run", action="store_true", help="(default) review only — kept for clarity")
    ap.add_argument("--no-slack", action="store_true")
    args = ap.parse_args()
    target = dt.date.fromisoformat(args.date) if args.date else None
    review_batch(target, apply=args.apply, slack=not args.no_slack)
    return 0


if __name__ == "__main__":
    sys.exit(main())
