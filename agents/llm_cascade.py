"""Shared LLM cascade (PD 2026-06-02): OpenAI GPT-5 → Gemini 2.5 Pro →
Anthropic (last resort). Used everywhere in the codebase that previously
called anthropic.Anthropic() directly. PD strict rule: NO Anthropic for
text generation when other providers are available.
"""
from __future__ import annotations
import logging
import os
from typing import Optional

log = logging.getLogger("agents.llm_cascade")


def call_text_cascade(system: str, user: str, *,
                      max_tokens: int = 8000,
                      anthropic_model: str = "claude-opus-4-7") -> str:
    """Try OpenAI GPT-5 → Gemini 2.5 Pro → Anthropic, in that order.
    Returns the first successful response text. Raises if all three fail.
    `anthropic_model` is only used on the last fallback hop.
    """
    # 1. OpenAI — fail FAST to Gemini (PD 2026-06-08: gpt-5 was timing out every
    # call and the SDK's internal retries × a 120s timeout made one proposal take
    # 6+ min → the launch batch stalled for hours). Short timeout + no SDK retries.
    _llm_timeout = int(os.environ.get("LLM_TIMEOUT_S", "45"))
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
                model=os.environ.get("OPENAI_FALLBACK_MODEL", "gpt-5"),
                messages=([
                    {"role": "system", "content": system}
                ] if system else []) + [
                    {"role": "user", "content": user}
                ],
            )
            log.info("LLM cascade: OpenAI used")
            circuit.mark_up("openai")
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
            timeout=int(os.getenv("LLM_TIMEOUT_S", "45")) * 1000))
        model_name = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-2.5-pro")
        resp = gclient.models.generate_content(
            model=model_name,
            contents=user,
            config=_gtypes.GenerateContentConfig(system_instruction=system or None),
        )
        log.info("LLM cascade: Gemini used")
        circuit.mark_up("gemini")
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
            model=anthropic_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    else:
        msg = client.messages.create(
            model=anthropic_model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user}],
        )
    log.info("LLM cascade: Anthropic last-resort used")
    parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
    return "".join(parts).strip()


def call_user_only(prompt: str, *, max_tokens: int = 4000) -> str:
    """Convenience for one-shot prompt without a system message."""
    return call_text_cascade("", prompt, max_tokens=max_tokens)
