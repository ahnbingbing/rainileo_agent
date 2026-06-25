"""rayleo_board conversational agent (PD 2026-06-21).

Turn the `rayleo_board` Slack channel into a natural-language control surface —
the PD talks to the channel like a CLI; an LLM parses intent, runs the matching
pipeline action, and replies. Two-tier by design (PD pick "둘 다"):

  • ROUTER     — known high-value actions mapped to the real functions
                 (concept-schedule / status / knowledge / veto / render-one).
  • ESCALATION — anything repo-level or ambiguous is queued for the CLI (me) so
                 a full agent picks it up; the board never silently guesses on
                 something it can't safely do.

Safety (PD pick "실행 전 확인"): read-only intents run immediately; costly or
irreversible ones (veto, render ≈ $50, delete, publish) post a one-line confirm
and wait for a thread reply (`응`/`yes` to run, `취소` to drop). A pending action
expires after PENDING_TTL_S so a stale confirm can't fire later.

This module owns NO Slack wiring of its own — `slack/app.py` routes board-channel
messages here via `handle_board_message`, passing the callbacks it needs
(`db` factory, `do_veto`) so we avoid importing app.py back (circular).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("board_agent")
ROOT = Path(__file__).resolve().parent.parent

# ── confirm-flow state (single socket-mode process → in-memory dict is fine) ──
_PENDING: dict[str, dict] = {}        # thread_root_ts -> {"action","params","ts"}
_PENDING_LOCK = threading.Lock()
PENDING_TTL_S = 180

# Costly / irreversible → must be confirmed before executing.
COSTLY = {"veto", "render"}

# The 03:00 producer on day D builds day D+1's batch. So the earliest date a
# /concept can still steer is the TARGET of the next 03:00 run:
#   before 03:00 today → next run is tonight, builds tomorrow      → today + 1
#   after  03:00 today → tonight's run already fired, next is D+1  → today + 2
def _earliest_concept_date(now: dt.datetime | None = None) -> dt.date:
    now = now or dt.datetime.now()
    return now.date() + dt.timedelta(days=2 if now.hour >= 3 else 1)


YES = {"응", "ㅇㅇ", "ㄱㄱ", "ok", "okay", "yes", "y", "네", "넵", "좋아",
       "그래", "confirm", "실행", "go", "ㅇ", "yep", "yo"}
NO = {"아니", "ㄴㄴ", "no", "n", "취소", "cancel", "안돼", "그만", "stop",
      "중지", "ㄴ", "nope"}


# ── LLM intent parse ─────────────────────────────────────────────────────────
_SYS = (
    "너는 'Ryani(랴니=강아지, 꼬리 없음)와 Leo(레오=고양이)' 펫 YouTube Shorts 채널의 "
    "제작 운영 비서다. PD가 Slack에서 자연어로 지시하면 의도를 파싱한다. "
    "반드시 JSON만 출력. 스키마:\n"
    '{"intent": one of '
    '["concept","status","knowledge_list","knowledge_answer","veto","render","help","chat","escalate"], '
    '"params": {...}, "reply": "PD에게 보낼 한국어 존댓말 1~2문장(이모지 약간)"}\n'
    "intent 가이드:\n"
    "- concept: 특정 날짜의 영상 컨셉/방향을 예약. params={date:'YYYY-MM-DD'|null, "
    "text:'컨셉 지시문 전체', lane:'ai_vtuber'|'real_footage'|null}. 날짜를 안 적었으면 null. "
    "연도를 생략하면 올해(아래 '오늘' 기준), 그 날짜가 이미 지났으면 내년. 절대 과거 연도로 추측하지 마라.\n"
    "- status: 예약된 컨셉/오늘내일 스케줄/배치 상태를 묻는다. params={}.\n"
    "- knowledge_list: 파이프라인이 PD에게 물은 '모르는 캐릭터 사실' 목록을 보고 싶다. params={}.\n"
    "- knowledge_answer: 그 질문에 답한다. params={id:'질문id'|null, answer:'답'}.\n"
    "- veto: 특정 영상을 내린다. params={video_id:'...'|null, delete:true|false}. "
    "delete 는 PD가 '완전삭제/영구삭제/delete' 라고 명시할 때만 true, 그냥 '내려/취소/비공개' 는 false.\n"
    "- render: 지금 즉시 한 편을 렌더(돈 듦). params={slug:'hawaii'|'homecam'|'chimipja'|null, "
    "text:'프리셋이 아니면 컨셉 지시문'}.\n"
    "- help: 뭘 할 수 있는지 묻는다. params={}.\n"
    "- chat: 인사·잡담·확인. params={}.\n"
    "- escalate: 위에 안 맞거나, 코드/파이프라인 수정·분석·디버깅 같이 CLI가 해야 할 복잡한 일. "
    "params={summary:'한 줄 요약'}.\n"
    "확실치 않으면 escalate. reply에는 무엇을 할지 또는 무엇이 필요한지 명확히 적어라."
)


def _board_llm(system: str, user: str, *, max_tokens: int = 700) -> str:
    """The board talks to PD directly — a low-volume, high-touch surface — so it uses a
    SMART model (Gemini 2.5 Pro by default) instead of the bulk pipeline's gpt-4.1-led
    cascade, which is tuned for cost on caption/concept volume and reads dumb in chat.
    Falls back to the generic cascade if Gemini is unavailable so the board never goes dead.
    Override with BOARD_MODEL."""
    try:
        from google import genai as _genai
        from google.genai import types as _gt
        key = os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("no GOOGLE_API_KEY")
        client = _genai.Client(api_key=key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("LLM_TIMEOUT_S", "90")) * 1000))
        resp = client.models.generate_content(
            model=os.getenv("BOARD_MODEL", "gemini-2.5-pro"), contents=user,
            config=_gt.GenerateContentConfig(system_instruction=system or None,
                                             max_output_tokens=max_tokens))
        out = (resp.text or "").strip()
        if not out:
            raise RuntimeError("empty gemini response")
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("board Gemini failed (%s) — falling back to cascade", e)
        from agents.llm_cascade import call_text_cascade
        return call_text_cascade(system, user, max_tokens=max_tokens)


def _parse(text: str) -> dict:
    today = dt.date.today().isoformat()
    raw = _board_llm(_SYS, f"오늘은 {today} 입니다.\nPD 메시지: {text}",
                     max_tokens=700).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    d = json.loads(raw)
    if not isinstance(d, dict) or "intent" not in d:
        raise ValueError("no intent")
    d.setdefault("params", {})
    d.setdefault("reply", "")
    return d


# ── action executors (return a string to post) ───────────────────────────────
def _act_concept(params: dict, db) -> str:
    from agents import arc
    text = (params.get("text") or "").strip()
    if not text:
        return ":warning: 컨셉 내용을 못 읽었어요. 예: `6/23 거실에서 햇빛 보는 랴니레오`"
    date_s = (params.get("date") or "").strip()
    earliest = _earliest_concept_date()
    if not date_s:
        d = earliest
        note = f" (날짜를 안 적으셔서 가장 빠른 적용일 *{d.isoformat()}* 로 잡았어요)"
    else:
        try:
            d = dt.date.fromisoformat(date_s)
        except ValueError:
            return f":x: 날짜 `{date_s}` 형식이 이상해요. `YYYY-MM-DD` 로 적어주세요."
        note = ""
        # Year-slip guard: an LLM sometimes emits a past year for a partial date
        # (e.g. "6/23" → 2024-06-23). Snap a past date to its next occurrence.
        today = dt.date.today()
        if d < today:
            snapped = d.replace(year=today.year)
            if snapped < today:
                snapped = d.replace(year=today.year + 1)
            if snapped != d:
                note = f" (연도 보정: {d.isoformat()}→{snapped.isoformat()})"
                d = snapped
        if d < earliest:
            return (f":warning: `{d.isoformat()}` 는 이미 배치가 만들어진(또는 만들어지는) "
                    f"날이라 이 컨셉을 못 태워요. 03:00 프로듀서가 *전날* 배치를 만들거든요. "
                    f"가장 빠른 적용일은 *{earliest.isoformat()}* 예요 — 그 날짜로 다시 "
                    f"말씀해 주시면 예약할게요.")
    lane = (params.get("lane") or "").strip()
    if lane == "ai_vtuber" and "적용 범위" not in text:
        text += ("\n★적용 범위: 이 컨셉은 ai_vtuber 슬롯에만 적용한다. real_footage 슬롯은 "
                 "무시하고 평소대로 제작하라.")
    with db() as con:
        arc.set_concept_directive(con, d.isoformat(), text)
    return (f":white_check_mark: `{d.isoformat()}` 컨셉 예약 완료{note}\n  → {text[:240]}"
            f"{'…' if len(text) > 240 else ''}\n_그날 03:00 배치가 이 방향을 최우선으로 만듭니다._")


def _last_batch_summary() -> str:
    """Best-effort summary of the most recent launch batch from launch.out.log.
    Reliable source (the DB youtube fields go stale). Returns '' if unavailable."""
    p = ROOT / "data" / "logs" / "launch.out.log"
    if not p.exists():
        return ""
    try:
        tail = p.read_text(errors="ignore").splitlines()[-800:]
    except Exception:
        return ""
    # Scheduled slots: last unique "예약완료" line per slot (slot + video_id + 공개예정).
    sched: dict[str, str] = {}
    for ln in tail:
        m = re.search(r"(\d{2}:\d{2})\s+(\w+)\s+예약완료.*?video_id=`([^`]+)`.*?공개예정\s+(\S+)", ln)
        if m:
            slot, lane, vid, when = m.groups()
            sched[slot] = f"  • `{slot}` {lane} → `{vid}` (공개 {when})"
    # Failed slots from the last JSON "failed": [...] block.
    failed: list[str] = []
    joined = "\n".join(tail)
    fm = list(re.finditer(r'"failed"\s*:\s*\[(.*?)\]', joined, re.S))
    if fm:
        failed = re.findall(r'"([^"]+)"', fm[-1].group(1))
    if not sched and not failed:
        return ""
    out = ["*최근 배치 결과:*"]
    out += list(sched.values()) or ["  • (예약된 슬롯 없음)"]
    if failed:
        out.append("  • 실패: " + ", ".join(failed))
    out.append("  _라이브 예약은 CLI에서 YouTube API로 재확인 권장._")
    return "\n".join(out)


def _ago(ts: str) -> str:
    """'YYYY-MM-DD HH:MM:SS'(UTC, SQLite datetime('now')) → '3분 전' 류 상대시각."""
    try:
        t = dt.datetime.strptime((ts or "").strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return ""
    secs = (dt.datetime.utcnow() - t).total_seconds()
    if secs < 0:
        return "방금"
    if secs < 90:
        return "방금"
    if secs < 3600:
        return f"{int(secs // 60)}분 전"
    if secs < 86400:
        return f"{int(secs // 3600)}시간 전"
    return f"{int(secs // 86400)}일 전"


def _esc_result_oneline(result: str | None) -> str:
    """완료된 요청의 result(스레드에 올린 전문)에서 한 줄 결과 신호만 뽑는다."""
    r = (result or "").strip()
    if not r:
        return "완료(결과 미기록)"
    if "[APPROVAL]" in r:
        return ":raised_hand: 승인 대기"
    if "스모크" in r and "실패" in r or "되돌렸" in r or "되돌려" in r:
        return ":x: 실패→되돌림"
    if "커밋 `" in r or ("커밋" in r and ":white_check_mark:" in r):
        return ":white_check_mark: 코드 수정·커밋"
    if r.startswith(":x:") or "처리 실패" in r:
        return ":x: 처리 실패"
    if "분석만" in r:
        return "분석만(코드변경 없음)"
    # 결론 한 줄 — 헤더/이모지/마크다운 줄을 건너뛰고 첫 의미있는 문장.
    for ln in r.splitlines():
        s = re.sub(r"[`*_>#]", "", ln).strip()
        s = re.sub(r"^:[a-z_]+:\s*", "", s).strip()
        if s and not s.startswith("---") and "자동 처리" not in s and "Slack" not in s:
            return s[:60] + ("…" if len(s) > 60 else "")
    return "완료"


def _act_status(db) -> str:
    """진행 중인 '업무' 중심 현황 (PD 2026-06-25).

    '현황'은 영상 배치 상태(동영상 현황)가 아니라 **지금 처리 중인 작업**이 먼저
    보여야 한다 — PD가 board에 던진 요청을 executor가 어디까지 처리했는지가 핵심.
    그래서 순서: ① 처리 중인 요청 → ② 방금 끝난 요청(결과 한 줄) → ③ PD 답 대기
    지식질문 → ④ 예약된 컨셉(앞으로 만들 영상) → ⑤ 최근 영상 배치 결과는 맨 끝
    보조 정보로 내린다.
    """
    from agents import arc, knowledge as kn
    today = dt.date.today().isoformat()
    lines = [":hourglass_flowing_sand: *진행 현황*"]
    with db() as con:
        # 진행 중 / 방금 끝난 요청 — AUTONOMOUS executor(scripts.process_board_
        # escalations)가 ~5분 틱마다 비운다. handled=0 = in-flight/큐 대기,
        # handled=1 = 처리 완료(스레드에 결과). "이거 하고 있어?"에 진짜로 답한다.
        try:
            open_esc = con.execute(
                "SELECT id, summary, ts FROM board_escalations WHERE handled=0 "
                "ORDER BY id ASC LIMIT 10").fetchall()
        except Exception:
            open_esc = []
        try:
            done_esc = con.execute(
                "SELECT id, summary, result, ts FROM board_escalations WHERE handled=1 "
                "ORDER BY id DESC LIMIT 5").fetchall()
        except Exception:
            done_esc = []
        rows = arc.list_concept_directives(con, today)
        pend = kn.pending_questions(con)

    # ① 처리 중인 요청 — 최우선
    if open_esc:
        lines.append(f"*:gear: 처리 중인 요청 {len(open_esc)}개* (executor가 ~5분마다 처리)")
        for e in open_esc:
            ago = _ago(e["ts"])
            tag = f" ({ago})" if ago else ""
            lines.append(f"  • `#{e['id']}`{tag} {(e['summary'] or '')[:90]}")
    else:
        lines.append("*:white_check_mark: 처리 중인 요청 없음* — 큐가 비어 있어요.")

    # ② 방금 끝난 요청 — 결과 한 줄
    if done_esc:
        lines.append("*:checkered_flag: 최근 처리 완료:*")
        for e in done_esc:
            ago = _ago(e["ts"])
            tag = f" ({ago})" if ago else ""
            res = _esc_result_oneline(e["result"])
            lines.append(f"  • `#{e['id']}`{tag} {(e['summary'] or '')[:60]} — {res}")

    # ③ PD 답 대기 (파이프라인이 막혀 PD를 기다리는 일)
    if pend:
        lines.append(f"*:question: 답 대기 지식질문 {len(pend)}개* — `지식질문 보여줘` 로 확인")

    # ④ 예약된 컨셉 — 앞으로 만들 영상(보조)
    if rows:
        lines.append("*:date: 예약된 컨셉:*")
        for r in rows:
            lines.append(f"  • `{r['target_date']}` — {r['directive'][:80]}")

    # ⑤ 최근 영상 배치 결과 — 맨 끝 보조. '동영상 현황'은 묻지 않은 한 부차적.
    batch = _last_batch_summary()
    if batch:
        lines.append(batch)
        lines.append("_라이브 유튜브 예약 상태는 CLI에서 API로 확인하세요 (DB는 stale)._")
    return "\n".join(lines)


def _act_knowledge_list(db) -> str:
    from agents import knowledge as kn
    with db() as con:
        pend = kn.pending_questions(con)
    if not pend:
        return ":white_check_mark: 대기 중인 지식 질문이 없어요."
    lines = [":question: *대기 중 지식질문* (답하려면 `<id>번 답: <내용>`)"]
    for q in pend:
        lines.append(f"  • `{q.get('id')}` [{q.get('subject','')}] {q.get('question','')}")
    return "\n".join(lines)


def _act_knowledge_answer(params: dict) -> str:
    from agents import knowledge as kn
    qid = str(params.get("id") or "").strip()
    fact = (params.get("answer") or "").strip()
    if not qid or not fact:
        return ":warning: `<id>번 답: <내용>` 형식으로 답해주세요."
    con = kn._db()
    row = con.execute("SELECT question, subject FROM character_facts WHERE id=?",
                      (qid,)).fetchone()
    if not row:
        return f":x: id `{qid}` 질문을 못 찾았어요. `지식질문 보여줘` 로 확인하세요."
    kn.add_answer(con, row["question"], fact, subject=row["subject"] or "")
    return (f":white_check_mark: 저장 — \"{row['question']}\" → {fact}\n"
            f"_다음 컨셉부터 반영되고 다시 묻지 않습니다._")


def _act_veto(params: dict, do_veto) -> str:
    vid = (params.get("video_id") or "").strip()
    if not vid:
        return (":warning: 어떤 영상인지 video_id가 필요해요. "
                "(런칭 슬롯 쓰레드에서 `veto` 라고 답하거나, video_id를 알려주세요.)")
    return do_veto(vid, delete=bool(params.get("delete")))


def _act_render(params: dict) -> str:
    slug = (params.get("slug") or "").strip()
    if slug not in {"hawaii", "homecam", "chimipja"}:
        return (":information_source: 지금 바로 렌더할 수 있는 프리셋은 "
                "`hawaii` / `homecam` / `chimipja` 예요. 새 컨셉이면 `컨셉 예약`으로 "
                "배치에 태우는 걸 추천해요(돈/시간 절약).")
    env = dict(os.environ, CONCEPT_BRAINSTORM="0")
    logp = ROOT / "data" / "logs" / f"render_av_one_{slug}.log"
    logp.parent.mkdir(parents=True, exist_ok=True)
    fh = open(logp, "a")
    subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "python"), "-m", "scripts.render_av_one", slug],
        cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT)
    return (f":clapper: `{slug}` 렌더 시작했어요 (백그라운드, ~$50). "
            f"로그: `{logp.name}`. 완료되면 알려드릴게요.")


def _act_escalate(text: str, params: dict, db, user: str,
                  channel: str = "", thread_ts: str = "") -> str:
    summary = (params.get("summary") or text)[:200]
    eid = "?"
    try:
        with db() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS board_escalations ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT (datetime('now')), "
                "author TEXT, request TEXT, summary TEXT, handled INTEGER DEFAULT 0)")
            cols = [r[1] for r in con.execute("PRAGMA table_info(board_escalations)")]
            for c, ddl in (("channel", "channel TEXT"), ("thread_ts", "thread_ts TEXT")):
                if c not in cols:
                    con.execute(f"ALTER TABLE board_escalations ADD COLUMN {ddl}")
            con.execute(
                "INSERT INTO board_escalations (author, request, summary, channel, thread_ts) "
                "VALUES (?,?,?,?,?)", (user or "", text, summary, channel, thread_ts))
            eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception as e:
        log.warning("escalation save failed: %s", e)
    # Autonomous now (PD 2026-06-25): kick the executor immediately so it's handled
    # WITHOUT waiting for a human CLI session. The executor (scripts.process_board_
    # escalations) implements the fix + commits + replies in this thread; paid renders /
    # uploads are impossible there (paid keys stripped) and come back as a 1-tap approval.
    _kick_executor()
    return (f":robot_face: 자동 처리를 시작했어요 — `#{eid}` \"{summary}\".\n"
            f"_분석·코드수정은 바로 반영해 이 스레드에 결과를 올릴게요. 돈 드는 렌더/업로드는 "
            f"승인 한 번만 받을게요. `현황` 으로 진행을 볼 수 있어요._")


def _kick_executor() -> None:
    """Fire-and-forget the autonomous executor so escalations don't wait for the 5-min
    launchd tick. Detached; the single-flight lock dedups against the launchd run."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        py = os.path.join(root, ".venv", "bin", "python")
        subprocess.Popen(
            [py, "-m", "scripts.process_board_escalations"],
            cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except Exception as e:  # noqa: BLE001
        log.warning("executor kick failed: %s", e)


_HELP = (
    ":wave: *rayleo_board 비서* — 자연어로 말씀하세요. 예시:\n"
    "  • `6/23 비오는날 창밖보는 랴니레오 ai_vtuber` → 그날 컨셉 예약\n"
    "  • `오늘내일 뭐 예약돼 있어?` → 현황\n"
    "  • `지식질문 보여줘` / `12번 답: 랴니는 물 좋아해` → 캐릭터 지식\n"
    "  • `이 영상 내려줘 <video_id>` → veto (실행 전 확인)\n"
    "  • `homecam 지금 렌더해줘` → 즉시 렌더 (실행 전 확인, ~$50)\n"
    "  • 그 외 복잡한 일(코드 수정·분석)은 CLI로 넘겨요.\n"
    "_돈 들거나 되돌리기 어려운 건 `응`/`취소` 로 한 번 더 확인합니다._"
)


def _dispatch(d: dict, *, text: str, db, do_veto, user: str,
              channel: str = "", thread_ts: str = "") -> str:
    intent = d.get("intent")
    p = d.get("params") or {}
    if intent == "concept":
        return _act_concept(p, db)
    if intent == "status":
        return _act_status(db)
    if intent == "knowledge_list":
        return _act_knowledge_list(db)
    if intent == "knowledge_answer":
        return _act_knowledge_answer(p)
    if intent == "veto":
        return _act_veto(p, do_veto)
    if intent == "render":
        return _act_render(p)
    if intent == "help":
        return _HELP
    if intent == "escalate":
        return _act_escalate(text, p, db, user, channel=channel, thread_ts=thread_ts)
    # chat
    return d.get("reply") or "네! 말씀하세요 🐾"


def _confirm_preview(d: dict) -> str:
    """One-line description of a costly action awaiting confirmation."""
    intent = d.get("intent"); p = d.get("params") or {}
    if intent == "veto":
        return f"영상 `{p.get('video_id','?')}` 을(를) {'삭제' if p.get('delete') else '비공개'} 처리"
    if intent == "render":
        return f"`{p.get('slug','?')}` 한 편 즉시 렌더 (~$50)"
    return d.get("reply") or intent


# ── entry point (called from slack/app.py) ───────────────────────────────────
def handle_board_message(client, event, *, db, do_veto):
    """Route one board-channel message. `db` = connection factory (context mgr);
    `do_veto(vid, delete=)` = app.py's shared veto core."""
    channel = event.get("channel", "")
    user = event.get("user", "")
    text = (event.get("text") or "").strip()
    ts = event.get("ts", "")
    thread_ts = event.get("thread_ts")
    if not text:
        return
    low = text.lower().strip().strip("!. ")

    # 1) Confirmation reply to a pending costly action (in its thread).
    if thread_ts:
        with _PENDING_LOCK:
            pend = _PENDING.get(thread_ts)
            if pend and (time.time() - pend["ts"] > PENDING_TTL_S):
                _PENDING.pop(thread_ts, None); pend = None
        if pend:
            if low in YES:
                with _PENDING_LOCK:
                    _PENDING.pop(thread_ts, None)
                try:
                    out = _dispatch(pend["d"], text=pend["text"], db=db,
                                    do_veto=do_veto, user=user,
                                    channel=channel, thread_ts=thread_ts)
                except Exception as e:
                    log.exception("board confirmed action failed")
                    out = f":x: 실행 실패: {str(e)[:300]}"
                _post(client, channel, thread_ts, out)
                return
            if low in NO:
                with _PENDING_LOCK:
                    _PENDING.pop(thread_ts, None)
                _post(client, channel, thread_ts, ":x: 취소했어요.")
                return
            # Anything else in the thread → drop the stale pending, fall through
            # and treat as a fresh request.
            with _PENDING_LOCK:
                _PENDING.pop(thread_ts, None)

    # 2) Fresh request — parse intent.
    reply_thread = thread_ts or ts
    try:
        d = _parse(text)
    except Exception as e:
        log.warning("board parse failed: %s", e)
        _post(client, channel, reply_thread,
              ":thinking_face: 잘 못 알아들었어요. `도움말` 이라고 하면 예시를 보여드릴게요.")
        return

    intent = d.get("intent")
    # 3) Costly → confirm first.
    if intent in COSTLY:
        with _PENDING_LOCK:
            _PENDING[reply_thread] = {"d": d, "text": text, "ts": time.time()}
        _post(client, channel, reply_thread,
              f":pause_button: *{_confirm_preview(d)}* 할까요?\n"
              f"_`응`/`yes` 면 실행, `취소` 면 취소 (3분 후 자동 만료)._")
        return

    # 4) Read-only / cheap → run now. Lead with the LLM's friendly reply, then
    #    the action result.
    try:
        out = _dispatch(d, text=text, db=db, do_veto=do_veto, user=user,
                        channel=channel, thread_ts=reply_thread)
    except Exception as e:
        log.exception("board action failed")
        out = f":x: 처리 실패: {str(e)[:300]}"
    lead = (d.get("reply") or "").strip()
    if lead and intent not in ("chat", "help") and not out.startswith(lead[:12]):
        out = f"{lead}\n{out}"
    _post(client, channel, reply_thread, out)


def _post(client, channel, thread_ts, text):
    try:
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
    except Exception as e:
        log.warning("board post failed: %s", e)
