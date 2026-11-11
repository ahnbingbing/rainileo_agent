"""Shared deterministic era-mix detection — generator ↔ checker lockstep.

An episode that cuts clips from very different capture dates together WITHOUT a
temporal-narration token reads as ONE present moment yet shows the same animal at
different ages (baby-Leo + grown, puppy-Ryani + now) — PD's "레오가 갑자기 작아짐".

PD 2026-06-23 wired the CHECKER side (reviewer._temporal_grounding_gate): post-render,
cap score ≤5 so it can't auto-publish. But the GENERATOR (Writer) never learned, so
rerolls didn't converge — Giri kept blocking the same defect. The real fix is to tell
the Writer BEFORE the render (producer._rf_structure_issues) to either unify the era or
narrate the time-distance. Narration IS the fix, not avoidance: a span-mix captioned
"그땐 아기였는데 지금은~" passes both gates.

This module is the SINGLE source of the span/token logic so the pre-render Writer gate
and the post-render Giri gate always agree on what counts as an un-narrated era-mix.
Change the rule here once and both lanes move together.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3

# Leo (orange tabby) born 2025-09-25 — his first year changes dramatically month to
# month, so a flat 1-year span rule misses a baby-kitten-vs-grown mix. See era_span_fire.
_LEO_BORN = _dt.date(2025, 9, 25)

# Tokens that mark a caption as narrating an archive clip's time-distance
# ("그때 / N년 전 / 아기 시절 / 자랐어요" …). Covers KO + EN. A single hit ANYWHERE in the
# episode's captions proves the era-mix is narrated as memory-lane and the gate stands down.
# Korean substring-matches, so bare nouns are dangerous: "아가" hits "닮아가는", "아기" hits
# "아기자기". We keep only tokens that don't collide, and space-guard the baby words
# ("아기 레오" matches; "아기자기" does not). Bias toward FIRING — a missing auto-pass token
# only over-flags, which PD prefers to a rubber-stamp.
_TEMPORAL_TOKENS = (
    "년 전", "년전", "개월 전", "개월전", "달 전", "주 전", "일 전",
    "그때", "그 때", "그땐", "그시절", "그 시절", "시절", "예전", "옛날", "과거",
    "어릴", "어렸", "아기 ", "아가 ", "새끼 ", "꼬꼬마", "갓난", "갓 태어",
    "작년", "재작년", "처음 만", "만나기 전", "만나기전", "처음 왔",
    "자랐", "커버린", "자라서", "자라났", "세월", "옛", "추억",
    # EN — only unambiguous time-distance phrases.
    "ago", "back then", "back when", "used to", "younger", "as a baby",
    "as a puppy", "as a kitten", "grew up", "years back", "as a pup", "as a kit",
)


def captions_narrate_era(cuts: list) -> bool:
    """True if ANY burned caption anywhere in the episode frames the footage as past.

    Scans only what actually gets burned on screen (captions/scenes arrays + flat
    ko/en/caption fields). Deliberately NOT time_ago_phrase — that is the renderer's
    INTERNAL knowledge of the gap, not a visible caption; counting it made every dated
    archive clip look 'narrated' and pass (false negative)."""
    blob: list[str] = []
    for c in cuts or []:
        if not isinstance(c, dict):
            continue
        for arr_key in ("captions", "scenes"):
            caps = c.get(arr_key) or []
            if isinstance(caps, list):
                for sc in caps:
                    if isinstance(sc, dict):
                        blob.append(str(sc.get("ko", "")))
                        blob.append(str(sc.get("en", "")))
                    else:
                        blob.append(str(sc))
        for k in ("ko", "en", "caption"):
            if c.get(k):
                blob.append(str(c[k]))
    text = " ".join(blob).lower()
    return any(tok.lower() in text for tok in _TEMPORAL_TOKENS)


def era_span_fire(cuts: list, db_path) -> dict | None:
    """Measure the capture-date span across an episode's cuts and decide whether it reads
    as an un-narrated era-mix. Returns a dict {span_days, span_years, leo_fire,
    youngest_leo_age, leo_span, reason} when it fires, else None.

    captured_iso is static asset metadata (not operational state), so the local DB is a
    safe source even though live/VM is authoritative for schedules. Best-effort: any
    missing data → None (never a false positive).

    Two ways an episode reads as an un-narrated era-mix:
      (1) GENERAL — any two source clips are > 1 year apart (archive mixes like puppy-Ryani
          2016 cut against present, the same animal visibly a different age).
      (2) LEO KITTEN FAST-GROWTH — when the YOUNGEST Leo clip is a clear kitten (< 6 months
          old) AND the Leo clips span > ~2.5 months, the visible age jump is real even if the
          calendar span is only a few months. Recent episodes use today's ~grown Leo
          (youngest > 6mo) so this never false-fires on them."""
    cuts = cuts or []
    if len(cuts) < 2:
        return None
    aids = [c.get("asset_id") or c.get("secondary_asset_id")
            for c in cuts if isinstance(c, dict)]
    aids = [a for a in aids if a]
    if len(aids) < 2:
        return None
    try:
        con = sqlite3.connect(str(db_path))
        try:
            qs = ",".join("?" * len(aids))
            rows = con.execute(
                f"SELECT asset_id, captured_iso, subjects_csv FROM assets "
                f"WHERE asset_id IN ({qs})",
                aids,
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return None
    dates: list[_dt.date] = []       # all dated cuts
    leo_dates: list[_dt.date] = []   # dates of cuts that contain Leo
    for _aid, iso, subj in rows:
        if not iso:
            continue
        try:
            d = _dt.date.fromisoformat(str(iso)[:10])
        except Exception:
            continue
        dates.append(d)
        if "leo" in (subj or "").lower():
            leo_dates.append(d)
    if len(dates) < 2:
        return None
    span_days = (max(dates) - min(dates)).days
    general_fire = span_days > 365
    leo_fire = False
    youngest_leo_age = leo_span = None
    if len(leo_dates) >= 2:
        youngest_leo_age = (min(leo_dates) - _LEO_BORN).days
        leo_span = (max(leo_dates) - min(leo_dates)).days
        if youngest_leo_age < 183 and leo_span > 75:
            leo_fire = True
    if not (general_fire or leo_fire):
        return None
    span_years = round(span_days / 365.25, 1)
    if leo_fire:
        reason = (f"아기 레오({youngest_leo_age / 30.4:.1f}개월) 클립과 "
                  f"{round(leo_span / 30.4, 1)}개월 뒤 다 큰 레오 클립이 한 회차에 섞였습니다")
    else:
        reason = f"클립 촬영일이 {span_years}년 차이로 벌어졌습니다"
    return {"span_days": span_days, "span_years": span_years, "leo_fire": leo_fire,
            "youngest_leo_age": youngest_leo_age, "leo_span": leo_span, "reason": reason}
