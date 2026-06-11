"""Central LLM / model canon (PD 2026-06-11).

ONE place for every model name. A swap (e.g. the broken gpt-5 → gpt-4.1) now
propagates everywhere instead of being hardcoded across llm_cascade /
writer_director / photo_selector / scripts and silently rotting in five files —
the same "fixed in one, stale in five" problem the character canon solved.

⚠️ gpt-5 LESSON (do not repeat): gpt-5 is a REASONING model. As a plain text-
completion model it spends its token budget on internal reasoning and returns
EMPTY visible output, and on big prompts it blows past the 45s timeout. That
silently dropped the WHOLE cascade (OpenAI→Gemini→Anthropic) to Anthropic-only for
weeks — slow + churny. NEVER put a reasoning model in a text-completion slot here.

Every name is env-overridable so PD can tune without code changes. The companion
guard scripts/check_canon.py fails if a banned model (gpt-5) reappears.
"""
from __future__ import annotations

import os


def _m(env: str, default: str) -> str:
    return os.getenv(env, default) or default


# ── Text completion — the OpenAI → Gemini → Anthropic cascade ──────────────
OPENAI_TEXT = _m("OPENAI_FALLBACK_MODEL", "gpt-4.1")      # NOT gpt-5 (reasoning → empty/timeout)
GEMINI_TEXT = _m("GEMINI_FALLBACK_MODEL", "gemini-2.5-pro")
ANTHROPIC_TEXT = _m("ANTHROPIC_TEXT_MODEL", "claude-opus-4-7")
ANTHROPIC_LIGHT = _m("ANTHROPIC_LIGHT_MODEL", "claude-sonnet-4-6")  # caption agent / lighter passes
CAPTION_JUDGE = _m("CAPTION_JUDGE_MODEL", OPENAI_TEXT)

# ── Vision (VLM tagging / review / scene+character gates) ───────────────────
VLM = _m("VLM_MODEL", "gemini-2.5-flash")

# ── Image generation ───────────────────────────────────────────────────────
IMAGE_GEN = _m("IMAGE_GEN_MODEL", "gemini-2.5-flash-image")  # regen / scene stills
OPENAI_IMAGE = _m("OPENAI_IMAGE_MODEL", "gpt-image-2")        # PD: character image gen

# Models that must NEVER be used as a text-completion model here (guarded).
BANNED_TEXT_MODELS = ("gpt-5", "gpt-5-mini", "o1", "o3")
