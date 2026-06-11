"""agents/circuit.py — LLM provider circuit breaker (PD 2026-06-08).

The cascade is OpenAI → Gemini → Anthropic. When a provider is DOWN (e.g. OpenAI
timing out on every call during a network blip), re-trying it first on every one
of an av concept's ~9 LLM calls wastes ~45s each (~7min/concept). Once a provider
fails, mark it down for a short cooldown so subsequent calls skip straight to the
next provider — then re-probe after the cooldown.

Process-local + best-effort; the GIL makes the dict ops safe enough for the
lane-parallel threads.
"""
from __future__ import annotations

import os
import time

_DOWN: dict[str, float] = {}   # provider -> unix ts until which it's "down"
COOLDOWN_S = int(os.getenv("PROVIDER_COOLDOWN_S", "300"))


def is_down(provider: str) -> bool:
    return _DOWN.get(provider, 0.0) > time.time()


def mark_down(provider: str, seconds: int | None = None) -> None:
    _DOWN[provider] = time.time() + (seconds if seconds is not None else COOLDOWN_S)


def mark_up(provider: str) -> None:
    _DOWN.pop(provider, None)


def reset_all() -> None:
    """PD 2026-06-10: clear ALL circuit state so the next call re-probes every
    provider. Call at the START of a run — a provider that recovered during the
    cooldown window (OpenAI/Gemini back up) shouldn't keep the whole run routed to
    the last fallback (Anthropic). Cheap insurance against a stale-open circuit."""
    _DOWN.clear()
