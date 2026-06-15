"""Produce + render ONE real_footage episode for a target date and print the mp4 path.

Uses the normal RF pipeline (concept-brainstorm → realfootage singlepass →
_render_realfootage_direct) with today's improved RF caption prompts. No upload here
— PD reviews + we pin it to a launch slot.

  .venv/bin/python -m scripts.render_rf_one
"""
import datetime as dt
from agents.producer import _db, _gather_context, propose_concepts, _render_realfootage_direct

TARGET = dt.date(2026, 6, 16)


def pcb(m):
    print("P:", m, flush=True)


def main():
    con = _db()
    print("gathering context...", flush=True)
    context = _gather_context(con, TARGET)
    print("proposing real_footage concept...", flush=True)
    concepts = propose_concepts(TARGET, context, style_filter="real_footage", progress_cb=pcb)
    if not concepts:
        print("NO CONCEPT", flush=True); return
    c = concepts[0]
    t = c.get("title"); t = t.get("ko") if isinstance(t, dict) else t
    print("TITLE:", t, flush=True)
    out, report, card_id = _render_realfootage_direct(c, TARGET, con, progress_cb=pcb)
    print("CARD:", card_id, flush=True)
    print("RENDER_OUT:", out, flush=True)


if __name__ == "__main__":
    main()
