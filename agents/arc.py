"""
agents/arc.py — Episode ARC: a unified, LLM-driven story spine across BOTH
lanes (ai_vtuber + real_footage). PD 2026-06-06.

The daily pipeline used to emit standalone gags with no memory. PD wants the
channel to BUILD a cumulative story (EP01 = the two meet, then spotlights, then
ongoing threads) — and it must be **LLM-driven and span both lanes as ONE
series**. Two layers:

1. LEDGER (`episode_arc`) — what already aired (LLM summary + threads), av + rf
   together.
2. SEASON PLAN (`arc_plan`) — a rolling ~1-MONTH rough roadmap the showrunner
   LLM drafts AHEAD of time and FLEXIBLY revises as episodes air. Not rigid:
   beats can shift. Both lanes draw from the same plan.

Per episode: `next_directive()` (showrunner LLM) reads the plan + the ledger and
emits a short directive for THIS episode — what it should contribute — which is
injected into that lane's writer. After render, `record_episode()` appends to
the ledger so the next directive sees it.

FUTURE (PD 2026-06-06): once the YouTube upload pipeline exists, the showrunner
should also weigh real engagement (which threads/characters performed) when
revising the plan. Hook: pass a `performance` summary into `_refresh_plan`.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

log = logging.getLogger("arc")


def enabled() -> bool:
    """PD 2026-06-06: arc is a POST-UPLOAD concern. Built and ready, but gated
    OFF by default so it adds no LLM cost/latency to current quality work.
    Flip ARC_ENABLED=1 once the YouTube upload pipeline (and its feedback) lands."""
    return os.getenv("ARC_ENABLED", "0") == "1"

# Regenerate the rolling month plan when it's older than this (days) or empty.
PLAN_MAX_AGE_DAYS = 7


def ensure_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS episode_arc (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id      TEXT,
            date         TEXT,
            render_style TEXT,
            title        TEXT,
            arc_summary  TEXT,
            threads      TEXT,   -- JSON {introduced:[],advanced:[],paid_off:[]}
            created_at   TEXT DEFAULT (datetime('now'))
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS arc_plan (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_json  TEXT,     -- LLM ~1-month rolling plan (beats, arcs, threads)
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    con.commit()


# ── Ledger ────────────────────────────────────────────────────────────
def series_so_far(con: sqlite3.Connection, n: int = 12) -> str:
    """Recent ledger (both lanes) as a compact Korean briefing + open threads."""
    if not enabled():
        return ""
    try:
        ensure_tables(con)
        rows = con.execute(
            "SELECT date, render_style, title, arc_summary, threads "
            "FROM episode_arc ORDER BY id DESC LIMIT ?", (n,),
        ).fetchall()
    except Exception as e:
        log.warning("series_so_far failed: %s", e)
        return ""
    if not rows:
        return "## 지금까지의 시리즈: (아직 없음 — 이번이 초반 회차다)"
    lines = ["## 지금까지의 시리즈 (오래된→최신, av+rf 통합):"]
    open_threads: list[str] = []
    for r in reversed(rows):
        date, style, title, summary, threads = r
        lines.append(f"- [{date}] ({style}) 「{title}」 — {summary or ''}")
        try:
            t = json.loads(threads or "{}")
            open_threads += (t.get("introduced") or []) + (t.get("advanced") or [])
            for th in (t.get("paid_off") or []):
                while th in open_threads:
                    open_threads.remove(th)
        except Exception:
            pass
    if open_threads:
        lines.append("열린 떡밥/진행 중: " + ", ".join(dict.fromkeys(open_threads)))
    return "\n".join(lines)


def record_episode(con: sqlite3.Connection, *, card_id: str, date: str,
                   render_style: str, title: str, concept: dict[str, Any]) -> None:
    """LLM-summarize this episode's contribution to the (shared) series; store."""
    if not enabled():
        return
    try:
        ensure_tables(con)
        prior = series_so_far(con, n=12)
        caps = [sc["ko"] for c in (concept.get("cuts") or [])
                for sc in (c.get("captions") or []) if sc.get("ko")]
        system = (
            "너는 'Ryani & Leo' 채널 showrunner다. 방금 나간 에피소드가 "
            "시리즈(ai_vtuber+real_footage 통합)에 무엇을 기여했는지 요약하라. JSON만:\n"
            '{"arc_summary":"1-2문장","threads":{"introduced":[],"advanced":[],"paid_off":[]}}'
        )
        user = json.dumps({
            "this_episode": {"title": title, "render_style": render_style,
                             "narrative_oneliner": concept.get("narrative_oneliner") or "",
                             "captions": caps[:20]},
            "series_so_far": prior,
        }, ensure_ascii=False)
        from agents.llm_cascade import call_text_cascade
        txt = _strip_fence(call_text_cascade(system, user, max_tokens=600).strip())
        data = json.loads(txt)
        arc_summary = (data.get("arc_summary") or "")[:600]
        threads = json.dumps(data.get("threads") or {}, ensure_ascii=False)
    except Exception as e:
        log.warning("arc summary failed (%s) — minimal entry", e)
        arc_summary = (concept.get("narrative_oneliner") or title or "")[:600]
        threads = "{}"
    try:
        con.execute(
            "INSERT INTO episode_arc (card_id,date,render_style,title,arc_summary,threads)"
            " VALUES (?,?,?,?,?,?)",
            (card_id, date, render_style, title, arc_summary, threads),
        )
        con.commit()
        log.info("arc recorded [%s] %s — %s", render_style, title, arc_summary[:80])
    except Exception as e:
        log.warning("arc insert failed: %s", e)


# ── Season plan (rolling ~1 month) ────────────────────────────────────
def _get_plan_row(con: sqlite3.Connection):
    ensure_tables(con)
    return con.execute(
        "SELECT plan_json, updated_at FROM arc_plan ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _plan_stale(updated_at: str | None, today: str) -> bool:
    if not updated_at:
        return True
    try:
        import datetime as dt
        d0 = dt.date.fromisoformat(str(updated_at)[:10])
        d1 = dt.date.fromisoformat(today[:10])
        return (d1 - d0).days >= PLAN_MAX_AGE_DAYS
    except Exception:
        return True


def _season_context(con: sqlite3.Connection, today: str) -> dict:
    """Season + upcoming holidays/milestones + live trends for the planner.
    PD 2026-06-06: the season plan must reflect 계절/공휴일/트렌드 — seasonal
    fruit/food/place, summer = Ryani swimming, holiday tie-ins, etc."""
    import datetime as dt
    out: dict = {}
    try:
        d = dt.date.fromisoformat(today[:10])
        m = d.month
        season = ("겨울" if m in (12, 1, 2) else "봄" if m in (3, 4, 5)
                  else "여름" if m in (6, 7, 8) else "가을")
        out["season"] = f"{season} ({m}월)"
        out["season_hint"] = (
            "계절에 맞는 과일/음식/장소/활동을 적극 엮어라. 예) 여름=수박·물놀이·"
            "랴니 수영·그늘·아이스팩, 가을=단풍·고구마·산책, 겨울=눈·군고구마·"
            "이불·난로, 봄=벚꽃·소풍·새싹. (단 자산에 있을 법한 것 위주, 무리한 "
            "촬영 강요 금지 — ai_vtuber는 상상 가능, real_footage는 실제 클립 필요.)"
        )
        # Upcoming milestones within ~5 weeks (month/day recurrence table).
        ups = []
        for off in range(0, 36):
            dd = d + dt.timedelta(days=off)
            for r in con.execute(
                "SELECT tag, subjects_csv, notes FROM milestones WHERE month=? AND day=?",
                (dd.month, dd.day),
            ).fetchall():
                ups.append({"date": dd.isoformat(), "tag": r[0],
                            "subjects": r[1], "notes": (r[2] or "")[:80]})
        out["upcoming_milestones"] = ups
    except Exception as e:
        log.warning("season ctx failed: %s", e)
    try:
        rows = con.execute(
            "SELECT category, title, notes FROM trends "
            "WHERE expiry_date IS NULL OR expiry_date >= ? "
            "ORDER BY fit_score DESC, discovered_at DESC LIMIT 8", (today[:10],),
        ).fetchall()
        out["trends"] = [{"category": r[0], "title": r[1], "notes": (r[2] or "")[:80]}
                         for r in rows]
    except Exception:
        out["trends"] = []
    return out


def _asset_inventory(con: sqlite3.Connection) -> dict:
    """PD 2026-06-07: real_footage can ONLY do topics that actually exist in the
    clip archive (e.g. there is NO watermelon footage — so no rf 수박 episode).
    Summarize what's really there so the planner grounds rf beats in real
    clips. (ai_vtuber can still imagine anything.)"""
    import json as _json, collections
    out: dict = {"video_count": 0, "activities": {}, "locations": {}, "props": {}}
    try:
        rows = con.execute(
            "SELECT activity, location_type, notes FROM assets "
            "WHERE kind='video' AND vlm_analyzed_at IS NOT NULL"
        ).fetchall()
        out["video_count"] = len(rows)
        acts, locs, props = collections.Counter(), collections.Counter(), collections.Counter()
        for r in rows:
            if r[0]:
                acts[r[0]] += 1
            if r[1]:
                locs[r[1]] += 1
            try:
                for p in (_json.loads(r[2] or "{}").get("contextual_props") or []):
                    props[p] += 1
            except Exception:
                pass
        out["activities"] = dict(acts.most_common(15))
        out["locations"] = dict(locs.most_common(10))
        out["props"] = dict(props.most_common(20))
    except Exception as e:
        log.warning("asset inventory failed: %s", e)
    return out


# Authoritative character facts for the showrunner — personality / ability /
# age that planning needs (visual markings live in character_sheets.md, the full
# source). PD 2026-06-07: the planner was INVENTING traits (e.g. "랴니 물 공포
# 극복") because no real facts were fed. Keep this in sync with character_sheets.md.
CHARACTER_FACTS = (
    "## 캐릭터 사실 (권위 — 여기 없는 성격/능력/공포는 발명 금지)\n"
    "- **레오(레오)**: 8개월 **수컷** 고양이(주황 태비). 2025-11-15 떠돌이로 구조됨 → "
    "랴니를 엄마로 여김('랴니엄마'는 레오 POV 호칭). 장난꾸러기·사냥꾼·매복 전문. "
    "세차를 무서워함. 고양이라 물을 피하고 물가에서 구경하는 쪽.\n"
    "- **랴니(랴니)**: 11살 **암컷(중성화)** 프렌치불독, 꼬리 없음. 의젓한 누나/엄마, "
    "차분·현명. ★ **물을 엄청 좋아하는 '물 매니아'**: 물만 보면 흥분해서 짖고, 특히 "
    "**고무호스/분수** 물을 보면 격하게 흥분해 **분수에 뛰어들려고 난리**. **수영도 아주 잘함"
    "('펠프스급')**. 겨울엔 **눈을 좋아하고 얼음 썰매를 탄다**. (거짓 금지: '랴니 물 공포/물 "
    "무서워함'은 완전히 틀림 — 정반대. 단 2016 아기 시절엔 잠깐 무서워했음 → 과거 회상에서만.) "
    "세차도 안 무서워함(레오와 대비).\n"
    "- **여름 물놀이/분수/수영 + 겨울 눈/얼음썰매 컨셉의 주인공 = 랴니.** 레오는 물가 구경/마른 쪽.\n"
    "- ⚠️ 위 목록에 없는 공포·능력·트레잇을 새로 지어내지 마라. 나이도 정확히(레오 8개월/"
    "랴니 11살) — 뒤바꾸지 마라.\n"
)


def _learned_facts(con: sqlite3.Connection) -> str:
    """Layer ③ PD-confirmed facts + Layer ① VLM observed profile, injected
    alongside CHARACTER_FACTS. Order = authority: PD facts first, observed last."""
    out = ""
    try:
        from agents import knowledge
        out += knowledge.facts_block(con)
    except Exception:
        pass
    try:
        from agents import pet_profile
        out += pet_profile.profile_block(con)
    except Exception:
        pass
    return out


def _refresh_plan(con: sqlite3.Connection, today: str) -> str:
    """(Re)draft the rolling ~1-month plan from ledger + season/holiday/trend
    + REAL clip inventory (so rf beats are grounded in footage that exists)."""
    prior = series_so_far(con, n=20)
    existing = _get_plan_row(con)
    existing_plan = existing[0] if existing else ""
    season = _season_context(con, today)
    inventory = _asset_inventory(con)
    system = (
        "너는 'Ryani & Leo' 채널 showrunner다. 앞으로 약 한 달(대략 4주)의 "
        "느슨한 시즌 플랜을 한국어로 짜라. ai_vtuber와 real_footage를 하나의 "
        "시리즈로 아우른다(둘이 같은 세계관/스토리를 공유). 규칙:\n"
        "- 캐릭터 소개→관계→성장→떡밥 회수처럼 큰 호를 그려라. 단 날짜별로 "
        "  못박지 말고 '주차/단계' 단위의 느슨한 흐름으로 (유연하게 바뀔 수 있음).\n"
        "- ★ 계절/공휴일/트렌드를 반드시 반영하라 (아래 season_context). 계절 "
        "  과일·음식·장소·활동(여름=수박·물놀이·수영 등; 랴니는 수영을 잘하니 여름 "
        "  물놀이의 주인공감), 다가오는 기념일/공휴일 타이인, 트렌드를 시즌 비트에 녹여라.\n"
        "- ★★ 캐릭터의 성격·능력·공포는 아래 CHARACTER_FACTS에 적힌 것만 써라. "
        "  없는 트레잇(없는 공포/능력)을 지어내지 마라. (예: '랴니 물 공포 극복'은 거짓 — "
        "  랴니는 수영을 잘한다.)\n"
        + CHARACTER_FACTS + _learned_facts(con) +
        "- ★ 한 달에 한 번은 '랴니&레오 재소개' 회차를 넣어라(정기 리프레시). "
        "  과거 부처님오신날 회차처럼 ai_vtuber로 AI 배경을 전환하며 보여주는 "
        "  연출이 좋다.\n"
        "- ★ ai_vtuber 레인은 상상/판타지 컨셉을 적극 활용하라 — 가본 적 없는 "
        "  곳(서핑, 하와이 여행, 우주 등)에 둘을 배치하는 컨셉, AI 배경전환 연출. "
        "  real_footage 레인은 실제 클립 기반이라 판타지 금지(현실 일상만). "
        "  즉 판타지/여행/배경전환 비트는 ai_vtuber에 배정.\n"
        "- ★★ real_footage 비트는 반드시 아래 asset_inventory(실제 보유 클립)에 "
        "  있는 활동/장소/소품에서만 뽑아라. 인벤토리에 없는 소재(예: 수박 클립이 "
        "  없으면 rf 수박 회차 금지)는 rf에 쓰지 마라 — 만들 수 없다. 계절 소재라도 "
        "  실제 footage가 없으면 rf 불가(그건 ai_vtuber 상상으로만). 인벤토리에 "
        "  풍부한 것(예: 산책/외출/카페/먹방/장난감/낮잠)을 계절 톤으로 엮어라.\n"
        "- 이미 한 것(series_so_far)은 반복하지 말고 그 위에 쌓아라.\n"
        "- 기존 플랜이 있으면 실제 나간 회차에 맞춰 유연하게 갱신(틀어진 건 조정).\n"
        "출력 JSON만: {\"arc_theme\":\"이번 시즌 큰 줄기 한 줄\","
        "\"phases\":[{\"phase\":\"1주차 등\",\"goal\":\"...\",\"beats\":[\"...\"],"
        "\"seasonal\":\"이 단계의 계절/공휴일/트렌드 요소\"}],"
        "\"open_threads\":[\"진행/회수할 떡밥\"],\"notes\":\"유연성 메모\"}"
    )
    user = json.dumps({"today": today, "season_context": season,
                       "asset_inventory": inventory,
                       "series_so_far": prior, "existing_plan": existing_plan},
                      ensure_ascii=False)
    from agents.llm_cascade import call_text_cascade
    txt = _strip_fence(call_text_cascade(system, user, max_tokens=1500).strip())
    json.loads(txt)  # validate
    con.execute("INSERT INTO arc_plan (plan_json) VALUES (?)", (txt,))
    con.commit()
    log.info("arc season plan refreshed")
    return txt


def get_or_refresh_plan(con: sqlite3.Connection, today: str) -> str:
    try:
        row = _get_plan_row(con)
        if row and not _plan_stale(row[1], today):
            return row[0]
        return _refresh_plan(con, today)
    except Exception as e:
        log.warning("get_or_refresh_plan failed: %s", e)
        row = _get_plan_row(con)
        return row[0] if row else ""


# Launch week intro schedule (PD 2026-06-07): the first days are DETERMINISTIC —
# both-together intro, then individual character intros with past⇄present
# memory-lane — so a new audience meets the two leads. Set LAUNCH_START_DATE
# (YYYY-MM-DD) = Day 1; offsets 0/1/2 below. Empty env → no overlay (plan-driven).
def _launch_intro_directive(today: str, render_style: str) -> str | None:
    import datetime as dt
    start = os.getenv("LAUNCH_START_DATE", "").strip()
    if not start:
        return None
    try:
        d0 = dt.date.fromisoformat(start)
        dnow = dt.date.fromisoformat(today[:10])
        offset = (dnow - d0).days
    except Exception:
        return None
    rf = render_style == "real_footage"
    inter = (" 런칭=소개 회차이니 둘이 뽀뽀·핥기·장난·나란히 붙기 등 **서로 상호작용**하는 "
             "순간을 우선 골라 관계를 보여줘라(솔로 컷 나열 금지). 캡션 톤은 **vlog 캐주얼 우대**"
             "(1인칭·친근, 동물농장 나레이터체보다), 단 단순한 묘사 금지 — 위트·속마음으로 풍부하게.")
    if offset == 0:  # Day 1 — 둘 함께 채널 첫인사
        return ("[런칭 Day1 — 둘 함께 첫인사] 채널 첫 등장. 랴니(11살 의젓한 누나/엄마)와 "
                "레오(8개월 장난꾸러기 아들) **둘을 함께** 소개하는 따뜻한 첫인사 + 여름 시즌 "
                "가벼운 훅." + inter + ("" if rf else " 상상 연출로 둘의 등장을 임팩트 있게."))
    if offset == 1:  # Day 2 — 레오 집중
        return ("[런칭 Day2 — 레오 집중 소개] 오늘은 **레오** 중심. 장난꾸러기·사냥꾼·매복 전문, "
                "2025-11-15 떠돌이로 구조돼 랴니를 엄마로 여기는 origin. " +
                ("**과거⇄현재 메모리레인**: 아기/구조 직후 레오 아카이브 클립(archive_videos) → "
                 "지금 8개월. 시점 명시 필수('구조된 날', '지금은'). 과거를 꺼내면 현재로 의미 연결."
                 if rf else "레오의 성격을 상상 연출로. 랴니는 보조.") + inter)
    if offset == 2:  # Day 3 — 랴니 집중
        return ("[런칭 Day3 — 랴니 집중 소개] 오늘은 **랴니** 중심. 의젓·현명한 누나, "
                "**수영을 아주 잘하는(펠프스급) 면모**(물 공포 아님). " +
                ("**과거⇄현재 메모리레인**: 과거 랴니 아카이브(archive_videos) → 지금 11살. "
                 "시점 명시 필수. 과거를 꺼내면 현재로 의미 연결. 물놀이 클립이 **실제 있을 때만** "
                 "수영 강조(없으면 물 얘기 억지로 넣지 마라)."
                 if rf else "랴니의 의젓함·수영 실력을 상상 연출로. 레오는 보조.") + inter)
    return None


def next_directive(con: sqlite3.Connection, *, today: str, render_style: str) -> str:
    """Showrunner directive for THIS episode: what it should contribute to the
    shared arc, given the rolling plan + what already aired. Injected into the
    lane's writer. Empty string on failure (writer just falls back to grounded)."""
    if not enabled():
        return ""
    # Launch-week intro overlay takes precedence (deterministic — no LLM
    # re-hallucination during the critical first impressions).
    overlay = _launch_intro_directive(today, render_style)
    if overlay:
        return overlay
    try:
        plan = get_or_refresh_plan(con, today)
        prior = series_so_far(con, n=12)
        system = (
            "너는 'Ryani & Leo' showrunner다. 아래 '시즌 플랜'과 '지금까지의 "
            f"시리즈'를 보고, 오늘의 {render_style} 에피소드가 시리즈상 무엇을 "
            "해야 할지 1-3문장 한국어 디렉티브로 줘라. 구체적 컨셉을 못박지 말고 "
            "'방향'만(예: 이번엔 레오의 호기심 떡밥을 한 단계 진전 / 랴니와의 관계 "
            "에 작은 변화). 자산에 없는 걸 강요하지 말고 유연하게. "
            "캐릭터 성격·능력·공포는 아래 CHARACTER_FACTS에 있는 것만(없는 트레잇 발명 금지). "
            "디렉티브 텍스트만 출력.\n" + CHARACTER_FACTS + _learned_facts(con)
        )
        user = json.dumps({"today": today, "render_style": render_style,
                           "season_plan": plan, "series_so_far": prior},
                          ensure_ascii=False)
        from agents.llm_cascade import call_text_cascade
        d = call_text_cascade(system, user, max_tokens=400).strip()
        return _strip_fence(d)
    except Exception as e:
        log.warning("next_directive failed: %s", e)
        return ""


def _strip_fence(txt: str) -> str:
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[1] if "\n" in txt else txt[3:]
        if txt.rstrip().endswith("```"):
            txt = txt.rstrip()[:-3]
    return txt.strip()
