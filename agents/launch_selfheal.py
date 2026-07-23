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
# render_error precedes giri_fail (PD 2026-07-05): the emptied-slot line is the generic
# "기리 미통과/렌더 실패 — 슬롯 비움" (both tokens, always emitted on empty), so keying
# giri_fail first mislabels a real render CRASH as giri_fail — which is TERMINAL (never
# retried), wrong for a likely-transient Seedance cut-miss. A render exception emits the
# CONCRETE "렌더 실패: <exc>" (colon) + the exception text ("cannot render", "missing
# animated mp4", …); those disambiguate a crash from a pure giri rejection. The bare
# "렌더 실패 —" of the generic line has no colon and won't match, so a genuine giri
# rejection still falls through to giri_fail.
# PD 2026-07-20 ("7/21 RF 예약이 하나도 없어"): a DETERMINISTIC content-quality gate that
# guts an episode below viability (RF coherence gate drops unrelated-outing cuts → too short;
# RF face gate drops face-leaking cuts → too few cuts) fails HARD, and self-heal used to answer
# with a futile SAME-concept re-render (render_error / asset_not_found paths) — the gate re-drops
# the identical cuts every round, so all 3 rounds die and the slot ships empty. Unlike a transient
# asset-download miss, retrying the same clips can NEVER recover; only a FRESH concept (different
# clips) can. So this class is matched FIRST and routed to the proven fresh-reroll path below.
CONTENT_GUTTED_PAT = (r"무관 outing 컷 드롭|컷간 일관성.*드롭|"
                      r"too few cuts left after dropping face-leaking|"
                      r"real_footage too short|refusing to publish a stub")
FAILURE_PATTERNS = [
    ("content_gutted",  CONTENT_GUTTED_PAT),
    ("asset_not_found", r"not found|Preprocessing photos.*failed|원본 재다운로드.*실패|No candidates"),
    ("validator_block", r"Validator blocked|건너뜀|블록"),
    ("face_defect",     r"얼굴 무결성|얼굴 왜곡|face[_ ]?defect|\borb\b"),
    ("render_error",    r"렌더 실패:|cannot render|missing animated mp4|direct render failed|Render attempt.*failed|Traceback|rc=1|EXC:"),
    ("giri_fail",       r"기리 미통과|수정 필요|폐기|미통과"),
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
    left for the next self-heal round at the SAME (low) render budget. Cost across the
    self-heal run is bounded by the round count (R1/R2/R3) × AV_MAX_RETRIES=0 (one
    render per slot per round); the hard SEEDANCE_MAX_CALLS ceiling backstops each
    EPISODE render (PD 2026-07-23 the counter resets per render_card, so it no longer
    accumulates across rounds/slots — that accumulation used to let a passed slot's
    re-render starve a failed slot's budget and empty a good slot)."""
    if category == "content_gutted":
        # A deterministic content gate gutted the episode — the SAME clips will re-drop the
        # same way, so retrying this concept is futile. Widen re-propose AND (below) route to
        # a fresh-concept reroll. Cheap for RF; the whole point is different clips.
        os.environ["AV_BLOCK_REPROPOSE"] = str(min(8, 5 + round_no))
        return "결정론 게이트 gutting — 완전히 새 컨셉(다른 클립)으로 재제안 (같은 컨셉 재렌더 무의미)"
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
        # PD 2026-07-23: the LLM diagnosis is a HINT, not ground truth — it confidently
        # hallucinates fix locations (07-25 12:30 RF: blamed a non-existent
        # `agents/real_footage/branding_cards.py` for what was actually a same-clip
        # collapse → too-short gutting; the real signal, "dropping branding asset", was
        # normal pool hygiene it latched onto). Verify the named file actually exists and
        # flag the diagnosis when it doesn't, so PD/the agent distrusts a fabricated path
        # instead of chasing it. Cheap deterministic check; never blocks.
        fix_file = str(d.get("fix_file") or "").strip()
        if fix_file:
            try:
                exists = (ROOT / fix_file).exists()
            except Exception:
                exists = False
            d["fix_file_exists"] = exists
            if not exists:
                d["low_confidence"] = True
                d["diag_warning"] = (
                    f"⚠️ 진단이 지목한 파일 `{fix_file}`이 저장소에 없음 — LLM 환각 가능성 "
                    f"높음. root_cause를 코드/로그로 재검증하고 지목 경로를 그대로 믿지 말 것.")
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
        # Roadmap B: also stream milestones into PD's board thread when a board rerender
        # set BOARD_PROGRESS_* on the env (filtered to milestones inside the helper).
        try:
            from agents.board_progress import post_board_progress
            post_board_progress(m)
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
    # PD 2026-07-14 ("왜 4개를 다 못 만들어"): an EXPENSIVE fail (rendered then failed Giri/face)
    # used to be immediately TERMINAL → the slot shipped EMPTY, so a batch routinely landed 2-3/4.
    # The cost rule that made it terminal was about not re-rendering the SAME broken concept every
    # round. So allow exactly ONE re-roll with a COMPLETELY FRESH concept (launch_pipeline re-
    # brainstorms; the failed concept is now in the exclude set, so the gag-dedup won't repeat it)
    # before giving up. One extra ~$50 AV render buys the 4th slot — PD's call. Kill: SELFHEAL_REROLL=0.
    rerolled: set = set()
    reroll_on = os.getenv("SELFHEAL_REROLL", "1") != "0"
    # PD 2026-07-23: real_footage renders are FREE (ffmpeg + Gemini, no Seedance $), so an RF
    # slot that fails Giri should keep rerolling FRESH concepts until one lands — the 3-round
    # cap that suits the PAID AV lane shipped a hard RF slot EMPTY after 3 tries (07-25 21:00
    # exhausted 3 rounds of 4-6/10 rejects). Give RF a higher round budget; the AV lane still
    # terminals out early on cost (EXPENSIVE below), so the extra rounds only exercise RF.
    # RF_SELFHEAL_ROUNDS=3 reverts to the old shared cap.
    _rounds = max(max_rounds, int(os.getenv("RF_SELFHEAL_ROUNDS", "6"))) \
        if any(l == "real_footage" for l, _ in want) else max_rounds

    for rnd in range(1, _rounds + 1):
        pending = [(l, h) for (l, h) in want if (l, h) not in done and (l, h) not in terminal]
        if not pending:
            break
        cap(f":arrows_counterclockwise: [self-heal R{rnd}/{_rounds}] 대상 슬롯: "
            f"{', '.join(f'{h} {l}' for l, h in pending)}")
        for (lane, slot) in pending:
            buf: list[str] = []
            try:
                # progress_cb → Slack workroom (cap) for humans; slot_log_cb → buf for
                # classify_failure. Both needed: with a Slack slot-thread active,
                # launch_pipeline's sp() posts to the thread and skips progress_cb, so the
                # PURE slot_log_cb sink is what actually feeds the failure classifier (else
                # every failure classifies "unknown" → blind retry). PD 2026-07-05.
                res = launch_pipeline(target, progress_cb=lambda m: cap(m, buf),
                                      slot_log_cb=buf.append,
                                      do_upload=do_upload, lane_filter=lane,
                                      slot_filter=slot, slack_client=slack_client,
                                      slack_channel=slack_channel,
                                      consolidate_videos=True)
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
                # An EXPENSIVE fail gets ONE fresh-concept re-roll (if rounds remain) before it's
                # terminal — so a Giri/face reject fills the slot on a new concept instead of
                # leaving it empty. The failed concept is now a card in the exclude set, so the
                # re-roll's brainstorm won't repeat it.
                # PD 2026-07-20: a content_gutted fail (deterministic gate over-drop → non-viable)
                # ALSO reroll to a fresh concept — but EVERY remaining round, not just once: the
                # concept is cheap to re-render and the ONLY recovery is different clips, so keep
                # cycling the pool until a coherent/face-clean concept lands (or rounds run out).
                # PD 2026-07-23: real_footage spends NO Seedance, so an RF failure of ANY kind
                # (giri_fail / content_gutted / …) is CHEAP — reroll a fresh concept every
                # remaining round, never cost-terminal. Only the AV lane keeps the EXPENSIVE
                # (Seedance-spent) terminal cap.
                _rf_free = (lane == "real_footage")
                _exp_reroll = (cat in EXPENSIVE and reroll_on
                               and (lane, slot) not in rerolled and rnd < _rounds)
                _content_reroll = (cat == "content_gutted" and reroll_on and rnd < _rounds)
                _rf_reroll = (_rf_free and reroll_on and rnd < _rounds)
                _reroll_now = _exp_reroll or _content_reroll or _rf_reroll
                is_terminal = (not _rf_free) and cat in EXPENSIVE and not _reroll_now
                fail_info[(lane, slot)] = {"cat": cat, "rem": rem, "terminal": is_terminal}
                if _reroll_now:
                    rerolled.add((lane, slot))
                    _why = ("결정론 게이트 gutting" if cat == "content_gutted"
                            else "미통과(무비용 RF)" if _rf_free else "비용 실패")
                    cap(f":game_die: [self-heal] {slot} {lane} {_why} → **완전히 새 컨셉으로 재롤** "
                        f"(같은 컨셉 재렌더 아님 — 다른 클립)")
                elif cat in EXPENSIVE and not _rf_free:
                    terminal.add((lane, slot))
                    cap(f":coin: [self-heal] {slot} {lane} 비용 발생 실패 → 재렌더 중단 "
                        f"(영상 저장됨, 추가 Seedance 안 씀)")
                if rnd == _rounds or (lane, slot) in terminal:
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
            {"date": str(target), "rounds": _rounds,
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
    # PD 2026-07-19: an EMPTY slot (self-heal exhausted → no video) used to be just one
    # line in the digest and got missed until PD spotted it by hand. Lead with a loud,
    # actionable alert naming the empty slots so a deficient batch can't slip by silently.
    if failed:
        _empties = ", ".join(f"{h} {_lbl(l)}" for (l, h) in failed)
        lines.insert(1, f":rotating_light: *빈 슬롯 {len(failed)}개 — 손수정 필요*: {_empties} "
                        f"(self-heal이 {_rounds}회 시도 후 junk 대신 비움)")
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
            if d.get("diag_warning"):
                line += f"\n     {d['diag_warning']}"
        lines.append(line)
    if failed:
        lines.append("  → 자동 재작업(최대 %d회)으로 못 푼 슬롯이에요. 위 진단대로 코드 수정/재렌더 "
                     "필요" % _rounds + (f" — 상세 {ap.name}" if ap else "") + ".")
    if done:
        lines.append("  ↳ 아래 이 쓰레드에 오늘 영상 전부 올려요. 리뷰는 여기서 — 취소는 "
                     "`veto <파일명>` (예: `veto " + next(iter(done.values())).get("fname", "260705_RF2100")
                     + "`).")
    # PD 2026-07-22: audit the LIVE YouTube schedule against the DB and warn on ORPHANS
    # (scheduled-public but no card → they double-book a slot, invisible to arc/veto — the
    # 07-23 duplicate-slot root). The upload path now vetoes superseded ids on replace; this
    # is the safety net that surfaces any that still slip. Non-fatal.
    try:
        from agents.reconcile import orphan_report
        _orph = orphan_report()
        if _orph:
            lines.append(_orph)
    except Exception as _oe:
        log.warning("reconcile orphan_report failed (non-fatal): %s", _oe)
    # Consolidated review thread (PD 2026-07-04): post the summary as the thread PARENT,
    # then upload every produced mp4 as a reply under it — one place to review the whole
    # day. Each upload is labelled with its schedule name and registered so an in-thread
    # `veto <name>` cancels THAT video (multiple videos share this thread, so plain `veto`
    # is ambiguous — the label disambiguates).
    summary_ts = None
    if slack_client and slack_channel:
        try:
            r = slack_client.chat_postMessage(channel=slack_channel, text="\n".join(lines))
            summary_ts = r.get("ts")
        except Exception as e:
            log.warning("summary post failed: %s", e)
            cap("\n".join(lines))
    else:
        cap("\n".join(lines))
    if progress_cb:
        progress_cb("\n".join(lines))
    if summary_ts and slack_client and slack_channel:
        from agents.launch import record_batch_video
        from agents.producer import _db
        for (l, h), v in done.items():
            outp = v.get("output")
            if not outp or not Path(outp).exists():
                continue
            fname = v.get("fname") or f"{target.strftime('%y%m%d')}_{_lbl(l)}{h.replace(':', '')}"
            vid = v.get("video_id")
            cmt = f":movie_camera: *{fname}* — 공개 {v.get('publish_at', '?')}"
            if vid:
                cmt += f" · `{vid}` · 취소: `veto {fname}`"
            try:
                slack_client.files_upload_v2(
                    channel=slack_channel, thread_ts=summary_ts, file=str(outp),
                    title=f"{fname}.mp4", initial_comment=cmt)
            except Exception as e:
                log.warning("summary video upload failed (%s): %s", fname, e)
            # Register (batch thread, label) → video for in-thread veto-by-label.
            if vid:
                try:
                    con = _db()
                    record_batch_video(con, thread_ts=summary_ts, fname=fname,
                                       channel=slack_channel, video_id=vid, lane=l,
                                       slot=h, target=target,
                                       publish_at=v.get("publish_at"))
                    con.close()
                except Exception as e:
                    log.warning("record batch video failed (%s): %s", fname, e)
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
    # Lead time: the 03:00 cron builds a batch LAUNCH_LEAD_DAYS ahead (default 1 = tomorrow).
    # PD 2026-07-21: set to 2 so a batch is ready two days out, giving a full extra day for
    # PD spot-check/veto before it goes public. An explicit --date always wins.
    _lead = max(1, int(os.getenv("LAUNCH_LEAD_DAYS", "1")))
    target = (dt.date.fromisoformat(a.date) if a.date
              else (dt.datetime.now(KST) + dt.timedelta(days=_lead)).date())
    r = run_with_selfheal(target, max_rounds=a.rounds, lane_filter=a.lane,
                          slot_filter=a.slot, do_upload=not a.no_upload,
                          progress_cb=lambda m: print(m, flush=True))
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
