"""agents/circuit.py — LLM provider circuit breaker (PD 2026-06-08, fixed 2026-06-12).

The cascade is OpenAI → Gemini → Anthropic. When a provider is genuinely DOWN (e.g.
OpenAI timing out on every call during a network blip), re-trying it first on every
one of a concept's ~10 LLM calls wastes the per-call timeout each time. So once a
provider is reliably failing, mark it down for a SHORT cooldown and skip straight to
the next — then re-probe.

PD 2026-06-12 ROOT-CAUSE FIX ("계속 openAI랑 제미나이가 안된다"): the old breaker opened
on a SINGLE failure and kept the provider down for 300s (5 min). So ONE transient
timeout disabled OpenAI for 5 minutes → every subsequent call fell to Gemini → one
Gemini 504 disabled it for 5 min too → the whole run routed to the slow Anthropic
fallback. The providers weren't actually down — the breaker over-reacted. Now: open
ONLY after FAILS_TO_OPEN (default 3) CONSECUTIVE failures, a success resets the count,
and the cooldown is short (default 45s) so a real blip self-heals in seconds.

Process-local + best-effort; the GIL makes the dict ops safe enough for threads.
"""
from __future__ import annotations

import os
import time

_DOWN: dict[str, float] = {}   # provider -> unix ts until which it's "down"
_FAILS: dict[str, int] = {}    # provider -> consecutive failure count
COOLDOWN_S = int(os.getenv("PROVIDER_COOLDOWN_S", "45"))
FAILS_TO_OPEN = int(os.getenv("PROVIDER_FAILS_TO_OPEN", "3"))


def is_down(provider: str) -> bool:
    return _DOWN.get(provider, 0.0) > time.time()


def mark_down(provider: str, seconds: int | None = None) -> None:
    """Record a failure. Open the circuit (skip this provider) ONLY after
    FAILS_TO_OPEN consecutive failures — a single transient timeout must NOT disable
    a working provider. `seconds` (when given) forces an immediate open with that
    cooldown (used for a hard, non-transient error)."""
    _FAILS[provider] = _FAILS.get(provider, 0) + 1
    if seconds is not None:
        _DOWN[provider] = time.time() + seconds
    elif _FAILS[provider] >= FAILS_TO_OPEN:
        _DOWN[provider] = time.time() + COOLDOWN_S


def mark_up(provider: str) -> None:
    """A success — clear the failure count AND any open state for this provider."""
    _FAILS.pop(provider, None)
    _DOWN.pop(provider, None)


def reset_all() -> None:
    """PD 2026-06-10: clear ALL circuit state so the next call re-probes every
    provider. Call at the START of a run — a provider that recovered during the
    cooldown window shouldn't keep the whole run routed to the last fallback."""
    _DOWN.clear()
    _FAILS.clear()
