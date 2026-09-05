"""Shared LLM cascade (PD 2026-06-02): OpenAI gpt-4.1 → Gemini 2.5 Pro →
Anthropic (last resort). Used everywhere in the codebase that previously
called anthropic.Anthropic() directly. PD strict rule: NO Anthropic for
text generation when other providers are available.
"""
from __future__ import annotations
import logging
import os

from agents import models as _models
from typing import Optional

log = logging.getLogger("agents.llm_cascade")


def call_text_cascade(system: str, user: str, *,
                      max_tokens: int = 8000,
                      anthropic_model: str | None = None) -> str:
    """Try OpenAI gpt-4.1 → Gemini 2.5 Pro → Anthropic, in that order.
    Returns the first successful response text. Raises if all three fail.
    `anthropic_model` is only used on the last fallback hop.
    """
    # 1. OpenAI — fail FAST to Gemini. NEVER use a reasoning model (gpt-5) here: it
    # returned empty output + timed out, silently dropping the whole cascade to
    # Anthropic (PD 2026-06-08/11). Model = models.OPENAI_TEXT (gpt-4.1). Short
    # timeout + no SDK retries so a slow provider can't stall the launch batch.
    _llm_timeout = int(os.environ.get("LLM_TIMEOUT_S", "90"))
    from agents import circuit
    # PD 2026-06-08: circuit breaker — skip a provider that just failed (cooldown)
    # instead of wasting the 45s timeout on every one of an av concept's ~9 calls.
    if circuit.is_down("openai"):
        log.info("LLM cascade: skip OpenAI (circuit open) → Gemini")
    else:
        try:
            from openai import OpenAI
            client = OpenAI(timeout=_llm_timeout, max_retries=0)
            resp = client.chat.completions.create(
                model=_models.OPENAI_TEXT,
                messages=([
                    {"role": "system", "content": system}
                ] if system else []) + [
                    {"role": "user", "content": user}
                ],
                max_completion_tokens=max_tokens,  # PD 2026-06-09: was unset → truncation
            )
            if resp.choices and resp.choices[0].finish_reason == "length":
                raise RuntimeError("openai output truncated")
            log.info("LLM cascade: OpenAI used")
            circuit.mark_up("openai")
            _log_text("openai", _models.OPENAI_TEXT, "openai_text", resp)
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            circuit.mark_down("openai")
            log.warning("OpenAI failed (%s) — circuit open, trying Gemini", e)
    # 2. Gemini
    try:
        if circuit.is_down("gemini"):
            log.info("LLM cascade: skip Gemini (circuit open) → Anthropic")
            raise RuntimeError("gemini circuit open")
        # PD 2026-06-08: use the NEW google.genai SDK with an http_options timeout.
        # The legacy google.generativeai SDK ignored the timeout on DNS failures and
        # hung 600s per call (intermittent googleapis DNS flakiness) — that single
        # bug made each proposal take 10+ min and stalled the whole launch batch.
        from google import genai as _genai
        from google.genai import types as _gtypes
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY missing")
        gclient = _genai.Client(api_key=api_key, http_options=_gtypes.HttpOptions(
            timeout=int(os.getenv("LLM_TIMEOUT_S", "90")) * 1000))
        model_name = _models.GEMINI_TEXT
        resp = gclient.models.generate_content(
            model=model_name,
            contents=user,
            config=_gtypes.GenerateContentConfig(
                system_instruction=system or None,
                max_output_tokens=max_tokens),  # PD 2026-06-09: was unset → ~8k truncation
        )
        try:
            fr = str((resp.candidates or [{}])[0].finish_reason or "")
            if "MAX_TOKENS" in fr.upper():
                raise RuntimeError("gemini output truncated")
        except (IndexError, AttributeError):
            pass
        log.info("LLM cascade: Gemini used")
        circuit.mark_up("gemini")
        _log_text("google", model_name, "gemini_text", resp)
        return (resp.text or "").strip()
    except Exception as e:
        if "circuit open" not in str(e):   # real failure, not a skip → open circuit
            circuit.mark_down("gemini")
        log.warning("Gemini failed (%s) — last fallback Anthropic", e)
    # 3. Anthropic last resort
    import anthropic
    client = anthropic.Anthropic()
    if system:
        msg = client.messages.create(
            model=(anthropic_model or _models.ANTHROPIC_TEXT),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    else:
        msg = client.messages.create(
            model=(anthropic_model or _models.ANTHROPIC_TEXT),
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user}],
        )
    log.info("LLM cascade: Anthropic last-resort used")
    if getattr(msg, "stop_reason", "") == "max_tokens":
        log.warning("cascade Anthropic output truncated (max_tokens=%s)", max_tokens)
    _log_text("anthropic", (anthropic_model or _models.ANTHROPIC_TEXT), "anthropic_text", msg)
    parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
    return "".join(parts).strip()


def _caller_label() -> str:
    """First frame OUTSIDE this module = the agent that made the cascade call.
    Attributes the ledger `stage` per-caller (was flat 'cascade' → all 234 calls/day
    lumped, so we couldn't see WHICH 40k-token prompt drove the $). Best-effort:
    'module.func' (e.g. 'producer._propose_realfootage_singlepass'); 'cascade' on any
    failure. CURRENT_STAGE env still wins when a caller sets it explicitly."""
    try:
        import sys
        f = sys._getframe(1)  # start above _caller_label; skip all in-module frames below
        while f is not None:
            mod = f.f_globals.get("__name__", "")
            if mod != __name__:  # left llm_cascade
                return f"{mod.rsplit('.', 1)[-1]}.{f.f_code.co_name}"
            f = f.f_back
    except Exception:
        pass
    return "cascade"


def _log_text(provider: str, model: str, price_key: str, resp) -> None:
    """Best-effort cost-ledger entry for one text call; pulls token usage when present."""
    try:
        from agents import api_ledger as _led
        meta = None
        u = getattr(resp, "usage", None)
        if u is not None:
            tot = (getattr(u, "total_tokens", None)
                   or ((getattr(u, "input_tokens", 0) or 0) + (getattr(u, "output_tokens", 0) or 0)))
            if tot:
                meta = {"tokens": int(tot)}
        _led.log_call(provider, "text", price_key=price_key, model=model,
                      stage=os.getenv("CURRENT_STAGE") or _caller_label(),
                      card_id=os.getenv("CURRENT_CARD_ID") or None, meta=meta)
    except Exception:
        pass


def call_user_only(prompt: str, *, max_tokens: int = 4000) -> str:
    """Convenience for one-shot prompt without a system message."""
    return call_text_cascade("", prompt, max_tokens=max_tokens)


# Anthropic Sonnet $/1M tokens: (input, cache-write, cache-read, output).
_CACHED_RATE = (3.0, 3.75, 0.30, 15.0)


def _log_cached(model: str, u) -> None:
    """Ledger entry for a cached Anthropic call — cost accounts for the cache
    discount (read ≈ 0.1× input), and meta carries cache_read/write so the report
    can SHOW the cache actually hitting."""
    try:
        from agents import api_ledger as _led
        inp = int(getattr(u, "input_tokens", 0) or 0)
        cw = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        cr = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        out = int(getattr(u, "output_tokens", 0) or 0)
        ri, rw, rr, ro = _CACHED_RATE
        cost = (inp * ri + cw * rw + cr * rr + out * ro) / 1_000_000.0
        _led.log_call("anthropic", "text", price_key="anthropic_text_cached", model=model,
                      stage=os.getenv("CURRENT_STAGE") or _caller_label(),
                      card_id=os.getenv("CURRENT_CARD_ID") or None, est_cost=cost,
                      meta={"tokens": inp + cw + cr + out, "cache_read": cr, "cache_write": cw})
    except Exception:
        pass


def call_text_cached(system: str, user: str, *, max_tokens: int = 12000,
                     model: str | None = None) -> str:
    """Anthropic-PRIMARY variant of call_text_cascade for HEAVY, REPEATED prompts.

    The large STATIC `system` is sent with cache_control=ephemeral (5-min TTL), so a
    retry loop that re-sends the same ~80k-token context (the RF single-pass pool on
    every Giri/reviewer re-proposal) pays full price ONCE (cache write, +25%) then
    ~90% off on each re-send (cache read). Put everything static in `system`; put only
    the per-attempt dynamic bit (Giri feedback) in `user`. Falls back to the normal
    OpenAI→Gemini cascade (same content) on ANY Anthropic failure, so it degrades to
    today's exact path. Caller opts in via its own flag (e.g. RF_PROMPT_CACHE)."""
    from agents import circuit
    model = model or os.getenv("RF_CACHE_MODEL", "claude-sonnet-4-6")
    if not circuit.is_down("anthropic"):
        try:
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=model, max_tokens=max_tokens,
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user or "위 지침대로 지금 작성하라."}],
            )
            if getattr(msg, "stop_reason", "") == "max_tokens":
                raise RuntimeError("anthropic output truncated")
            circuit.mark_up("anthropic")
            _log_cached(model, getattr(msg, "usage", None))
            return "".join(b.text for b in msg.content
                           if getattr(b, "type", "") == "text").strip()
        except Exception as e:
            circuit.mark_down("anthropic")
            log.warning("cached Anthropic failed (%s) — falling back to cascade", e)
    return call_text_cascade(system, user, max_tokens=max_tokens)
