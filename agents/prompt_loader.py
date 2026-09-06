"""Prompt-set loader — lets a DIET'd v2 prompt set be A/B-tested against v1 without
touching the live v1 files or the pipeline (PD 2026-09-07).

The goal of v2 is a leaner prompt injection (dedup, resolve contradictions, compress
verbose PD prose to principle→why→one-example) with ZERO quality change. To prove that
safely, v2 lives in `agents/prompts/v2/<same-name>.md` and is loaded ONLY when
`PROMPT_SET=v2`; otherwise (default) the untouched v1 file is used. A v2 file that
doesn't exist yet transparently falls back to v1, so we can diet one prompt at a time
and A/B incrementally.

Usage: replace `SOME_PROMPT_PATH.read_text(encoding="utf-8")` with
`prompt_loader.load(SOME_PROMPT_PATH)`.
"""
from __future__ import annotations
import os
from pathlib import Path


def active_set() -> str:
    return os.getenv("PROMPT_SET", "v1").strip().lower()


def prompt_path(v1: Path) -> Path:
    """Resolve to the v2 sibling (`<dir>/v2/<name>`) when PROMPT_SET=v2 and it exists;
    else the given v1 path. Pure path resolution — never reads the file."""
    v1 = Path(v1)
    if active_set() == "v2":
        v2 = v1.parent / "v2" / v1.name
        if v2.exists():
            return v2
    return v1


def load(v1: Path, *, encoding: str = "utf-8") -> str:
    return prompt_path(v1).read_text(encoding=encoding)
