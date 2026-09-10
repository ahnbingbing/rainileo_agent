"""B4 — grammar-copy Writer (Phase B).

Given a target edit_grammar (velocity/meme/story) and a candidate clip pool (each clip with its
grounded VLM fields), this Writer does two jobs — CAST + COPY:

  1. CAST : assign clips → the grammar's 5 structural roles (soccer/play1/swim/play2/belly) by
            CONTENT fit (high-motion → climax/payoff role, calm → anchor role), preferring a
            coherent setting/arc (do not mix indoor+outdoor unless intentional).
  2. COPY : write grammar-appropriate KO/EN captions (+ TTS narration for story), one per beat,
            GROUNDED to the assigned clip's scene description — never invent an event/sound the
            clip doesn't show (same anti-fabrication discipline as the RF single-pass writer).

Output feeds `scripts.impact_edit.render_grammar(grammar, out, clips=<clips>, copy=<copy>)`.
The ENGINE owns motion-window pick + timing + slow-mo; this Writer owns which clip plays when and
what it says. This module does NOT touch the live render path — it is consumed by the B4 dry-run
harness until PD signs off on quality (then B2/B3/B5 wire it into launch + cameraman).
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("agents.edit_grammar_writer")

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "edit_grammar_copy.md"

ROLES = ["soccer", "play1", "swim", "play2", "belly"]

# Per-grammar contract the Writer must satisfy — injected into the prompt so the model produces
# EXACTLY the shape each grammar's build_* consumes (see scripts/impact_edit.py build_* docstrings).
GRAMMAR_SPEC = {
    "velocity": {
        "shape": '{"clips": {<role>: <asset_id> ...}, "copy": {"captions": [{"ko": "<hook>"}, {"ko": "<drop>"}]}}',
        "beats": ("2 short KO-only punch lines over a fast beat-synced montage: "
                  "[0] HOOK (opening energy title), [1] DROP (the peak/climax hit). Big, punchy, ≤10 chars each."),
    },
    "meme": {
        "shape": '{"clips": {<role>: <asset_id> ...}, "copy": {"captions": [{"ko": "..","en": ".."} × 7]}}',
        "beats": ("7 reaction-meme captions IN ORDER, each bilingual (short KO + short EN), over jump-cuts/"
                  "zoom-punches/freezes: [0] HOOK (our-house-energy title), [1] first ?!?! reaction, "
                  "[2] caught-in-4K freeze, [3] hold-up beat, [4] enter-the-other-pet, [5] that pet's deadpan denial, "
                  "[6] AGAIN?! climax. Punchy internet-meme voice; KO ≤12 chars, EN ≤18 chars."),
    },
    "story": {
        "shape": ('{"clips": {<role>: <asset_id> ...}, "copy": {"beats": [{"role": <role>, "kind": '
                  '"cold_open|payoff|calm|normal", "ko": "..", "ko2": "..(optional 2nd line)", '
                  '"narration": "..(short TTS line)", "box": true|false} ...]}}'),
        "beats": ("A PAYOFF-FIRST arc of 7-9 beats. The FIRST beat kind=cold_open shows the RESULT/climax "
                  "(the most dramatic clip) with a 'why is this happening?' hook. Then REWIND (kind=calm) and "
                  "BUILD the cause across the remaining clips, and near the end kind=payoff RETURNS to the climax "
                  "clip in slow-mo, then a short RESOLVE. Each beat.role must be one you cast in clips. "
                  "narration = ONE short spoken sentence (it is TTS — keep it SHORT so it fits its scene). "
                  "ko = on-screen caption (short). box=true only for punchy title/hook beats."),
    },
}


def _pool_for_prompt(pool: list[dict]) -> list[dict]:
    """Trim each candidate clip to the grounded fields the Writer needs to cast + ground copy."""
    out = []
    for a in pool:
        out.append({
            "asset_id": a.get("asset_id"),
            "sc": (a.get("scene_description") or a.get("sc") or "")[:220],
            "activity": a.get("activity"),
            "subjects": a.get("subjects_csv") or a.get("subjects"),
            "dur": round(float(a.get("duration_sec") or a.get("dur") or 0), 1),
            "loc": a.get("location_type") or a.get("loc"),
        })
    return out


def _robust_parse(text: str) -> dict:
    """Reuse the pipeline's balanced-brace JSON parser (handles prose preamble / KO brackets /
    fences — the D_nonjsonparse fix). Falls back to a plain slice."""
    try:
        from agents.producer import _robust_json_parse
        v = _robust_json_parse(text)
        if isinstance(v, list):
            v = next((x for x in v if isinstance(x, dict)), {})
        return v if isinstance(v, dict) else {}
    except Exception:
        s, e = text.find("{"), text.rfind("}")
        return json.loads(text[s:e + 1]) if s >= 0 and e > s else {}


def propose_grammar_copy(grammar: str, pool: list[dict], *, slot_hhmm: str | None = None,
                         model: str | None = None) -> dict:
    """Cast clips + write grounded copy for one grammar. Returns {"clips": {...}, "copy": {...}}.
    Raises ValueError on an unknown grammar; raises on parse failure so the dry-run surfaces it."""
    if grammar not in GRAMMAR_SPEC:
        raise ValueError(f"unknown grammar: {grammar}")
    from agents import prompt_loader as _pl
    from agents import llm_cascade as _llm

    spec = GRAMMAR_SPEC[grammar]
    system = _pl.load(_PROMPT_PATH)
    user = json.dumps({
        "grammar": grammar,
        "roles": ROLES,
        "beat_structure": spec["beats"],
        "output_shape": spec["shape"],
        "slot_hhmm": slot_hhmm,
        "candidate_clips": _pool_for_prompt(pool),
    }, ensure_ascii=False)

    raw = _llm.call_text_cached(system, user, max_tokens=6000,
                                model=model or os.getenv("GRAMMAR_WRITER_MODEL"))
    obj = _robust_parse(raw)
    clips = obj.get("clips") or {}
    copy = obj.get("copy") or {}
    if not clips or not copy:
        raise RuntimeError(f"grammar writer returned incomplete output (clips={bool(clips)}, copy={bool(copy)})")
    # every role referenced by story beats must have a cast clip
    if grammar == "story":
        missing = {b.get("role") for b in copy.get("beats", [])} - set(clips)
        if missing:
            raise RuntimeError(f"story beats reference uncast roles: {missing}")
    return {"clips": clips, "copy": copy}
