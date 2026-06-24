"""Launch batch self-heal (PD 2026-06-10).

After the launch batch runs its slots, find the FAILED / empty slots, classify
each failure, apply a safe remediation, and REGENERATE — up to N rounds (default 3).

The per-slot pipeline already self-corrects internally (Validator-block re-propose
×5, render-fail retry ×3, face-defect → real-clip swap, osxphotos serialize lock).
This OUTER loop re-runs slots that STILL fail after those, escalating the safe knobs
each round, and — for failures it cannot auto-fix — produces an LLM DIAGNOSIS
(root cause + the code file/function to fix) written to an artifact for the
agent/PD to apply.

SAFETY: this loop NEVER blind-edits source in an unattended run. Auto-applied
remediations are limited to safe env knobs (retry counts, re-propose width). A
genuine code bug is surfaced as a diagnosis artifact — the agent (or PD) reviews
and applies it, then re-runs. That is the "agent fixes the code" loop, kept safe.

CLI:
    .venv/bin/python -m agents.launch_selfheal --date 2026-06-11 --lane ai_vtuber --rounds 3
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("agents.launch_selfheal")
ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "data" / "artifacts" / "selfheal"
KST = dt.timezone(dt.timedelta(hours=9))

# Ordered: first match wins. Patterns matched against the slot's captured log.
FAILURE_PATTERNS = [
    ("asset_not_found", r"not found|Preprocessing photos.*failed|원본 재다운로드.*실패|No candidates"),
    ("validator_block", r"Validator blocked|건너뜀|블록"),
    ("face_defect",     r"얼굴 무결성|얼굴 왜곡|face[_ ]?defect|\borb\b"),
    ("giri_fail",       r"기리 미통과|수정 필요|폐기|미통과"),
    ("render_error",    r"렌더 실패|Render attempt.*failed|rc=1|EXC:"),
    ("no_concept",      r"컨셉 없음|컨셉 실패"),
]


def classify_failure(logtext: str) -> str:
    for cat, pat in FAILURE_PATTERNS:
        if re.search(pat, logtext, re.IGNORECASE):
            return cat
    return "unknown"


def _remediate(category: str, round_no: int) -> str:
    """Apply a SAFE knob for the next round (no source edits). Returns a label.

    PD 2026-06-10 COST: remediation must NOT escalate Seedance spend. CHEAP failures
    (validator block / asset / no-concept — rejected BEFORE Seedance) get a wider
    re-propose. EXPENSIVE failures (render/giri/face — Seedance already spent) get
    NO retry bump — re-rendering them ×N compounded into the ~$100 runaway. They are
    left for the next self-heal round at the SAME (low) render budget, and the hard
    SEEDANCE_MAX_CALLS ceiling backstops the whole run."""
    if category in ("asset_not_found", "validator_block", "no_concept"):
        # cheap (pre-Seedance) — widen re-propose to find a renderable concept
        os.environ["AV_BLOCK_REPROPOSE"] = str(min(8, 5 + round_no))
        return f"재제안 폭 ↑ ({os.environ['AV_BLOCK_REPROPOSE']}) — 다른 자산/컨셉 (무비용)"
    if category in ("giri_fail", "render_error", "face_defect"):
        # EXPENSIVE — do NOT bump render retries (cost). Retry once at base budget.
        # PD 2026-06-11: a CAPTION-shaped giri_fail is already auto-salvaged INLINE
        # (producer rewrites captions on the existing render, $0, re-reviews) before
        # it ever reaches this outer loop — so a giri_fail arriving here is genuinely
        # NOT caption-fixable (structural: marking/face/render). Keep it terminal.
        return "Seedance 비용 발생 실패 — 재시도 예산 유지(상향 금지), 하드 ceiling 적용"
    return "일반 재시도"


def diagnose_failure(category: str, logtext: str, target: dt.date,
                     lane: str, slot: str) -> dict:
    """LLM diagnosis of a PERSISTENT failure → root cause + the code file/function
    to fix. Surfaced for the agent/PD to apply; never auto-edited here."""
    try:
        from agents.llm_cascade import call_text_cascade
        system = (
            "You are a senior engineer debugging a Python video-generation pipeline "
            "(agents/*.py, scripts/*.py). Given ONE failed launch slot's log, identify "
            "the ROOT CAUSE and the SPECIFIC code file + function to fix. Be concrete. "
            "Return ONLY JSON: {\"root_cause\":str, \"fix_file\":str, "
            "\"fix_function\":str, \"fix_summary\":str, \"risk\":\"low|med|high\"}.")
        user = (f"category={category}\nlane={lane} slot={slot} date={target}\n\n"
                f"LOG (tail):\n{logtext[-4000:]}")
        txt = call_text_cascade(system, user, max_tokens=1200).strip()
        txt = re.sub(r"^```(?:json)?\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
        d = json.loads(txt)
        d["category"] = category
        return d
    except Exception as e:
        return {"category": category, "root_cause": f"(LLM diagnose failed: {e})"}


def run_with_selfheal(target: dt.date, *, max_rounds: int = 3,
                      lane_filter: str | None = None, slot_filter: str | None = None,
                      do_upload: bool = True, progress_cb=None) -> dict:
    """Run the launch slots; re-run failed ones with remediation up to `max_rounds`.
    Returns {done, failed, diagnoses}."""
    from agents.launch import launch_pipeline, day_assignments

    # Slack wiring (PD 2026-06-10): post to the workroom + open per-slot threads,
    # exactly like launch.main — without this the self-heal run was stdout-only and
    # nothing showed up in the Slack workroom.
    slack_client = slack_channel = None
    try:
        _ch = os.environ.get("SLACK_WORKROOM_CHANNEL")
        _tok = os.environ.get("SLACK_BOT_TOKEN")
        if _ch and _tok:
            from slack_sdk import WebClient
            slack_client = WebClient(token=_tok)
            slack_channel = _ch
            slack_client.chat_postMessage(
                channel=_ch,
                text=(f":wrench: *self-heal* {target.isoformat()} "
                      f"{lane_filter or ''} {slot_filter or ''} — 실패 슬롯 분석→재생성 "
                      f"(최대 {max_rounds}회, 슬롯별 쓰레드에서 진행상황·결과)"))
    except Exception as e:
        log.warning("self-heal slack wiring failed (stdout only): %s", e)

    def cap(m, buf=None):
        if buf is not None:
            buf.append(m)
        if slack_client and slack_channel:
            try:
                slack_client.chat_postMessage(channel=slack_channel, text=m)
            except Exception:
                pass
        if progress_cb:
            progress_cb(m)

    assignments = day_assignments(target)
    if lane_filter:
        assignments = [(l, h) for l, h in assignments if l == lane_filter]
    if slot_filter:
        assignments = [(l, h) for l, h in assignments if h == slot_filter]
    want = list(dict.fromkeys(assignments))  # ordered-unique (lane, slot)
    done: dict[tuple, dict] = {}
    diagnoses: list[dict] = []
    fail_info: dict[tuple, dict] = {}  # (lane,slot) → {cat, rem, terminal} for the final summary
    # PD 2026-06-10 COST: a slot whose failure already SPENT Seedance (rendered then
    # failed Giri / face gate) is TERMINAL — re-rendering it every round is what
    # compounded to ~$100. It's saved for PD review (save-AV) and NOT retried. Only
    # CHEAP failures (validator block / asset / no-concept — rejected before Seedance)
    # keep retrying across rounds.
    EXPENSIVE = {"giri_fail", "face_defect"}
    terminal: set = set()

    for rnd in range(1, max_rounds + 1):
        pending = [(l, h) for (l, h) in want if (l, h) not in done and (l, h) not in terminal]
        if not pending:
            break
        cap(f":arrows_counterclockwise: [self-heal R{rnd}/{max_rounds}] 대상 슬롯: "
            f"{', '.join(f'{h} {l}' for l, h in pending)}")
        for (lane, slot) in pending:
            buf: list[str] = []
            try:
                res = launch_pipeline(target, progress_cb=lambda m: cap(m, buf),
                                      do_upload=do_upload, lane_filter=lane,
                                      slot_filter=slot, slack_client=slack_client,
                                      slack_channel=slack_channel)
            except Exception as e:
                log.exception("self-heal slot run failed (%s %s)", lane, slot)
                res = []
                buf.append(f"EXC: {e}")
            if res and res[0].get("output"):
                done[(lane, slot)] = res[0]
                cap(f":white_check_mark: [self-heal] {slot} {lane} 성공 (R{rnd})")
            else:
                logtext = "\n".join(buf)
                cat = classify_failure(logtext)
                rem = _remediate(cat, rnd)
                cap(f":x: [self-heal] {slot} {lane} 실패 ({cat}) — 조치: {rem}")
                fail_info[(lane, slot)] = {"cat": cat, "rem": rem, "terminal": cat in EXPENSIVE}
                if cat in EXPENSIVE:
                    terminal.add((lane, slot))
                    cap(f":coin: [self-heal] {slot} {lane} 비용 발생 실패 → 재렌더 중단 "
                        f"(영상 저장됨, 추가 Seedance 안 씀)")
                if rnd == max_rounds or (lane, slot) in terminal:
                    diag = diagnose_failure(cat, logtext, target, lane, slot)
                    diagnoses.append({"lane": lane, "slot": slot, **diag})
                    cap(f":mag: [self-heal] {slot} {lane} 진단: "
                        f"{diag.get('root_cause', '?')[:120]} "
                        f"→ {diag.get('fix_file', '?')}:{diag.get('fix_function', '?')}")

    failed = [(l, h) for (l, h) in want if (l, h) not in done]
    ap = None
    if diagnoses:
        ART.mkdir(parents=True, exist_ok=True)
        ap = ART / f"selfheal_{target.isoformat()}.json"
        ap.write_text(json.dumps(
            {"date": str(target), "rounds": max_rounds,
             "failed": [f"{h} {l}" for l, h in failed], "diagnoses": diagnoses},
            ensure_ascii=False, indent=2), encoding="utf-8")

    # Consolidated end-of-batch summary to Slack (PD 2026-06-24): one digest with
    # successes, per-slot failure reasons, and the LLM diagnosis (root cause + the code
    # file/function to fix) for slots the auto re-work couldn't resolve.
    def _lbl(l):
        return "AV" if l == "ai_vtuber" else "RF"
    diag_by = {(d.get("lane"), d.get("slot")): d for d in diagnoses}
    lines = [f":checkered_flag: *배치 써머리* {target.isoformat()} — 성공 "
             f"{len(done)}/{len(want)}" + ("" if failed else " (전부 성공 🎉)")]
    for (l, h), v in done.items():
        lines.append(f"  ✅ {h} {_lbl(l)} — `{v.get('video_id', '-')}` "
                     f"(공개 {v.get('publish_at', '?')})")
    for (l, h) in failed:
        fi = fail_info.get((l, h), {})
        term = " · 비용발생→재렌더 중단(영상 저장됨)" if fi.get("terminal") else ""
        line = f"  ❌ {h} {_lbl(l)} — 실패: {fi.get('cat', '?')}{term}"
        d = diag_by.get((l, h))
        if d:
            line += (f"\n     🔍 원인: {str(d.get('root_cause', '?'))[:150]}"
                     f"\n     🛠 수정: {d.get('fix_file', '?')}:{d.get('fix_function', '?')} "
                     f"(risk {d.get('risk', '?')})")
        lines.append(line)
    if failed:
        lines.append("  → 자동 재작업(최대 %d회)으로 못 푼 슬롯이에요. 위 진단대로 코드 수정/재렌더 "
                     "필요" % max_rounds + (f" — 상세 {ap.name}" if ap else "") + ".")
    cap("\n".join(lines))
    return {
        "done": {f"{l}/{h}": v for (l, h), v in done.items()},
        "failed": [f"{h} {l}" for l, h in failed],
        "diagnoses": diagnoses,
    }


def main() -> int:
    import argparse
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(
        description="Launch batch self-heal (analyze → remediate → regenerate ×N)")
    p.add_argument("--date", default=None, help="target date YYYY-MM-DD (default: tomorrow KST)")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--lane", choices=["ai_vtuber", "real_footage"], default=None)
    p.add_argument("--slot", default=None, help="single HH:MM slot")
    p.add_argument("--no-upload", action="store_true")
    a = p.parse_args()
    target = (dt.date.fromisoformat(a.date) if a.date
              else (dt.datetime.now(KST) + dt.timedelta(days=1)).date())
    r = run_with_selfheal(target, max_rounds=a.rounds, lane_filter=a.lane,
                          slot_filter=a.slot, do_upload=not a.no_upload,
                          progress_cb=lambda m: print(m, flush=True))
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
