#!/usr/bin/env python3
"""scripts/pin_episode.py — PIN an already-rendered episode mp4 to a future
(date, lane, timeslot) so the daily launch publishes THAT file at that slot and
does NOT re-render a fresh one.

PD 2026-06-12: PD reviews the explore-heavy launch batches (do_upload off) and
sometimes a test render is great ("이거 6/15 12:30 슬롯에 써도 될 듯"). This promotes
it: it re-targets the episode's `cards` row to the chosen date/slot with
state='rendered', uploaded=0, youtube_publish_at=<slot time>. `launch.py`'s
`_pinned_episode_for` then skips propose+render for that slot and schedules this
file at the slot's publish time (still PD-vetoable in the per-slot Slack thread).

Usage:
    python scripts/pin_episode.py \
        --video data/output/episodes/episode_rf_20260612_164341.mp4 \
        --date 2026-06-15 --slot 12:30 --lane real_footage

The slot must match the lane the Latin square assigns that day (the script warns
if it doesn't). Lanes: ai_vtuber | real_footage.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from agents.launch import publish_at_for, day_assignments  # noqa: E402
from agents.producer import _db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Pin a rendered episode to a slot.")
    ap.add_argument("--video", required=True, help="path to the rendered mp4")
    ap.add_argument("--date", required=True, help="target publish date YYYY-MM-DD")
    ap.add_argument("--slot", required=True, help="timeslot HH:MM, e.g. 12:30")
    ap.add_argument("--lane", required=True,
                    choices=["ai_vtuber", "real_footage", "cartoon_sticker"])
    ap.add_argument("--title", default=None,
                    help="optional YouTube title override (else uses card theme)")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.is_absolute():
        video = (ROOT / video).resolve()
    if not video.exists():
        print(f"❌ video not found: {video}")
        return 1
    target = dt.date.fromisoformat(args.date)

    # Sanity: does the Latin square put `lane` at this slot on this date?
    assigned = dict((hh, ln) for ln, hh in day_assignments(target))
    if assigned.get(args.slot) != args.lane:
        print(f"⚠️  경고: {args.date} {args.slot} 슬롯은 라틴스퀘어상 "
              f"'{assigned.get(args.slot)}' 인데 '{args.lane}'로 핀하려 합니다. "
              f"(레인 강제 오버라이드 — 의도한 게 맞는지 확인)")

    pa = publish_at_for(target, args.slot)
    con = _db()
    row = con.execute(
        "SELECT card_id, theme FROM cards WHERE output_video_path=? "
        "ORDER BY updated_at DESC LIMIT 1", (str(video),)).fetchone()

    if row:
        card_id = row["card_id"]
        con.execute(
            "UPDATE cards SET date=?, render_style=?, state='rendered', uploaded=0, "
            "youtube_video_id=NULL, youtube_publish_at=?, updated_at=datetime('now') "
            "WHERE card_id=?",
            (target.isoformat(), args.lane, pa, card_id))
        theme = args.title or row["theme"] or "(제목 없음)"
        print(f"📌 기존 카드 재타겟: {card_id[:8]} → {args.date} {args.slot} "
              f"({args.lane})  공개예정={pa}\n   제목: {theme}")
    else:
        card_id = str(uuid.uuid4())
        import json as _json
        payload = _json.dumps({"draft": {"title": args.title or "Ryani & Leo"}},
                              ensure_ascii=False)
        con.execute(
            "INSERT INTO cards (card_id, date, created_at, author, card_type, "
            "theme, tone_primary, state, payload_json, render_style, "
            "output_video_path, uploaded, youtube_publish_at, updated_at) "
            "VALUES (?,?,datetime('now'),'pd_pin','daily',?,?, 'rendered', ?, ?, ?, 0, ?, datetime('now'))",
            (card_id, target.isoformat(), args.title or "(pinned)", "warm",
             payload, args.lane, str(video), pa))
        print(f"📌 새 핀 카드 생성: {card_id[:8]} → {args.date} {args.slot} "
              f"({args.lane})  공개예정={pa}")
    con.commit()
    con.close()
    print("✅ 핀 완료. 해당 날짜 런칭 배치가 이 슬롯을 재렌더하지 않고 이 파일을 예약합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
