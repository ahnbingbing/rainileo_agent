"""
scripts/trend_feed.py — populate the `trends` table that the arc season-planner and
the concept brainstorm already READ but nothing ever WROTE (it sat empty, so the AV
engine had no signal for timely hooks like the World Cup — PD 2026-06-24).

Two sources:
1. Curated CALENDAR (deterministic, reliable): Korean holidays + mega events with date
   windows + an ANGLE MENU. A recurring event may run several times during its window,
   but each episode must take a DIFFERENT angle (World Cup: 대결 → 응원모드 → 골세리머니
   → …). The menu + recent-card scan give the brainstorm fresh angles to rotate through.
2. LIVE discovery (Gemini google_search grounding, best-effort): current viral pet
   memes / challenges + confirmation of in-progress events. Degrades to no-op on failure.

The arc reads `trends WHERE expiry_date IS NULL OR expiry_date >= today ORDER BY
fit_score DESC`, so populating this flows straight into season_plan → directive →
brainstorm. concept_brainstorm.active_trend_hooks() injects the live rows + angle
rotation directly into the brainstorm prompt.

Usage:
    python scripts/trend_feed.py refresh            # calendar + live discovery
    python scripts/trend_feed.py refresh --no-live  # calendar only
    python scripts/trend_feed.py list               # show active trends
    python scripts/trend_feed.py add --title "..." --category meme --days 14 --fit 0.7
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"

# ── Curated calendar ──────────────────────────────────────────────────────────
# window: ("MM-DD","MM-DD") recurs every year; ("YYYY-MM-DD","YYYY-MM-DD") is a fixed
# instance (lunar holidays / specific sporting events). `lead_days` = how many days
# BEFORE the start to begin surfacing it. `angles` = distinct sub-concepts to rotate.
# `hint` = how to deliver the theme visually (props only — NO clothing/anthropomorph).
EVENTS: list[dict] = [
    {"key": "worldcup2026", "title": "2026 북중미 월드컵 (FIFA)", "category": "sports",
     "window": ("2026-06-11", "2026-07-19"), "lead_days": 3, "fit": 0.95,
     "match": ["월드컵", "축구", "world cup"],
     "angles": ["거실 결승 랴니 vs 레오 대결", "온 가족 응원모드(응원봉/머플러 prop, 옷 금지)",
                "골 세리머니 챌린지", "하프타임 간식 쟁탈전", "승부차기 긴장 클로즈업",
                "경기 보다 잠든 레오 vs 끝까지 보는 랴니"],
     "hint": "축구공 prop + GOAL!/스코어보드 오버레이로 월드컵 테마 시각화. 유니폼 금지(소품으로)."},
    {"key": "halloween", "title": "할로윈", "category": "holiday",
     "window": ("10-25", "10-31"), "lead_days": 4, "fit": 0.9,
     "match": ["할로윈", "halloween", "호박"],
     "angles": ["호박/잭오랜턴 탐험", "유령·마녀 상상(misty)", "트릭오어트릿 간식 작전",
                "검은고양이 레오 미스터리 누아르", "거실이 유령의 집으로 변신"],
     "hint": "호박/사탕/박쥐 소품 + 오버레이. 의상은 소품으로만, 입히지 말 것."},
    {"key": "chuseok2026", "title": "추석", "category": "holiday",
     "window": ("2026-09-24", "2026-09-26"), "lead_days": 5, "fit": 0.9,
     "match": ["추석", "송편", "보름달", "한가위"],
     "angles": ["송편 빚기 구경/간식 도둑", "보름달 소원 상상", "차례상 간식 지키기",
                "고향 가는 길(메모리레인)", "둥근 보름달 = 츄르 상상"],
     "hint": "송편/보름달/한복은 소품·배경으로(입히지 말 것). 보름달 오버레이 가능."},
    {"key": "christmas", "title": "크리스마스", "category": "holiday",
     "window": ("12-15", "12-25"), "lead_days": 6, "fit": 0.92,
     "match": ["크리스마스", "christmas", "산타", "트리"],
     "angles": ["트리 꾸미기 대소동", "산타 선물 기다리기 상상", "양말 속 간식 발견",
                "첫눈 눈놀이(랴니 눈 좋아함)", "거실이 겨울왕국으로 변신"],
     "hint": "트리/양말/선물상자/눈 소품 + 오버레이. 산타모자 등 의상 금지(소품)."},
    {"key": "seollal2026", "title": "설날", "category": "holiday",
     "window": ("2026-02-16", "2026-02-18"), "lead_days": 5, "fit": 0.9,
     "match": ["설날", "세배", "떡국", "새해"],
     "angles": ["세배하고 세뱃돈(간식) 받기", "떡국 먹방", "복주머니 속 간식",
                "새해 다짐 상상", "메모리레인: 작년 설 vs 올해"],
     "hint": "복주머니/떡국/한복은 소품·배경으로. 입히지 말 것."},
    {"key": "childrensday", "title": "어린이날", "category": "holiday",
     "window": ("05-03", "05-05"), "lead_days": 3, "fit": 0.8,
     "match": ["어린이날"],
     "angles": ["놀이공원 상상", "선물 받는 막내 레오", "풍선/장난감 대소동"],
     "hint": "풍선/장난감 소품."},
    {"key": "pepero", "title": "빼빼로데이", "category": "holiday",
     "window": ("11-09", "11-11"), "lead_days": 2, "fit": 0.7,
     "match": ["빼빼로"],
     "angles": ["빼빼로 모양 간식 도전", "11:11 줄맞춤 챌린지"],
     "hint": "막대 간식 소품(초콜릿은 펫에 금지 — 펫 간식 막대로 대체)."},
    {"key": "firstsnow", "title": "첫눈/겨울 눈놀이", "category": "season",
     "window": ("12-01", "01-31"), "lead_days": 0, "fit": 0.72,
     "match": ["눈", "첫눈", "눈놀이", "썰매"],
     "angles": ["눈밭 질주(랴니 눈 매니아)", "눈사람 만들기 구경", "썰매 타기"],
     "hint": "실제 눈 클립 있으면 RF, 없으면 AV 상상. 랴니=눈/얼음 좋아함(canon)."},
]


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    return con


def _resolve_window(ev: dict, today: dt.date) -> tuple[dt.date, dt.date] | None:
    """Return this year's (start, end) for the event, or None if unparseable."""
    s, e = ev["window"]
    try:
        if len(s) == 5:  # MM-DD recurring
            y = today.year
            start = dt.date(y, int(s[:2]), int(s[3:]))
            end = dt.date(y, int(e[:2]), int(e[3:]))
            if end < start:  # wraps year-end (e.g. 12-01..01-31)
                end = dt.date(y + 1, int(e[:2]), int(e[3:]))
            # if we're already past this year's window, roll to next year
            if today > end:
                start = start.replace(year=y + 1)
                end = end.replace(year=end.year + 1)
            return start, end
        else:  # fixed YYYY-MM-DD instance
            return dt.date.fromisoformat(s), dt.date.fromisoformat(e)
    except Exception:
        return None


def _recent_used_angles(con: sqlite3.Connection, ev: dict, today: dt.date) -> list[str]:
    """Recent card themes (last 30d) matching this event — so the brainstorm rotates
    to a NEW angle instead of repeating the one we just did."""
    since = (today - dt.timedelta(days=30)).isoformat()
    likes = " OR ".join(["theme LIKE ?"] * len(ev["match"]))
    rows = con.execute(
        f"SELECT theme FROM cards WHERE date >= ? AND ({likes})",
        [since, *[f"%{m}%" for m in ev["match"]]]).fetchall()
    return [r[0] for r in rows if r[0]]


_ALLOWED_CAT = {"format", "challenge", "meme", "audio", "event", "holiday", "sports", "season"}


def _upsert(con: sqlite3.Connection, trend_id: str, source: str, category: str,
            title: str, fit: float, expiry: str, notes: dict) -> None:
    if category not in _ALLOWED_CAT:  # LLM discovery may invent labels (festival/viral/…)
        category = "event"
    con.execute(
        "INSERT INTO trends (trend_id, source, category, title, fit_score, expiry_date, "
        "discovered_at, notes) VALUES (?,?,?,?,?,?,datetime('now'),?) "
        "ON CONFLICT(trend_id) DO UPDATE SET fit_score=excluded.fit_score, "
        "expiry_date=excluded.expiry_date, title=excluded.title, notes=excluded.notes",
        (trend_id, source, category, title, fit, expiry,
         json.dumps(notes, ensure_ascii=False)))


def refresh_calendar(con: sqlite3.Connection, today: dt.date) -> int:
    """Upsert every event whose surfacing window (lead_days before start .. end)
    contains today. Returns how many are active."""
    n = 0
    for ev in EVENTS:
        win = _resolve_window(ev, today)
        if not win:
            continue
        start, end = win
        surface_from = start - dt.timedelta(days=ev.get("lead_days", 3))
        if not (surface_from <= today <= end):
            continue
        used = _recent_used_angles(con, ev, today)
        remaining = [a for a in ev["angles"]
                     if not any(a[:6] in u or u in a for u in used)] or ev["angles"]
        # bump fit a touch once the event is actually live (not just upcoming)
        fit = ev["fit"] + (0.03 if start <= today <= end else 0.0)
        notes = {"angles_remaining": remaining, "recent_used": used,
                 "hint": ev["hint"], "window": [start.isoformat(), end.isoformat()],
                 "live": start <= today <= end}
        _upsert(con, f"cal_{ev['key']}_{start.year}", "calendar", ev["category"],
                ev["title"], round(fit, 3), end.isoformat(), notes)
        n += 1
    con.commit()
    return n


def discover_live(con: sqlite3.Connection, today: dt.date) -> int:
    """Best-effort live-trend discovery via Gemini google_search grounding. Finds
    current viral pet memes/challenges a Korean pet channel could ride. Degrades to
    a no-op (returns 0) on any failure — the calendar is the reliable backbone."""
    try:
        from google import genai
        from google.genai import types as t
    except Exception:
        return 0
    prompt = (
        f"오늘은 {today.isoformat()}. 한국의 반려동물(강아지+고양이) 유튜브 숏츠 채널이 "
        "지금 '따라 할 수 있는' 시의성 소재만 골라줘: (1) 요즘 유행하는 펫/릴스 밈·챌린지, "
        "(2) 진행 중이거나 임박한 큰 시즌 이벤트(스포츠/축제/기념일). 각 항목은 채널이 영상으로 "
        "재현 가능한 구체 소재여야 한다(추상 트렌드 X). 최대 6개. "
        "JSON 배열만: [{\"title\":\"소재명\",\"category\":\"meme|challenge|event\","
        "\"why\":\"펫채널이 어떻게 재현할지 한 줄\",\"fit\":0.0~1.0,\"expires_in_days\":정수}]"
    )
    items = []
    for _attempt in range(3):  # grounding occasionally returns empty/non-JSON — retry
        try:
            c = genai.Client()
            r = c.models.generate_content(
                model="gemini-flash-latest", contents=prompt,
                config=t.GenerateContentConfig(tools=[t.Tool(google_search=t.GoogleSearch())]))
            txt = (r.text or "").strip()
            i, j = txt.find("["), txt.rfind("]")
            items = json.loads(txt[i:j + 1]) if i >= 0 and j > i else []
            if items:
                break
        except Exception:
            items = []
    n = 0
    for it in items if isinstance(items, list) else []:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        days = int(it.get("expires_in_days", 14) or 14)
        expiry = (today + dt.timedelta(days=max(3, min(days, 45)))).isoformat()
        tid = "disc_" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
        _upsert(con, tid, "discovery", it.get("category", "meme"), title,
                float(it.get("fit", 0.6) or 0.6), expiry,
                {"why": it.get("why", ""), "discovered": today.isoformat()})
        n += 1
    con.commit()
    return n


def refresh(con: sqlite3.Connection, today: dt.date, live: bool = True) -> dict:
    cal = refresh_calendar(con, today)
    disc = discover_live(con, today) if live else 0
    return {"calendar_active": cal, "discovered": disc}


def _main() -> None:
    ap = argparse.ArgumentParser()
    datep = argparse.ArgumentParser(add_help=False)
    datep.add_argument("--date", default=None, help="override today (YYYY-MM-DD)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", parents=[datep]); r.add_argument("--no-live", action="store_true")
    sub.add_parser("list", parents=[datep])
    a = sub.add_parser("add", parents=[datep])
    a.add_argument("--title", required=True); a.add_argument("--category", default="meme")
    a.add_argument("--days", type=int, default=14); a.add_argument("--fit", type=float, default=0.7)
    a.add_argument("--note", default="")
    args = ap.parse_args()
    con = _conn()
    today = dt.date.fromisoformat(args.date) if getattr(args, "date", None) else dt.date.today()
    if args.cmd == "refresh":
        print(json.dumps(refresh(con, today, live=not args.no_live), ensure_ascii=False))
    elif args.cmd == "list":
        for row in con.execute(
                "SELECT category, title, fit_score, expiry_date, source FROM trends "
                "WHERE expiry_date IS NULL OR expiry_date >= ? ORDER BY fit_score DESC",
                (today.isoformat(),)):
            print(f"  [{row[4]}/{row[0]}] {row[2]}  {row[1]}  (만료 {row[3]})")
    elif args.cmd == "add":
        tid = "manual_" + hashlib.sha1(args.title.encode()).hexdigest()[:10]
        _upsert(con, tid, "manual", args.category, args.title, args.fit,
                (today + dt.timedelta(days=args.days)).isoformat(), {"why": args.note})
        con.commit(); print(f"added: {args.title}")
    con.close()


if __name__ == "__main__":
    _main()
