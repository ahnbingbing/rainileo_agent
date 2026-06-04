"""
agents/verifiers.py — per-stage hard validators with auto-fix retry.

Built 2026-05-31. Each agent (Writer/Director/Caption Polisher/Cameraman) has
historically failed silently — output the wrong thing, downstream stages
accept it, final video has the bug. This module adds STRICT validation
gates between stages: catch the bug, run a TARGETED fix LLM call ONCE, and
either accept the fix or log a hard failure.

Philosophy:
- Cheap targeted retry (just the violating field) vs expensive whole-pipeline retry.
- Hard schema violations are non-negotiable (\\n in ko, en empty, 입니다 ending).
- Soft warnings are logged but don't block (e.g. duration_seconds slightly off).

Public API:
    verify_captions(concepts)        → (ok: bool, issues: list[str])
    auto_fix_captions(concepts)      → (fixed_concepts, n_fixes_applied)
    verify_director_cuts(concepts)   → (ok, issues)
    verify_seedance_cut(mp4, cc)     → (ok, issues)
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger("verifiers")

# ────────────────────────────────────────────────────────────────────────
# Writer / Polisher caption validation
# ────────────────────────────────────────────────────────────────────────
_FORBIDDEN_KO_ENDINGS = re.compile(
    r"(습니다|입니다|었습니다|았습니다|했습니다|었어요습니다)\s*[.!?]?\s*$"
)
_HAS_NEWLINE = re.compile(r"\n")
# Abstract-subject nothing-sentence: short subject + 이에요/예요 alone
_ABSTRACT_NOTHING = re.compile(
    r"^(오늘도?|이|그|결국|하루|어쨌든)\s*\S{0,8}(이에요|예요|이었어요)\s*[.!?]?\s*$"
)
KO_MAX = 14
EN_MAX = 28


def verify_captions(concepts: list[dict]) -> tuple[bool, list[dict]]:
    """Return (all_ok, list of {cut_tag, scene_idx, ko, en, issues[]}).

    Each entry's `issues` is a list of human-readable strings describing what
    failed. If issues is non-empty, the caller can target a fix at exactly
    that scene.
    """
    findings: list[dict] = []
    for c in concepts:
        for cut in c.get("cuts", []):
            tag = cut.get("cut_tag") or cut.get("tag") or "(unknown)"
            for i, sc in enumerate(cut.get("captions", []) or []):
                ko = (sc.get("ko") or "").strip()
                en = (sc.get("en") or "").strip()
                issues = []
                if _FORBIDDEN_KO_ENDINGS.search(ko):
                    issues.append("ko ends with 습니다/입니다 (해요체 위반)")
                if _HAS_NEWLINE.search(ko):
                    issues.append("ko contains \\n line break (must be wrapped by render system, not Writer)")
                if _HAS_NEWLINE.search(en):
                    issues.append("en contains \\n")
                if _ABSTRACT_NOTHING.search(ko):
                    issues.append(f"ko is abstract-subject nothing-sentence ('{ko}')")
                if len(ko) > KO_MAX:
                    issues.append(f"ko is {len(ko)} chars (max {KO_MAX})")
                if len(en) > EN_MAX:
                    issues.append(f"en is {len(en)} chars (max {EN_MAX})")
                if not en:
                    issues.append("en is empty (likely merged into ko bug)")
                if not ko:
                    issues.append("ko is empty")
                if issues:
                    findings.append({
                        "cut_tag": tag,
                        "scene_idx": i,
                        "ko": ko,
                        "en": en,
                        "issues": issues,
                    })
    return (len(findings) == 0, findings)


_FIX_CAPTION_SYSTEM = """\
You are a Korean caption fixer. The user will give you ONE bad caption scene
with specific issues. Output ONLY a JSON object with the corrected `ko` and
`en` fields. No other text.

Rules:
- ko ≤ 14 chars, en ≤ 28 chars.
- ko ends in 해요체 (~요/~네요/~죠/~에요/~거든요). NO 습니다/입니다.
- ko in Korean only. en in English only. NO \\n in either.
- NO abstract-subject "X 이에요" patterns. Use concrete verb.
- Preserve the original meaning + 동물농장 narrator tone.

Output schema:
{"ko": "...", "en": "..."}
"""


def _fix_one_caption(scene: dict, issues: list[str]) -> dict | None:
    """PD 2026-06-02: LLM cascade (OpenAI → Gemini → Anthropic) instead of
    direct Anthropic. Returns dict with ko/en or None on failure."""
    from agents.llm_cascade import call_text_cascade
    user = json.dumps(
        {
            "current_ko": scene.get("ko", ""),
            "current_en": scene.get("en", ""),
            "issues": issues,
        },
        ensure_ascii=False,
    )
    try:
        text = call_text_cascade(_FIX_CAPTION_SYSTEM, user, max_tokens=400)
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        out = json.loads(text)
        ko = (out.get("ko") or "").strip()
        en = (out.get("en") or "").strip()
        if not ko or not en:
            return None
        return {"ko": ko, "en": en}
    except Exception as e:
        log.warning("caption fix failed: %s", e)
        return None


def auto_fix_captions(concepts: list[dict],
                       max_fixes: int = 30) -> tuple[list[dict], int]:
    """Run verify_captions, attempt a targeted LLM fix per failing scene.

    Modifies concepts in place. Returns (concepts, n_fixes_applied)."""
    ok, findings = verify_captions(concepts)
    if ok:
        return concepts, 0
    log.info("auto_fix_captions: %d bad scenes found", len(findings))
    by_lookup: dict[tuple[str, int], dict] = {}
    for c in concepts:
        for cut in c.get("cuts", []):
            tag = cut.get("cut_tag") or cut.get("tag") or "(unknown)"
            for i, sc in enumerate(cut.get("captions", []) or []):
                by_lookup[(tag, i)] = sc
    n_applied = 0
    for f in findings[:max_fixes]:
        sc = by_lookup.get((f["cut_tag"], f["scene_idx"]))
        if sc is None:
            continue
        fixed = _fix_one_caption(sc, f["issues"])
        if fixed:
            sc["ko"] = fixed["ko"]
            sc["en"] = fixed["en"]
            n_applied += 1
            log.info("fixed %s scene[%d]: ko=%r en=%r",
                     f["cut_tag"], f["scene_idx"], sc["ko"], sc["en"])
        else:
            log.warning("could not auto-fix %s scene[%d] — keeping original",
                        f["cut_tag"], f["scene_idx"])
    return concepts, n_applied


# ────────────────────────────────────────────────────────────────────────
# Director cut validation
# ────────────────────────────────────────────────────────────────────────
SPEED_ADVERBS = ("slowly", "gently", "gradually", "smoothly", "softly")
FURNITURE_SINGLETON_WORDS = ("sofa", "bench", "piano", "TV stand", "scratcher")


def verify_director_cuts(concepts: list[dict]) -> tuple[bool, list[dict]]:
    findings: list[dict] = []
    for c in concepts:
        for cut in c.get("cuts", []):
            tag = cut.get("cut_tag") or cut.get("tag") or "(unknown)"
            issues = []
            if not cut.get("shot_size"):
                issues.append("missing shot_size")
            if not cut.get("camera_move"):
                issues.append("missing camera_move")
            mp = (cut.get("motion_prompt") or cut.get("veo_prompt") or "").lower()
            if mp and not any(adv in mp for adv in SPEED_ADVERBS):
                issues.append("motion_prompt has no speed adverb (slowly/gently/gradually/etc.)")
            # Furniture singleton — each item should appear ≤ 2 times (once for
            # location, once for character relative position is allowed)
            for word in FURNITURE_SINGLETON_WORDS:
                if mp.count(word) > 2:
                    issues.append(f"{word} mentioned {mp.count(word)} times in motion_prompt — singleton risk")
            dur = cut.get("duration_seconds", 0)
            beats = cut.get("action_beats") or []
            if dur > 6 and len(beats) < 2:
                issues.append(f"duration {dur}s but only {len(beats)} action_beats — likely dead tail")
            if issues:
                findings.append({"cut_tag": tag, "issues": issues})
    return (len(findings) == 0, findings)


# ────────────────────────────────────────────────────────────────────────
# Cameraman per-cut validation (post-Seedance)
# ────────────────────────────────────────────────────────────────────────
def verify_seedance_cut(mp4: Path, expected_duration_s: float) -> tuple[bool, list[str]]:
    issues = []
    if not mp4.exists():
        return False, [f"mp4 missing: {mp4}"]
    size = mp4.stat().st_size
    if size < 100_000:
        issues.append(f"mp4 too small ({size} bytes) — likely render failed")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(mp4)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        actual = float(result.stdout.strip())
        if actual < expected_duration_s * 0.9:
            issues.append(f"duration {actual:.2f}s < expected {expected_duration_s:.2f}s × 0.9")
    except Exception as e:
        issues.append(f"ffprobe failed: {e}")
    return (len(issues) == 0, issues)
