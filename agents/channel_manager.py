"""agents/channel_manager.py — Channel Manager agent.

Giri reviews ONE episode through the audience's eyes; the Channel Manager runs
the WHOLE channel with data. It owns three things the pipeline was missing:

  Phase 1 (here): PACKAGING — generate a hook YouTube title + SEO description +
    concept-specific hashtags per episode, replacing the static "스토리 제목 +
    고정 3태그" defaults. The 3 packaging tones are EXPERIMENT ARMS rotated per
    episode (like the launch-month RF/AV A/B) and learned until one stabilizes.
  Phase 2: close the bandit loop (choose_lane/timeslot actually steer launch).
  Phase 3: live-channel-state recommendations ("어디 더 넣을지").

Design: notes/channel_manager_design.md. Reuses agents/llm_cascade for the LLM
and youtube/upload boundary unchanged — we only write richer values into
cards.payload_json.draft.{title,description,hashtags}.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

from agents.llm_cascade import call_text_cascade

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "agent.db"
_PROMPT = ROOT / "agents" / "prompts" / "channel_manager_packaging.md"

# Packaging experiment arms. Rotated per-episode (Phase 1 round-robin), later
# Thompson-sampled (Phase 2). When P(best)≥θ & n≥N the dimension "stabilizes" and
# we pin the winner — same philosophy as RF/AV launch-month A/B.
PACKAGING_ARMS = ("hook_search", "hook_strong", "search_strong")


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _ensure_packaging_log(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS packaging_log ("
        " card_id TEXT, arm TEXT, title TEXT, description TEXT,"
        " hashtags_json TEXT, lane TEXT, generated_at TEXT DEFAULT (datetime('now')))"
    )
    con.commit()


def stabilized_arm() -> str | None:
    """Once a packaging arm clearly wins (bandit P(best)≥θ & enough n), return it so
    rotation stops and we pin the winner. Until then None → keep round-robin testing
    all 3 arms (forced even A/B beats Thompson here — guarantees every arm is tried)."""
    try:
        from agents import bandit
        arm = bandit.stabilized("packaging")
        return arm if arm in PACKAGING_ARMS else None
    except Exception:
        return None


def choose_packaging(card_id: str, *, arm: str | None = None) -> str:
    """Pick a packaging arm for this card. Explicit arm wins; else a stabilized
    winner; else deterministic round-robin by card_id hash (stable per episode,
    spread across episodes) so the 3 tones rotate evenly during the experiment."""
    if arm in PACKAGING_ARMS:
        return arm
    fixed = stabilized_arm()
    if fixed in PACKAGING_ARMS:
        return fixed
    import hashlib
    h = int(hashlib.sha1((card_id or "default").encode("utf-8")).hexdigest()[:8], 16)
    return PACKAGING_ARMS[h % len(PACKAGING_ARMS)]


def _salvage_json(txt: str) -> dict:
    txt = re.sub(r"^```(?:json)?\s*", "", txt.strip())
    txt = re.sub(r"\s*```$", "", txt)
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def actual_captions_for_video(video_path) -> list[str]:
    """The FINAL on-screen captions of a rendered episode (VLM-rewritten post-render to match
    the real footage) — the ground truth of what the video actually shows. Read from the render
    workdir's captions.json, matched by the YYYYMMDD_HHMMSS stamp in the video filename. Empty on
    any miss. PD 2026-07-16: title/description kept drifting because they came from the CONCEPT,
    which the render diverges from (a '주방 대작전' concept rendered a nap); the burned captions are
    grounded, so package from THEM."""
    import re as _re, glob as _glob
    try:
        m = _re.search(r"_(\d{8}_\d{6})", Path(video_path).stem)
        if not m:
            return []
        for wd in _glob.glob(str(ROOT / "data" / "tmp" / f"cameraman_*_{m.group(1)}")):
            cap = json.loads((Path(wd) / "captions.json").read_text(encoding="utf-8"))
            out = []
            for tag, e in cap.items():
                if tag.startswith("_") or not isinstance(e, dict):
                    continue
                for s in (e.get("scenes") or []):
                    if isinstance(s, dict) and (s.get("ko") or "").strip():
                        out.append(s["ko"].strip())
            if out:
                return out
    except Exception:
        pass
    return []


def _concept_brief(concept: dict) -> str:
    """Compact, packaging-relevant view of the concept for the LLM. When `actual_captions`
    (the final burned, VLM-grounded on-screen text) is present, it is the AUTHORITATIVE content —
    the packaging prompt titles from it, not the concept, so title/description match the video."""
    actual = [c for c in (concept.get("actual_captions") or []) if isinstance(c, str) and c.strip()]
    caps = []
    for c in (concept.get("cuts") or [])[:8]:
        for s in (c.get("captions") or c.get("scenes") or []):
            ko = s.get("ko") if isinstance(s, dict) else None
            if ko:
                caps.append(ko)
    brief = {
        "concept_title_hint": concept.get("title"),
        "narrative_oneliner": concept.get("narrative_oneliner") or concept.get("logline"),
        "lane": concept.get("render_style") or concept.get("lane"),
        "subjects": concept.get("subjects"),
        "tone": concept.get("tone") or concept.get("tone_style"),
        # actual_captions = ground truth of what's on screen; captions = concept-stage (may drift)
        "actual_on_screen_captions": actual[:16] if actual else None,
        "captions": (actual or caps)[:14],
        # era of the footage (season + pet age) — title/desc must respect it, never invent a season
        "content_era": concept.get("content_era") or None,
    }
    return json.dumps(brief, ensure_ascii=False)


def make_packaging(concept: dict, *, card_id: str, arm: str | None = None,
                   log_to_db: bool = True) -> dict:
    """Generate {title, description, hashtags, arm} for an episode. `arm` defaults
    to the rotation pick. Pure metadata — does NOT upload; the caller writes the
    result into cards.payload_json.draft and the existing upload path uses it."""
    arm = choose_packaging(card_id, arm=arm)
    system = _PROMPT.read_text(encoding="utf-8")
    user = (f"arm: {arm}\n컨셉:\n{_concept_brief(concept)}\n\n"
            f"위 컨셉을 arm='{arm}' 강조로 패키징해서 JSON 객체만 출력.")
    txt = call_text_cascade(system, user, max_tokens=1200).strip()
    out = _salvage_json(txt)
    # Normalize
    title = (out.get("title") or concept.get("title") or "Ryani & Leo").strip()
    desc = (out.get("description") or "").strip()
    desc = desc.replace("\\n", "\n")  # some LLM hops double-escape newlines
    tags = out.get("hashtags") or []
    tags = [str(t).strip() for t in tags if str(t).strip()]
    result = {"title": title[:100], "description": desc[:5000],
              "hashtags": tags[:30], "arm": arm}
    if log_to_db:
        try:
            con = _db()
            _ensure_packaging_log(con)
            con.execute(
                "INSERT INTO packaging_log (card_id, arm, title, description, "
                "hashtags_json, lane) VALUES (?,?,?,?,?,?)",
                (card_id, arm, result["title"], result["description"],
                 json.dumps(result["hashtags"], ensure_ascii=False),
                 concept.get("render_style") or concept.get("lane")))
            con.commit()
            con.close()
        except Exception as e:
            log.warning("packaging_log insert failed: %s", e)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — channel-state recommender ("어디 더 넣을지")
# Composes bandit posteriors + video_performance + LIVE YouTube schedule state into
# actionable guidance. Reads the channel via the API (NOT the stale DB) per the
# verify-via-API rule, and degrades gracefully when offline.
# ─────────────────────────────────────────────────────────────────────────────

_LANE_KO = {"ai_vtuber": "AV", "real_footage": "RF"}


def _live_upcoming() -> list[dict]:
    """Scheduled-but-unpublished uploads from the live channel (private + future
    publishAt), newest publish first. [] if the API is unavailable."""
    try:
        from youtube.upload import get_youtube
        yt = get_youtube()
        r = yt.search().list(part="id", forMine=True, type="video",
                             order="date", maxResults=25).execute()
        ids = [i["id"]["videoId"] for i in r.get("items", []) if i.get("id", {}).get("videoId")]
        if not ids:
            return []
        d = yt.videos().list(part="snippet,status", id=",".join(ids)).execute()
        out = []
        for v in d.get("items", []):
            pa = v["status"].get("publishAt")
            if pa and v["status"].get("privacyStatus") == "private":
                out.append({"video_id": v["id"], "publish_at": pa,
                            "title": v["snippet"]["title"]})
        return sorted(out, key=lambda x: x["publish_at"])
    except Exception as e:
        log.warning("live upcoming fetch failed: %s", e)
        return []


def recommend(con: sqlite3.Connection | None = None) -> dict:
    """What to push / where, from data. Returns a structured dict; recommend_text
    renders it for Slack. Never raises — partial data still yields guidance."""
    from agents import bandit
    own = con is None
    con = con or _db()
    try:
        a = bandit.analyze(con)
    except Exception as e:
        log.warning("recommend: bandit.analyze failed: %s", e)
        a = {"n_total": 0, "levels": {}}
    levels = a.get("levels", {})

    def _leader(level: str):
        lvl = {k: v for k, v in levels.get(level, {}).items() if k not in ("?", None)}
        if not lvl:
            return None
        k, v = max(lvl.items(), key=lambda kv: kv[1].get("p_best", 0.0))
        return {"key": k, "p_best": v.get("p_best", 0.0), "n": v.get("n", 0),
                "reward": v.get("mean_reward"),
                "stabilized": bandit.stabilized(level, con) == k}

    def _thin(level: str, min_n: int = 4):
        return [k for k, v in levels.get(level, {}).items()
                if k not in ("?", None) and v.get("n", 0) < min_n]

    rec = {
        "n_total": a.get("n_total", 0),
        "lane": _leader("lane"),
        "timeslot": _leader("timeslot"),
        "packaging": _leader("packaging"),
        "thin_packaging": _thin("packaging"),
        "thin_timeslot": _thin("timeslot"),
        "upcoming": _live_upcoming(),
        "actions": [],
    }

    # synthesize concrete actions
    acts = rec["actions"]
    lane = rec["lane"]
    if lane:
        if lane["stabilized"]:
            acts.append(f"레인: {_LANE_KO.get(lane['key'], lane['key'])} 우위 확정 "
                        f"(P={lane['p_best']}, n={lane['n']}) → 할당이 자동으로 그쪽으로 기웁니다.")
        elif rec["n_total"] >= 6:
            acts.append(f"레인: {_LANE_KO.get(lane['key'], lane['key'])}가 앞서나 "
                        f"(P={lane['p_best']}) 아직 미확정 — 균형 유지하며 더 모읍니다.")
        else:
            acts.append(f"레인: 표본 {rec['n_total']}편으로 판단 이름 — 균등 탐색 계속.")
    ts = rec["timeslot"]
    if ts and ts.get("reward") is not None:
        acts.append(f"시간대: {ts['key']} 성과 선두 (reward={ts['reward']}, n={ts['n']}).")
    if rec["thin_packaging"]:
        acts.append(f"패키징: {', '.join(rec['thin_packaging'])} 톤 데이터 부족 — "
                    f"이 톤들이 더 배정되도록 회전 중(안정화 전).")
    pk = rec["packaging"]
    if pk and pk.get("stabilized"):
        acts.append(f"패키징: '{pk['key']}' 톤 우승 확정 → 이후 전 회차 이 톤으로 고정.")
    if not rec["upcoming"]:
        acts.append("예약: 라이브에 예약된 영상이 없음(또는 API 미연결) — 다음 슬롯 채움 확인 필요.")
    else:
        acts.append(f"예약: 라이브에 {len(rec['upcoming'])}편 예약됨 (가장 임박: "
                    f"{rec['upcoming'][0]['publish_at']}).")
    if own:
        con.close()
    return rec


def portfolio_signal(con: sqlite3.Connection | None = None) -> str:
    """Phase 4 — feed performance back into the CONCEPT stage as PATTERN guidance:
    which energy / format / packaging performs, NOT which topics to repeat (freshness
    still owns topics). Returns "" when there's too little data to signal. Built to be
    injected into concept_brainstorm's system prompt next to the freshness/exclude block."""
    from agents import bandit
    own = con is None
    con = con or _db()
    try:
        a = bandit.analyze(con)
    except Exception:
        a = {"n_total": 0, "levels": {}}
    if a.get("n_total", 0) < 4:
        if own:
            con.close()
        return ""  # too little data — don't bias the concept stage on noise
    parts = []
    for lvl, label in (("lane", "레인"), ("timeslot", "시간대"), ("packaging", "패키징")):
        d = {k: v for k, v in a["levels"].get(lvl, {}).items()
             if k not in ("?", None) and v.get("mean_reward") is not None}
        if d:
            k, _v = max(d.items(), key=lambda kv: kv[1].get("mean_reward") or 0)
            parts.append(f"{label}={_LANE_KO.get(k, k)}")
    tops = []
    try:
        rows = con.execute(
            "SELECT vp.retention_pct AS ret, c.theme AS theme "
            "FROM video_performance vp LEFT JOIN cards c ON c.card_id=vp.card_id "
            "WHERE vp.retention_pct > 5 AND c.theme IS NOT NULL "
            "ORDER BY vp.retention_pct DESC LIMIT 3").fetchall()
        tops = [f"{(r['theme'] or '')[:22]}({r['ret']:.0f}%)" for r in rows]
    except Exception:
        pass
    if own:
        con.close()
    if not parts and not tops:
        return ""
    s = "\n\n[성과 신호 — 잘 되는 '결' 참고용 (소재 베끼기 금지)]\n"
    if parts:
        s += "  우세 패턴: " + ", ".join(parts) + "\n"
    if tops:
        s += "  유지율 톱: " + ", ".join(tops) + "\n"
    s += ("  → 위 '결'(에너지·포맷·패키징)만 새 소재에 녹여라. 같은 주제 반복은 금지 — "
          "신선도 규칙이 소재를 지배한다.")
    return s


def recommend_text(con: sqlite3.Connection | None = None) -> str:
    r = recommend(con)
    lines = [f"*채널 매니저 추천* — 성과표본 {r['n_total']}편"]
    for a in r["actions"]:
        lines.append(f"• {a}")
    if r["upcoming"]:
        lines.append("\n— 예약 라인업 (라이브) —")
        for u in r["upcoming"][:6]:
            lines.append(f"  {u['publish_at']} · {u['title'][:46]}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Channel Manager — packaging + recommend")
    p.add_argument("--recommend", action="store_true", help="print channel recommendation")
    p.add_argument("--concept-json", help="path to a concept JSON file")
    p.add_argument("--card-id", default="test")
    p.add_argument("--arm", choices=PACKAGING_ARMS, default=None,
                   help="force a packaging arm (else rotation)")
    p.add_argument("--all-arms", action="store_true",
                   help="generate all 3 arms for comparison")
    args = p.parse_args()
    if args.recommend:
        print(recommend_text())
        raise SystemExit(0)
    concept = json.loads(Path(args.concept_json).read_text(encoding="utf-8")) if args.concept_json else {}
    if args.all_arms:
        for a in PACKAGING_ARMS:
            r = make_packaging(concept, card_id=args.card_id, arm=a, log_to_db=False)
            print(f"\n===== arm={a} =====")
            print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        r = make_packaging(concept, card_id=args.card_id, arm=args.arm, log_to_db=False)
        print(json.dumps(r, ensure_ascii=False, indent=2))
