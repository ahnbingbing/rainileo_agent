"""agents/channel_manager.py — Channel Manager agent.

Giri reviews ONE episode through the audience's eyes; the Channel Manager runs
the WHOLE channel with data. It owns three things the pipeline was missing:

  Phase 1 (here): PACKAGING — generate a hook YouTube title + SEO description +
    concept-specific hashtags per episode, replacing the static "스토리 제목 +
    고정 3태그" defaults. The 3 packaging tones are EXPERIMENT ARMS rotated per
    episode (like the launch-month RF/AV A/B) and learned until one stabilizes.
  Phase 2: close the bandit loop (choose_lane/timeslot actually steer launch).
  Phase 3: live-channel-state recommendations ("어디 더 넣을지").

Design: notes/channel_manager_design.md. Reuses agents/llm_cascade for the LLM
and youtube/upload boundary unchanged — we only write richer values into
cards.payload_json.draft.{title,description,hashtags}.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

from agents.llm_cascade import call_text_cascade

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"
_PROMPT = ROOT / "agents" / "prompts" / "channel_manager_packaging.md"

# Packaging experiment arms. Rotated per-episode (Phase 1 round-robin), later
# Thompson-sampled (Phase 2). When P(best)≥θ & n≥N the dimension "stabilizes" and
# we pin the winner — same philosophy as RF/AV launch-month A/B.
PACKAGING_ARMS = ("hook_search", "hook_strong", "search_strong")


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _ensure_packaging_log(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS packaging_log ("
        " card_id TEXT, arm TEXT, title TEXT, description TEXT,"
        " hashtags_json TEXT, lane TEXT, generated_at TEXT DEFAULT (datetime('now')))"
    )
    con.commit()


def stabilized_arm() -> str | None:
    """Once a packaging arm clearly wins (bandit P(best)≥θ & enough n), return it so
    rotation stops and we pin the winner. Until then None → keep round-robin testing
    all 3 arms (forced even A/B beats Thompson here — guarantees every arm is tried)."""
    try:
        from agents import bandit
        arm = bandit.stabilized("packaging")
        return arm if arm in PACKAGING_ARMS else None
    except Exception:
        return None


def choose_packaging(card_id: str, *, arm: str | None = None) -> str:
    """Pick a packaging arm for this card. Explicit arm wins; else a stabilized
    winner; else deterministic round-robin by card_id hash (stable per episode,
    spread across episodes) so the 3 tones rotate evenly during the experiment."""
    if arm in PACKAGING_ARMS:
        return arm
    fixed = stabilized_arm()
    if fixed in PACKAGING_ARMS:
        return fixed
    import hashlib
    h = int(hashlib.sha1((card_id or "default").encode("utf-8")).hexdigest()[:8], 16)
    return PACKAGING_ARMS[h % len(PACKAGING_ARMS)]


def _salvage_json(txt: str) -> dict:
    txt = re.sub(r"^```(?:json)?\s*", "", txt.strip())
    txt = re.sub(r"\s*```$", "", txt)
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _concept_brief(concept: dict) -> str:
    """Compact, packaging-relevant view of the concept for the LLM."""
    caps = []
    for c in (concept.get("cuts") or [])[:8]:
        for s in (c.get("captions") or c.get("scenes") or []):
            ko = s.get("ko") if isinstance(s, dict) else None
            if ko:
                caps.append(ko)
    brief = {
        "title": concept.get("title"),
        "narrative_oneliner": concept.get("narrative_oneliner") or concept.get("logline"),
        "lane": concept.get("render_style") or concept.get("lane"),
        "subjects": concept.get("subjects"),
        "tone": concept.get("tone") or concept.get("tone_style"),
        "captions": caps[:14],
    }
    return json.dumps(brief, ensure_ascii=False)


def make_packaging(concept: dict, *, card_id: str, arm: str | None = None,
                   log_to_db: bool = True) -> dict:
    """Generate {title, description, hashtags, arm} for an episode. `arm` defaults
    to the rotation pick. Pure metadata — does NOT upload; the caller writes the
    result into cards.payload_json.draft and the existing upload path uses it."""
    arm = choose_packaging(card_id, arm=arm)
    system = _PROMPT.read_text(encoding="utf-8")
    user = (f"arm: {arm}\n컨셉:\n{_concept_brief(concept)}\n\n"
            f"위 컨셉을 arm='{arm}' 강조로 패키징해서 JSON 객체만 출력.")
    txt = call_text_cascade(system, user, max_tokens=1200).strip()
    out = _salvage_json(txt)
    # Normalize
    title = (out.get("title") or concept.get("title") or "Ryani & Leo").strip()
    desc = (out.get("description") or "").strip()
    desc = desc.replace("\\n", "\n")  # some LLM hops double-escape newlines
    tags = out.get("hashtags") or []
    tags = [str(t).strip() for t in tags if str(t).strip()]
    result = {"title": title[:100], "description": desc[:5000],
              "hashtags": tags[:30], "arm": arm}
    if log_to_db:
        try:
            con = _db()
            _ensure_packaging_log(con)
            con.execute(
                "INSERT INTO packaging_log (card_id, arm, title, description, "
                "hashtags_json, lane) VALUES (?,?,?,?,?,?)",
                (card_id, arm, result["title"], result["description"],
                 json.dumps(result["hashtags"], ensure_ascii=False),
                 concept.get("render_style") or concept.get("lane")))
            con.commit()
            con.close()
        except Exception as e:
            log.warning("packaging_log insert failed: %s", e)
    return result


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Channel Manager — packaging (Phase 1)")
    p.add_argument("--concept-json", help="path to a concept JSON file")
    p.add_argument("--card-id", default="test")
    p.add_argument("--arm", choices=PACKAGING_ARMS, default=None,
                   help="force a packaging arm (else rotation)")
    p.add_argument("--all-arms", action="store_true",
                   help="generate all 3 arms for comparison")
    args = p.parse_args()
    concept = json.loads(Path(args.concept_json).read_text(encoding="utf-8")) if args.concept_json else {}
    if args.all_arms:
        for a in PACKAGING_ARMS:
            r = make_packaging(concept, card_id=args.card_id, arm=a, log_to_db=False)
            print(f"\n===== arm={a} =====")
            print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        r = make_packaging(concept, card_id=args.card_id, arm=args.arm, log_to_db=False)
        print(json.dumps(r, ensure_ascii=False, indent=2))
