"""recaption_slot — the CAPTION-PRESERVE re-render (roadmap A1/A2).

When PD's review is a CAPTION fix ("캡션이 안 맞아 / 여러 번 바뀌게 / 고쳐"), the footage
is fine — only the captions are wrong. The full re-render path re-proposes the whole
concept and re-selects footage from the pool, which is how the 7/7 RF1230 re-render
swapped the original 2017 clip for a different 2024 one. This path PRESERVES the slot's
original clips and only refreshes the captions:

  find the slot's card → render_card(use_brain=False, concept=<its own payload>) so the
  SAME clips are pinned and the caption stage (VLM Layer 2) re-grounds per-beat captions
  → reupload to the same slot/publish time (title unchanged = same concept, no stale title).

Use for "캡션 고쳐" reviews. For a genuine "다시 만들어 / 컨셉 변경" use the full re-render
(launch_selfheal), which now also seeds the clip-cooldown from recently-published episodes.

  PYTHONPATH=. .venv/bin/python scripts/recaption_slot.py --date 2026-07-07 --lane real_footage --slot 12:30
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"


def _find_slot_card(con: sqlite3.Connection, target: dt.date, render_style: str, publish_at: str):
    """The card that occupies this slot (most-recently-updated match), even if it was
    archived by a prior un-list. Its payload carries the original clips."""
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM cards WHERE date=? AND render_style=? AND youtube_publish_at=? "
        "ORDER BY updated_at DESC LIMIT 1",
        (target.isoformat(), render_style, publish_at)).fetchone()
    return row


def recaption_slot(target: dt.date, lane: str, slot: str, progress_cb=None) -> dict:
    from agents.launch import publish_at_for
    from agents.cameraman import render_card
    from scripts.reupload_episode import reupload_episode

    publish_at = publish_at_for(target, slot)
    con = sqlite3.connect(DB_PATH)
    card = _find_slot_card(con, target, lane, publish_at)
    if not card:
        con.close()
        raise SystemExit(f"no card for {target} {lane} {slot} (publish_at={publish_at})")
    card_id = card["card_id"]
    concept = json.loads(card["payload_json"] or "{}")
    n_clips = len({c.get("asset_id") for c in concept.get("cuts", []) if c.get("asset_id")})
    # render_card needs an approved card; the slot card is likely 'archived' after an
    # un-list. Flip to approved for this surgical re-render (it re-renders its OWN concept).
    con.execute("UPDATE cards SET state='approved' WHERE card_id=?", (card_id,))
    con.commit(); con.close()

    # ⚠️ CAPTION-PRESERVE MUST NOT RE-RENDER AV. render_card(use_brain=False) re-runs the WHOLE
    # pipeline; for real_footage that's cheap (re-trim the same clip + re-ground captions, no
    # Seedance), but for ai_vtuber it REGENERATES the GPT character stills AND calls Seedance i2v
    # (~$50) — so "캡션만 고쳐" spent $50 and re-rolled the characters (PD 2026-08-27, "왜 캐릭터를
    # 생산해 / seedance 왜 호출해"). The AV lane must instead REUSE the already-rendered cuts and
    # only refresh + re-burn the captions.
    if lane == "ai_vtuber":
        summary = _recaption_av_preserve(card_id, concept, out_target=target, slot=slot,
                                         progress_cb=progress_cb)
        summary["clips_preserved"] = n_clips
        return summary
    if progress_cb:
        progress_cb(f":art: 캡션 보존 재렌더 — 원본 {n_clips}개 클립 그대로, 캡션만 재생성")
    out = render_card(card_id, use_brain=False, concept=concept, progress_cb=progress_cb)
    if progress_cb:
        progress_cb(f":arrow_up: 재업로드 → {slot} 슬롯 (같은 컨셉/제목)")
    summary = reupload_episode(card_id, str(out))
    summary["mode"] = "caption_preserve"
    summary["clips_preserved"] = n_clips
    return summary


def _recaption_av_preserve(card_id: str, concept: dict, *, out_target, slot, progress_cb=None) -> dict:
    """AV caption-preserve: reuse the ALREADY-rendered i2v cuts, refresh only the captions, and
    re-burn — NEVER regen stills or call Seedance. Fails fast (routes to CLI/rebuild) if the
    rendered cuts are gone, rather than falling through to an expensive render."""
    import glob, os, subprocess
    from scripts.reupload_episode import reupload_episode
    from agents.writer_director import run_caption_agent, run_caption_polisher
    # 1) the workdir that actually HAS the rendered cuts (AV render retries leave empty dirs;
    #    picking the newest-by-name would grab an empty one — pick the one with the most cuts).
    cands = [(w, len(glob.glob(os.path.join(w, "animated", "*.mp4"))))
             for w in glob.glob(str(ROOT / "data" / "tmp" / f"cameraman_{card_id[:8]}_*"))]
    cands = [(w, n) for w, n in cands if n > 0]
    if not cands:
        raise SystemExit(f"AV caption-preserve: no rendered cuts found for {card_id[:8]} — "
                         f"footage was cleaned; use full rebuild or a CLI recaption.")
    wd = Path(max(cands, key=lambda x: x[1])[0])
    tags = sorted(Path(p).stem for p in glob.glob(str(wd / "animated" / "*.mp4")))
    if progress_cb:
        progress_cb(f":art: AV 캡션 보존 — 렌더된 {len(tags)}컷 그대로, 캡션만 재생성 (Seedance 호출 없음)")
    # 2) refresh captions via the caption agent (+ any PD direction), on the SAME concept — no render.
    c = dict(concept)
    _dir = os.getenv("PD_RERENDER_DIRECTIVE", "").strip()
    if _dir:
        c["reviewer_feedback"] = _dir  # surfaced to the caption agent / polisher
    captioned = run_caption_agent([c], progress_cb=progress_cb)
    polished = run_caption_polisher(captioned, progress_cb=progress_cb) or captioned
    new_cuts = (polished[0] if polished else c).get("cuts") or []
    # map refreshed captions to the existing cut files IN ORDER (robust to tag naming drift)
    caps: dict = {}
    for tag, cut in zip(tags, new_cuts):
        caps[tag] = {"scenes": cut.get("captions") or []}
    if not caps:
        raise SystemExit("AV caption-preserve: caption agent produced no captions — aborting (no render).")
    cp = wd / "recap_slot_caps.json"
    cp.write_text(json.dumps(caps, ensure_ascii=False, indent=2), encoding="utf-8")
    out = ROOT / "data" / "output" / "episodes" / f"episode_recap_slot_{card_id[:8]}.mp4"
    # 3) re-burn onto the existing cuts + assemble — ffmpeg only, no Seedance
    subprocess.run([str(ROOT / ".venv" / "bin" / "python"), "-m", "scripts.recaption_finish",
                    "--workdir", str(wd), "--captions", str(cp), "--out", str(out)],
                   check=True, cwd=str(ROOT))
    if progress_cb:
        progress_cb(f":arrow_up: 재업로드 → {slot} 슬롯 (영상 그대로, 캡션만)")
    summary = reupload_episode(card_id, str(out))
    summary["mode"] = "caption_preserve_av"
    return summary


def main() -> int:
    import os
    p = argparse.ArgumentParser(description="Caption-preserve re-render of one launch slot")
    p.add_argument("--date", required=True)
    p.add_argument("--lane", required=True, choices=["ai_vtuber", "real_footage"])
    p.add_argument("--slot", required=True, help="HH:MM")
    a = p.parse_args()
    sys.path.insert(0, str(ROOT))

    def _pcb(m):
        print(m, flush=True)
        try:
            from agents.board_progress import post_board_progress
            post_board_progress(m)
        except Exception:
            pass

    r = recaption_slot(dt.date.fromisoformat(a.date), a.lane, a.slot, progress_cb=_pcb)
    print("RECAPTION_RESULT:", json.dumps(r, ensure_ascii=False), flush=True)
    try:
        from agents.board_progress import post_board_progress
        post_board_progress(
            f":white_check_mark: `{a.date} {a.slot}` 캡션 보존 재렌더 완료 — "
            f"새 영상 `{r.get('new_video_id','?')}` {a.slot} 재예약", force=True)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
