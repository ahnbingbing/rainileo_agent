"""agents/still_select.py — Giri picks the best still from a best-of-N batch.

The AV still→Seedance method generates N candidate stills per cut
(`scripts/gen_still_multiref.py`, drift-bounded). PD's rule (2026-06-15):
generate **5 per cut and let Giri pick among them** — selection is the
reviewer's job, not a coin flip or the first plausible frame.

This module is the 5→1 gate. It judges each candidate with the SAME
audience-first lens the episode reviewer uses (notes/shorts_review_agent_giri.md),
grounded in the character canon (agents/canon.py) and the cut's own intent
(what this frame is supposed to set up before Seedance animates it), and
returns the single winner plus per-candidate reasoning.

Giri ranks; pd_taste only *informs* the lens (PD's past picks), it does not
override — the still pick is the reviewer's call.

    from agents import still_select
    pick = still_select.pick_best_still(
        [Path("cut1_1.png"), ...], cut=cut_dict, concept=concept, lane="ai_vtuber")
    winner = pick["winner_path"]
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("agents.still_select")

from agents import canon

try:
    from agents import pd_taste
except Exception:  # pd_taste optional
    pd_taste = None


_LENS = """You are Giri, the channel's reviewer, picking the ONE still that will
become a Seedance i2v first-frame. This frame decides the cut — drift is bounded
to whatever you choose, so pick the candidate that best sets up the shot.

Judge with the audience lens FIRST (would a scroller stop and watch?), then the
floors below. Among candidates that clear the floors, pick the most appealing.

FLOORS (a candidate failing any of these is disqualified unless ALL fail):
1. Character fidelity — markings/age/eyes/breed must match canon. Ryani: petite
   black Frenchie, NO tail, thin blaze + white chin/chest/toes. Leo: orange tabby,
   ~8mo lean young-adult, yellow-green eyes (NOT gold/amber), nose scar. No
   anthropomorphic clothing.
2. Scene-lock — frame must read as the cut's stated location, not a teleport.
3. Intent match — the pose/framing must enable the cut's motion (e.g. a cut whose
   action is "sits up amazed" needs a still where the pose can spring into that).
4. Craft — 9:16 vertical, clean composition, no melted faces/extra limbs/warps.

Return STRICT JSON, no prose. In every string field use ONLY plain ASCII —
no double-quotes, apostrophes, or smart quotes inside the text (they break the
JSON). Keep each verdict under 8 words.
{"winner": <0-based index>,
 "reason": "<one line: why this beats the rest>",
 "candidates": [{"index": <i>, "score": <1-10>, "verdict": "<short>"}, ...]}"""


def _canon_for(subjects: str) -> str:
    block = canon.image_canon(subjects or "")
    return block


def pick_best_still(
    candidates: list[Path | str],
    *,
    cut: dict | None = None,
    concept: dict | None = None,
    lane: str = "ai_vtuber",
    con: sqlite3.Connection | None = None,
) -> dict:
    """VLM-judge N candidate stills for one cut; return the winner.

    Returns {"winner": idx, "winner_path": Path, "reason": str,
             "candidates": [{"index","score","verdict"}, ...]}.
    On any failure falls back to winner=0 (first candidate) so the pipeline
    never blocks on the picker.
    """
    paths = [Path(c) for c in candidates]
    paths = [p for p in paths if p.exists()]
    if not paths:
        raise ValueError("no candidate stills exist")
    if len(paths) == 1:
        return {"winner": 0, "winner_path": paths[0], "reason": "only candidate",
                "candidates": [{"index": 0, "score": 0, "verdict": "sole"}]}

    cut = cut or {}
    subjects = cut.get("subjects") or cut.get("subjects_csv") or ""
    intent = (
        f"Cut beat: {cut.get('beat') or cut.get('tag') or '?'}\n"
        f"Subjects: {subjects or '?'}\n"
        f"Location/scene: {(cut.get('scene') or cut.get('location') or cut.get('motion_prompt') or '')[:300]}\n"
        f"Regen/look: {(cut.get('regen_prompt') or cut.get('regen_direction') or '')[:300]}\n"
        f"Intended motion: {(cut.get('motion_prompt') or '')[:300]}\n"
        f"Caption(s): {' / '.join(c.get('ko','') for c in (cut.get('captions') or []) if isinstance(c, dict))}"
    )

    taste = ""
    if pd_taste is not None and con is not None:
        try:
            taste = pd_taste.taste_digest(con, lane=lane, kind="still") or ""
        except Exception:
            taste = ""

    try:
        from google import genai as _genai
        from google.genai import types as _types
        from PIL import Image
    except Exception as e:
        log.warning("genai/PIL unavailable, defaulting to first still: %s", e)
        return {"winner": 0, "winner_path": paths[0], "reason": "picker unavailable",
                "candidates": [{"index": i, "score": 0, "verdict": ""} for i in range(len(paths))]}

    client = _genai.Client(
        api_key=os.environ["GOOGLE_API_KEY"],
        http_options=_types.HttpOptions(timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))),
    )
    model_name = os.getenv("VLM_MODEL", "gemini-2.5-flash")

    parts = []
    for i, fp in enumerate(paths):
        img = Image.open(fp)
        if img.mode != "RGB":
            img = img.convert("RGB")
        if max(img.size) > 1024:
            r = 1024 / max(img.size)
            img = img.resize((int(img.width * r), int(img.height * r)))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        parts.append(_types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
        parts.append(f"[candidate index {i}]")

    prompt = _LENS + "\n\n## Character canon:\n" + _canon_for(subjects)
    prompt += "\n\n## This cut's intent:\n" + intent
    if concept:
        title = concept.get("title")
        title = title.get("ko") if isinstance(title, dict) else title
        prompt += f"\n\n## Episode concept: {title}"
    if taste:
        prompt += "\n\n## PD taste signal (informs the lens, does NOT override):\n" + taste
    prompt += f"\n\nThere are {len(paths)} candidates (indices 0..{len(paths)-1}). Pick exactly one winner."
    parts.append(prompt)

    try:
        resp = client.models.generate_content(
            model=model_name, contents=parts,
            config=_types.GenerateContentConfig(response_mime_type="application/json"))
        t = (resp.text or "").strip()
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        try:
            data = json.loads(t)
            win = int(data.get("winner", 0))
            reason = data.get("reason", "")
            cands_out = data.get("candidates", [])
        except Exception:
            # Model broke JSON (usually an unescaped quote in a verdict string).
            # Salvage the decision: the winner index is all we strictly need.
            m = re.search(r'"winner"\s*:\s*(\d+)', t)
            win = int(m.group(1)) if m else 0
            reason = "(salvaged from malformed JSON)"
            cands_out = []
            for cm in re.finditer(r'"index"\s*:\s*(\d+)\s*,\s*"score"\s*:\s*(\d+)', t):
                cands_out.append({"index": int(cm.group(1)), "score": int(cm.group(2)), "verdict": ""})
        if not (0 <= win < len(paths)):
            win = 0
        return {"winner": win, "winner_path": paths[win],
                "reason": reason, "candidates": cands_out}
    except Exception as e:
        log.warning("still pick failed (%s); defaulting to first", e)
        return {"winner": 0, "winner_path": paths[0], "reason": f"picker error: {e}",
                "candidates": [{"index": i, "score": 0, "verdict": ""} for i in range(len(paths))]}


def contact_sheet(candidates: list[Path | str], out: Path, *, winner: int | None = None,
                  label: str = "") -> Path:
    """Tile N candidates into one labeled image (winner boxed green)."""
    from PIL import Image, ImageDraw, ImageFont
    paths = [Path(c) for c in candidates if Path(c).exists()]
    if not paths:
        raise ValueError("no candidates to tile")
    thumbs = []
    TW = 360
    for p in paths:
        im = Image.open(p).convert("RGB")
        r = TW / im.width
        thumbs.append(im.resize((TW, int(im.height * r))))
    H = max(t.height for t in thumbs)
    pad, top = 8, 28
    sheet = Image.new("RGB", (len(thumbs) * (TW + pad) + pad, H + top + pad), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    d.text((pad, 6), label, fill=(230, 230, 230), font=font)
    x = pad
    for i, t in enumerate(thumbs):
        sheet.paste(t, (x, top))
        tag = f"#{i}" + (" ★WIN" if winner == i else "")
        col = (80, 230, 120) if winner == i else (200, 200, 200)
        d.text((x + 4, top + 2), tag, fill=col, font=font)
        if winner == i:
            d.rectangle([x, top, x + TW, top + t.height], outline=(80, 230, 120), width=4)
        x += TW + pad
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out
