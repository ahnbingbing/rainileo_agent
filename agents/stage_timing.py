"""
agents/stage_timing.py — lightweight per-stage wall-clock timing.

The RF render is slow (~9 min/episode) and the cost is spread across many
stages (Editor LLM, per-cut caption VLMs, ffmpeg trim/burn/assemble, the Giri
retry loop). Guessing the bottleneck wastes renders — so wrap each stage in
`stage(name)` and read `dump()` to see, sorted biggest-first, exactly where the
wall clock went.

Enabled with STAGE_TIMING=1 (no-op otherwise, so it's safe to leave wired in
production). The accumulator is thread-local, so concurrent renders
(GRAMMAR_RENDER_CONCURRENCY) record into separate ledgers instead of mixing.
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time

log = logging.getLogger("agents.stage_timing")

ENABLED = os.getenv("STAGE_TIMING", "0") == "1"
_local = threading.local()


def _ledger() -> list[tuple[str, float]]:
    rows = getattr(_local, "rows", None)
    if rows is None:
        rows = _local.rows = []
    return rows


def reset() -> None:
    """Clear this thread's ledger — call at the start of a render you want to profile."""
    _ledger().clear()


def record(name: str, seconds: float) -> None:
    """Record a stage measured elsewhere (e.g. a subprocess already timed by _run)."""
    if not ENABLED:
        return
    _ledger().append((name, seconds))
    log.info("[stage-timing] %-36s %8.2fs", name, seconds)


@contextlib.contextmanager
def stage(name: str):
    """Time the wrapped block and append (name, seconds) to the thread ledger."""
    if not ENABLED:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(name, time.perf_counter() - t0)


def dump(header: str = "", progress_cb=None) -> list[tuple[str, float]]:
    """Log the ledger sorted slowest-first with % of total; return the sorted rows."""
    if not ENABLED:
        return []
    rows = sorted(_ledger(), key=lambda r: r[1], reverse=True)
    total = sum(s for _, s in rows) or 1e-9
    log.info("══ stage timing %s — total %.1fs ══", header, total)
    for name, s in rows:
        log.info("  %8.2fs  %5.1f%%  %s", s, 100 * s / total, name)
    if progress_cb and rows:
        top = rows[0]
        progress_cb(f":stopwatch: timing {header}: 총 {total:.0f}s · 최대 {top[0]} {top[1]:.0f}s")
    return rows
