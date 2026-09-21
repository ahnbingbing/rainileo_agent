"""Footage-diversity gate for the senior director's within-batch A/B/C sources.

PD 2026-09-21: because one batch makes 9 videos (3 sources × 3 edit grammars), the
three sources must not be built from the SAME video. The gate judges diversity on
FOOTAGE, not concept — "three walks" is fine (same theme, different clips); the same
clip recut three times is a repost. Overlap is measured in shared footage-seconds:
two sources conflict when they share ≥ FOOTAGE_OVERLAP_MAX of the smaller cast's
seconds (identical clip sets = 1.0). A small shared segment is tolerated.

A source's cast is a list of clip segments: {"asset_id", "trim_start", "trim_dur"}.
"""
from __future__ import annotations

import os

FOOTAGE_OVERLAP_MAX = float(os.getenv("FOOTAGE_OVERLAP_MAX", "0.6"))


def _segments_by_asset(cast: list) -> dict[str, list[tuple[float, float]]]:
    """{asset_id: merged [(start, end), ...]}. Overlapping/adjacent intervals of the
    SAME clip are merged so a source can't inflate its own seconds by listing a clip
    twice, and so cross-source overlap is never double-counted."""
    raw: dict[str, list[tuple[float, float]]] = {}
    for c in cast or []:
        aid = c.get("asset_id")
        if not aid:
            continue
        start = float(c.get("trim_start") or 0.0)
        dur = float(c.get("trim_dur") or 0.0)
        if dur <= 0:
            continue
        raw.setdefault(aid, []).append((start, start + dur))
    merged: dict[str, list[tuple[float, float]]] = {}
    for aid, ivals in raw.items():
        ivals.sort()
        out: list[tuple[float, float]] = []
        for s, e in ivals:
            if out and s <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], e))
            else:
                out.append((s, e))
        merged[aid] = out
    return merged


def _total_seconds(by_asset: dict[str, list[tuple[float, float]]]) -> float:
    return sum(e - s for ivals in by_asset.values() for s, e in ivals)


def _shared_seconds(a: dict[str, list[tuple[float, float]]],
                    b: dict[str, list[tuple[float, float]]]) -> float:
    """Footage-seconds present in BOTH casts (same asset_id, intersecting intervals)."""
    total = 0.0
    for aid, a_ivals in a.items():
        b_ivals = b.get(aid)
        if not b_ivals:
            continue
        for s0, e0 in a_ivals:
            for s1, e1 in b_ivals:
                lo, hi = max(s0, s1), min(e0, e1)
                if hi > lo:
                    total += hi - lo
    return total


def overlap_ratio(source_a: dict, source_b: dict) -> float:
    """Shared footage-seconds / smaller cast's total seconds. 1.0 = identical footage,
    0.0 = no shared clips. A source with no measurable cast returns 0.0 (can't judge)."""
    a = _segments_by_asset(source_a.get("cast") or [])
    b = _segments_by_asset(source_b.get("cast") or [])
    ta, tb = _total_seconds(a), _total_seconds(b)
    if ta <= 0 or tb <= 0:
        return 0.0
    return _shared_seconds(a, b) / min(ta, tb)


def violations(sources: list, threshold: float | None = None) -> list[tuple[int, int, float]]:
    """Pairs (i, j, ratio) whose footage overlap ≥ threshold — too similar, reroll one.
    i < j; ratios sorted worst-first so the caller rerolls the most-overlapping source."""
    thr = FOOTAGE_OVERLAP_MAX if threshold is None else threshold
    out: list[tuple[int, int, float]] = []
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            r = overlap_ratio(sources[i], sources[j])
            if r >= thr:
                out.append((i, j, r))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def used_asset_ids(sources: list) -> set[str]:
    """All asset_ids cast across the given sources — for exclude_asset_ids when
    rerolling one source so it casts genuinely different clips."""
    ids: set[str] = set()
    for s in sources:
        for c in (s.get("cast") or []):
            if c.get("asset_id"):
                ids.add(c["asset_id"])
    return ids
