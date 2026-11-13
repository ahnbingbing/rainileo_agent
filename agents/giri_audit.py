"""Giri self-audit — surface GAPS between PD's accumulated standards and what Giri enforces.

PD-mirror P3 (PD 2026-11-13). review-learning updates the GENERATORS, and P0 now feeds the
pd_taste corpus to the CHECKER — but Giri's ENFORCEMENT (the deterministic gates in reviewer.py)
only grows when a human adds a gate. This batch closes the loop: it reads PD's repeated standards
(the pd_taste veto/correction corpus) and the gates Giri currently enforces, then asks an LLM the
one question that keeps Giri a living mirror — "what does PD repeatedly flag that Giri has NO gate
for?" — and emits a GAP REPORT.

It NEVER edits code. Blind LLM auto-synthesis of gates is exactly the risk the design forbids
(giri-update is non-negotiable: a human applies each gap as a deterministic cap + a known-bad
regression). This batch only SURFACES gaps — the report is the input to that human step.

Run periodically (weekly cron via deploy/run_job.sh); the session-start hook reads the latest
report (notes/giri_audit_latest.md) cheaply so the gaps stay visible without re-running the audit.

    python -m agents.giri_audit [--slack] [--lane real_footage|ai_vtuber] [--n 80]
"""
from __future__ import annotations

import argparse
import inspect
import os
import sqlite3
from pathlib import Path

import logging

log = logging.getLogger("agents.giri_audit")

ROOT = Path(__file__).resolve().parent.parent
LATEST = ROOT / "notes" / "giri_audit_latest.md"


def enumerate_gates() -> list[tuple[str, str]]:
    """Introspect reviewer.py for its deterministic gates — the enforcement Giri HAS today.
    Returns (name, one-line purpose) so the audit knows what's already covered."""
    try:
        from agents import reviewer as rv
    except Exception as e:
        log.warning("enumerate_gates: reviewer import failed: %s", e)
        return []
    gates = []
    for name, fn in inspect.getmembers(rv, inspect.isfunction):
        if not (name.startswith("_") and (name.endswith("_gate") or name.startswith("_check_"))):
            continue
        doc = (fn.__doc__ or "").strip().replace("\n", " ")
        gates.append((name, doc[:180]))
    return sorted(gates)


def corpus_brief(con: sqlite3.Connection, *, lane: str | None = None, n: int = 80) -> str:
    """PD's accumulated standards — the veto/correction corpus, in the checker framing. This is
    the ground truth the audit measures Giri's gates against (PD's words, not a retro synthesis)."""
    try:
        from agents import pd_taste as _pt
        return _pt.taste_digest(con, lane=lane, n=n, for_review=True) or ""
    except Exception as e:
        log.warning("corpus_brief failed: %s", e)
        return ""


_AUDIT_SYSTEM = """\
너는 Giri(렌더 후 리뷰 에이전트)의 자가-감사자다. Giri는 PD의 거울 — PD가 축적해온 기준의 총합을
비판적으로 적용해야 한다. 그런데 Giri의 '강제'(결정론 게이트)는 사람이 게이트를 추가할 때만 늘어나,
PD가 반복해 말한 기준인데 Giri가 아직 안 잡는 '갭'이 쌓인다.

입력: (A) PD가 실제로 내린 리젝/교정 corpus, (B) Giri가 지금 enforce하는 게이트 목록.
너의 일: **PD가 반복적으로 지적/교정하는데 (A)에 있는데 (B)의 어떤 게이트도 안 잡는 기준**을 찾아라.

규칙:
- 코드를 쓰지 마라. 갭을 surface만 한다(사람이 giri-update로 결정론 게이트+회귀로 적용한다).
- 이미 게이트가 있는 건 갭이 아니다((B)와 대조해 중복 제외).
- 회의적·구체적으로. 한 번 나온 취향은 갭 아님 — **반복** 패턴만.
- 각 갭: {PD_기준(한 줄), corpus_근거(어느 결정들), 지금_왜_새나, 제안_결정론_체크(한 줄, 코드 아님)}.

출력(JSON): {"gaps":[{"standard","evidence","why_missed","suggested_check"}], "note":"<전체 한줄 평>"}
갭이 없으면 "gaps":[] + note로 "현재 커버 양호" 명시.
"""


def find_gaps(corpus: str, gates: list[tuple[str, str]]) -> str | None:
    """LLM gap analysis. Best-effort — returns a markdown report, or None on any failure (the
    caller still writes the deterministic corpus+gates dump so a human can eyeball)."""
    if not corpus:
        return None
    gate_lines = "\n".join(f"- {n}: {d}" for n, d in gates) or "(none)"
    user = (f"## (A) PD corpus (리젝/교정)\n{corpus}\n\n"
            f"## (B) Giri 현재 게이트 ({len(gates)}개)\n{gate_lines}\n\n"
            "위 규칙대로 갭 리포트 JSON 하나만 출력하라.")
    try:
        from agents.writer_director import _call_anthropic, DIRECTOR_MODEL
        model = os.getenv("GIRI_AUDIT_MODEL", DIRECTOR_MODEL)
        out = _call_anthropic(_AUDIT_SYSTEM, user, model=model,
                              max_tokens=int(os.getenv("GIRI_AUDIT_MAX_TOKENS", "3000")))
        return _render(out)
    except Exception as e:
        log.warning("find_gaps LLM failed: %s", e)
        return None


def _render(raw: str) -> str:
    """Turn the LLM JSON into a readable gap report (falls back to the raw text)."""
    import json as _j
    try:
        a, b = raw.find("{"), raw.rfind("}")
        obj = _j.loads(raw[a:b + 1]) if a != -1 and b != -1 else {}
    except Exception:
        return raw.strip()
    gaps = obj.get("gaps") or []
    lines = [f"# Giri 자가-감사 갭 리포트 — {len(gaps)}건", ""]
    if obj.get("note"):
        lines += [f"> {obj['note']}", ""]
    for i, g in enumerate(gaps, 1):
        lines.append(f"## {i}. {g.get('standard', '?')}")
        if g.get("evidence"):
            lines.append(f"- **근거**: {g['evidence']}")
        if g.get("why_missed"):
            lines.append(f"- **왜 새나**: {g['why_missed']}")
        if g.get("suggested_check"):
            lines.append(f"- **제안 체크(사람이 giri-update로 적용)**: {g['suggested_check']}")
        lines.append("")
    lines.append("_자동 코드수정 없음 — 각 갭은 giri-update 스킬로 결정론 게이트+회귀 적용._")
    return "\n".join(lines)


def run(*, lane: str | None = None, n: int = 80, slack: bool = False,
        save: bool = True) -> str:
    """Run the audit: corpus + gates → gap report. Writes LATEST (for session-start) and
    optionally posts to the board channel. Returns the report text."""
    con = sqlite3.connect(ROOT / "data" / "agent.db")
    try:
        corpus = corpus_brief(con, lane=lane, n=n)
        gates = enumerate_gates()
    finally:
        con.close()
    report = find_gaps(corpus, gates)
    if not report:
        # No LLM / no corpus — still emit a deterministic dump so the gap-hunt isn't blocked.
        gate_lines = "\n".join(f"- {n_}: {d}" for n_, d in gates)
        report = (f"# Giri 자가-감사 (LLM 미사용 덤프)\n\n"
                  f"## PD corpus\n{corpus or '(비어있음 — pd_selections 미수집)'}\n\n"
                  f"## Giri 게이트 {len(gates)}개\n{gate_lines}\n")
    if save:
        try:
            LATEST.write_text(report, encoding="utf-8")
        except Exception as e:
            log.warning("giri_audit save failed: %s", e)
    if slack:
        try:
            from agents import notify
            notify.warn(":mag: *Giri 자가-감사 갭 리포트*\n" + report[:2800])
        except Exception as e:
            log.warning("giri_audit slack post failed: %s", e)
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--slack", action="store_true")
    ap.add_argument("--lane", default=None)
    ap.add_argument("--n", type=int, default=80)
    a = ap.parse_args()
    print(run(lane=a.lane, n=a.n, slack=a.slack))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
