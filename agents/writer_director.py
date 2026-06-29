"""
agents/writer_director.py — Story-focused Writer + cinematography Director.

Replaces the single-pass Producer concept proposal with:
  Writer (3-pass: draft → self-critique → revise) → story-only concept
       ↓
  Director (1-pass) → adds shot size, camera move, lighting, veo/regen/motion prompts

All passes use Claude Opus 4.7. Few-shot exemplars are pulled from past concepts
with Giri review score ≥ 8.

Public entry:
    propose_concepts_v2(target_date, context, style_filter=None) -> list[dict]

The returned dict matches the legacy producer_propose.md output schema, so the
Cameraman pipeline doesn't need changes.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

from agents import models as _models
import re
import sqlite3
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
from zoneinfo import ZoneInfo as _ZoneInfo
KST = _ZoneInfo("Asia/Seoul")
load_dotenv(ROOT / ".env")
log = logging.getLogger("agents.writer_director")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
PROMPTS_DIR = ROOT / "agents" / "prompts"

WRITER_STORY_PROMPT = PROMPTS_DIR / "writer_story.md"
WRITER_REALFOOTAGE_PROMPT = PROMPTS_DIR / "writer_realfootage.md"
WRITER_CRITIQUE_PROMPT = PROMPTS_DIR / "writer_critique.md"
WRITER_REVISE_PROMPT = PROMPTS_DIR / "writer_revise.md"
DIRECTOR_SHOTS_PROMPT = PROMPTS_DIR / "director_shots.md"
CAPTION_AGENT_PROMPT = PROMPTS_DIR / "caption_agent.md"
CAMERAMAN_VALIDATOR_PROMPT = PROMPTS_DIR / "cameraman_validator.md"

CHARACTER_SHEETS = PROMPTS_DIR / "character_sheets.md"
SORA_LESSONS = ROOT / "notes" / "sora2_motion_lessons.md"
# PD 2026-06-13: editing/clip-selection JUDGMENT guide — agent decides format/tempo/
# trim/length from the original creative intent (not hardcoded). See editing_direction.md.
EDITING_DIRECTION = PROMPTS_DIR / "editing_direction.md"
# Companion PALETTE of diverse editing techniques to choose from (anti-default-to-one).
EDITING_TECHNIQUES = PROMPTS_DIR / "editing_techniques.md"
PROVEN_MOTION_PROMPTS = ROOT / "notes" / "proven_motion_prompts.json"

WRITER_MODEL = os.getenv("WRITER_MODEL", _models.ANTHROPIC_TEXT)
DIRECTOR_MODEL = os.getenv("DIRECTOR_MODEL", _models.ANTHROPIC_TEXT)

# Few-shot threshold
GIRI_FEWSHOT_MIN = float(os.getenv("GIRI_FEWSHOT_MIN", "8.0"))
GIRI_FEWSHOT_N = int(os.getenv("GIRI_FEWSHOT_N", "2"))


# ──────────────────────────────────────────────────────────────────────
# LLM wrapper
# ──────────────────────────────────────────────────────────────────────
def _call_anthropic(system: str, user: str, *, model: str,
                    max_tokens: int = 16000,
                    cache_system: bool = True) -> str:
    """LLM cascade for the HEAVY Writer/Director generations: Anthropic (primary,
    prompt-cached) → OpenAI gpt-4.1 → Gemini 2.5 Pro.

    These calls pair a ~25KB system prompt with a ~16k-token JSON output. Anthropic
    is primary here because (a) its 5-min ephemeral prompt cache gives ~90% input
    discount on the system repeated across a concept's many calls, and (b) it actually
    COMPLETES the long JSON within budget — whereas gpt-4.1/Gemini on the short
    fail-fast timeout cannot emit 16k output tokens in time and reliably time out,
    stalling the run and forcing the slow fall-through anyway. The many small, fast
    cascade calls elsewhere stay OpenAI-primary (agents/llm_cascade.py); only these
    heavy generations route Anthropic-first.

    Circuit breakers skip a provider that just failed so a down provider doesn't burn
    the timeout on every call. `model` applies to the Anthropic call.
    """
    from agents import circuit
    # 1) Anthropic primary — prompt-cached, completes the 25KB-system + 16k-JSON call
    if not circuit.is_down("anthropic"):
        try:
            out = _call_anthropic_raw(system, user, model=model,
                                      max_tokens=max_tokens, cache_system=cache_system)
            circuit.mark_up("anthropic")
            return out
        except Exception as e:
            circuit.mark_down("anthropic")
            log.warning("Anthropic primary failed (%s) — trying OpenAI", e)
    else:
        log.info("writer_director: skip Anthropic (circuit open) → OpenAI")
    # 2) OpenAI fallback
    if not circuit.is_down("openai"):
        try:
            out = _call_openai_fallback(system, user, max_tokens=max_tokens)
            circuit.mark_up("openai")
            return out
        except Exception as e:
            circuit.mark_down("openai")
            log.warning("OpenAI fallback failed (%s) — trying Gemini", e)
    else:
        log.info("writer_director: skip OpenAI (circuit open) → Gemini")
    # 3) Gemini last fallback (only if Anthropic + OpenAI both down)
    return _call_gemini_fallback(system, user, max_tokens=max_tokens)


def _call_anthropic_raw(system: str, user: str, *, model: str,
                          max_tokens: int = 16000,
                          cache_system: bool = True) -> str:
    """Pure Anthropic call (no fallback). Used directly when we need to
    fail explicitly, and wrapped by _call_anthropic for the fallback path."""
    client = anthropic.Anthropic()
    if cache_system:
        system_param = [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]
    else:
        system_param = system
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_param,
        messages=[{"role": "user", "content": user}],
    )
    usage = getattr(msg, "usage", None)
    if usage is not None:
        log.info("LLM usage: input=%s cache_created=%s cache_read=%s output=%s",
                 getattr(usage, "input_tokens", 0),
                 getattr(usage, "cache_creation_input_tokens", 0),
                 getattr(usage, "cache_read_input_tokens", 0),
                 getattr(usage, "output_tokens", 0))
    # PD 2026-06-09: detect truncation (output hit max_tokens) → raise so the caller
    # retries with a higher limit instead of silently passing a cut-off JSON.
    if getattr(msg, "stop_reason", "") == "max_tokens":
        raise RuntimeError(f"anthropic output truncated (max_tokens={max_tokens})")
    parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
    return "".join(parts).strip()


def _call_openai_fallback(system: str, user: str, max_tokens: int = 16000) -> str:
    """OpenAI gpt-4.1 fallback. Loses Anthropic prompt caching but keeps the
    pipeline productive."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed")
    client = OpenAI(timeout=int(os.getenv("LLM_TIMEOUT_S", "45")), max_retries=0)
    model = os.environ.get("OPENAI_FALLBACK_MODEL", _models.OPENAI_TEXT)
    # PD 2026-06-09: pass the output-token budget (was unset → could truncate the
    # large Director JSON). gpt-4.1 uses max_completion_tokens.
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_completion_tokens=max_tokens,
    )
    log.info("OpenAI fallback used (model=%s)", model)
    if resp.choices and resp.choices[0].finish_reason == "length":
        raise RuntimeError(f"openai output truncated (max={max_tokens})")
    return (resp.choices[0].message.content or "").strip()


def _call_gemini_fallback(system: str, user: str, max_tokens: int = 16000) -> str:
    """Gemini 2.5 Pro fallback — the last hop when Anthropic AND OpenAI are
    both unavailable (PD 2026-06-02 chain: Anthropic → OpenAI → Gemini)."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set — Gemini fallback unavailable")
    # PD 2026-06-08: NEW google.genai SDK with an http timeout. The legacy
    # google.generativeai SDK ignored timeouts on DNS failures and hung 600s per
    # call (intermittent googleapis DNS flakiness) — that stalled av slots for ~40min.
    from google import genai as _genai
    from google.genai import types as _gtypes
    gclient = _genai.Client(api_key=api_key, http_options=_gtypes.HttpOptions(
        timeout=int(os.getenv("LLM_TIMEOUT_S", "45")) * 1000))
    model_name = os.environ.get("GEMINI_FALLBACK_MODEL", _models.GEMINI_TEXT)
    # PD 2026-06-09 (THE truncation bug): Gemini defaults max_output_tokens to ~8k,
    # so the Director's ~20k-token JSON was silently CUT OFF mid-sentence (set_description
    # ended at 'SOUTH wall', motion_prompt mid-word) → Validator blocked the concept.
    # Set the real budget + detect MAX_TOKENS truncation → raise.
    resp = gclient.models.generate_content(
        model=model_name, contents=user,
        config=_gtypes.GenerateContentConfig(
            system_instruction=system or None,
            max_output_tokens=max_tokens),
    )
    log.info("Gemini fallback used (model=%s)", model_name)
    try:
        fr = str((resp.candidates or [{}])[0].finish_reason or "")
        if "MAX_TOKENS" in fr.upper():
            raise RuntimeError(f"gemini output truncated (max_output_tokens={max_tokens})")
    except (IndexError, AttributeError):
        pass
    return (resp.text or "").strip()


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()


def _parse_json_loose(text: str) -> Any:
    """Parse JSON, tolerating fences and surrounding prose."""
    t = _strip_fences(text)
    # strict=False tolerates raw control chars (unescaped newlines/tabs in a string).
    try:
        return json.loads(t, strict=False)
    except json.JSONDecodeError:
        # Try to find a JSON array or object inside, with a trailing-comma repair
        # (PD 2026-06-09: common LLM-fallback malformation).
        for pattern in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
            m = re.search(pattern, t)
            if m:
                frag = m.group(0)
                try:
                    return json.loads(frag, strict=False)
                except json.JSONDecodeError:
                    return json.loads(re.sub(r',\s*([}\]])', r'\1', frag), strict=False)
        raise


# ──────────────────────────────────────────────────────────────────────
# Few-shot retrieval from DB
# ──────────────────────────────────────────────────────────────────────
def _few_shots_from_db(con: sqlite3.Connection, *,
                       min_score: float = GIRI_FEWSHOT_MIN,
                       n: int = GIRI_FEWSHOT_N) -> list[dict]:
    """Find past concepts with Giri review score >= min_score.

    Strategy: take retry_log rows with giri_score >= min and verdict='업로드',
    map their card_id back to the daily_proposals.finalized_json that produced
    them (best-effort: match by date). Return up to n distinct concepts.
    """
    try:
        rows = con.execute(
            """
            SELECT DISTINCT rl.card_id, rl.giri_score, c.date, c.payload_json,
                   c.render_style, c.theme
            FROM retry_log rl
            JOIN cards c ON c.card_id = rl.card_id
            WHERE rl.giri_score >= ? AND rl.giri_verdict = '업로드'
            ORDER BY rl.giri_score DESC, rl.created_at DESC
            LIMIT ?
            """,
            (min_score, n * 3),  # pull extras in case some payloads are malformed
        ).fetchall()
    except sqlite3.OperationalError:
        # retry_log or cards table missing — silently fall back
        return []

    out = []
    seen_dates = set()
    for r in rows:
        if r["date"] in seen_dates:
            continue
        try:
            payload = json.loads(r["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        # Try to find the concept in daily_proposals (richer than card payload)
        prop_row = con.execute(
            """
            SELECT finalized_json FROM daily_proposals
            WHERE target_date = ? AND status IN ('confirmed', 'produced', 'published')
            ORDER BY id DESC LIMIT 1
            """,
            (r["date"],),
        ).fetchone()

        concepts = []
        if prop_row and prop_row["finalized_json"]:
            try:
                concepts = json.loads(prop_row["finalized_json"])
            except json.JSONDecodeError:
                concepts = []

        # Pick the concept matching this card's render_style + theme
        match = None
        if concepts:
            for c in concepts:
                if c.get("render_style") == r["render_style"] and (
                    r["theme"] is None or c.get("title", "").startswith(str(r["theme"])[:8])
                ):
                    match = c
                    break
            if not match:
                match = concepts[0]

        out.append({
            "score": r["giri_score"],
            "date": r["date"],
            "render_style": r["render_style"],
            "concept": match or payload,
        })
        seen_dates.add(r["date"])
        if len(out) >= n:
            break

    return out


# ──────────────────────────────────────────────────────────────────────
# Writer (3-pass)
# ──────────────────────────────────────────────────────────────────────
def _build_writer_user_prompt(target_date: dt.date, context: dict,
                              n_concepts: int, style_filter: str | None,
                              few_shots: list[dict]) -> str:
    """Build the user prompt body for the Writer's draft pass."""
    body: dict[str, Any] = {
        "target_date": target_date.isoformat(),
        "n_concepts_required": n_concepts,
        "style_filter": style_filter,
        "context": context,
    }
    # PD 2026-06-13: surface the MACRO context + any Reviewer rewrite directive at the
    # top level so the Writer can't miss them — avoid repeating recent uploads.
    if isinstance(context, dict) and context.get("macro_context"):
        body["macro_context_recent_uploads"] = context["macro_context"]
    if isinstance(context, dict) and context.get("reviewer_feedback"):
        body["reviewer_rewrite_directive"] = context["reviewer_feedback"]
    # PD 2026-06-12: is a PD-designated concept active? (arc_directive carries the
    # "[PD 지정 컨셉 …]" prefix.) If so, the few-shots — which are recent high-Giri
    # concepts and may be the very episodes PD just rejected (e.g. the cafe ones) —
    # must NOT pull the writer back to their SUBJECT/LOCATION/PREMISE. They were
    # anchoring the AV writer back to "카페 첫 방문" despite a snack-time directive.
    _ctx_blob = json.dumps(context, ensure_ascii=False) if isinstance(context, dict) else str(context)
    _pd_directive_active = "[PD 지정 컨셉" in _ctx_blob or "PD 지정" in _ctx_blob
    # PD 2026-06-12: a "few-shot note" alone did NOT stop the AV writer drifting to
    # an exemplar's premise — recent high-Giri food/gag concepts re-anchored a
    # '빛나는 소라' 세계관 훅 directive into "사료 폭풍" (a feeding gag). The exemplar
    # CONCEPT TEXT is the anchor, not the note. So when a PD-designated concept is
    # active, drop the exemplars entirely (channel tone already lives in the system
    # prompt). Reversible via PD_DIRECTIVE_DROPS_FEWSHOTS=0.
    _drop_fewshots = _pd_directive_active and os.getenv("PD_DIRECTIVE_DROPS_FEWSHOTS", "1") != "0"
    if _drop_fewshots:
        body["few_shot_note"] = (
            "⚠️ A PD-DESIGNATED concept is active (context.arc_directive, marked "
            "'[PD 지정 컨셉 — 최우선]'). The PD directive ALONE decides WHAT this episode is "
            "about — subject, location, premise, beats. No few-shot exemplars are "
            "provided ON PURPOSE: recent high-score concepts (food/cafe/gag episodes) "
            "would anchor you back to their premise, which is a hard failure here. "
            "Build the concept from the PD directive only; for caption voice use the "
            "channel's general tone (TV동물농장 추측형·속마음 / 세나개), not any specific past episode."
        )
    elif few_shots:
        body["few_shot_exemplars"] = [
            {
                "from_date": fs["date"],
                "giri_score": fs["score"],
                "render_style": fs["render_style"],
                "concept": fs["concept"],
            }
            for fs in few_shots
        ]
        body["few_shot_note"] = (
            "These past concepts scored ≥8 in Giri review. Use them as quality "
            "anchors for caption tone, story arc, and beat structure. Do NOT "
            "copy them — they are exemplars, not templates."
        )

    if style_filter:
        instruction = (
            f"Output EXACTLY 1 concept with render_style='{style_filter}'. "
            "Return a JSON array of length 1."
        )
    else:
        instruction = (
            "Output EXACTLY 2 concepts: 1 with render_style='ai_vtuber' AND "
            "1 with render_style='real_footage'. Return a JSON array of length 2."
        )

    # PD 2026-06-02: force a specific real_footage editing concept for A/B
    # testing. Valid: rapid_montage / long_take / twist_ending /
    # themed_compilation / photo_i2v / split_screen / slow_mo / before_after
    # / cross_cutting.
    forced = os.getenv("FORCE_EDITING_CONCEPT", "").strip().lower()
    if forced:
        body["forced_editing_concept"] = forced
        instruction += (
            f" The real_footage concept MUST use editing_concept='{forced}' "
            "(see writer_story.md a-i mapping). Set top-level "
            f"`editing_concept: \"{forced}\"` and align per-cut "
            "`edit_effect` to match. Use the concept's signature pattern — "
            "do NOT silently fall back to rapid_montage. Explain your "
            "asset→concept mapping in `rationale`."
        )

    body["output_instruction"] = instruction
    # Validator revision feedback (PD 2026-06-02 retry path)
    revision_feedback = context.get("_revision_feedback")
    if revision_feedback:
        body["validator_revision_feedback"] = revision_feedback
        body["output_instruction"] = (
            "This is a REVISION pass. A previous attempt was blocked by the "
            "Cameraman Validator. Read `validator_revision_feedback`, fix "
            "the listed Tier-1 issues, and re-output the concept. Keep the "
            "beat structure when possible — change only what the validator "
            "flagged. " + instruction
        )
    return json.dumps(body, ensure_ascii=False, default=str)


def run_writer(target_date: dt.date, context: dict, *,
               n_concepts: int = 2, style_filter: str | None = None,
               few_shots: list[dict] | None = None,
               progress_cb=None) -> list[dict]:
    """Run the 3-pass Writer: draft → self-critique → revise.

    PD 2026-06-03: when style_filter='real_footage', uses the specialized
    real_footage prompt (writer_realfootage.md) instead of the generic
    writer_story.md. The generic writer kept hallucinating dramatic
    narratives (X 대신 Y 이겼어요 / 범인 누구일까요) on observational clips
    because its few_shots and prompt anchor toward TV동물농장 dramaturgy.
    The real_footage prompt is observational and asset-grounded."""
    if few_shots is None:
        few_shots = []

    is_realfootage = (style_filter or "").lower() == "real_footage"
    if is_realfootage:
        try:
            story_system = WRITER_REALFOOTAGE_PROMPT.read_text(encoding="utf-8")
            if progress_cb:
                progress_cb(":pencil: Real_footage Writer (specialized — draft only, no critique/revise)")
            # Filter few_shots to real_footage only — TV동물농장 dramaturgy
            # exemplars would contaminate the observational tone.
            few_shots = [fs for fs in few_shots
                         if (fs.get("render_style") or "").lower() == "real_footage"]
        except FileNotFoundError:
            log.warning("writer_realfootage.md not found — falling back to generic")
            story_system = WRITER_STORY_PROMPT.read_text(encoding="utf-8")
            is_realfootage = False
    else:
        story_system = WRITER_STORY_PROMPT.read_text(encoding="utf-8")
    # PD 2026-06-13: give the Writer the editing/selection JUDGMENT guide too, so the
    # story's intent already considers format/tempo/caption-timing (agent decides).
    try:
        story_system += "\n\n---\n\n## 편집·클립선택 판단 가이드\n\n" \
            + EDITING_DIRECTION.read_text(encoding="utf-8") \
            + "\n\n---\n\n" + EDITING_TECHNIQUES.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("editing guides unreadable (writer): %s", e)
    critique_system = WRITER_CRITIQUE_PROMPT.read_text(encoding="utf-8")
    revise_system = WRITER_REVISE_PROMPT.read_text(encoding="utf-8")

    # ── Pass 1: Draft ──
    if progress_cb:
        progress_cb(":pencil: Writer pass 1/3 — draft 작성 중...")
    draft_user = _build_writer_user_prompt(
        target_date, context, n_concepts, style_filter, few_shots
    )
    draft_text = _call_anthropic(
        story_system, draft_user, model=WRITER_MODEL, max_tokens=16000
    )
    try:
        draft = _parse_json_loose(draft_text)
    except json.JSONDecodeError as e:
        log.error("Writer draft JSON parse failed: %s\nfirst 500:\n%s", e, draft_text[:500])
        raise RuntimeError(f"Writer draft pass produced non-JSON: {e}")

    if not isinstance(draft, list) or not draft:
        raise RuntimeError(f"Writer draft pass returned wrong shape: {type(draft).__name__}")

    # PD 2026-06-03: route critique/revise to specialized prompts for
    # real_footage. Generic critique focused on "스토리 아크" which forced
    # Writer to merge observational cuts into single dramatic narrative.
    # Real_footage critique/revise instead focus on per-cut uniqueness +
    # asset-fidelity (does each cut's action match its asset_id's sc?).
    if is_realfootage:
        try:
            critique_system = (PROMPTS_DIR / "writer_critique_realfootage.md").read_text(encoding="utf-8")
            revise_system = (PROMPTS_DIR / "writer_revise_realfootage.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            log.warning("real_footage critique/revise prompts missing — using generic")

    # ── Pass 2: Self-critique ──
    if progress_cb:
        progress_cb(":mag: Writer pass 2/3 — self-critique 중...")
    critique_user = json.dumps(
        {"draft_concepts": draft},
        ensure_ascii=False, default=str,
    )
    critique_text = _call_anthropic(
        critique_system, critique_user, model=WRITER_MODEL, max_tokens=8000
    )
    try:
        critique = _parse_json_loose(critique_text)
    except json.JSONDecodeError as e:
        log.warning("Critique JSON parse failed (continuing without revise): %s", e)
        if progress_cb:
            progress_cb(":warning: critique 파싱 실패 — draft 그대로 사용")
        return draft

    # ── Pass 3: Revise ──
    if progress_cb:
        weakest_links = [
            c.get("weakest_link", "?")
            for c in critique.get("critiques", [])
        ]
        progress_cb(f":wrench: Writer pass 3/3 — revise (weak: {' / '.join(weakest_links)[:120]})")
    revise_user = json.dumps(
        {"previous_draft": draft, "critique": critique},
        ensure_ascii=False, default=str,
    )
    revise_text = _call_anthropic(
        revise_system, revise_user, model=WRITER_MODEL, max_tokens=16000
    )
    try:
        revised = _parse_json_loose(revise_text)
    except json.JSONDecodeError as e:
        log.warning("Revise JSON parse failed (falling back to draft): %s", e)
        if progress_cb:
            progress_cb(":warning: revise 파싱 실패 — draft 그대로 사용")
        return draft

    if not isinstance(revised, list) or not revised:
        log.warning("Revise returned wrong shape — falling back to draft")
        return draft

    return revised


# ──────────────────────────────────────────────────────────────────────
# Director (1-pass)
# ──────────────────────────────────────────────────────────────────────
def _build_director_user_prompt(story_concepts: list[dict],
                                set_library: list[dict],
                                object_references: list[dict],
                                set_objects: list[dict] | None = None,
                                pd_background_refs: list[dict] | None = None,
                                character_knowledge: list[dict] | None = None,
                                character_objects: list[dict] | None = None) -> str:
    """Build the Director's user prompt with story + reference materials."""
    body = {
        "story_concepts": story_concepts,
        "set_library": set_library,
        "object_references": object_references,
        "set_objects": set_objects or [],
        "pd_background_refs": pd_background_refs or [],
        "character_knowledge": character_knowledge or [],
        "character_objects": character_objects or [],
        "instruction": (
            "Add cinematography fields (shot_size, camera_move, angle, lighting, "
            "action_beats) and per-cut prompts (veo_prompt for text_to_video, "
            "regen_prompt + motion_prompt for image_to_video) to every cut. "
            "Preserve all Writer-authored fields verbatim (beat, who, space, action, "
            "transition_in, duration_seconds, captions, function, episode_date, "
            "episode_time). "
            "**Use Writer's `episode_date` + `episode_time` + the chosen "
            "set_anchor's `window_directions` from set_library** to write an "
            "ACCURATE lighting description in `set_description` — sun position, "
            "color temperature, intensity, which window(s) currently let light "
            "in. Seoul latitude ~37.5°N; compute sunrise/sunset by season. The "
            "same lighting goes in every cut's prepended set_description so the "
            "episode's time-of-day stays consistent unless the story explicitly "
            "advances time across cuts. "
            "**When humans appear in a cut**, use `character_knowledge[]` "
            "(VLM-learned appearance) + `character_objects[]` (recurring "
            "outfits/hair/accessories) to describe their body, clothes, hair "
            "concretely. Always pair with one face-hiding technique (`framed "
            "from neck down` / `from behind` / `low pet eye-level angle` / "
            "`face cropped by foreground`). Never invent appearance stereotypes; "
            "if a character has no character_knowledge yet, write a very generic "
            "body description and lean on the face-hiding angle. "
            "Also add concept-level regen_direction and set_anchor."
        ),
    }
    return json.dumps(body, ensure_ascii=False, default=str)


def run_director(story_concepts: list[dict], context: dict,
                 progress_cb=None) -> list[dict]:
    """Run the 1-pass Director: add cinematography to each cut."""
    director_system_base = DIRECTOR_SHOTS_PROMPT.read_text(encoding="utf-8")

    # PD 2026-06-12: the Director system was ~91KB (director_shots 50KB + these refs
    # ~41KB) + 20K output tokens → OpenAI/Gemini TIME OUT on the request (only the slow
    # Anthropic fallback finished, so every concept pass crawled). CAP each injected
    # REFERENCE so the prompt stays lean — the core rules live in director_shots.md.
    # Env DIRECTOR_REF_MAXCHARS (per ref).
    _refcap = int(os.getenv("DIRECTOR_REF_MAXCHARS", "5000"))

    def _ref(title: str, path, fmt_json: bool = False):
        try:
            t = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        if len(t) > _refcap:
            t = t[:_refcap] + "\n…(이하 생략 — 핵심만)"
        return (f"## {title}\n\n```json\n{t}\n```" if fmt_json
                else f"## {title}\n\n{t}")

    refs = [r for r in (
        _ref("EDITING & CLIP-SELECTION JUDGMENT", EDITING_DIRECTION),
        _ref("EDITING TECHNIQUE PALETTE", EDITING_TECHNIQUES),
        _ref("CHARACTER SHEETS", CHARACTER_SHEETS),
        _ref("VEO/SORA MOTION LESSONS", SORA_LESSONS),
        _ref("PROVEN MOTION PROMPTS", PROVEN_MOTION_PROMPTS, fmt_json=True),
    ) if r]

    director_system = director_system_base + "\n\n---\n\n" + "\n\n---\n\n".join(refs)

    if progress_cb:
        progress_cb(":clapper: Director — 시네마토그래피 패스 중...")

    user = _build_director_user_prompt(
        story_concepts,
        context.get("set_library", []),
        context.get("object_references", []),
        context.get("set_objects", []),
        context.get("pd_background_refs", []),
        context.get("character_knowledge", []),
        context.get("character_objects", []),
    )
    out_text = _call_anthropic(
        director_system, user, model=DIRECTOR_MODEL,
        # PD 2026-06-16: 9000 truncated the per-cut cinematography (rich regen/
        # motion prompts ×6 cuts) → Director raised "output truncated" → the whole
        # writer_director path fell back to legacy (no tags/set_anchor/wink). The
        # Writer passes already use 16000; match it so the Director can finish.
        max_tokens=int(os.getenv("DIRECTOR_MAX_TOKENS", "16000"))
    )
    try:
        out = _parse_json_loose(out_text)
    except json.JSONDecodeError as e:
        log.error("Director JSON parse failed: %s\nfirst 500:\n%s", e, out_text[:500])
        raise RuntimeError(f"Director pass produced non-JSON: {e}")

    # Normalize to the list-of-concepts shape the pipeline expects. Anthropic (now the
    # primary for these heavy calls) tends to return EITHER a single concept object
    # {title, cuts, ...} OR a wrapper {"concepts": [...]} — both valid JSON but not the
    # bare list OpenAI used to emit. Unwrap them instead of falling all the way back to
    # the legacy single-pass (which loses the Writer/Director quality + director_shots
    # rules). A bare single concept → wrap in a 1-element list.
    if isinstance(out, dict):
        if out.get("cuts") or out.get("title") or out.get("theme"):
            out = [out]  # a single concept object
        else:
            # Wrapper dict: pull the concepts list out of whatever key it used. Try
            # known names first, then ANY non-empty list value (Anthropic varies the
            # wrapper key), then a tag-keyed dict of concept objects as last resort.
            known = ("concepts", "cuts_concepts", "results", "items", "storyboard",
                     "output", "episodes", "shots", "directed", "director_output")
            picked = None
            for k in known:
                if isinstance(out.get(k), list) and out[k]:
                    picked = out[k]
                    break
            if picked is None:
                list_vals = [v for v in out.values() if isinstance(v, list) and v]
                if list_vals:
                    picked = list_vals[0]
                elif out and all(isinstance(v, dict) for v in out.values()):
                    picked = list(out.values())  # tag-keyed dict of concepts
            if picked is None:
                log.error("Director dict not normalizable; keys=%s", list(out.keys()))
            out = picked if picked is not None else out

    if not isinstance(out, list) or not out:
        raise RuntimeError(f"Director returned wrong shape: {type(out).__name__}")

    return out


# ──────────────────────────────────────────────────────────────────────
# Public entry
# ──────────────────────────────────────────────────────────────────────
def propose_concepts_v2(target_date: dt.date, context: dict, *,
                        style_filter: str | None = None,
                        progress_cb=None,
                        con: sqlite3.Connection | None = None) -> list[dict]:
    """Writer (3-pass) → Director (1-pass) → list[concept].

    Output shape matches the legacy producer_propose.md output so the rest of
    the Producer pipeline (photo_selector, retry_loop, cameraman) is unchanged.
    """
    own_con = False
    if con is None:
        con = sqlite3.connect(DB_PATH, timeout=30)
        con.row_factory = sqlite3.Row
        own_con = True

    try:
        n_concepts = 1 if style_filter else 2
        few_shots = _few_shots_from_db(con)
        if progress_cb and few_shots:
            score_str = ", ".join("{:.1f}".format(fs["score"]) for fs in few_shots)
            progress_cb(
                f":sparkles: Few-shot 예시 {len(few_shots)}개 로드 (scores: {score_str})"
            )

        story = run_writer(
            target_date, context,
            n_concepts=n_concepts,
            style_filter=style_filter,
            few_shots=few_shots,
            progress_cb=progress_cb,
        )

        directed = run_director(story, context, progress_cb=progress_cb)
        # Caption Agent (2026-06-02, PD-driven): specialized narrator-script
        # pass between Director and Polisher. Takes Director's cuts (with
        # action_beats / motion_prompt) and rewrites captions for TV동물농장
        # tone, scene_setter, mixed registers, action-first timing.
        captioned = run_caption_agent(directed, progress_cb=progress_cb)
        polished = run_caption_polisher(captioned, progress_cb=progress_cb)
        # PD 2026-06-09: auto-enrich thin set_descriptions from set_library BEFORE
        # validation, so a short Director description gets a rich learned-room
        # background instead of being blocked into an empty slot.
        polished = _enrich_thin_set_descriptions(polished)
        # Cameraman Validator (2026-06-02): pre-dispatch sanity gate.
        # Catches causal/physical/spatial incoherence before Seedance burns
        # cost. On verdict='blocked', RETRY with Writer revision pass (up
        # to 1 retry per concept), not skip — PD 2026-06-02 correction.
        polished = run_cameraman_validator(polished, progress_cb=progress_cb)
        polished = _retry_blocked_concepts(
            polished, target_date, context, style_filter, few_shots,
            progress_cb=progress_cb,
        )
        # Per-agent verification (PD 2026-05-31): hard-validate captions and
        # auto-fix any that slipped past Polisher with targeted LLM calls.
        try:
            from agents.verifiers import (
                verify_captions, auto_fix_captions, verify_director_cuts
            )
            ok, findings = verify_captions(polished)
            if not ok:
                if progress_cb:
                    progress_cb(
                        f":mag: Caption verifier — {len(findings)}개 위반 발견, "
                        "targeted fix 시도 중..."
                    )
                polished, n_fixed = auto_fix_captions(polished)
                if progress_cb:
                    progress_cb(f":sparkles: Caption verifier — {n_fixed}개 fix 적용")
            dir_ok, dir_findings = verify_director_cuts(polished)
            if not dir_ok:
                log.warning("Director verifier: %d issues (soft, not blocking): %s",
                            len(dir_findings),
                            [(f["cut_tag"], f["issues"]) for f in dir_findings[:3]])
                if progress_cb and dir_findings:
                    progress_cb(
                        f":warning: Director verifier soft-warn: "
                        f"{len(dir_findings)} cuts have issues (logged)"
                    )
        except Exception as e:
            log.warning("verifier wiring failed (non-fatal): %s", e)
        return _normalize_for_downstream(polished)
    finally:
        if own_con:
            con.close()


# ──────────────────────────────────────────────────────────────────────
# Caption Polisher (2026-05-31, PD-driven redesign)
# ──────────────────────────────────────────────────────────────────────
CAPTION_POLISHER_SYSTEM = """\
You are the "Caption Polisher" for the Ryani & Leo YouTube Shorts channel.
You receive a finished storyboard (cuts with action_beats + tempo + initial
captions) and you replace each cut's captions with a polished, broadcast-
ready version.

**CRITICAL FIELD SEPARATION (most common bug):**
- `ko` field MUST contain ONLY Korean text. No English. No `\\n` line breaks. ONE short phrase.
- `en` field MUST contain ONLY English text. No Korean. No `\\n` line breaks. ONE short phrase.
- NEVER write `"ko": "한국어\\nEnglish here"` with empty `"en": ""`. This is the bug we are fixing.
- Each scene MUST have BOTH ko and en populated.

**Hard rules (NON-NEGOTIABLE):**

1. **종결 어미 = "해요/아요/어요/네요/죠/거든요" 체 only.**
   - ❌ "신호입니다" → ✅ "신호예요"
   - ❌ "벌어졌습니다" → ✅ "벌어졌어요"
   - ❌ "있었습니다" → ✅ "있었어요"

2. **각 scene `ko` ≤ 14자, `en` ≤ 28자.** 길면 다음 scene으로 split.

3. **SOV 어순 (Subject-Object-Verb).** 영어식 어순 금지.
   - ❌ "랴니가 보냈어요 신호를"  (V before O)
   - ✅ "랴니가 신호를 보냈어요"

4. **추상 주어 + "이에요/예요" nothing-sentence 금지.**
   - ❌ "오늘도 이 둘이에요" (의미 비어있음)
   - ✅ "오늘도 둘이 같이 있어요"

5. **Scene 분할로 spoiler 방지.** action_beats에 reveal 액션 시점이 있다면, 그 시점 BEFORE 캡션은 setup, AFTER 캡션은 payoff. 액션 도중에 결과 캡션 노출 금지.

6. **Scene timing은 cut의 duration_seconds 안에 맞추라.** 4초 cut에 5개 scene 우겨넣지 마라. 권장 = 한 scene 2~3초.

7. **`ko` / `en` 필드에 `\\n` 줄바꿈 금지** — render system이 자동 wrap. 강제 `\\n`은 어색한 줄바꿈 생성.

8. **`caption_position`**: pets이 frame 하단에 누워있거나 발라당이면 `"top"`, 일반적으로 `"bottom"`.

9. **TV동물농장 narrator voice 유지** — "과연", "아니나 다를까", "그 순간" 같은 서사 연결어를 자연스럽게 활용. 단순 묘사 금지.

**Output schema:** Return a JSON array, one object per cut, with EXACTLY
this structure:
```json
[
  {
    "cut_tag": "cut1_intro",
    "caption_position": "bottom",
    "captions": [
      {"start": 0.0, "end": 2.0, "ko": "짧은 마디", "en": "Short line."},
      {"start": 2.0, "end": 4.5, "ko": "다음 마디예요", "en": "Next line."}
    ]
  },
  ...
]
```

Preserve the cut order. Don't add any prose around the JSON. The cut_tag
must match the input cuts' tags exactly (cut1_intro, cut2_develop, etc.).

**Example — IN vs OUT:**

INPUT (current_captions, the bad merged format we are fixing):
```json
[
  {"start": 0, "end": 5, "ko": "어깨를 낮추고, 엉덩이를 올리고, '웡!' — 11년 경력의 공식 신호입니다\\nChest down, bottom up, 'Woof!' — textbook perfect.", "en": ""}
]
```

CORRECT OUTPUT (this cut split into 3 short scenes, ko/en separate, 해요체):
```json
{
  "cut_tag": "cut2_develop",
  "caption_position": "bottom",
  "captions": [
    {"start": 0.0, "end": 1.5, "ko": "어깨를 낮추고", "en": "Shoulders down,"},
    {"start": 1.5, "end": 3.0, "ko": "엉덩이를 번쩍", "en": "rump straight up,"},
    {"start": 3.0, "end": 5.0, "ko": "'웡!' 11년 경력이에요", "en": "'Woof!' Eleven years of practice."}
  ]
}
```

Note in the correct output: ko is short ≤14ch, en is short ≤28ch, NO `\\n`,
both fields populated separately, 해요체 ("이에요"), action split across
scenes so caption timing matches the dog's motion.
"""


def _build_polisher_user_prompt(concepts: list[dict]) -> str:
    payload = []
    for c in concepts:
        for i, cut in enumerate(c.get("cuts", [])):
            tag = cut.get("cut_tag") or cut.get("tag") or f"cut{i+1}"
            payload.append({
                "cut_tag": tag,
                "duration_seconds": cut.get("duration_seconds", 5),
                "tempo_factor": cut.get("tempo_factor", 1.0),
                "beat": cut.get("beat"),
                "action": cut.get("action") or cut.get("description"),
                "action_beats": cut.get("action_beats", []),
                "current_captions": cut.get("captions", []),
                "function": cut.get("function"),
            })
    return json.dumps(
        {
            "cuts": payload,
            "instruction": (
                "Polish each cut's captions per the system rules. Replace "
                "current_captions with a new array. Match timing to action_beats "
                "so reveals don't spoiler. Output JSON array of polished cuts."
            ),
        },
        ensure_ascii=False,
    )


def _years_ago_phrase(days: int) -> str:
    """PD 2026-06-11: natural Korean time-ago phrase — '0.6년 전' reads awkward.
    <~45d → '' (recent, don't force a time mention); <1y → 'N개월 전'; else 'N년 전'."""
    if days is None or days < 45:
        return ""
    if days < 365:
        m = max(1, round(days / 30.0))
        return f"{m}개월 전"
    return f"{round(days / 365.25)}년 전"


def _stamp_years_ago(concepts: list[dict]) -> None:
    """Stamp cut['years_ago'] (+ cut['time_ago_phrase']) from each cut's asset_id
    captured_iso vs the concept's target date. Memory-lane (PD 2026-06-07): past
    clips must be narrated with their time point. Best-effort; never raises."""
    import datetime as _dt
    import sqlite3 as _sql
    db_path = ROOT / "data" / "agent.db"
    if not db_path.exists():
        return
    try:
        con = _sql.connect(str(db_path))
    except Exception:
        return
    try:
        for c in concepts:
            # target date for the relative calc
            tgt = None
            for k in ("target_date", "date", "episode_date"):
                v = c.get(k)
                if v:
                    try:
                        tgt = _dt.date.fromisoformat(str(v)[:10]); break
                    except Exception:
                        pass
            if tgt is None:
                tgt = _dt.datetime.now(KST).date()
            for cut in c.get("cuts") or []:
                if cut.get("years_ago") is not None:
                    continue
                aid = cut.get("asset_id") or cut.get("secondary_asset_id")
                if not aid:
                    continue
                try:
                    row = con.execute(
                        "SELECT captured_iso FROM assets WHERE asset_id=?", (aid,)
                    ).fetchone()
                except Exception:
                    continue
                if not row or not row[0]:
                    continue
                try:
                    d0 = _dt.date.fromisoformat(str(row[0])[:10])
                    _days = (tgt - d0).days
                    cut["years_ago"] = round(_days / 365.25, 1)
                    cut["time_ago_phrase"] = _years_ago_phrase(_days)
                    # PD 2026-06-30: stamp the subject's life-era at capture so a
                    # memory-lane opener leads with the endearing young era ("아기 레오")
                    # instead of generic season/weather. Deterministic from canon birth.
                    try:
                        from agents import canon as _canon
                        who = (cut.get("who") or "").lower()
                        pet = "leo" if "leo" in who and "ryani" not in who else (
                            "ryani" if "ryani" in who and "leo" not in who else "")
                        era = _canon.age_era_at(pet, str(row[0])) if pet else ""
                        if era:
                            cut["subject_era"] = era
                            cut["subject_era_label"] = f"{era} {'레오' if pet=='leo' else '랴니'}"
                    except Exception:
                        pass
                except Exception:
                    continue
    finally:
        con.close()


def run_caption_agent(concepts: list[dict],
                       progress_cb=None) -> list[dict]:
    """Caption Agent — TV동물농장 narrator script writer (PD 2026-06-02).

    Sits between Director and Polisher. Takes Director's storyboard with
    cinematography fields populated and produces specialized caption text:
    scene-setter, mixed tone registers, action-first timing (start ≥ 1.5s).
    Polisher still runs after for final 종결어미 / SOV cleanup.

    Failures fall back silently to Director's captions (no regression).
    """
    if not concepts:
        return concepts
    if progress_cb:
        progress_cb(":writing_hand: Caption Agent — narrator script 작성 중...")
    try:
        system = CAPTION_AGENT_PROMPT.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("Caption Agent prompt missing — skipping pass")
        return concepts
    # PD 2026-06-02 root-cause fix: ensure every cut has a unique tag BEFORE
    # the Caption Agent prompt is built. Without tags, all cuts would map to
    # the same scripted_cut by None key → 4 cuts get identical captions, and
    # dedup can't differentiate them.
    import re as _re
    for c in concepts:
        for i, cut in enumerate(c.get("cuts") or [], start=1):
            tag = (cut.get("tag") or cut.get("cut_tag") or "").strip()
            if not tag:
                beat = (cut.get("beat") or f"cut{i}").strip()
                slug = _re.sub(r"[^a-zA-Z0-9_]+", "_", beat) or f"cut{i}"
                cut["tag"] = f"cut{i}_{slug}"

    # PD 2026-06-07: stamp years_ago on each cut from its asset's captured_iso so
    # the Caption Agent narrates the time point of PAST (archive) clips. Lane-
    # shared (av + rf). Best-effort — missing date / no asset_id → no stamp.
    _stamp_years_ago(concepts)

    user = _build_caption_agent_user_prompt(concepts)
    try:
        # PD 2026-06-02 3-way competition: run Sonnet + gpt-4.1 + Gemini 2.5 Pro
        # in parallel, judge with Opus 4.7, apply winner. Cost ~$0.08/concept.
        scripted = _caption_agent_competition(system, user, progress_cb=progress_cb)
    except Exception as e:
        log.warning("Caption Agent competition failed (keeping Director captions): %s", e)
        return concepts
    if not isinstance(scripted, list):
        log.warning("Caption Agent returned non-list, ignoring")
        return concepts

    by_tag = {p.get("cut_tag"): p for p in scripted if isinstance(p, dict)}
    n_replaced = 0
    for c in concepts:
        for cut in c.get("cuts", []):
            tag = cut.get("cut_tag") or cut.get("tag")
            scripted_cut = by_tag.get(tag)
            if not scripted_cut:
                continue
            new_caps = scripted_cut.get("captions")
            if isinstance(new_caps, list) and new_caps:
                cut["captions"] = new_caps
                n_replaced += 1
            pos = scripted_cut.get("caption_position")
            if pos in ("top", "bottom"):
                cut["caption_position"] = pos
    log.info("Caption Agent: rewrote captions for %d cuts", n_replaced)
    # PD 2026-06-02: post-Caption-Agent sanity sweeps.
    # PD 2026-06-03 expanded: enforce 추측형 어미 + lane-specific tone +
    # 동물농장 register diversity (programmatic, not prompt-only).
    for c in concepts:
        _split_merged_ko_en(c)
        _enforce_wink_empty_captions(c)
        _validate_korean_characters(c)
        _validate_pet_age_attribution(c)
        _enforce_speculative_endings(c, progress_cb=progress_cb)
        _enforce_lane_tone(c, progress_cb=progress_cb)
        _enforce_min_caption_display(c)
        _rewrite_duplicate_captions(c, progress_cb=progress_cb)
    return concepts


# Mental-state words that must NOT be paired with 단정형 endings.
# When narrator describes pet mental state with these words, must use 추측형.
_MENTAL_STATE_WORDS = [
    "슬프", "기쁘", "행복", "외롭", "심심", "불안", "신나", "흥분", "졸리",
    "피곤", "지루", "심심", "궁금", "당황", "놀라", "삐졌", "토라",
    "사랑", "그리워", "보고싶", "미안", "고마", "화나", "짜증",
]

# 단정형 endings (직접 마지막 1글자 또는 2글자 패턴)
_DECLARATIVE_PATTERNS = [
    r"슬프다$", r"기쁘다$", r"행복하다$", r"외롭다$", r"심심하다$",
    r"불안하다$", r"신난다$", r"흥분한다$", r"졸리다$", r"피곤하다$",
    r"지루하다$", r"궁금하다$", r"당황한다$", r"놀랐다$", r"삐졌다$",
    r"토라졌다$", r"사랑한다$", r"그립다$", r"보고싶다$", r"미안하다$",
    r"화났다$", r"짜증난다$",
]

# 추측형 권장 어미
_SPECULATIVE_HINTS = [
    "인가 봐요", "한가 봐요", "는가 봐요", "모양입니다", "모양이에요",
    "듯합니다", "듯해요", "인 듯", "한 듯",
]


def _enforce_speculative_endings(c: dict, progress_cb=None) -> None:
    """PD 2026-06-03: TV동물농장 narrator signature is 추측형 어미 (`~인가
    봐요` / `~모양입니다`) when commenting on pet mental state. Scan all
    captions for 단정형 declarative endings paired with mental-state words
    and either auto-fix or log a warning. This makes the prompt rule
    pipeline-enforced rather than agent-promise."""
    import re as _re
    for cut in c.get("cuts") or []:
        for cap in cut.get("captions") or []:
            ko = (cap.get("ko") or "").strip()
            if not ko:
                continue
            # Skip character POV (「레오: ~」 or (랴니의 ~)) — those are
            # direct quotes, not narrator observations.
            if ko.startswith("「") or ko.startswith("(") or ":" in ko[:6]:
                continue
            # Check for 단정형 mental-state ending
            for pat in _DECLARATIVE_PATTERNS:
                if _re.search(pat, ko):
                    log.warning(
                        "speculative-ending: caption uses 단정형 mental-state "
                        "'%s' — should be 추측형 (~인가 봐요 / ~모양입니다)",
                        ko[:30]
                    )
                    if progress_cb:
                        progress_cb(
                            f":warning: 추측형 어미 위반: '{ko[:25]}' → "
                            "TV동물농장 톤은 ~인가 봐요 / ~모양입니다"
                        )
                    break


# Real_footage lane keywords that should NOT appear (those are AI-vtuber
# / TV동물농장 narrator markers — real_footage should stay vlog tone).
_AI_VTUBER_REGISTER_HINTS = [
    "이쯤 되면", "본격", "그런데 말입니다", "결국", "사실은",
    "베테랑", "그 순간", "일촉즉발",
]


def _enforce_lane_tone(c: dict, progress_cb=None) -> None:
    """PD 2026-06-03: lane-specific tone must be PIPELINE-enforced not
    just prompt-requested. real_footage is casual vlog observation;
    ai_vtuber can use TV동물농장 dramatic narrator markers. If a
    real_footage concept has captions reading as ai_vtuber narrator,
    log a warning so future iteration sees the divergence."""
    render_style = (c.get("render_style") or "").strip().lower()
    if render_style != "real_footage":
        return
    violations = []
    for cut in c.get("cuts") or []:
        for cap in cut.get("captions") or []:
            ko = (cap.get("ko") or "").strip()
            for marker in _AI_VTUBER_REGISTER_HINTS:
                if marker in ko:
                    violations.append((ko[:30], marker))
                    break
    if violations:
        for v_ko, marker in violations[:3]:
            log.warning(
                "lane-tone: real_footage caption uses ai_vtuber marker "
                "'%s' → '%s' (should stay casual vlog tone)",
                marker, v_ko
            )
        if progress_cb and violations:
            progress_cb(
                f":warning: real_footage 톤 위반 {len(violations)}건 — "
                "vlog tone 유지 (동물농장 narrator markers 제거 필요)"
            )


def _validate_pet_age_attribution(c: dict) -> None:
    """Detect age/veteran mis-attribution (PD 2026-06-02). Ryani is 11yo
    senior; Leo is 8mo young. If a caption attributes Leo as "11년차" or
    Ryani as "8개월/막내", log a warning. Best-effort: a heuristic check
    that flags obvious confusion patterns."""
    LEO_AGE_BAD = ["11년", "11살", "베테랑", "노련", "시니어", "할머니견",
                    "11-year", "veteran", "senior"]
    RYANI_AGE_BAD = ["8개월", "막내", "신참", "초보", "rookie", "baby"]
    for cut in c.get("cuts") or []:
        for cap in cut.get("captions") or []:
            ko = cap.get("ko", "") or ""
            en = cap.get("en", "") or ""
            text = f"{ko} {en}".lower()
            if "레오" in text or "leo:" in text:
                for bad in LEO_AGE_BAD:
                    if bad.lower() in text:
                        log.warning(
                            "age mis-attribution: caption mentions Leo with "
                            "senior-marker '%s' → '%s'", bad, ko[:30]
                        )
                        break
            if "랴니" in text or "ryani:" in text:
                for bad in RYANI_AGE_BAD:
                    if bad.lower() in text:
                        log.warning(
                            "age mis-attribution: caption mentions Ryani with "
                            "young-marker '%s' → '%s'", bad, ko[:30]
                        )
                        break


def _validate_korean_characters(c: dict) -> None:
    """Catch foreign-script characters (Arabic, Cyrillic, CJK extension etc.)
    that occasionally appear in LLM Korean output. PD 2026-06-02 saw
    "아گ작" — Arabic 'گ' mid-Korean. Strip the bad character and log.
    Allow ASCII (English fallback in en field), Korean Hangul, basic punct."""
    import re as _re
    # Allowed: Hangul syllables (가-힣), Hangul jamo, ASCII letters/digits/
    # punctuation (including \n), CJK punctuation, common symbols ♥ ♡ — ! ? . , : ; …
    allowed_re = _re.compile(
        r"[가-힣ㄱ-ㅎㅏ-ㅣ"           # Hangul
        r" -~"              # ASCII printable + space
        r"♥♡♪♫❤"                   # hearts + music
        r"—–\-…．。、，‘’“”\"'"     # CJK punctuation
        r"\s\n\r"
        r"]"
    )
    for cut in c.get("cuts") or []:
        for cap in cut.get("captions") or []:
            for field in ("ko", "en"):
                text = cap.get(field, "")
                if not text:
                    continue
                # Find characters NOT in allowed set
                bad = [ch for ch in text if not allowed_re.match(ch)]
                if bad:
                    bad_set = "".join(sorted(set(bad)))
                    cleaned = "".join(ch for ch in text if allowed_re.match(ch))
                    log.warning("caption %s '%s' had foreign chars [%s] → cleaned to '%s'",
                                field, text, bad_set, cleaned)
                    cap[field] = cleaned.strip()


def _enforce_min_caption_display(c: dict) -> None:
    """Every body caption scene must display for ≥ 2.7 seconds. PD
    2026-06-02: shorter than that = viewer can't read KO+EN. PD 2026-06-30:
    bumped 2.5→2.7 (captions still flashing past before readable). If a scene
    is too short, extend its end (clamp to next scene's start or cut duration)."""
    MIN_DISPLAY = 2.7
    for cut in c.get("cuts") or []:
        if cut.get("function") == "wink_ending":
            continue  # wink has its own 1.5s rule
        caps = cut.get("captions") or []
        cut_dur = float(cut.get("duration_seconds") or 5)
        for i, cap in enumerate(caps):
            start = float(cap.get("start", 0))
            end = float(cap.get("end", cut_dur))
            if end - start >= MIN_DISPLAY:
                continue
            # Try to extend end. Next scene's start is the ceiling (if exists),
            # otherwise cut duration.
            ceiling = float(caps[i + 1]["start"]) if i + 1 < len(caps) else cut_dur
            new_end = min(start + MIN_DISPLAY, ceiling)
            if new_end > end:
                log.info("min-display: extended cap %s end %.2f → %.2f",
                         cap.get("ko", "")[:20], end, new_end)
                cap["end"] = new_end


def _split_merged_ko_en(c: dict) -> None:
    """When Caption Agent (or Polisher) dumps both languages into the ko
    field with a `\\n` separator and leaves en empty, split them. Heuristic:
    after `\\n`, if the tail is ASCII-dominant (English), move it to en."""
    for cut in c.get("cuts") or []:
        for cap in cut.get("captions") or []:
            ko = (cap.get("ko") or "")
            en = (cap.get("en") or "").strip()
            if "\n" in ko and not en:
                head, _, tail = ko.partition("\n")
                tail_stripped = tail.strip()
                ascii_chars = sum(1 for ch in tail_stripped if ord(ch) < 128)
                if tail_stripped and ascii_chars / len(tail_stripped) > 0.7:
                    cap["ko"] = head.strip()
                    cap["en"] = tail_stripped
                    log.info("ko/en split: '%s' / '%s'", cap["ko"], cap["en"])


# A cut "winks" if it's the tagged closer OR a wink is written into its
# tag/beat/action/motion. \bwink avoids matching "twinkle"; 윙크/찡긋 cover KO.
_WINK_RE = re.compile(r"(?:\bwink|윙크|찡긋)", re.IGNORECASE)


def _looks_like_wink(cut: dict) -> bool:
    if cut.get("function") == "wink_ending":
        return True
    blob = " ".join(str(cut.get(k, "")) for k in
                    ("tag", "beat", "action", "description",
                     "motion_prompt", "veo_prompt"))
    return bool(_WINK_RE.search(blob))


def _strip_wink_language(cut: dict) -> None:
    """Remove wink sentences from a NON-closer cut so it keeps its story content
    but no longer ANIMATES a wink. Only strips when something survives (never
    blanks a prompt). Also drops wink-only captions like '...찡긋 ♥'."""
    for k in ("motion_prompt", "veo_prompt", "action", "description"):
        txt = cut.get(k)
        if not txt:
            continue
        # Split on sentence/clause boundaries (EN .!? — and em/en dashes that the
        # Director uses to attach the wink beat), drop fragments mentioning a wink.
        frags = re.split(r"(?<=[.!?])\s+|\s*[—–]\s*", txt)
        kept = [f for f in frags if f and not _WINK_RE.search(f)]
        if kept and len(kept) != len(frags):
            cut[k] = " ".join(kept).strip()
    caps = cut.get("captions")
    if isinstance(caps, list):
        pruned = [cap for cap in caps
                  if not _WINK_RE.search(str(cap.get("ko", "")) + " "
                                         + str(cap.get("en", "")))]
        if pruned:  # never leave a cut with zero captions
            cut["captions"] = pruned


def _enforce_wink_empty_captions(c: dict) -> None:
    """Exactly ONE closing wink. The wink is the channel sign-off — a single
    zoom-in close-up with the happy caption "오늘도 햅삐 ♥ / Happy as ever ♥".

    PD 2026-06-14: the episode sometimes showed TWO wink animations (a Writer-
    emitted wink + the auto-appended one, or a mid-episode wink callback). The
    old rule only EMPTIED the extra winks' captions, but an empty-caption wink
    still RENDERS a second wink animation → visible double-wink bug.

    PD 2026-06-16: the dedupe keyed ONLY on function=="wink_ending", so a wink
    the Director wrote into a story beat (e.g. a 'resolution_wink' cut whose
    function is a description string) slipped through and the episode still
    double-winked (the resolution wink + the auto-appended closer). Now we catch
    EMBEDDED winks too: the last wink-like cut becomes the single canonical
    closer; every other wink-like cut keeps its content but has its wink STRIPPED
    (not dropped — we don't lose the resolution beat)."""
    canonical = {
        "start": 4.5, "end": 6.8,  # held through the close-up wink landing
        "ko": "오늘도 햅삐 ♥",
        "en": "Happy as ever ♥",
    }
    cuts = c.get("cuts") or []
    if not cuts:
        return
    wink_idxs = [i for i, cut in enumerate(cuts) if _looks_like_wink(cut)]
    if not wink_idxs:
        return
    closer_idx = wink_idxs[-1]       # auto-appended cut_wink_ending is always last
    # Strip embedded winks from every OTHER wink-like cut (preserve their story).
    for i in wink_idxs[:-1]:
        _strip_wink_language(cuts[i])
    closer = cuts[closer_idx]
    closer["function"] = "wink_ending"
    closer["captions"] = [dict(canonical)]
    # Drop any stray function-tagged wink_ending cuts that aren't the closer,
    # then force the closer to be the very last cut.
    new_cuts = [cut for i, cut in enumerate(cuts)
                if i == closer_idx or cut.get("function") != "wink_ending"]
    if new_cuts and new_cuts[-1] is not closer:
        new_cuts = [cut for cut in new_cuts if cut is not closer] + [closer]
    c["cuts"] = new_cuts
    # PD 2026-06-23: the 햅삐 sign-off also leaked into a STORY cut's CAPTION (a closer
    # wrote "오늘도 햅삐 ♥" before the wink) → the episode showed "오늘도 햅삐" TWICE. The
    # warm sign-off belongs ONLY to the final wink, so scrub it from every non-closer cut's
    # captions (drop the offending scene; keep the cut's other scenes).
    for _cut in new_cuts:
        if _cut is closer:
            continue
        _scs = _cut.get("captions") or []
        _kept = [s for s in _scs if not (isinstance(s, dict) and (
            "오늘도 햅삐" in (s.get("ko", "") or "")
            or "happy as ever" in (s.get("en", "") or "").lower()))]
        if len(_kept) != len(_scs):
            _cut["captions"] = _kept
    # PD 2026-06-17: Seedance spontaneously winks/blinks pets even when NO cut asked
    # for it → a stray wink right before the closing wink (PD kept seeing a double
    # wink with clean concepts). Pin every NON-closer cut to eyes-open so the only
    # wink in the episode is the ending.
    _NOWINK = (" Both pets keep their eyes OPEN and natural the whole cut — NO "
               "winking, NO one-eye close / blink-wink (the single closing wink "
               "belongs ONLY to the final cut).")
    for cut in new_cuts:
        if cut is closer:
            continue
        for _k in ("motion_prompt", "veo_prompt"):
            _v = cut.get(_k)
            if _v and "NO winking" not in _v:
                cut[_k] = _v + _NOWINK
    if len(wink_idxs) > 1:
        log.info("wink dedupe: %d wink-like cuts → 1 closer (stripped %d embedded)",
                 len(wink_idxs), len(wink_idxs) - 1)


def _rewrite_duplicate_captions(concept: dict, progress_cb=None) -> None:
    cuts = concept.get("cuts") or []
    if not cuts:
        return
    seen: dict[str, dict] = {}  # ko -> first occurrence ref
    dupes: list[dict] = []  # collect (cut_tag, cap_idx, dup_caption, original_cut_action)
    for cut in cuts:
        action_ref = (cut.get("action") or cut.get("description") or "")[:300]
        motion_ref = (cut.get("motion_prompt") or "")[:300]
        for capi, cap in enumerate(cut.get("captions") or []):
            ko = (cap.get("ko") or "").strip()
            if not ko:
                continue
            if ko in seen:
                dupes.append({
                    "cut_tag": cut.get("tag", ""),
                    "cap_index": capi,
                    "duplicate_ko": ko,
                    "duplicate_en": cap.get("en", ""),
                    "action_context": action_ref,
                    "motion_context": motion_ref,
                    "first_occurrence_tag": seen[ko].get("tag", ""),
                })
            else:
                seen[ko] = cut
    if not dupes:
        return
    if progress_cb:
        progress_cb(
            f":pencil2: 자막 dedup: {len(dupes)}개 중복 → 재작성 중..."
        )
    rewrite_system = (
        "You rewrite Korean YouTube Shorts captions that duplicated across "
        "cuts. For each duplicate, write a NEW Korean + English caption that "
        "(a) preserves the same meaning relative to the cut's action, "
        "(b) uses a different register (의성어 / 위트 / 미스터리 / 캐릭터 "
        "thoughts / reaction) than the duplicate, (c) ≤ 14자 ko, ≤ 28자 en. "
        "Return ONLY a JSON array of {cut_tag, cap_index, ko, en} objects, "
        "same length and order as input."
    )
    user = json.dumps({"duplicates": dupes}, ensure_ascii=False, indent=2)
    try:
        out = _call_anthropic(
            rewrite_system, user,
            model=os.environ.get("CAPTION_AGENT_MODEL", _models.ANTHROPIC_LIGHT),
            max_tokens=2000,
        )
        rewrites = _parse_json_loose(out)
        if not isinstance(rewrites, list):
            return
        # Apply rewrites by (cut_tag, cap_index)
        idx_by_tag = {cut.get("tag"): cut for cut in cuts}
        n_applied = 0
        for r in rewrites:
            if not isinstance(r, dict):
                continue
            cut = idx_by_tag.get(r.get("cut_tag"))
            if not cut:
                continue
            caps = cut.get("captions") or []
            ci = r.get("cap_index")
            if not isinstance(ci, int) or ci < 0 or ci >= len(caps):
                continue
            new_ko = (r.get("ko") or "").strip()
            new_en = (r.get("en") or "").strip()
            if new_ko:
                caps[ci]["ko"] = new_ko
            if new_en:
                caps[ci]["en"] = new_en
            n_applied += 1
        log.info("caption rewrite: %d duplicates rewritten", n_applied)
        if progress_cb:
            progress_cb(f":sparkles: 자막 dedup → {n_applied}개 재작성 완료")
    except Exception as e:
        log.warning("caption rewrite failed (keeping duplicates): %s", e)


def _caption_agent_competition(system: str, user: str,
                                 progress_cb=None) -> list:
    """PD 2026-06-02: run Sonnet 4.6 + gpt-4.1 + Gemini 2.5 Pro in parallel,
    then use Opus 4.7 as judge to pick the best-fit caption set. Returns
    the winning JSON array.

    Failures from individual providers are tolerated — the judge picks from
    whoever succeeded. If all fail, raise (caller falls back to Director
    captions).
    """
    import concurrent.futures as _cf
    # PD 2026-06-02: NEVER use Anthropic for caption/text generation.
    # Cascade order for writing = gpt-4.1 > Gemini Pro > Anthropic (last resort).
    # Caption competition now only runs OpenAI + Gemini in parallel.
    # Anthropic stays in `_call_anthropic`'s last-fallback hop for other agents.
    providers = {
        "openai_gpt5": lambda: _call_openai_text(system, user, model=_models.OPENAI_TEXT),
        "gemini_pro": lambda: _call_gemini_text(
            system, user, model=_models.GEMINI_TEXT,
        ),
    }
    raw_outputs: dict[str, str] = {}
    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        future_to_name = {ex.submit(fn): name for name, fn in providers.items()}
        for fut in _cf.as_completed(future_to_name):
            name = future_to_name[fut]
            try:
                raw_outputs[name] = fut.result()
            except Exception as e:
                log.warning("Caption Agent provider %s failed: %s", name, e)
    if not raw_outputs:
        raise RuntimeError("All caption providers failed")
    # Parse each — keep only those that returned a valid JSON list
    parsed: dict[str, list] = {}
    for name, text in raw_outputs.items():
        try:
            obj = _parse_json_loose(text)
            if isinstance(obj, list):
                parsed[name] = obj
        except Exception as e:
            log.warning("Caption Agent %s parse failed: %s", name, e)
    if not parsed:
        raise RuntimeError("No caption provider returned valid JSON")
    if len(parsed) == 1:
        winner = next(iter(parsed))
        log.info("only one caption provider succeeded → %s", winner)
        if progress_cb:
            progress_cb(f":trophy: Caption: {winner} (자동 선택, 단일 성공)")
        return parsed[winner]
    # Judge (PD 2026-06-02: NO Anthropic for text — use gpt-4.1 as judge).
    judge_system = (
        "You are a Korean YouTube Shorts caption judge for the channel "
        "Ryani & Leo. You see N caption proposals for the same storyboard. "
        "Pick the ONE that best matches TV동물농장 narrator voice: setup → "
        "payoff in 2 scenes per cut, character POV (「레오: ...」 / 「랴니: "
        "...」 / (랴니의 작전)), 의성어/위트/미스터리 mix across cuts, action-"
        "matched text (no caption ahead of what's on screen), no duplicate "
        "phrases between cuts, 종결어미 = 해요/거든요/죠. Wink cuts MUST be "
        "empty. Return ONLY this JSON: {\"winner\": \"<name>\", \"reason\": "
        "\"<one short sentence>\"}"
    )
    judge_user_payload = {
        "proposals": {name: arr for name, arr in parsed.items()},
    }
    judge_user = (
        "Proposals from each provider (key = provider name, value = the "
        "JSON caption array they returned):\n"
        + json.dumps(judge_user_payload, ensure_ascii=False, indent=2)
        + "\n\nPick the winner per the rules and return the JSON object."
    )
    try:
        # Judge runs on OpenAI gpt-4.1 (PD 2026-06-02: no Anthropic for text).
        out = _call_openai_text(judge_system, judge_user,
                                  model=os.environ.get("CAPTION_JUDGE_MODEL", _models.OPENAI_TEXT))
        verdict = _parse_json_loose(out)
        winner = verdict.get("winner") if isinstance(verdict, dict) else None
        reason = verdict.get("reason", "") if isinstance(verdict, dict) else ""
        if winner not in parsed:
            log.warning("judge returned unknown winner=%r, picking first", winner)
            winner = next(iter(parsed))
            reason = "judge fallback (first available)"
    except Exception as e:
        log.warning("Caption judge failed: %s — picking openai_gpt5", e)
        winner = "openai_gpt5" if "openai_gpt5" in parsed else next(iter(parsed))
        reason = "judge call failed, fallback"
    log.info("Caption competition winner: %s — %s", winner, reason)
    if progress_cb:
        progress_cb(
            f":trophy: Caption: {winner} ({len(parsed)}/3 후보 中) — {reason[:60]}"
        )
    return parsed[winner]


def _call_openai_text(system: str, user: str, model: str = _models.OPENAI_TEXT) -> str:
    """OpenAI text generation via Responses API. Returns the full text body."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed")
    client = OpenAI(timeout=int(os.getenv("LLM_TIMEOUT_S", "45")), max_retries=0)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _call_gemini_text(system: str, user: str,
                       model: str = _models.GEMINI_TEXT) -> str:
    """Gemini text generation. Returns the body."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")
    # PD 2026-06-08: NEW google.genai SDK w/ http timeout — the legacy SDK hung
    # 600s on DNS failures (the Caption Agent competition's gemini_pro provider was
    # the last 600s sink → ~1h45m av runs).
    from google import genai as _genai
    from google.genai import types as _gtypes
    gclient = _genai.Client(api_key=api_key, http_options=_gtypes.HttpOptions(
        timeout=int(os.getenv("LLM_TIMEOUT_S", "45")) * 1000))
    resp = gclient.models.generate_content(
        model=model, contents=user,
        config=_gtypes.GenerateContentConfig(system_instruction=system or None))
    return (resp.text or "").strip()


def _build_caption_agent_user_prompt(concepts: list[dict]) -> str:
    """Build a NARRATIVE-AWARE user prompt for the Caption Agent.

    PD 2026-06-02: "이야기랑도 안맞아. 캡션 만드는 방식의 문제."
    Pass Writer's full narrative intent (story_seed, tone, callback, oneliner,
    pd_keyword) AND Writer's original captions (intent reference, not copy)
    so the Caption Agent crafts captions ALIGNED with the story Writer set
    out — not just descriptions of what Seedance renders.
    """
    payload = []
    for c in concepts:
        cuts_brief = []
        for cut in c.get("cuts", []):
            cuts_brief.append({
                "cut_tag": cut.get("tag") or cut.get("cut_tag"),
                "beat": cut.get("beat", ""),
                "function": cut.get("function", ""),
                "who": cut.get("who", ""),
                "space": cut.get("space", ""),  # PD 2026-06-02: location per cut
                "location_type": cut.get("location_type", ""),  # asset metadata
                "years_ago": cut.get("years_ago"),  # PD 2026-06-07: past clip → caption must state time point

                "action": cut.get("action", "") or cut.get("description", ""),
                "action_beats": cut.get("action_beats", []),
                # PD 2026-06-11 bugfix: what the VLM saw ACTUALLY render (set by the
                # post-render rewrite). It was NEVER passed here, so the prompt's
                # ground-truth rules (no false "등장"/entrance, onomatopoeia only when
                # the sound/motion was observed) silently had no data to work with.
                # NOTE: this is for TRUTHFULNESS guards (don't claim what wasn't shown)
                # — it does NOT mean dumb the story down to a failed render. When the
                # render misses the planned beat (e.g. Seedance didn't draw the surf),
                # the right fix is RE-GEN, not downgrading the caption. (PD: gen이
                # 잘못된 것 — 나중에 할일.)
                "vlm_actual_action": cut.get("vlm_actual_action", ""),
                "motion_prompt": (cut.get("motion_prompt") or "")[:800],
                "duration_seconds": cut.get("duration_seconds", 5),
                "chain_from_prev": cut.get("chain_from_prev", False),
                "seedance_mode": cut.get("seedance_mode", ""),
                # Writer's ORIGINAL captions: intent reference (NOT to copy
                # verbatim — Caption Agent is supposed to upgrade tone).
                "writer_intent_captions": cut.get("captions", []),
                "transition_in": cut.get("transition_in", ""),
            })
        payload.append({
            "title": c.get("title", ""),
            "episode_format": c.get("episode_format", ""),
            "episode_time": c.get("episode_time", ""),
            "set_anchor": c.get("set_anchor", ""),
            "set_description": (c.get("set_description", "") or "")[:300],
            "subjects": c.get("subjects", []),
            "wink_subject": c.get("wink_subject", ""),
            # Writer's narrative context — the storytelling spine
            "narrative_oneliner": c.get("narrative_oneliner", "") or c.get("title", ""),
            "story_seed": (c.get("story_seed", "") or "")[:600],
            "tone": c.get("tone", ""),
            "coherence_note": c.get("coherence_note", "") or c.get("callback", ""),
            "pd_keyword": c.get("pd_keyword", ""),
            "concept_summary": (c.get("concept_summary", "") or
                                  c.get("description", ""))[:400],
            "cuts": cuts_brief,
        })
    return (
        "Concepts (one per array element):\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\nIMPORTANT: each concept has Writer's narrative_oneliner, "
        "story_seed, tone, coherence_note (callback to past episodes), "
        "pd_keyword — these are the STORYTELLING DIRECTION. Each cut has "
        "writer_intent_captions = the Writer's first draft (intent reference). "
        "Your captions should EXTEND and elevate Writer's intent in TV동물"
        "농장 tone — never contradict or diverge from the story Writer set "
        "out. If Writer's intent caption says 'Ryani signals Leo with a play "
        "bow but Leo doesn't understand', your caption must keep that beat "
        "alive — don't replace it with a different beat.\n\n"
        "Return ONLY a JSON array of cut-caption objects, in cut order. "
        "Use the same cut_tag values from the input."
    )


def _retry_blocked_concepts(concepts: list[dict], target_date, context,
                              style_filter, few_shots,
                              progress_cb=None, max_retries: int = 1) -> list[dict]:
    """For each concept whose Validator verdict is 'blocked', re-run the
    Writer with the validator's revision_request as additional guidance.
    Then re-run Director → Caption Agent → Polisher → Validator on JUST
    that revised concept. Replace the original. Cap at max_retries.

    PD 2026-06-02 correction: don't skip blocked concepts, retry them.
    Env `SKIP_VALIDATOR_RETRY=1` disables the retry pass (fast iteration mode).
    """
    if os.getenv("SKIP_VALIDATOR_RETRY", "0") == "1":
        if progress_cb:
            progress_cb(":fast_forward: SKIP_VALIDATOR_RETRY=1 — Validator retry 건너뛰기")
        return concepts
    out = []
    for c in concepts:
        v = c.get("cameraman_validation") or {}
        if v.get("verdict") != "blocked":
            out.append(c)
            continue
        # Build revision feedback from Validator output
        issues = v.get("issues") or []
        tier1 = [iss for iss in issues if iss.get("tier") == 1]
        revision_req = v.get("revision_request") or ""
        feedback_text = (
            "Previous attempt was BLOCKED by Cameraman Validator. "
            "Tier-1 issues that MUST be fixed:\n"
            + "\n".join(
                f"- [{iss.get('type')}] cut {iss.get('cut_tag')}: "
                f"{iss.get('description')} → {iss.get('fix_hint','')}"
                for iss in tier1
            )
            + (f"\n\nRevision request: {revision_req}" if revision_req else "")
            + "\n\nRewrite this concept to address ALL Tier-1 issues. Keep "
            "the original beat structure when possible — just fix the "
            "coherence problems."
        )
        if progress_cb:
            progress_cb(
                f":repeat: Validator blocked '{(c.get('title') or '?')[:40]}' "
                f"— Writer 리트라이 (Tier1 {len(tier1)}개)"
            )
        # Re-run Writer with feedback for just this concept
        try:
            ctx_with_feedback = dict(context)
            ctx_with_feedback["_revision_feedback"] = feedback_text
            ctx_with_feedback["_revise_concept_title"] = c.get("title", "")
            revised_story = run_writer(
                target_date, ctx_with_feedback,
                n_concepts=1,
                style_filter=c.get("render_style") or style_filter,
                few_shots=few_shots,
                progress_cb=progress_cb,
            )
            if not revised_story:
                log.warning("Writer revision returned empty — keeping blocked")
                out.append(c)
                continue
            revised_directed = run_director(revised_story, context,
                                             progress_cb=progress_cb)
            revised_captioned = run_caption_agent(revised_directed,
                                                    progress_cb=progress_cb)
            revised_polished = run_caption_polisher(revised_captioned,
                                                      progress_cb=progress_cb)
            revised_polished = _enrich_thin_set_descriptions(revised_polished)
            revised_validated = run_cameraman_validator(revised_polished,
                                                          progress_cb=progress_cb)
            new_c = revised_validated[0] if revised_validated else c
            new_verdict = (new_c.get("cameraman_validation") or {}).get("verdict")
            log.info("retry result: verdict=%s", new_verdict)
            if progress_cb:
                progress_cb(
                    f":arrows_counterclockwise: 리트라이 후 verdict={new_verdict}"
                )
            out.append(new_c)
        except Exception as e:
            log.warning("Retry failed for blocked concept: %s — keeping original", e)
            out.append(c)
    return out


def _enforce_editing_concept_signature(c: dict) -> list[dict]:
    """PD 2026-06-03: programmatic post-Validator hardening. The LLM
    validator can be lenient on numeric checks (cut duration ≤4s for
    rapid_montage). This applies deterministic per-concept signature
    rules and returns a list of violations. Each violation is a Tier-1
    issue that escalates to BLOCK.

    PD 2026-06-03 v2: fall back to env FORCE_EDITING_CONCEPT when Writer
    didn't set the field (Writer skipping editing_concept was silently
    bypassing this check). Also catches action-field duplication across
    cuts as a hard-block via programmatic scan (previously LLM-only).
    """
    ec = (c.get("editing_concept") or "").strip().lower()
    if not ec:
        ec = os.getenv("FORCE_EDITING_CONCEPT", "").strip().lower()
    cuts = c.get("cuts") or []
    issues = []

    def _add(t, desc):
        issues.append({
            "tier": 1, "type": "editing_signature", "cut_tag": t,
            "description": desc, "fix_hint": "edit_effect / duration / fields 수정",
        })

    # PD 2026-06-03: action-field duplication is a hard-block (was LLM-only).
    # Writer was writing the same action across all 5 cuts → broken episodes
    # shipped because LLM Validator missed it.
    actions = [(ct.get("action") or "").strip() for ct in cuts]
    actions_nonempty = [a for a in actions if a]
    if len(actions_nonempty) >= 2:
        from collections import Counter
        cnt = Counter(actions_nonempty)
        most_common, freq = cnt.most_common(1)[0]
        if freq >= 2:
            _add("concept",
                 f"action-duplication: {freq} cuts share identical action "
                 f"('{most_common[:60]}...') — each cut.action must describe "
                 "only what's visible in that 5-7s clip")

    if not ec:
        return issues

    if ec == "rapid_montage":
        # PD 2026-06-03: allow 1 breath/punctuation cut to deviate from
        # the fast signature for rhythm variety.
        fast = [ct for ct in cuts
                if (ct.get("edit_effect") or "").lower() in ("speed_1.3x", "speed_1.5x")]
        if len(fast) < max(2, len(cuts) - 1):  # ≥most cuts fast, allow 1 deviation
            _add("concept", f"rapid_montage: only {len(fast)}/{len(cuts)} cuts use speed_1.3x/1.5x "
                            "(dominant rapid signature required, 1 cut variation OK)")
        long_cuts = [ct for ct in cuts if (ct.get("duration_seconds") or 0) > 4]
        if len(long_cuts) > 1:  # allow 1 breath cut up to 6s
            _add(long_cuts[0].get("tag", "?"),
                 f"rapid_montage: {len(long_cuts)} cuts >4s (1 breath cut OK, more breaks rhythm)")
    elif ec == "long_take":
        kb = [ct for ct in cuts if (ct.get("edit_effect") or "").lower() == "ken_burns"]
        if not kb:
            _add("concept", "long_take: no cut uses ken_burns")
        if len(cuts) > 2:
            _add("concept", f"long_take: {len(cuts)} cuts (≤2 required)")
    elif ec == "twist_ending":
        if cuts:
            last = (cuts[-1].get("edit_effect") or "").lower()
            if last not in ("freeze_last_frame", "zoom_in_slow"):
                _add(cuts[-1].get("tag", "?"),
                     f"twist_ending: last cut edit_effect='{last}' (expected freeze_last_frame or zoom_in_slow)")
    elif ec == "themed_compilation":
        if not c.get("theme_tag"):
            _add("concept", "themed_compilation: missing concept.theme_tag")
        missing_meaning = [ct.get("tag", "?") for ct in cuts if not ct.get("meaning")]
        if missing_meaning:
            _add(missing_meaning[0],
                 f"themed_compilation: {len(missing_meaning)} cuts lack `meaning` field")
        if len(cuts) < 3:
            _add("concept", f"themed_compilation: {len(cuts)} cuts (≥3 required)")
    elif ec == "photo_i2v":
        non_photo = [ct.get("tag", "?") for ct in cuts
                     if (ct.get("source_hint") or "").lower() != "photo_i2v"]
        if non_photo:
            _add(non_photo[0],
                 f"photo_i2v: {len(non_photo)} cuts not using source_hint=photo_i2v")
    elif ec == "split_screen":
        split = [ct for ct in cuts
                 if (ct.get("edit_effect") or "").lower() in ("split_horizontal", "split_vertical")]
        if not split:
            _add("concept", "split_screen: no cut uses split_horizontal/vertical")
        missing_sec = [ct.get("tag", "?") for ct in split if not ct.get("secondary_asset_id")]
        if missing_sec:
            _add(missing_sec[0],
                 f"split_screen: {len(missing_sec)} split cuts lack secondary_asset_id")
    elif ec == "slow_mo":
        slow = [ct for ct in cuts
                if (ct.get("edit_effect") or "").lower() in ("speed_0.3x", "speed_0.5x")]
        if not slow:
            _add("concept", "slow_mo: no cut uses speed_0.3x/0.5x")
    elif ec == "before_after":
        if len(cuts) != 2:
            _add("concept", f"before_after: {len(cuts)} cuts (exactly 2 required)")
        elif cuts:
            ef1 = (cuts[0].get("edit_effect") or "").lower()
            ef2 = (cuts[1].get("edit_effect") or "").lower()
            if ef1 != "static":
                _add(cuts[0].get("tag", "?"),
                     f"before_after: cut1 edit_effect='{ef1}' (expected static)")
            if ef2 not in ("freeze_last_frame", "zoom_in_slow"):
                _add(cuts[1].get("tag", "?"),
                     f"before_after: cut2 edit_effect='{ef2}' (expected freeze_last_frame or zoom_in_slow)")
    elif ec == "cross_cutting":
        spaces = [ct.get("space") for ct in cuts]
        distinct = set(s for s in spaces if s)
        if len(distinct) < 2:
            _add("concept", f"cross_cutting: only {len(distinct)} distinct space(s) (≥2 required)")
        # Check alternation
        if len(spaces) >= 4:
            alternates = all(spaces[i] != spaces[i + 1] for i in range(len(spaces) - 1) if spaces[i] and spaces[i + 1])
            if not alternates:
                _add("concept", "cross_cutting: cuts do not alternate between spaces")
    return issues


def build_set_description_from_library(set_anchor: str) -> str:
    """PD 2026-06-09: render set_library[anchor].persistent_background (+ room
    geometry) into a rich camera-sweep set_description, so a THIN Director-written
    one can be AUTO-ENRICHED from the LEARNED room data instead of getting blocked
    (→ empty slot). This is the better realization of PD's 'describe the 할머니집
    from learned data, very detailed' — pull from stored room facts, don't depend
    on the Director writing 400+ chars every time."""
    if not set_anchor:
        return ""
    try:
        lib = json.loads((ROOT / "data" / "set_library.json").read_text(encoding="utf-8"))
    except Exception:
        return ""
    e = lib.get(set_anchor) or {}
    pb = e.get("persistent_background") or {}
    if not isinstance(pb, dict):
        return ""
    parts: list[str] = []
    if pb.get("summary"):
        parts.append(str(pb["summary"]).strip())
    wall = pb.get("wall_treatment") or pb.get("wall")
    floor = pb.get("floor_type") or pb.get("floor")
    win = pb.get("window_or_light") or pb.get("window") or pb.get("light")
    if wall:
        parts.append("벽: " + str(wall).strip())
    if floor:
        parts.append("바닥: " + str(floor).strip())
    mf = pb.get("main_furniture") or pb.get("recurring_items")
    if isinstance(mf, list) and mf:
        parts.append("주요 가구(각자 고정된 위치 유지): " +
                     "; ".join(str(x).strip() for x in mf if x))
    elif isinstance(mf, str) and mf.strip():
        parts.append("주요 가구: " + mf.strip())
    if win:
        parts.append("창문/조명: " + str(win).strip())
    # PD-authoritative facts (e.g. sink mounted at counter height)
    notes = e.get("pd_notes") or []
    if isinstance(notes, list) and notes:
        parts.append("고정 사실: " + " ".join(str(n).strip() for n in notes)[:400])
    if not parts:
        return ""
    return ("Korean home interior. " + " ".join(parts) +
            " 카메라가 방을 천천히 훑듯, 위 요소가 모두 고정된 위치에 보인다. "
            "배경 오브젝트는 정적이고 펫만 움직인다.")


def _looks_truncated(s: str) -> bool:
    """PD 2026-06-09: an LLM output cut off mid-sentence (Gemini/OpenAI hit a token
    cap) ends without terminal punctuation. The Director's long set_description kept
    truncating ('...is clean white painted (NOT beige') → Validator blocked every
    render. Detect it so we can replace it from the library."""
    t = (s or "").rstrip()
    if not t:
        return False
    return t[-1] not in ".!?。」'\")" + "”’…"


def _enrich_thin_set_descriptions(concepts: list[dict]) -> list[dict]:
    """PD 2026-06-09: before the validator runs, fill in a thin / missing / TRUNCATED
    set_description from set_library so the concept renders with a rich background
    (Seedance is weak at backgrounds) instead of being BLOCKED into an empty slot."""
    for c in concepts or []:
        sa = (c.get("set_anchor") or "").strip()
        sd = (c.get("set_description") or "").strip()
        if not sa:
            continue
        thin = len(sd) < 400
        truncated = _looks_truncated(sd)
        if not (thin or truncated):
            continue
        built = build_set_description_from_library(sa)
        if not built:
            continue
        # Truncated → REPLACE (the cut-off tail is unusable). Thin → prepend library.
        if truncated and not thin:
            c["set_description"] = built
            why = "truncated→replaced"
        else:
            c["set_description"] = built if not sd else (built + " " + sd)
            why = "thin→prepended"
        log.info("set_description %s '%s' from set_library[%s] (%d→%d chars)",
                 why, (c.get("title") or "?")[:40], sa, len(sd), len(c["set_description"]))
    return concepts


def run_cameraman_validator(concepts: list[dict],
                              progress_cb=None) -> list[dict]:
    """Pre-dispatch sanity gate (PD 2026-06-02). Reads the Director +
    Caption Agent output and asks Sonnet 4.6 whether the storyboard will
    hold together as a coherent short before we spend Seedance $$$.

    Stamps `cameraman_validation` on each concept with verdict + issues.
    Does NOT block dispatch by itself — Cameraman/Producer checks the
    verdict and decides. Failure is silent (no validation result attached).
    """
    if not concepts:
        return concepts
    if progress_cb:
        progress_cb(":mag: Cameraman Validator — 인과/현실성 검증 중...")
    try:
        system = CAMERAMAN_VALIDATOR_PROMPT.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("Cameraman Validator prompt missing — skipping")
        return concepts
    for c in concepts:
        try:
            user = _build_validator_user_prompt(c)
            out_text = _call_anthropic(
                system, user,
                model=os.environ.get("VALIDATOR_MODEL", _models.ANTHROPIC_LIGHT),
                max_tokens=4000,
            )
            result = _parse_json_loose(out_text)
            if not isinstance(result, dict):
                continue
            c["cameraman_validation"] = result
            verdict = result.get("verdict", "approved")
            score = result.get("score_1_10", 0)
            n_issues = len(result.get("issues") or [])
            # PD 2026-06-03: layer programmatic editing_concept signature
            # checks on top of LLM verdict. LLM can be lenient; numeric
            # checks (cut duration ≤4s for rapid_montage, exactly 2 cuts
            # for before_after) are deterministic.
            extra_issues = _enforce_editing_concept_signature(c)
            if extra_issues:
                existing = list(result.get("issues") or [])
                result["issues"] = existing + extra_issues
                # Any extra issue = Tier 1 BLOCKER → upgrade verdict
                if verdict != "blocked":
                    result["verdict"] = "blocked"
                    verdict = "blocked"
                    result["summary"] = (
                        (result.get("summary") or "") +
                        f" Programmatic signature check: {len(extra_issues)} violation(s)."
                    ).strip()
                n_issues = len(result["issues"])
                log.warning(
                    "Programmatic signature check found %d issue(s) — "
                    "verdict escalated to blocked", len(extra_issues)
                )
            log.info("Validator [%s]: verdict=%s score=%s issues=%d",
                     (c.get("title") or "?")[:40], verdict, score, n_issues)
            if progress_cb:
                emoji = {"approved": ":white_check_mark:", "revise": ":warning:",
                         "blocked": ":no_entry:"}.get(verdict, ":mag:")
                progress_cb(
                    f"{emoji} Validator: {verdict} ({score}/10, {n_issues} issues)"
                )
        except Exception as e:
            log.warning("Validator failed for one concept (skipping): %s", e)
    return concepts


def _build_validator_user_prompt(concept: dict) -> str:
    # PD 2026-06-02: inject each cut's asset scene_description so the Validator
    # can enforce asset-fidelity (Writer must not invent objects not in clip).
    asset_ids = [c.get("asset_id") for c in concept.get("cuts", []) if c.get("asset_id")]
    asset_sc_map: dict[str, str] = {}
    if asset_ids:
        try:
            import sqlite3 as _sqlite3
            con = _sqlite3.connect(str(DB_PATH))
            placeholders = ",".join("?" * len(asset_ids))
            rows = con.execute(
                f"SELECT asset_id, scene_description, pd_notes, activity, focus_subject, "
                f"location_type FROM assets WHERE asset_id IN ({placeholders})",
                asset_ids,
            ).fetchall()
            for aid, sc, pd_n, act, focus, loc in rows:
                gt = (pd_n or "").strip() or (sc or "")
                asset_sc_map[aid] = json.dumps({
                    "sc": gt[:400],
                    "is_pd_corrected": bool((pd_n or "").strip()),
                    "activity": act or "",
                    "focus_subject": focus or "",
                    "location_type": loc or "",
                }, ensure_ascii=False)
            con.close()
        except Exception as e:
            log.warning("validator: asset sc lookup failed: %s", e)

    cuts_brief = []
    for cut in concept.get("cuts", []):
        aid = cut.get("asset_id")
        entry = {
            "tag": cut.get("tag") or cut.get("cut_tag"),
            "beat": cut.get("beat", ""),
            "function": cut.get("function", ""),
            "who": cut.get("who", ""),
            "action": cut.get("action", "") or cut.get("description", ""),
            "action_beats": cut.get("action_beats", []),
            "motion_prompt": (cut.get("motion_prompt") or "")[:1000],
            "captions": cut.get("captions", []),
            "duration_seconds": cut.get("duration_seconds", 5),
            "chain_from_prev": cut.get("chain_from_prev", False),
            "seedance_mode": cut.get("seedance_mode", ""),
            "set_anchor": cut.get("set_anchor", ""),
            "asset_id": aid,
        }
        if aid and aid in asset_sc_map:
            entry["asset_ground_truth"] = json.loads(asset_sc_map[aid])
        cuts_brief.append(entry)
    payload = {
        "title": concept.get("title", ""),
        "episode_format": concept.get("episode_format", ""),
        "episode_time": concept.get("episode_time", ""),
        "set_anchor": concept.get("set_anchor", ""),
        "set_description": (concept.get("set_description") or "")[:300],
        "subjects": concept.get("subjects", []),
        "wink_subject": concept.get("wink_subject", ""),
        "cuts": cuts_brief,
    }
    return (
        "Concept to validate:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\nValidate per the Cameraman Validator rules. Return ONLY the "
        "JSON result, no prose."
    )


def run_caption_polisher(concepts: list[dict],
                          progress_cb=None) -> list[dict]:
    """Post-Director caption rewrite pass. PD requested 2026-05-31 after
    multiple iterations of slipped 입니다 체 + spoiler timing + awkward Korean.
    Single Anthropic call replaces the captions[] in each cut with a polished
    version. Failures fall back silently to Director's captions."""
    if not concepts:
        return concepts
    if progress_cb:
        progress_cb(":lipstick: Caption Polisher — 자막 다듬는 중...")
    user = _build_polisher_user_prompt(concepts)
    try:
        out_text = _call_anthropic(
            CAPTION_POLISHER_SYSTEM, user,
            model=os.environ.get("CAPTION_POLISHER_MODEL", DIRECTOR_MODEL),
            max_tokens=8000,
        )
        polished = _parse_json_loose(out_text)
    except Exception as e:
        log.warning("Caption Polisher failed (keeping Director captions): %s", e)
        return concepts
    if not isinstance(polished, list):
        log.warning("Caption Polisher returned non-list, ignoring")
        return concepts

    by_tag = {p.get("cut_tag"): p for p in polished if isinstance(p, dict)}
    for c in concepts:
        for cut in c.get("cuts", []):
            tag = cut.get("cut_tag") or cut.get("tag")
            polished_cut = by_tag.get(tag)
            if not polished_cut:
                continue
            new_caps = polished_cut.get("captions")
            if isinstance(new_caps, list) and new_caps:
                cut["captions"] = new_caps
            pos = polished_cut.get("caption_position")
            if pos in ("top", "bottom"):
                cut["caption_position"] = pos
    return concepts


def _normalize_for_downstream(concepts: list[dict]) -> list[dict]:
    """Ensure each cut has a `description` field (alias of `action`) so the
    legacy producer/cameraman code paths that look up `cut["description"]`
    keep working. Also ensure `pd_keyword` and `coherence_note` exist at
    the concept level (used by Slack proposal formatting).

    real_footage concepts: clear generation_mode (only meaningful for
    ai_vtuber pipeline) to avoid downstream confusion.
    """
    for c in concepts:
        c.setdefault("pd_keyword", "")
        c.setdefault("coherence_note", c.get("callback", ""))
        if c.get("render_style") == "real_footage":
            c["generation_mode"] = None
        for cut in c.get("cuts", []):
            if "description" not in cut:
                cut["description"] = cut.get("action", "")

    # PD 2026-06-01 chain-mode pipeline: (1) if Director packed multi-Shot
    # syntax into a single cut, split it into N chain cuts; (2) mark short-
    # tier (single set_anchor) concepts for chained i2v dispatch.
    for c in concepts:
        _split_shot_markers_into_cuts(c)
        _consolidate_short_to_one_take(c)
        _apply_editing_concept_effects(c)  # PD 2026-06-04
    return concepts


def _apply_editing_concept_effects(c: dict) -> None:
    """PD 2026-06-04: stamp the editing_concept's edit_effect signature onto
    each cut AFTER the split. The Writer often outputs 1 cut (or cuts without
    edit_effect), the split makes N cuts but doesn't apply per-mode effects,
    so all 9 modes rendered as identical static cuts → no differentiation.
    This applies the deterministic signature so each mode looks distinct."""
    ec = (c.get("editing_concept") or "").strip().lower()
    if not ec:
        ec = os.getenv("FORCE_EDITING_CONCEPT", "").strip().lower()
    if not ec:
        return
    # Only for real_footage (ai_vtuber has its own chain-mode logic)
    if (c.get("render_style") or "").lower() != "real_footage":
        return
    cuts = c.get("cuts") or []
    if not cuts:
        return
    n = len(cuts)
    # Stamp editing_concept onto the concept so downstream + validator see it.
    c["editing_concept"] = ec

    def _set(cut, eff, dur=None):
        cut["edit_effect"] = eff
        if dur is not None:
            cut["duration_seconds"] = dur

    if ec == "rapid_montage":
        # Most cuts fast (speed_1.3x), short durations; allow 1 breath cut.
        for i, cut in enumerate(cuts):
            if i == 2 and n >= 4:  # middle breath cut
                _set(cut, "ken_burns", 5)
            else:
                _set(cut, "speed_1.3x", 3)
    elif ec == "long_take":
        # Keep ≤2 cuts; ken_burns slow observation. Merge extras if >2.
        if n > 2:
            c["cuts"] = cuts[:2]
            cuts = c["cuts"]
        for cut in cuts:
            _set(cut, "ken_burns", 7)
    elif ec == "twist_ending":
        for i, cut in enumerate(cuts):
            if i == n - 1:
                _set(cut, "freeze_last_frame")
            else:
                _set(cut, "static")
    elif ec == "themed_compilation":
        # Varied effects across cuts for thematic montage feel.
        palette = ["static", "ken_burns", "zoom_in_slow", "pan_right", "static"]
        for i, cut in enumerate(cuts):
            _set(cut, palette[i % len(palette)])
        c.setdefault("theme_tag", c.get("title", "")[:40])
        for i, cut in enumerate(cuts):
            cut.setdefault("meaning", (cut.get("captions") or [{}])[0].get("ko", "") or f"moment {i+1}")
    elif ec == "photo_i2v":
        for cut in cuts:
            cut["source_hint"] = "photo_i2v"
            _set(cut, "static")
    elif ec == "slow_mo":
        # Slow-mo the kick (last cut); others normal.
        for i, cut in enumerate(cuts):
            _set(cut, "speed_0.5x" if i == n - 1 else "static")
    elif ec == "before_after":
        # Exactly 2 cuts: cut1 static, cut2 freeze. Trim extras.
        if n > 2:
            c["cuts"] = [cuts[0], cuts[-1]]
            cuts = c["cuts"]
        _set(cuts[0], "static")
        if len(cuts) > 1:
            _set(cuts[1], "freeze_last_frame")
    elif ec == "cross_cutting":
        # No effect change; alternation handled by cut.space. Keep static.
        for cut in cuts:
            cut.setdefault("edit_effect", "static")
    elif ec == "split_screen":
        # Needs secondary_asset_id; mark first cut for split if available.
        # Best-effort: leave to Writer's secondary_asset_id. Static fallback.
        for cut in cuts:
            cut.setdefault("edit_effect", "static")


def _split_shot_markers_into_cuts(c: dict) -> None:
    """Script-based cut splitter (PD 2026-06-01 PM, second redirect):
    "꼭 5초 균등이 아니라 스크립트 기반으로 잘라야지."

    Writer/Director often pack the whole story into ONE cut with multiple
    captions and a multi-Shot motion_prompt. This splitter uses the
    CAPTIONS (the narrator script) as the authoritative cut boundaries.
    Each caption = one chain cut. The motion_prompt's Shot N: blocks are
    paired 1:1 with captions when counts match; otherwise the full
    motion_prompt is replayed with the per-cut caption's text injected as
    an action hint.

    Cap at 5 cuts to keep cost bounded ($1.50 max per short episode).

    PD 2026-06-03: for real_footage, ALSO split per-cut UNIQUE action
    derived from each caption (not copying global narrative). Previous
    bug: split copied source cut's action verbatim to all N cuts.
    """
    cuts = c.get("cuts") or []
    if len(cuts) != 1:
        return
    cut = cuts[0]
    # PD 2026-06-12: this splitter clones the SINGLE source cut into N cuts via
    # `**cut.items()`, which copies its `asset_id` to every cut. For ai_vtuber
    # that's correct (N chain cuts from one regen). For REAL_FOOTAGE it means one
    # real clip is re-trimmed N times → the SAME footage repeats with different
    # captions ("동일 구간 무한 반복" — the cafe-loop bug). RF cuts must each be a
    # DISTINCT clip, which only comes from a multi-cut concept (len(cuts)!=1, left
    # untouched above). So for real_footage we do NOT split: the single clip stays
    # ONE cut and plays once, with its multiple captions kept as time-spaced scenes
    # over the clip's real duration. (A 1-clip RF concept = a short single-clip
    # episode; if it needs more beats the writer must supply more clips.)
    if (c.get("render_style") or "").lower() == "real_footage":
        return
    captions = cut.get("captions") or []
    if len(captions) < 2:
        return  # no script beats to split on

    motion = cut.get("motion_prompt") or cut.get("veo_prompt") or ""
    import re as _re
    shot_pattern = _re.compile(r"\bShot\s+(\d+)\s*:\s*", _re.IGNORECASE)
    markers = list(shot_pattern.finditer(motion))
    preamble = motion[:markers[0].start()].strip() if markers else motion.strip()
    shot_bodies: list[str] = []
    for idx, m in enumerate(markers):
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(motion)
        body = motion[m.end():end].strip(" .,;\n")
        if body:
            shot_bodies.append(body)

    # Cap caption-driven cut count at 5
    target_caps = captions
    if len(target_caps) > 5:
        # Merge adjacent captions to reach 5 cuts
        step = len(target_caps) / 5
        target_caps = [target_caps[int(i * step)] for i in range(5)]

    new_cuts: list[dict] = []
    for i, cap in enumerate(target_caps):
        # Pair motion: if shot_bodies count matches caption count → 1:1.
        # If more shots than caps → distribute shots evenly. If fewer shots
        # → reuse the closest one. If no shots → use preamble + caption hint.
        if shot_bodies:
            if len(shot_bodies) == len(target_caps):
                shot_text = shot_bodies[i]
            else:
                # Map cut i (0..N-1) to nearest shot index (0..M-1)
                shot_idx = int(i * len(shot_bodies) / len(target_caps))
                shot_idx = min(shot_idx, len(shot_bodies) - 1)
                shot_text = shot_bodies[shot_idx]
        else:
            shot_text = (cap.get("ko") or "")
        cut_motion = (preamble + " " + shot_text).strip() if preamble else shot_text
        new_cut = {
            **{k: v for k, v in cut.items() if k not in (
                "motion_prompt", "veo_prompt", "captions", "tag",
                # PD 2026-06-03: don't copy global action/description to
                # every new cut — that was the action-duplication bug.
                # Each cut gets its own action derived from its caption.
                "action", "description",
            )},
            "tag": f"cut{i+1}_chain",
            "motion_prompt": cut_motion,
            "veo_prompt": cut_motion,
            # Per-cut action derived from this caption + shot_text
            "action": (shot_text or (cap.get("ko") or ""))[:300],
            "description": (cap.get("ko") or "")[:200],
            # Caption shows 1.0-5.0s — give the visual 1s to establish
            # before the narrator comments (PD 2026-06-01 PM: caption was
            # ahead of action on chain cuts, e.g. "아그작" caption appeared
            # before Leo started eating). Narrator-after-action pattern.
            "captions": [{
                "start": 1.0,
                "end": 5.0,
                "ko": cap.get("ko", ""),
                "en": cap.get("en", ""),
            }],
        }
        new_cuts.append(new_cut)
    log.info(
        "script-split: %d captions × %d shot-blocks → %d chain cuts (each 5s)",
        len(captions), len(shot_bodies), len(new_cuts),
    )
    c["cuts"] = new_cuts


def _consolidate_short_to_one_take(c: dict) -> None:
    """Mark single-space concepts as 'chained' short — multiple cuts dispatched
    sequentially, each using the previous cut's last frame as input.

    History:
    - v1 (2026-06-01 AM): merged all cuts into ONE Seedance call with multi-shot
      syntax + 4x ffmpeg slowdown. Solved bg drift, but 4x slow motion + caption
      desync issues.
    - v2 (2026-06-01 PM, PD redirect): keep multiple cuts at natural speed.
      Cut 1 uses regen-still + ref mode. Cut N (N>1) uses cut N-1's last
      ffmpeg-extracted frame as i2v input. Background and character continuity
      cascade through the frame chain. No slowdown. Each Seedance call is
      independent but visually contiguous because the input frame anchors
      everything.

    Cameraman sees `chain_mode: true` on the concept and dispatches cuts 2..N
    with `--mode i2v --image <prev_last_frame.jpg>` instead of regen still.
    """
    cuts = c.get("cuts") or []
    if not cuts:
        return
    if c.get("render_style") == "real_footage":
        return  # Lane 3 is its own thing
    # Infrastructure-level enforcement (overrides Writer's choice):
    # single set_anchor across all cuts → ALWAYS one-take, regardless of
    # cut count or total duration. PD's pivot rule (2026-06-01): short =
    # single space → one-take. Duration in the source cuts is a Writer-side
    # density signal we pack into multi-shot syntax inside one 5s clip.
    # Only multi-space concepts stay mid (Writer/Director paths handle bg
    # transitions there).
    set_anchors = {
        (cut.get("set_anchor") or c.get("set_anchor") or "").strip()
        for cut in cuts
    }
    total_dur = sum(int(cut.get("duration_seconds") or 5) for cut in cuts)
    single_space = len(set_anchors - {""}) <= 1
    forced_short = single_space and len(cuts) <= 6
    writer_fmt = (c.get("episode_format") or "").strip().lower()
    fmt = "short" if forced_short else (writer_fmt or "mid")
    if forced_short and writer_fmt and writer_fmt != "short":
        log.info(
            "episode_format override: Writer said '%s' but single-space "
            "(%d cuts, %ds total) → forcing 'short' (one-take consolidation)",
            writer_fmt, len(cuts), total_dur,
        )
    c["episode_format"] = fmt
    if fmt != "short":
        return
    # Chain mode: keep cuts, just enforce per-cut Seedance limits.
    # Cameraman will chain cut N (N>1) onto cut N-1's last frame.
    c["chain_mode"] = True
    # PD 2026-06-10 (A): a chained i2v cut INHERITS the previous cut's last-frame
    # composition, so chaining EVERY cut locked the framing and the Director's varied
    # shot_size never rendered → "비슷한 컷이 계속" (cuts all look the same). Fix: when a
    # cut's shot_size differs from the previous by ≥ AV_CHAIN_SHOT_DELTA levels, render
    # it INDEPENDENT (ref mode) so the new framing actually appears; small/no change →
    # chain for continuity. Restores shot variety while keeping a one-take feel.
    _SHOT_ORD = {
        "extreme_close_up": 0, "ecu": 0, "extreme_closeup": 0,
        "close_up": 1, "cu": 1, "closeup": 1,
        "medium_close_up": 2, "mcu": 2, "medium_closeup": 2,
        "medium_shot": 3, "ms": 3, "medium": 3,
        "medium_wide": 4, "mws": 4, "medium_wide_shot": 4, "medium_full": 4,
        "wide_shot": 5, "ws": 5, "wide": 5, "full_shot": 5, "fs": 5,
        "establishing": 6, "els": 6, "extreme_wide": 6, "ews": 6,
    }
    def _shot_ord(s):
        return _SHOT_ORD.get((s or "").strip().lower().replace("-", "_"), 3)

    def _pets_in(cut: dict) -> frozenset:
        """The PET subjects of a cut (ryani/leo), ignoring human_hand. A cut that
        focuses on a different pet than the previous one is a montage beat, not a
        continuation — chaining it from the prev pet's last frame is wrong."""
        t = (str(cut.get("who") or "") + " "
             + " ".join(str(s) for s in (cut.get("subjects") or []))).lower()
        t = t.replace("랴니", "ryani").replace("레오", "leo")
        return frozenset(p for p in ("ryani", "leo") if p in t)

    _chain_delta = int(os.getenv("AV_CHAIN_SHOT_DELTA", "2"))
    for i, cut in enumerate(cuts):
        cut["duration_seconds"] = min(int(cut.get("duration_seconds") or 5), 5)
        prev = cuts[i - 1] if i > 0 else None
        # PD 2026-06-22: break the chain (independent ref render from the cut's OWN
        # still) on a SUBJECT change too, not only a big framing change. A single-space
        # episode can still be a multi-action MONTAGE (랴니 코→랴니 브이→레오 꼬리잡기→…);
        # chaining a leo cut from a ryani cut's last frame discarded the leo still and
        # collapsed the cut into the prior dog frame, so the caption matched nothing.
        big_shot_change = (prev is not None and abs(
            _shot_ord(cut.get("shot_size")) - _shot_ord(prev.get("shot_size"))) >= _chain_delta)
        subject_change = (prev is not None
                          and _pets_in(cut) and _pets_in(prev)
                          and _pets_in(cut) != _pets_in(prev))
        if i == 0:
            cut.setdefault("seedance_mode", "ref")
        elif big_shot_change or subject_change:
            # new composition (framing or subject) → render from this cut's own still
            cut["seedance_mode"] = "ref"
            cut["chain_from_prev"] = False
        else:
            cut["seedance_mode"] = "i2v"
            cut["chain_from_prev"] = True
        cut.pop("target_duration_seconds", None)

        # Universal caption-after-action delay (PD 2026-06-02: "캡션이 동작
        # 보다 앞에 나오네? 아직도?" → "캡션 내용이 나온뒤에 캐릭터들이 움직여"
        # = 1.5s floor still too early). Body cuts start ≥ 2.0s. Gives
        # Seedance time to ESTABLISH the visual action before narrator
        # caption appears. Applies even when Writer wrote multi-cut
        # directly (splitter didn't fire) — universal floor.
        for cap in (cut.get("captions") or []):
            if cap.get("start", 0) < 2.0:
                cap["start"] = 2.0

    # Wink ending auto-append (PD 2026-06-01 PM): each short episode ends
    # with a story-driven wink. Subject = whoever the punchline lands on.
    # PD 2026-06-14: never double-wink — if the Writer already ended on a wink,
    # drop it before appending so exactly one closing wink survives. (The sweep
    # _enforce_wink_empty_captions also dedupes, but guard at the source too.)
    cuts = [cc for cc in cuts if cc.get("function") != "wink_ending"]
    wink_subject = _pick_wink_subject(c)
    # PD 2026-06-17: if BOTH pets are in the episode, the ending is a two-pet wink
    # EXCHANGE — the OTHER pet winks first, then wink_subject (story winner) gives the
    # final 햅삐 wink. Solo episode → single wink.
    _subs = [str(s).lower() for s in (c.get("subjects") or [])]
    _has_r = any(("ryani" in s or "랴니" in s) for s in _subs)
    _has_l = any(("leo" in s or "레오" in s) for s in _subs)
    _other = ("leo" if wink_subject == "ryani" else "ryani") if (_has_r and _has_l) else None
    wink_cut = _build_wink_cut(wink_subject, cuts[-1], other=_other)
    cuts.append(wink_cut)
    c["cuts"] = cuts
    log.info(
        "chain-mode short: %d cuts × 5s + 1 wink (%s) = %d total",
        len(cuts) - 1, wink_subject, len(cuts),
    )

    # Scene-setter prepend (PD 2026-06-01 PM): "지금은 새벽 4시" 같은 시간/
    # 장소 컨텍스트 캡션을 cut1 시작에 자동 삽입. Writer가 episode_time을
    # 설정했으면 한국어로 자연어 변환 후 0-2s 슬롯에 배치, 본문 narrator
    # 캡션은 2.5s 이후로 밀어냄.
    _prepend_scene_setter(c)


def _prepend_scene_setter(c: dict) -> None:
    """PD 2026-06-02 rule: scene_setter only when time matters for the story.

    Skip prepend unless one of:
      - time-as-drama: episode_time is unusual (새벽/심야, before 6am / after 10pm)
        AND not a generic gag concept.
      - multi-time compression: concept has a `time_compression` flag (Writer
        sets when using shape (b) — hourly cross-cuts).
    For typical "랴니 플레이바우" concepts at 14:30, scene_setter is noise.
    """
    cuts = c.get("cuts") or []
    if not cuts:
        return
    first = cuts[0]
    if first.get("function") == "wink_ending":
        return
    time_str = (c.get("episode_time") or "").strip()
    if not time_str:
        return
    # Check if Writer flagged time as story-critical
    time_compression = bool(c.get("time_compression"))
    title = (c.get("title") or "").lower()
    seed = (c.get("story_seed") or "").lower()
    drama_words_ko = ("새벽", "심야", "한밤", "동트", "밤중", "꼭두")
    drama_words_en = ("dawn", "midnight", "late night", "predawn")
    drama_hit = any(w in title + seed for w in drama_words_ko + drama_words_en)
    try:
        hour = int(time_str.split(":")[0])
    except (ValueError, IndexError):
        hour = -1
    is_unusual_hour = hour >= 0 and (hour < 6 or hour >= 22)
    if not (time_compression or (is_unusual_hour and drama_hit)):
        log.info(
            "scene-setter SKIP — time not story-critical (time=%s, drama=%s, compression=%s)",
            time_str, drama_hit, time_compression,
        )
        return
    ko_time = _format_korean_time(time_str)
    if not ko_time:
        return
    en_time = _format_english_time(time_str)
    existing = first.get("captions") or []
    for cap in existing:
        if (cap.get("ko") or "").strip().startswith("지금은"):
            log.info("scene-setter already present (Caption Agent) — skip prepend")
            return
    scene_setter = {
        "start": 0.0, "end": 2.0,
        "ko": f"지금은 {ko_time}",
        "en": en_time,
    }
    for cap in existing:
        if cap.get("start", 0) < 2.5:
            cap["start"] = 2.5
    first["captions"] = [scene_setter] + existing
    log.info("scene-setter prepended (drama/compression): '%s' / '%s'",
             scene_setter["ko"], scene_setter["en"])


def _format_korean_time(time_str: str) -> str:
    """05:00 → '새벽 5시', 14:30 → '오후 2시 반' style."""
    try:
        parts = time_str.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return ""
    if h < 0 or h > 23:
        return ""
    half = " 반" if 20 <= m < 40 else ""
    if h < 5:
        return f"새벽 {h}시{half}"
    if h < 12:
        h12 = h
        return f"아침 {h12}시{half}"
    if h == 12:
        return f"낮 12시{half}"
    if h < 18:
        return f"오후 {h - 12}시{half}"
    if h < 22:
        return f"저녁 {h - 12}시{half}"
    return f"밤 {h - 12}시{half}"


def _format_english_time(time_str: str) -> str:
    """05:00 → '5 AM, Korea.', 14:30 → '2:30 PM, Korea.'"""
    try:
        parts = time_str.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return ""
    if h == 0:
        h12, period = 12, "AM"
    elif h < 12:
        h12, period = h, "AM"
    elif h == 12:
        h12, period = 12, "PM"
    else:
        h12, period = h - 12, "PM"
    if m == 0:
        return f"{h12} {period}."
    return f"{h12}:{m:02d} {period}."


def _pick_wink_subject(c: dict) -> str:
    """Pick Leo vs Ryani for the wink ending based on STORY WINNER, not
    mention count.

    The winner = whoever the PUNCHLINE lands in favor of / who ended up with
    the payoff. That includes the active schemer (outsmarted / stole / teased)
    AND the passive beneficiary who simply ended up with it (e.g. Leo ate all of
    Ryani's food → Leo; Ryani only THOUGHT she got the treat but Leo ate it →
    Leo). NOT whoever was named last in captions — that's often the LOSER whose
    obliviousness / misbelief IS the punchline. The Writer should set the
    explicit `wink_subject`; this keyword guess is a fallback and can't reliably
    read a semantic twist, so it never overrides the explicit field.

    Priority: explicit `wink_subject` field on concept → schemer/winner
    patterns in captions → loser-pattern inverted → subjects[0] → ryani.
    """
    # 1. Explicit field (Writer/Director can set this)
    explicit = (c.get("wink_subject") or "").strip().lower()
    if explicit in ("ryani", "leo"):
        return explicit

    # 1b. PD 2026-06-12: a SOLO story (only one pet present — e.g. a memory-lane
    # "3년 전 랴니의 하루") must have THAT pet wink. Don't fall through to pattern
    # matching that could pick the absent pet.
    subs = [str(s).lower() for s in (c.get("subjects") or [])]
    has_r = any(("ryani" in s or "랴니" in s) for s in subs)
    has_l = any(("leo" in s or "레오" in s) for s in subs)
    if has_r and not has_l:
        return "ryani"
    if has_l and not has_r:
        return "leo"

    cuts = c.get("cuts") or []
    all_text = ""
    for cut in cuts:
        for cap in (cut.get("captions") or []):
            all_text += " " + (cap.get("ko") or "")

    # 2. Winner patterns (whoever acts/schemes/gets/takes — OR provokes/teases
    #    and gets a reaction out of the other; PD 2026-06-06: the active
    #    prankster wins. Leo teasing Ryani with his tail → Leo winks).
    winner_patterns = {
        "ryani": [
            "랴니의 작전", "랴니가 슬쩍", "랴니가 회수", "랴니가 가로채",
            "랴니가 챙겨", "랴니가 줍", "랴니가 먼저", "랴니가 가져",
            "랴니가 뺏어", "랴니의 회수", "랴니가 챙겼", "랴니가 발견",
            "랴니가 약올", "랴니가 놀려", "랴니가 장난", "랴니: 이거",
            # 결국 차지한 수혜자도 승자 (PD: 능동 갸그가 아니어도 펀치라인이 향한 쪽)
            "랴니가 다 먹", "랴니가 먹어치", "랴니가 독차지", "랴니가 차지",
            "랴니가 꿀꺽", "랴니가 낼름", "랴니가 혼자 다",
        ],
        "leo": [
            "레오의 작전", "레오가 슬쩍", "레오가 회수", "레오가 가로채",
            "레오가 챙겨", "레오가 먼저", "레오가 가져", "레오가 뺏어",
            "레오의 회수", "레오가 챙겼", "레오가 발견",
            "레오가 약올", "레오가 놀려", "레오가 장난", "레오: 이거",
            "레오가 꼬리", "레오의 꼬리", "레오가 시전",
            "레오가 다 먹", "레오가 먹어치", "레오가 독차지", "레오가 차지",
            "레오가 꿀꺽", "레오가 낼름", "레오가 혼자 다",
        ],
    }
    for subj, patterns in winner_patterns.items():
        if any(p in all_text for p in patterns):
            return subj

    # 3. Loser patterns inverted (named pet is unaware/missed → OTHER wins)
    # Loser = unaware OR the one REACTING to the other's prank (PD 2026-06-06:
    # the reactor — 웡!/발끈/왜그래 — is the target; the prankster wins/winks).
    loser_patterns = {
        "leo": ["레오는 모르", "레오만 모르", "레오야 진짜 몰랐", "레오야 정말 몰랐",
                "레오는 몰랐", "레오가 뒤늦게", "레오만 뒤늦게",
                "레오가 발끈", "레오: 왜그래", "레오: 왜 그래", "레오가 야옹"],
        "ryani": ["랴니는 모르", "랴니만 모르", "랴니야 진짜 몰랐", "랴니야 정말 몰랐",
                  "랴니는 몰랐", "랴니가 뒤늦게", "랴니만 뒤늦게",
                  "랴니가 발끈", "랴니: 왜그래", "랴니: 왜 그래", "랴니가 웡", "랴니: 웡"],
    }
    for loser, patterns in loser_patterns.items():
        if any(p in all_text for p in patterns):
            return "ryani" if loser == "leo" else "leo"

    # 4. Fallback to concept subjects[0]
    subs = c.get("subjects") or []
    if subs:
        first = subs[0].lower()
        if "ryani" in first or "랴니" in first:
            return "ryani"
        if "leo" in first or "레오" in first:
            return "leo"
    return "ryani"


def _wink_char_desc(s: str) -> str:
    """Marking-exact character descriptor for the wink cut (canon-consistent)."""
    if s == "leo":
        return ("Leo — MALE 8-month-old orange tabby cat (he/him, channel's 아들 레오), "
                "pale yellow-green chartreuse eyes, white chin tuft, lean agile young male "
                "body, paler cream-orange cheeks and belly than the back")
    return ("Ryani — FEMALE 11-year-old senior black French Bulldog (she/her, channel's "
            "랴니엄마). SPAYED FEMALE — NO male anatomy. THIN Boston Terrier-style white "
            "blaze (a NARROW line, NOT a wide splash) from nose to forehead, a faint subtle "
            "eyebrow-like white mark above each eye (NOT a bold round dot), silver-grey aged "
            "muzzle, white chin, large white chest patch, bat ears, ABSOLUTELY NO TAIL (her "
            "rear is bare and tailless), petite refined feminine body, only black/white/grey, no brown")


def _build_wink_cut(subject: str, prev_cut: dict, other: str | None = None) -> dict:
    """Chained wink-ending cut. PD 2026-06-17: when BOTH pets are present (`other`
    given), render a TWO-pet wink EXCHANGE — `other` winks first, then `subject`
    gives the FINAL 햅삐 wink (the sign-off). Solo single-wink otherwise. Always
    chain_from_prev so lighting/setting cascade from the prior cut."""
    names = {"leo": "Leo", "ryani": "Ryani"}
    if other and other != subject:
        # Two-pet exchange: `other` winks first → `subject` (story winner) winks last.
        motion = (
            "Continue seamlessly from the previous moment — SAME setting, SAME lighting, "
            "shadow direction and color temperature as the input frame. BOTH pets are in "
            f"frame together: {_wink_char_desc(other)}; AND {_wink_char_desc(subject)}. "
            "They settle for a beat (a small natural breath). Over ~2s the camera slowly "
            "pushes IN (smooth forward dolly, no panning) to a cozy tight two-shot. FIRST, "
            f"{names.get(other, other)} turns to the camera and gives a slow, playful WINK "
            "— one eye closes for a clear beat while the other stays open — with a little "
            f"smile. A BEAT LATER, {names.get(subject, subject)} turns to the camera and "
            "gives the FINAL slow, deliberate happy WINK (the closing sign-off), one eye "
            "closed with a smug satisfied smile. Each pet winks ONCE, in TURN (NOT at the "
            "same time). A brief hold after the final wink, then the clip ends — NO long lingering. Casual iPhone "
            "snapshot, natural fur strands, no studio polish. Completely bare-furred — NO "
            "clothing, NO collar, NO accessories. Keep each pet's exact markings (Ryani has NO tail)."
        )
        who = f"{other},{subject}"
        action = f"{names.get(other, other)} winks first, then {names.get(subject, subject)} gives the final happy wink"
    else:
        # Solo single wink (only one pet in the episode).
        char_desc = _wink_char_desc(subject)
        motion = (
            "Continue seamlessly from the previous moment — SAME pose, SAME setting, "
            "SAME lighting, shadow direction and color temperature as the input "
            f"frame. {char_desc} stays where it was and settles for a beat (a small "
            "natural movement — a relaxed breath, ears shifting). Then, over about "
            "2 seconds, the camera slowly pushes IN with a smooth forward dolly (no "
            "panning) toward an intimate tight CLOSE-UP where the face fills the "
            "frame. As the lens pushes in, the subject slowly turns its head to look "
            "directly into the camera and holds steady, warm eye contact for a clear "
            "beat. Then a slow, deliberate, playful WINK — one eye closes for a "
            "noticeable moment while the other stays wide open on the camera — with "
            "a subtle smug satisfied smile, mouth corner slightly raised. Hold the "
            "close-up wink and smile briefly, then the clip ends — NO long lingering. "
            "Casual iPhone snapshot, natural fur strands visible at this close "
            "distance, no studio polish. Completely bare-furred — NO clothing, "
            "NO collar, NO accessories."
        )
        who = subject
        action = f"{subject} winks at camera"
    # Tight closer duration (retention: no long linger). Two-pet exchange needs one
    # beat more for the sequential turn; the caption lands AFTER the wink, last ~2.4s.
    _wd = 6 if (other and other != subject) else 5
    return {
        "tag": "cut_wink_ending",
        "beat": "wink_ending",
        "who": who,
        "function": "wink_ending",
        "action": action,
        "description": action,
        # PD 2026-06-26 (retention): the closer is a TIGHT button, NOT a 7s linger — a long
        # slow wink was the back-half sag (viewers bleed ~15s onward). Enough for the ~2s
        # push-in + wink + caption to read, no more. Solo 5s; two-pet exchange 6s (see _wd).
        "duration_seconds": _wd,
        "seedance_mode": "i2v",
        "chain_from_prev": True,
        "motion_prompt": motion,
        "veo_prompt": motion,
        "regen_prompt": "",  # chain mode skips regen
        # Caption appears once the wink has landed (after the push-in + wink),
        # held through the lingering tail.
        "captions": [{
            "start": round(_wd - 2.4, 1), "end": round(_wd - 0.1, 1),
            "ko": "오늘도 햅삐 ♥",
            "en": "Happy as ever ♥",
        }],
        "caption_position": prev_cut.get("caption_position", "bottom"),
        "set_anchor": prev_cut.get("set_anchor", ""),
        "set_description": prev_cut.get("set_description", ""),
        "shot_size": "extreme_close_up",
        "camera_move": "push_in",
        "angle": "pet_eye_level",
        "lighting": prev_cut.get("lighting", ""),
    }


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    import argparse
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Writer+Director (Opus 4.7, 3+1 pass)")
    p.add_argument("--date", default=None,
                   help="target date YYYY-MM-DD (default: tomorrow KST)")
    p.add_argument("--style", choices=["ai_vtuber", "real_footage"],
                   help="produce only this style (default: both)")
    p.add_argument("--out", default=None,
                   help="write JSON to this path (default: stdout)")
    args = p.parse_args()

    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
    if args.date:
        target = dt.date.fromisoformat(args.date)
    else:
        target = (dt.datetime.now(KST) + dt.timedelta(days=1)).date()

    # Pull the same context the Producer would
    from agents.producer import _db, _gather_context
    con = _db()
    context = _gather_context(con, target)

    def _log(msg: str) -> None:
        print(msg, flush=True)

    concepts = propose_concepts_v2(
        target, context, style_filter=args.style,
        progress_cb=_log, con=con,
    )
    out_str = json.dumps(concepts, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(out_str, encoding="utf-8")
        print(f"\n[ok] wrote {len(concepts)} concepts → {args.out}")
    else:
        print(out_str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
