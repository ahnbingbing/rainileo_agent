#!/usr/bin/env python3
"""scripts/api_cost_report.py — daily AI-API usage / cost digest (PD 2026-06-25).

Answers "어디서 돈이 샜나" every morning. Reads the api_calls ledger (agents/api_ledger.py)
and summarizes spend by provider × stage for the KST day, flags Seedance call-count
multiplication (the real cost driver), and — best-effort — pulls OpenAI's real billed
cost from the Costs API when an admin key is available.

Counts are EXACT (one ledger row per billable hop). Dollar figures are ESTIMATES from
the price map unless the real OpenAI Costs API responds. Google/BytePlus have no simple
per-key billing API, so those stay estimates from our own call counts.

Run:  .venv/bin/python -m scripts.api_cost_report           # print yesterday+today (KST)
      .venv/bin/python -m scripts.api_cost_report --post    # also post to workroom
      .venv/bin/python -m scripts.api_cost_report --date 2026-06-24 --post
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
KST = dt.timezone(dt.timedelta(hours=9))


def _utc_range_for_kst_day(day: dt.date) -> tuple[str, str]:
    """[start,end) UTC ISO strings (sqlite datetime fmt) covering one KST calendar day."""
    start_kst = dt.datetime(day.year, day.month, day.day, tzinfo=KST)
    end_kst = start_kst + dt.timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (start_kst.astimezone(dt.timezone.utc).strftime(fmt),
            end_kst.astimezone(dt.timezone.utc).strftime(fmt))


def _day_rows(con: sqlite3.Connection, day: dt.date) -> list[sqlite3.Row]:
    lo, hi = _utc_range_for_kst_day(day)
    try:
        return con.execute(
            "SELECT provider, service, stage, COUNT(*) n, SUM(units) units, "
            "SUM(est_cost_usd) est FROM api_calls WHERE ts >= ? AND ts < ? "
            "GROUP BY provider, service, stage ORDER BY est DESC", (lo, hi)).fetchall()
    except sqlite3.OperationalError:
        return []  # ledger table not created yet (no calls logged)


def _openai_real_cost(day: dt.date) -> str | None:
    """Best-effort REAL billed USD from OpenAI Costs API. Needs an admin key
    (OPENAI_ADMIN_KEY, sk-admin-...). Returns a one-line string or None."""
    key = os.getenv("OPENAI_ADMIN_KEY")
    if not key:
        return None
    start = int(dt.datetime(day.year, day.month, day.day, tzinfo=KST)
                .astimezone(dt.timezone.utc).timestamp())
    url = f"https://api.openai.com/v1/organization/costs?start_time={start}&limit=31"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        total = 0.0
        for bucket in data.get("data", []):
            for res in bucket.get("results", []):
                total += float((res.get("amount") or {}).get("value") or 0)
        return f"OpenAI 실제 청구(Costs API): ${total:.2f}"
    except Exception as e:  # noqa: BLE001
        return f"OpenAI 실제 청구 조회 실패: {str(e)[:120]}"


def build_report(day: dt.date) -> str:
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    rows = _day_rows(con, day)
    con.close()
    if not rows:
        return (f":moneybag: *API 비용 리포트 — {day} (KST)*\n"
                "기록된 API 호출 없음 (그날 렌더가 없었거나, 계측 배포 전 회차).")

    by_provider: dict[str, float] = {}
    seedance_calls = 0
    veo_calls = 0
    lines = [f":moneybag: *API 비용 리포트 — {day} (KST)*", "",
             "*단계별 (호출수 · 추정$):*"]
    total = 0.0
    for r in rows:
        prov, svc, stage = r["provider"], r["service"], r["stage"] or "-"
        n, est = r["n"], (r["est"] or 0.0)
        total += est
        by_provider[prov] = by_provider.get(prov, 0.0) + est
        if svc == "seedance":
            seedance_calls += n
        if svc == "veo":
            veo_calls += n
        lines.append(f"  • {prov}/{svc} `{stage}` — {n}콜 · ~${est:.2f}")

    lines.append("")
    lines.append("*provider 합계 (추정):* " +
                 ", ".join(f"{p} ~${c:.2f}" for p, c in
                           sorted(by_provider.items(), key=lambda x: -x[1])))
    lines.append(f"*하루 추정 총액:* ~${total:.2f}")

    # Cost-driver signal: video calls are the spend; many calls per episode = re-render leak.
    vid = seedance_calls + veo_calls
    if vid:
        lines.append("")
        lines.append(f"*영상 생성 호출:* Seedance {seedance_calls} + Veo {veo_calls} = {vid}콜")
        if seedance_calls > 12:
            lines.append(f"  :rotating_light: Seedance {seedance_calls}콜 — 깨끗한 4컷×에피면 "
                         "에피당 ~4~6콜이라 재렌더/게이트-힐로 컷이 불어났을 가능성. "
                         "`SEEDANCE_MAX_CALLS`·게이트 힐 트라이 점검.")

    real = _openai_real_cost(day)
    if real:
        lines.append("")
        lines.append(f":receipt: {real}")
    else:
        lines.append("")
        lines.append("_참고: $는 우리 호출수×단가 **추정**. OpenAI 실제 청구는 admin 키"
                     "(`OPENAI_ADMIN_KEY`) 넣으면 Costs API로 실값 조회. Google/BytePlus는 "
                     "키 기반 billing API가 없어 추정만._")
    return "\n".join(lines)


def _post_workroom(text: str) -> None:
    chan = os.getenv("SLACK_WORKROOM_CHANNEL")
    tok = os.getenv("SLACK_BOT_TOKEN")
    if not chan or not tok:
        print("no SLACK_WORKROOM_CHANNEL/SLACK_BOT_TOKEN — skipping post", file=sys.stderr)
        return
    from slack_sdk import WebClient
    WebClient(token=tok).chat_postMessage(channel=chan, text=text, unfurl_links=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="KST day YYYY-MM-DD (default: yesterday + today)")
    ap.add_argument("--post", action="store_true", help="post to workroom")
    a = ap.parse_args()

    if a.date:
        days = [dt.date.fromisoformat(a.date)]
    else:
        today = dt.datetime.now(KST).date()
        days = [today - dt.timedelta(days=1), today]

    parts = [build_report(d) for d in days]
    report = "\n\n".join(parts)
    print(report)
    if a.post:
        _post_workroom(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
