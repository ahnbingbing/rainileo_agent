"""agents/mv_reviewer.py — the MV lane's Giri: a skeptical gate that catches the recurring
defect classes BEFORE PD does.

Born 2026-11-24: the MV pipeline shipped every defect straight to PD because nothing reviewed it
(mv_producer._auto_judge was a stub returning True). PD was the only gate, so the same mistakes —
repeated clip, indoor-footage-as-snow, caption desync, a foreign pet in a solo cut, a stale concept
— recurred every MV. This mirrors agents/reviewer.py (the daily Giri): DETERMINISTIC checks that
score-cap defect classes, applied critically. The rules live in agents/mv_principles.py so the
GENERATORS that follow them and the GATE that enforces them read ONE source.

review(m) returns a report dict {점수, 판정, issues[], 최종_결정} (same vocab as the daily reviewer).
Wire it into mv_producer so still_qc (pre-paid-render, manifest-level) and final_qc enforce it — or,
in PD-gate mode, so the reviewer's issues are surfaced to PD instead of PD having to find them.

Every check has a kill-switch (MV_REVIEW_<NAME>=0) and traces to a real repeated PD correction.
Prefer adding a deterministic check here over re-teaching an LLM — that is how PD stops being the gate.
"""
from __future__ import annotations

import logging
import os

from agents import mv_principles as P

log = logging.getLogger("agents.mv_reviewer")

_PASS = ("업로드", "즉시 업로드", "소폭 수정 후 업로드")


def _on(name: str) -> bool:
    return os.getenv(f"MV_REVIEW_{name}", "1") != "0"


def review(m: dict, *, past_themes: list[str] | None = None) -> dict:
    """Run the deterministic MV defect gates over a manifest. Returns a Giri-shaped report.

    `past_themes` = themes of prior MVs (for the freshness check); the producer supplies them.
    Verdict is '업로드' only if no gate fired; any fired gate caps the score and flips to '수정 필요'
    (or worse), with a human-readable reason in report['issues']."""
    report: dict = {"점수": 10, "판정": "업로드", "issues": []}

    if _on("DIVERSITY"):
        P.check_rf_clip_diversity(m, report)
    if _on("SETTING"):
        P.check_rf_setting_grounding(m, report)
    if _on("SYNC"):
        P.check_caption_sync(m, report)
    if _on("SOLOPET"):
        P.check_solo_cut_foreign_pet(m, report)
    if _on("SMILE"):
        P.check_anthropomorphic_smile(m, report)
    if _on("FRESHNESS"):
        P.check_concept_freshness(m, past_themes, report)

    # Optional VLM layer (OFF by default — adds cost/latency). When enabled it would check each AV
    # still visually: solo cut shows exactly ONE animal, Ryani has NO tail + slender face + correct
    # markings, Leo is an orange tabby. The deterministic checks above catch the generation-time root
    # causes (prompt names both pets, etc.); this would catch a model that drifts despite a clean
    # prompt. Wire via agents.openai_vision when the cost is justified.
    if _on("VLM") and os.getenv("MV_REVIEW_VLM", "0") == "1":
        try:
            _vlm_still_checks(m, report)
        except Exception as e:  # noqa: BLE001
            log.warning("mv_reviewer VLM layer failed (non-fatal): %s", e)

    report["최종_결정"] = report["판정"]
    n = len(report["issues"])
    log.info("mv_reviewer: %s (%d/10, %d issue%s)", report["판정"], report["점수"], n, "" if n == 1 else "s")
    return report


def passed(report: dict) -> bool:
    """True iff the MV cleared the gate (no defect fired)."""
    return report.get("판정") in _PASS and int(report.get("점수", 0)) >= 7


def summary(report: dict) -> str:
    """One-line human/Slack summary of a review."""
    head = f"[MV리뷰] {report.get('판정')} ({report.get('점수')}/10)"
    if report.get("issues"):
        return head + " — " + " · ".join(report["issues"])
    return head + " — 결함 없음"


# ── optional VLM still checks (off by default; see note above) ────────────────────────
def _vlm_still_checks(m: dict, report: dict) -> None:
    """Visual confirmation of the character-lock + solo-animal-count principles on AV stills.
    Stub hook: enable with MV_REVIEW_VLM=1 and implement against agents.openai_vision. Kept separate
    so the deterministic core never depends on a paid call."""
    raise NotImplementedError("VLM still-review layer not wired yet (MV_REVIEW_VLM=1 reserved)")
