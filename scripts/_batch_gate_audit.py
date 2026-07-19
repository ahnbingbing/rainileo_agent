"""Post-batch audit of the C14 RF review gates (PD 2026-07-19): after the 03:00 batch,
report which granularity gates FIRED and flag any sign of OVER-DROP, so a misfiring gate
(the retro's recurring trap) is caught the morning after instead of by a manual spot-check.

Gates audited (fire markers logged by cameraman):
  #1 subject-exit tail trim   — "주체 이탈 tail 트림"
  #2 cross-cut coherence drop — "컷간 일관성" / "coherence gate: dropped"
  #4 empty-slot alert         — "빈 슬롯" (launch summary)

For each of the target date's rendered RF/AV cards it reports the final cut count + duration,
and ⚠️-flags an episode where a drop/trim gate fired AND the result looks gutted (≤1 body cut
or < MIN_OK_SEC) — a human should eyeball those. Prints a report; --slack posts it.

  .venv/bin/python -m scripts._batch_gate_audit [--date YYYY-MM-DD] [--slack]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "agent.db"
LAUNCH_LOG = ROOT / "data" / "logs" / "cron.launch.log"
KST = dt.timezone(dt.timedelta(hours=9))
MIN_OK_SEC = float(os.getenv("GATE_AUDIT_MIN_SEC", "14"))

_GATE_MARKERS = {
    "#1 tail-trim": ("주체 이탈 tail 트림", "subject-exit tail trim"),
    "#2 coherence": ("컷간 일관성", "coherence gate: dropped"),
    "#4 empty-slot": ("빈 슬롯",),
}


def _dur(p: str) -> float:
    try:
        return float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip() or 0)
    except Exception:
        return 0.0


def _cut_count_of(card_id: str) -> int:
    for wd in sorted(glob.glob(str(ROOT / "data" / "tmp" / f"cameraman_{card_id[:8]}_*")),
                     reverse=True):
        cj = Path(wd) / "captions.json"
        if cj.exists():
            try:
                c = json.loads(cj.read_text(encoding="utf-8"))
                return len([k for k in c if not k.startswith("_")])
            except Exception:
                pass
    return -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(KST).date().isoformat())
    ap.add_argument("--slack", action="store_true")
    a = ap.parse_args()

    # recent tail of the launch log (the batch that just ran)
    log_tail = ""
    if LAUNCH_LOG.exists():
        try:
            log_tail = LAUNCH_LOG.read_text(errors="ignore")[-200_000:]
        except Exception:
            log_tail = ""
    fired = {name: [ln.strip()[:160] for ln in log_tail.splitlines()
                    if any(m in ln for m in markers)][-6:]
             for name, markers in _GATE_MARKERS.items()}

    con = sqlite3.connect(str(DB)); con.row_factory = sqlite3.Row
    # only the PUBLISHED (scheduled) episodes — the real batch, not self-heal reject alternates.
    rows = con.execute(
        "SELECT card_id, render_style, state, output_video_path FROM cards "
        "WHERE date=? AND render_style IN ('real_footage','ai_vtuber') "
        "AND state='published' AND youtube_video_id IS NOT NULL "
        "ORDER BY youtube_publish_at", (a.date,)).fetchall()
    con.close()

    eps, flags = [], []
    for r in rows:
        n = _cut_count_of(r["card_id"])
        d = _dur(r["output_video_path"]) if r["output_video_path"] else 0.0
        lane = "RF" if r["render_style"] == "real_footage" else "AV"
        eps.append((lane, r["card_id"][:8], n, d))
        # over-drop suspicion only matters for RF (the gates are RF-lane)
        if lane == "RF" and ((0 <= n <= 1) or (0 < d < MIN_OK_SEC)):
            flags.append(f"{lane} {r['card_id'][:8]} — {n}컷 {d:.0f}s")

    n_fired = sum(1 for v in fired.values() if v)
    lines = [f":microscope: *배치 게이트 감사 {a.date}* — 발화 게이트 {n_fired}/3, "
             f"에피소드 {len(eps)}개"]
    for name, hits in fired.items():
        if hits:
            lines.append(f"  • {name} 발화 {len(hits)}회:")
            lines += [f"      {h}" for h in hits[-3:]]
        else:
            lines.append(f"  • {name} — 미발화")
    lines.append("  에피소드 컷/길이: " + ", ".join(f"{l}{c}({n}컷·{d:.0f}s)"
                 for l, c, n, d in eps))
    if flags:
        lines.append(":warning: *과드롭 의심(눈으로 확인)*: " + "; ".join(flags))
    else:
        lines.append("  과드롭 의심 없음 ✅")

    report = "\n".join(lines)
    print(report)
    if a.slack:
        try:
            from slack_sdk import WebClient
            ch = os.environ.get("SLACK_WORKROOM_CHANNEL")
            tok = os.environ.get("SLACK_BOT_TOKEN")
            if ch and tok:
                WebClient(token=tok).chat_postMessage(channel=ch, text=report[:2900])
                print("posted to slack")
        except Exception as e:
            print(f"slack post failed: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
