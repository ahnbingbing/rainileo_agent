#!/usr/bin/env python
"""Render TOMORROW's 2 RF slots (2026-06-22) in REVIEW mode — no upload, no Slack.

PD 2026-06-21: hand-make the 2 RF videos for tomorrow's slots now (pet-label
backlog paused for this), Giri-gated. Approved ones get pinned to the RF slots
afterwards so the 03:00 launch batch skips RF and only renders the 2 AV slots.
Runs launch_pipeline with lane_filter=real_footage, do_upload=False, no slack.
"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.launch import launch_pipeline  # noqa: E402

TARGET = dt.date(2026, 6, 22)


def main() -> int:
    def prog(m: str) -> None:
        print(m, flush=True)

    results = launch_pipeline(
        TARGET,
        progress_cb=prog,
        video_cb=None,
        do_upload=False,        # review only — no YouTube schedule
        lane_filter="real_footage",
        slack_client=None,      # no Slack — deliver mp4s out-of-band
        slack_channel=None,
    )
    print("=== RF REVIEW RESULTS ===", flush=True)
    print(json.dumps(results, default=str, ensure_ascii=False, indent=2), flush=True)
    outs = [r.get("output") for r in (results or []) if r and r.get("output")]
    print(f"=== rendered {len(outs)} RF mp4(s) ===", flush=True)
    for o in outs:
        print(o, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
