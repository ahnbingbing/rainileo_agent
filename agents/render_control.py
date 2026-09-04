"""Cooperative render STOP flag (PD 2026-09-04).

There was NO way to halt an in-flight render / self-heal: a "stop" in Slack was ignored
and a batch ground on for ~30min (the CLI couldn't kill it on prod either — no signal path,
no handler). A single file flag that every render loop checks at a SAFE boundary (each
self-heal round, each slot) lets "stop" abort BEFORE the next render, gracefully. The
already-running ffmpeg/Seedance subprocess finishes (a subprocess can't be interrupted
mid-call), then the loop stops rather than starting the next one — so "stop" takes effect
within one cut/round instead of never.

A TTL auto-clears the flag so a forgotten "stop" can't silently block the nightly 03:00
batch forever. Slack "go"/"resume" clears it immediately.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

log = logging.getLogger("agents.render_control")
ROOT = Path(__file__).resolve().parent.parent
STOP_FILE = ROOT / "data" / "tmp" / ".render_stop"


def _ttl() -> int:
    try:
        return int(os.getenv("RENDER_STOP_TTL_SEC", "21600"))  # 6h default
    except Exception:
        return 21600


def request_stop(reason: str = "") -> None:
    """Ask all render loops to halt at their next safe boundary."""
    try:
        STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text(f"{int(time.time())}|{reason}", encoding="utf-8")
        log.info("render STOP requested: %s", reason or "(no reason)")
    except Exception as e:
        log.warning("request_stop failed: %s", e)


def clear_stop() -> None:
    """Resume: remove the stop flag."""
    try:
        STOP_FILE.unlink()
        log.info("render STOP cleared — renders may resume")
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("clear_stop failed: %s", e)


def stop_requested() -> bool:
    """True if a (non-expired) stop is in effect. Fail-safe: an unreadable flag counts as
    stop; an expired flag auto-clears and returns False so the nightly batch isn't blocked."""
    try:
        raw = STOP_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    except Exception:
        return True
    try:
        ts = int(raw.split("|", 1)[0])
    except Exception:
        ts = 0
    ttl = _ttl()
    if ttl > 0 and ts and (time.time() - ts) > ttl:
        clear_stop()
        return False
    return True


def stop_reason() -> str:
    try:
        return STOP_FILE.read_text(encoding="utf-8").split("|", 1)[1]
    except Exception:
        return ""
