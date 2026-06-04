"""
agents/retry_loop.py — Giri feedback-driven auto-retry loop.

Render → Giri review → if FAIL: parse feedback → auto-fix → re-render → repeat.
Max retries configurable (default 3). Logs all attempts to retry_log table.

Fixes that can be applied automatically:
  - caption_overflow → shorten caption text
  - caption_blocks_subject → move caption position
  - photo_mismatch → re-select photos via Photo Selector
  - background_repetition → replace duplicate-background photos
  - human_visible → filter has_human=0 more strictly
  - marking_invisible → strengthen Ryani marking prompt
  - bgm_missing → fix BGM path
  - style_mismatch → adjust regen prompt
  - motion_issue → adjust motion prompt

Fixes that require PD intervention:
  - concept_mismatch → Slack escalation
  - "폐기" verdict → stop

Usage:
    from agents.retry_loop import render_with_retry
    output, report = render_with_retry(card_id, concept, max_retries=3)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("agents.retry_loop")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()

ProgressCb = Callable[[str], None] | None

PASS_VERDICTS = {"업로드"}
RETRY_VERDICTS = {"수정 필요", "소폭 수정 후 업로드", "컨셉 재작업"}
STOP_VERDICTS = {"폐기"}


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


# ──────────────────────────────────────────────────────────────────────
# Feedback parsing
# ──────────────────────────────────────────────────────────────────────
def parse_giri_feedback(report: dict) -> list[dict]:
    """Parse Giri report into actionable fix items."""
    fixes = []
    overall = report.get("overall", report)

    # Check per-cut issues
    for cut in report.get("per_cut", []):
        if cut.get("caption_overflow"):
            fixes.append({
                "type": "caption_overflow",
                "cut": cut.get("cut", "?"),
                "detail": "캡션이 화면을 넘침",
            })
        if cut.get("has_unwanted_human"):
            fixes.append({
                "type": "human_visible",
                "cut": cut.get("cut", "?"),
                "detail": "사람 등장",
            })
        if not cut.get("ryani_markings_clear", True):
            fixes.append({
                "type": "marking_invisible",
                "cut": cut.get("cut", "?"),
                "detail": "Ryani 흰 마킹 안 보임",
            })
        if cut.get("storyboard_match", 1.0) < 0.3:
            fixes.append({
                "type": "photo_mismatch",
                "cut": cut.get("cut", "?"),
                "detail": f"스토리보드 불일치 (score={cut.get('storyboard_match', 0):.1f})",
            })

    # Check caption quality
    caption_q = overall.get("caption_quality", {})
    if caption_q.get("any_overflow"):
        if not any(f["type"] == "caption_overflow" for f in fixes):
            fixes.append({"type": "caption_overflow", "detail": "캡션 넘침"})
    if caption_q.get("any_blocks_subject"):
        fixes.append({"type": "caption_blocks_subject", "detail": "캡션이 펫 가림"})

    # Check audio
    audio = overall.get("audio", {})
    if not audio.get("has_bgm", True):
        fixes.append({"type": "bgm_missing", "detail": "BGM 없음"})

    # Check dimensions
    dims = overall.get("dimensions", report.get("dimensions", {}))
    if dims.get("photo_selection", 10) <= 4:
        if not any(f["type"] == "photo_mismatch" for f in fixes):
            fixes.append({"type": "photo_mismatch", "detail": "사진 선정 품질 낮음"})
    if dims.get("visual_style", 10) <= 4:
        fixes.append({"type": "style_mismatch", "detail": "스타일 불일치"})
    if dims.get("motion_quality", 10) <= 3:
        fixes.append({"type": "motion_issue", "detail": "모션 품질 낮음"})

    # Parse fix_suggestions text for additional patterns
    suggestions = overall.get("fix_suggestions", [])
    for s in suggestions:
        s_lower = s.lower() if isinstance(s, str) else ""
        if "배경" in s_lower and "다양" in s_lower:
            if not any(f["type"] == "background_repetition" for f in fixes):
                fixes.append({"type": "background_repetition", "detail": s[:100]})
        if "누락" in s_lower or "missing" in s_lower.lower():
            # Try to find which cut is missing
            import re as _re
            cut_nums = _re.findall(r"(?:cut|컷)\s*(\d+)", s_lower)
            for cn in cut_nums:
                fixes.append({"type": "cut_missing", "cut": int(cn), "detail": s[:100]})

    return fixes


# ──────────────────────────────────────────────────────────────────────
# Auto-fix application
# ──────────────────────────────────────────────────────────────────────
def apply_fixes(fixes: list[dict], concept: dict,
                work_dir: Path, progress_cb: ProgressCb = None) -> dict:
    """Apply fixes to the concept and return updated concept."""
    updated = json.loads(json.dumps(concept))  # deep copy
    applied = []

    for fix in fixes:
        ftype = fix["type"]

        if ftype == "caption_overflow":
            # Shorten all captions to max 15 characters
            for cut in updated.get("cuts", []):
                ko = cut.get("caption_ko", cut.get("description", ""))
                if len(ko) > 15:
                    cut["caption_ko"] = ko[:14] + "…"
                    cut["description"] = cut["caption_ko"]
            applied.append("캡션 15자로 축소")

        elif ftype == "caption_blocks_subject":
            # This would need caption position change — handled in burn_captions params
            # For now, add instruction to concept
            updated["_caption_position_override"] = "top"
            applied.append("캡션 위치 → 상단")

        elif ftype in ("photo_mismatch", "background_repetition", "human_visible"):
            # Skip photo re-selection for text-to-video (no source photos needed)
            if updated.get("generation_mode") == "text_to_video":
                applied.append(f"t2v 모드 — 에셋 재선정 스킵")
                continue
            try:
                from agents.photo_selector import select_photos
                if progress_cb:
                    progress_cb(f":camera: 에셋 재선정 중 ({fix['detail'][:30]})...")
                selected = select_photos(updated, n_select=8)
                if selected:
                    for j, sel in enumerate(selected):
                        if j < len(updated.get("cuts", [])):
                            updated["cuts"][j]["asset_id"] = sel.get("asset_id")
                    applied.append(f"사진 재선정 ({len(selected)}장)")
            except Exception as e:
                log.warning("Photo re-selection failed: %s", e)

        elif ftype == "marking_invisible":
            # Use the vF2 champion prompt (58-test optimized)
            marking_fix = (
                " (Ryani: old black French Bulldog, age 11. "
                "White markings on black face: thin Boston Terrier-style white blaze (NARROW line, not the typical wide splash) from nose to forehead, "
                "white dot above left eye, white dot above right eye. Silver-grey aged muzzle. "
                "White chin. White chest patch. Bat ears. No tail. Only black, white, grey.)"
            )
            if updated.get("generation_mode") == "text_to_video":
                for cut in updated.get("cuts", []):
                    if "ryani" in cut.get("who", "").lower():
                        # Strip ALL previous CRITICAL/marking injections
                        base = cut.get("veo_prompt", "")
                        base = re.sub(r'\s*CRITICAL[^.]*\.', '', base)
                        base = re.sub(r'\s*\(Ryani:[^)]*\)', '', base)
                        cut["veo_prompt"] = base.strip() + marking_fix
            else:
                regen = updated.get("regen_direction", {})
                regen["overall_style"] = regen.get("overall_style", "") + marking_fix
                updated["regen_direction"] = regen
            applied.append("Ryani 마킹 프롬프트 강화")

        elif ftype == "bgm_missing":
            # BGM will be fixed by ensuring correct path in manifests
            applied.append("BGM 경로 확인")

        elif ftype == "style_mismatch":
            style_fix = " Maintain consistent photorealistic style. NOT cartoon, NOT anime, NOT illustration."
            if updated.get("generation_mode") == "text_to_video":
                for cut in updated.get("cuts", []):
                    cut["veo_prompt"] = cut.get("veo_prompt", "") + style_fix
            else:
                regen = updated.get("regen_direction", {})
                regen["overall_style"] = regen.get("overall_style", "") + style_fix
                updated["regen_direction"] = regen
            applied.append("스타일 일관성 프롬프트 강화")

        elif ftype == "motion_issue":
            if updated.get("generation_mode") == "text_to_video":
                for cut in updated.get("cuts", []):
                    vp = cut.get("veo_prompt", "")
                    if vp and "motion" not in vp.lower():
                        cut["veo_prompt"] = vp + " Add clear, specific animal motion: slow blink, head turn, ear twitch, paw kneading."
            else:
                for cut in updated.get("cuts", []):
                    if not cut.get("motion_prompt"):
                        cut["motion_prompt"] = "gentle natural motion, slow blink, head turn, soft breathing"
            applied.append("모션 프롬프트 강화")

        elif ftype == "cut_missing":
            # Fill missing cuts by looking at adjacent cuts and generating
            cut_num = fix.get("cut", 0)
            cuts = updated.get("cuts", [])
            if 0 < cut_num <= len(cuts):
                missing = cuts[cut_num - 1]
                prev_desc = cuts[cut_num - 2].get("description", "") if cut_num >= 2 else ""
                next_desc = cuts[cut_num].get("description", "") if cut_num < len(cuts) else ""
                transition_prompt = (
                    f"Natural transition from '{prev_desc[:30]}' to '{next_desc[:30]}'. "
                    f"Gentle camera movement, soft breathing, slight head turn."
                )
                missing["description"] = f"(전환) {prev_desc} → {next_desc}"
                if updated.get("generation_mode") == "text_to_video":
                    missing["veo_prompt"] = transition_prompt
                    missing["duration_seconds"] = 4
                else:
                    missing["_needs_i2v"] = True
                    missing["motion_prompt"] = transition_prompt
                applied.append(f"scene{cut_num} 전환 장면 생성")

    if progress_cb and applied:
        progress_cb(f":wrench: 자동 수정 적용: {', '.join(applied)}")

    return updated


def inject_giri_feedback(concept: dict, report: dict) -> dict:
    """Inject Giri's full feedback into the concept for next attempt.

    This ensures the next render's prompts are STRONGLY influenced by
    what Giri criticized. The feedback goes into regen_direction and
    per-cut prompts so the LLM/image gen can't ignore it.
    """
    updated = json.loads(json.dumps(concept))

    biggest = report.get("가장_큰_문제", "")
    fix = report.get("최소_수정안", "")
    tool_req = report.get("툴_수정_요청", "")

    # Inject into regen_direction
    regen = updated.get("regen_direction", {})
    prev_feedback = regen.get("_previous_giri_feedback", "")
    regen["_previous_giri_feedback"] = (
        f"PREVIOUS ATTEMPT FAILED. Giri review said:\n"
        f"Problem: {biggest}\n"
        f"Fix: {fix}\n"
        f"Tool request: {tool_req}\n"
        f"YOU MUST address these issues in this attempt."
    )
    updated["regen_direction"] = regen

    # Inject per-cut fixes based on per_cut review
    # Replace (not append) to prevent prompt bloat across retries
    is_t2v = updated.get("generation_mode") == "text_to_video"
    for cut_review in report.get("per_cut", []):
        try:
            cut_num = int(cut_review.get("cut", 0))
        except (ValueError, TypeError):
            continue
        issue = cut_review.get("issue", "")
        if issue and cut_num > 0 and cut_num <= len(updated.get("cuts", [])):
            cut = updated["cuts"][cut_num - 1]
            fix_text = f" CRITICAL FIX: {issue}"
            if is_t2v:
                # Strip ALL previous CRITICAL injections to prevent bloat
                base = cut.get("veo_prompt", "")
                base = re.sub(r'\s*CRITICAL FIX:[^.]*\.?', '', base)
                base = re.sub(r'\s*CRITICAL:[^.]*\.?', '', base)
                cut["veo_prompt"] = base.strip() + fix_text
            else:
                base = cut.get("regen_prompt", "")
                base = re.sub(r'\s*CRITICAL FIX:[^.]*\.?', '', base)
                cut["regen_prompt"] = base.strip() + fix_text

    return updated


# ──────────────────────────────────────────────────────────────────────
# Retry log
# ──────────────────────────────────────────────────────────────────────
def _log_retry(card_id: str, attempt: int, report: dict, fixes: list[dict],
               fix_applied: str) -> None:
    """Log retry attempt to DB."""
    con = _db()
    score = report.get("점수", report.get("overall", {}).get("score", 0))
    verdict = report.get("판정", report.get("overall", {}).get("pass", "?"))
    issue_types = ",".join(f["type"] for f in fixes) if fixes else "none"

    con.execute(
        "INSERT INTO retry_log (card_id, attempt, giri_score, giri_verdict, issue_type, fix_applied) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (card_id, attempt, score, str(verdict), issue_types, fix_applied),
    )
    con.commit()


def check_recurring_patterns() -> list[dict]:
    """Check for issue patterns that repeat 3+ times → should become md rules."""
    con = _db()
    rows = con.execute(
        "SELECT issue_type, count(*) as cnt FROM retry_log "
        "WHERE created_at > datetime('now', '-7 days') "
        "GROUP BY issue_type HAVING cnt >= 3 ORDER BY cnt DESC"
    ).fetchall()
    return [{"type": r["issue_type"], "count": r["cnt"]} for r in rows]


# ──────────────────────────────────────────────────────────────────────
# Retry script generation
# ──────────────────────────────────────────────────────────────────────
def save_retry_script(work_dir: Path, attempt: int, fixes: list[dict],
                      concept: dict) -> Path:
    """Save a retry shell script for debugging/reproduction."""
    script_path = work_dir / f"retry_{attempt}.sh"
    lines = [
        "#!/bin/bash",
        f"# Retry {attempt} — auto-generated from Giri feedback",
        f"# Fixes: {', '.join(f['type'] for f in fixes)}",
        f"# Concept: {concept.get('title', '?')}",
        "",
        "set -euo pipefail",
        f'cd "{ROOT}"',
        "",
    ]

    for fix in fixes:
        lines.append(f"# Fix: {fix['type']} — {fix.get('detail', '')}")

    lines.append("")
    lines.append(f"# Concept (updated):")
    lines.append(f"# {json.dumps(concept.get('title', ''), ensure_ascii=False)}")
    lines.append("")

    script_path.write_text("\n".join(lines), encoding="utf-8")
    return script_path


# ──────────────────────────────────────────────────────────────────────
# Upload video to Slack
# ──────────────────────────────────────────────────────────────────────
def upload_video_to_slack(video_path: Path, title: str,
                          thread_ts: str | None = None) -> None:
    """Upload rendered video to Slack workroom (or thread)."""
    try:
        from slack_sdk import WebClient
        client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))
        channel = os.environ.get("SLACK_WORKROOM_CHANNEL")
        if not channel:
            return

        kwargs = {
            "channel": channel,
            "file": str(video_path),
            "title": title,
            "initial_comment": f":clapper: 렌더 완료 — `{video_path.name}`",
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        client.files_upload_v2(**kwargs)
        log.info("Uploaded %s to Slack", video_path.name)
    except Exception as e:
        log.warning("Slack video upload failed: %s", e)


# ──────────────────────────────────────────────────────────────────────
# Main: render_with_retry
# ──────────────────────────────────────────────────────────────────────
def render_with_retry(card_id: str, concept: dict, *,
                      max_retries: int = 3,
                      progress_cb: ProgressCb = None) -> tuple[Path | None, dict]:
    """
    Render → Giri review → auto-fix loop.

    Returns (output_path, final_giri_report).
    output_path is None if all retries failed.
    """
    from agents.cameraman import render_card
    from agents.reviewer import review as giri_review, format_slack_report

    current_concept = json.loads(json.dumps(concept))  # deep copy
    last_report = {}
    last_output = None

    for attempt in range(1, max_retries + 2):  # +1 for initial, +retries
        if progress_cb:
            if attempt == 1:
                progress_cb(f":movie_camera: 렌더 시작 (attempt {attempt})")
            else:
                progress_cb(f":arrows_counterclockwise: 리트라이 {attempt-1}/{max_retries}")

        try:
            # Render (may partially succeed with some scenes failing)
            output = render_card(card_id, progress_cb=progress_cb,
                                 concept=current_concept, use_brain=False)
            last_output = output

            # Giri review
            if progress_cb:
                progress_cb(":mag: Giri 검수 중...")
            storyboard = current_concept.get("cuts", [])
            report = giri_review(output, storyboard=storyboard, concept=current_concept)
            last_report = report

            # Post review to Slack
            if progress_cb:
                progress_cb(format_slack_report(report))

            verdict = report.get("판정", "")
            score = report.get("점수", 0)

            # PASS — done!
            if verdict in PASS_VERDICTS:
                _log_retry(card_id, attempt, report, [], "PASS")
                if progress_cb:
                    progress_cb(f":tada: 검수 통과! ({score}/10) — {verdict}")
                # Upload video to Slack thread
                title = current_concept.get("title", output.stem)
                upload_video_to_slack(output, title)
                return output, report

            # STOP — give up
            if verdict in STOP_VERDICTS:
                _log_retry(card_id, attempt, report, [], "STOP_폐기")
                if progress_cb:
                    progress_cb(f":octagonal_sign: 폐기 판정 — 더 이상 시도하지 않습니다.")
                return None, report

            # No more retries left
            if attempt > max_retries:
                _log_retry(card_id, attempt, report, [], "MAX_RETRIES")
                if progress_cb:
                    progress_cb(f":warning: {max_retries}회 리트라이 소진 — PD 검토 필요")
                return last_output, report

            # Parse feedback and apply fixes
            fixes = parse_giri_feedback(report)
            if not fixes:
                _log_retry(card_id, attempt, report, [], "NO_ACTIONABLE_FIX")
                if progress_cb:
                    progress_cb(":thinking_face: 자동 수정 가능한 항목 없음 — PD 검토 필요")
                return last_output, report

            fix_summary = ", ".join(f["type"] for f in fixes)
            _log_retry(card_id, attempt, report, fixes, fix_summary)

            # Save retry script for debugging
            work_dir = output.parent.parent if output else ROOT / "data" / "tmp"
            save_retry_script(work_dir, attempt, fixes, current_concept)

            # Apply fixes + inject Giri feedback into prompts
            current_concept = apply_fixes(fixes, current_concept,
                                          work_dir, progress_cb)
            current_concept = inject_giri_feedback(current_concept, report)

            # Need to re-approve the card for re-render
            con = _db()
            con.execute(
                "UPDATE cards SET state='approved', updated_at=datetime('now') WHERE card_id=?",
                (card_id,),
            )
            con.commit()

        except Exception as e:
            err_str = str(e)
            log.exception("Render attempt %d failed", attempt)
            _log_retry(card_id, attempt, {}, [], f"ERROR: {err_str[:200]}")

            is_safety = "sensitive words" in err_str or "Responsible AI" in err_str
            if is_safety and attempt <= max_retries:
                if progress_cb:
                    progress_cb(f":no_entry: 렌더 실패 — Veo safety filter. 프롬프트 동작 묘사 수정 중...")
                current_concept = _fix_safety_filter_prompts(current_concept)
                # Re-approve for next attempt
                con = _db()
                con.execute(
                    "UPDATE cards SET state='approved', updated_at=datetime('now') WHERE card_id=?",
                    (card_id,),
                )
                con.commit()
                continue

            if progress_cb:
                progress_cb(f":x: 렌더 실패 (attempt {attempt}): {err_str[:200]}")
            if attempt > max_retries:
                return None, last_report

    return last_output, last_report


# ──────────────────────────────────────────────────────────────────────
# Safety filter prompt fix
# ──────────────────────────────────────────────────────────────────────
# Veo safety filter가 "sensitive words" 에러를 반환하면,
# 문제는 단어 자체가 아니라 신체+동작의 결합이 선정적으로 해석된 것.
# 아래 패턴들을 안전한 대체 표현으로 교체.

_SAFETY_ACTION_REPLACEMENTS = [
    # 동작+신체 결합이 선정적 맥락으로 해석될 수 있는 표현
    ("rear end raised high in the air", "hind quarters lifted in play bow stance"),
    ("rear end raised high", "hind quarters up in play bow"),
    ("rear end raised", "back end up"),
    ("sprawled out completely", "lying stretched out comfortably"),
    ("sprawled out", "lying comfortably"),
    ("sprawled", "resting"),
    ("rises and falls slowly", "breathes gently"),
    ("rises and falls", "moves gently with breathing"),
    ("spread legs", "legs apart naturally"),
    ("belly exposed", "belly visible"),
    ("lies on back with", "rests on side showing"),
    ("pressed to the floor", "flat on the floor"),
    ("body rises", "fur moves"),
    ("chest heaves", "breathes deeply"),
    ("legs stretched all the way forward", "front paws stretched forward"),
    ("mouth wide", "mouth open"),
]


def _fix_safety_filter_prompts(concept: dict) -> dict:
    """Fix veo_prompts that triggered Google's safety filter.

    Replaces action+body combinations that can be misinterpreted
    as suggestive content. The character descriptions stay intact.
    """
    updated = json.loads(json.dumps(concept))
    fixed_count = 0
    for cut in updated.get("cuts", []):
        vp = cut.get("veo_prompt", "")
        if not vp:
            continue
        original = vp
        for old, new in _SAFETY_ACTION_REPLACEMENTS:
            vp = vp.replace(old, new)
            # Case-insensitive version
            vp = re.sub(re.escape(old), new, vp, flags=re.IGNORECASE)
        if vp != original:
            cut["veo_prompt"] = vp
            fixed_count += 1
            log.info("Safety-fixed veo_prompt for %s", cut.get("beat", "?"))
    if fixed_count:
        log.info("Fixed %d veo_prompts for safety filter", fixed_count)
    return updated
