"""Senior Director — batch showrunner.

Originates 3 DIVERSE real_footage source bibles (A/B/C) per batch. Each source is
later cut into 3 edit-grammar variants (velocity/meme/story) and drained over 2 days,
so the source-level creativity + diversity here is the quality lever for the whole batch.

Diversity of ORIGIN drives diversity of RESULT (Phase 1 fixes the mode per source):
  A = 함미하비 note  → the owner's own description of a moment (richest ground truth)
  B = VLM 관찰       → a striking observed behavior nobody narrated
  C = 컨셉/arc       → intent/timeliness leads, clips serve it
The footage-diversity gate (agents.footage_diversity) then enforces that the three
casts don't share ≥60% of their footage — concept overlap ("three walks") is fine,
the same clip recut three times is not.

Dry-run:  .venv/bin/python -m agents.senior_director --date 2026-09-25
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path

from agents import footage_diversity

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "agents" / "prompts" / "senior_director.md"

MAX_REROLLS = int(os.getenv("SENIOR_DIRECTOR_REROLLS", "2"))


def _db():
    from agents.producer import _db as _pdb
    return _pdb()


# ── Origination material ───────────────────────────────────────────────────

def _grandma_notes(con, limit: int = 12) -> list[dict]:
    """A — recent clips carrying the owner's own description (assets.pd_notes)."""
    rows = con.execute(
        "SELECT asset_id, pd_notes, activity, subjects_csv, scene_description, "
        "       duration_sec, captured_iso, location_type "
        "FROM assets WHERE kind='video' AND pd_notes IS NOT NULL AND trim(pd_notes) != '' "
        "  AND vlm_analyzed_at IS NOT NULL AND coalesce(quality_score,0) >= 0.6 "
        "ORDER BY captured_iso DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def _striking_behaviors(con, limit: int = 15) -> list[dict]:
    """B — recent clips whose VLM notes carry notable micro-behaviors / intent."""
    rows = con.execute(
        "SELECT asset_id, notes, activity, subjects_csv, scene_description, "
        "       duration_sec, captured_iso, location_type "
        "FROM assets WHERE kind='video' AND notes IS NOT NULL "
        "  AND vlm_analyzed_at IS NOT NULL AND coalesce(quality_score,0) >= 0.6 "
        "ORDER BY captured_iso DESC LIMIT 200").fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            n = json.loads(r["notes"] or "{}")
        except Exception:
            continue
        micro = n.get("micro_behaviors") or []
        intent = n.get("pet_intent") or ""
        if not micro and not intent:
            continue
        out.append({
            "asset_id": r["asset_id"], "activity": r["activity"],
            "subjects_csv": r["subjects_csv"], "scene_description": r["scene_description"],
            "duration_sec": r["duration_sec"], "captured_iso": r["captured_iso"],
            "location_type": r["location_type"],
            "micro_behaviors": micro, "pet_intent": intent,
            "looking_at": n.get("looking_at") or "", "props": n.get("contextual_props") or [],
        })
        if len(out) >= limit:
            break
    return out


def _arc_directive(con, target: dt.date) -> str:
    """C — the themed/seasonal showrunner directive for this batch."""
    try:
        from agents import arc
        return arc.next_directive(con, today=target.isoformat(), render_style="real_footage") or ""
    except Exception as e:  # noqa: BLE001
        log.warning("senior_director: arc directive failed: %s", e)
        return ""


def _candidate_clips(con, target: dt.date) -> list[dict]:
    """Shared fresh clip pool the sources cast from (producer's diversity-sampled best)."""
    try:
        from agents import producer
        ctx = producer._gather_context(con, target)
        return (ctx.get("best_videos") or [])
    except Exception as e:  # noqa: BLE001
        log.warning("senior_director: candidate pool failed: %s", e)
        return []


# ── Prompt assembly ────────────────────────────────────────────────────────

def _fmt_notes(notes: list[dict]) -> str:
    lines = []
    for n in notes:
        lines.append(f"- {n['asset_id']} ({n.get('captured_iso','?')[:10]}, "
                     f"{n.get('location_type') or '?'}, {n.get('duration_sec') or '?'}s): "
                     f"NOTE = {n.get('pd_notes','').strip()[:220]}")
    return "\n".join(lines) or "(없음 — B/C 발원으로 대체하되 3소스는 채워라)"


def _fmt_behaviors(bs: list[dict]) -> str:
    lines = []
    for b in bs:
        lines.append(f"- {b['asset_id']} ({b.get('captured_iso','?')[:10]}, "
                     f"{b.get('location_type') or '?'}): {b.get('subjects_csv') or '?'} | "
                     f"activity={b.get('activity') or '?'} | intent={b.get('pet_intent') or '-'} | "
                     f"micro={','.join(b.get('micro_behaviors') or []) or '-'} | "
                     f"sc={(b.get('scene_description') or '')[:120]}")
    return "\n".join(lines) or "(없음)"


def _fmt_clips(clips: list[dict]) -> str:
    lines = []
    for c in clips:
        lines.append(f"- {c.get('id')} ({c.get('date','?')}, {c.get('loc') or '?'}, "
                     f"{c.get('dur') or '?'}s, {c.get('sub') or '?'}, motion={c.get('motion','?')}): "
                     f"{(c.get('sc') or '')[:130]}")
    return "\n".join(lines) or "(빈 풀 — 클립 없음)"


def _build_system(con, target: dt.date) -> str:
    """STATIC context (prompt + candidate pool) → cached across rerolls."""
    base = PROMPT_PATH.read_text(encoding="utf-8")
    clips = _candidate_clips(con, target)
    return (base
            + "\n\n---\n## 캐스팅 가능한 클립 풀 (여기서만 asset_id를 골라라)\n"
            + _fmt_clips(clips))


def _build_user(con, target: dt.date, feedback: str = "") -> str:
    notes = _grandma_notes(con)
    bs = _striking_behaviors(con)
    directive = _arc_directive(con, target)
    parts = [
        f"# 배치 대상일: {target.isoformat()}",
        "\n## 소스 A 발원 — 함미하비 노트 (owner의 말)\n" + _fmt_notes(notes),
        "\n## 소스 B 발원 — VLM 관찰된 행동\n" + _fmt_behaviors(bs),
        "\n## 소스 C 발원 — arc/컨셉 디렉티브\n" + (directive or "(디렉티브 없음 — 신선 클립에서 시의성 있는 테마를 잡아라)"),
        "\n소스 3개(A/B/C)를 출력하라. 위 self-check를 모두 통과시켜라. JSON 배열만.",
    ]
    if feedback:
        parts.append("\n## 재작성 피드백 (반드시 반영)\n" + feedback)
    return "\n".join(parts)


# ── Parse + diversity enforcement ──────────────────────────────────────────

def _parse_sources(text: str) -> list[dict]:
    from agents.producer import _robust_json_parse
    v = _robust_json_parse(text, allow_llm_repair=False)
    if isinstance(v, dict):
        v = [v]
    return [s for s in (v or []) if isinstance(s, dict) and s.get("cast")]


def propose_sources(target: dt.date, con=None, progress_cb=None) -> list[dict]:
    """Originate 3 diverse source bibles for `target`'s batch. Applies the footage-
    diversity gate and rerolls until A/B/C don't share ≥60% footage (or rerolls run out).
    Returns the source list (may still contain a residual violation if unresolved — the
    caller/log surfaces it rather than silently shipping samey footage)."""
    from agents.llm_cascade import call_text_cached
    _con = con or _db()
    system = _build_system(_con, target)
    feedback = ""
    sources: list[dict] = []
    for attempt in range(MAX_REROLLS + 1):
        user = _build_user(_con, target, feedback)
        text = call_text_cached(system, user, max_tokens=8000).strip()
        sources = _parse_sources(text)
        if progress_cb:
            progress_cb(f":clapper: senior director 시도 {attempt+1} — 소스 {len(sources)}개")
        if len(sources) < 3:
            feedback = "소스가 3개 미만이었다. 반드시 A/B/C 세 개를 채워라."
            continue
        viol = footage_diversity.violations(sources)
        if not viol:
            break
        pairs = ", ".join(f"{sources[i].get('source_id','?')}↔{sources[j].get('source_id','?')}"
                          f"({r:.0%})" for i, j, r in viol)
        used = footage_diversity.used_asset_ids(sources)
        feedback = (f"소스들이 같은 동영상을 너무 많이 쓴다({pairs}, 60% 룰 위반). 겹치는 소스를 "
                    f"다른 클립으로 다시 캐스팅하라. 이미 쓴 asset_id는 최대한 피하라:\n"
                    + ", ".join(sorted(used)))
        if progress_cb:
            progress_cb(f":warning: footage 60% 위반 {pairs} → 리롤")
    return sources


def _cli() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Senior Director — 3-source dry-run")
    ap.add_argument("--date", default=None, help="target date YYYY-MM-DD (default: +2d KST)")
    ap.add_argument("--out", default=None, help="write sources JSON to this path")
    args = ap.parse_args()
    target = (dt.date.fromisoformat(args.date) if args.date
              else dt.date.today() + dt.timedelta(days=2))
    sources = propose_sources(target, progress_cb=lambda m: print(m))
    print(f"\n=== {len(sources)} sources for {target} ===")
    for s in sources:
        cast = s.get("cast") or []
        secs = sum(float(c.get("trim_dur") or 0) for c in cast)
        print(f"\n[{s.get('source_id')}] origination={s.get('origination')} "
              f"| subjects={s.get('subjects')} | timeframe={s.get('timeframe')}")
        print(f"  title: {s.get('title')}")
        print(f"  oneliner: {s.get('narrative_oneliner')}")
        print(f"  through_line: {s.get('through_line')}")
        print(f"  cast ({len(cast)} clips, {secs:.0f}s): "
              + ", ".join(c.get('asset_id', '?') for c in cast))
        if s.get("knowledge_questions"):
            print(f"  knowledge_questions: {s['knowledge_questions']}")
    viol = footage_diversity.violations(sources)
    print(f"\nfootage-diversity violations: {viol or 'NONE ✓'}")
    if args.out:
        Path(args.out).write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
