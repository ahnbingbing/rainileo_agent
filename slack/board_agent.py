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
    "너는 'Ryani(랴니) & Leo(레오)' 펫 YouTube Shorts 채널의 제작 운영 파트너야. PD와 Slack board "
    "채널에서 직접 대화하면서, 자연어 지시를 이해해 실제 액션으로 연결하고, 질문엔 똑똑하게 답한다.\n\n"
    "[채널 맥락] 랴니=흑백 프렌치불독(꼬리 없음, 11살 의젓한 누나), 레오=주황 태비 고양이(8개월 호기심 "
    "막내). 제작 레인 둘: ai_vtuber(AI 생성 숏츠)와 real_footage(실제 영상). 매일 03:00 배치가 **다음날(D+1)** "
    "4편을 자동 제작·예약하고, board에서 PD가 컨셉을 잡거나 영상을 내리거나 현황을 보거나 한다.\n"
    "★**날짜 해석(중요, 자주 헷갈림)**: '오늘 새벽 03:00 배치가 만든 것' = **내일(오늘+1) 공개분**이다. "
    "'오늘 공개되는 영상'은 *어제* 배치가 만든 거라 서로 다르다 — 절대 섞지 마라. PD가 '오늘 새벽/오늘 "
    "배치/내일 공개분/방금 만든 거'를 물으면 **내일 날짜(오늘+1)로** youtube_schedule을 조회하고, "
    "**4슬롯(보통 4편)이 다 나오는지 세어서** 빠짐없이 답해라(2편만 보이면 잘렸거나 덜 조회한 것).\n"
    "코드·파이프라인 수정/분석 요청은 자율 실행기가 받아 바로 처리한다(예전처럼 사람 CLI를 기다리지 않는다).\n\n"
    "너는 고정 메뉴를 고르는 게 아니라, 아래 '툴'을 직접 호출해 라이브 데이터를 확인하고 액션을 "
    "실행하는 에이전트다. 사실은 추측하지 말고 반드시 툴로 확인해라. 한 번에 **JSON 하나만** 출력 "
    "(JSON 밖 텍스트 절대 금지) — 두 형태 중 하나:\n"
    '  {"tool": "<툴이름>", "args": {...}}   ← 툴 호출\n'
    '  {"final": "<PD에게 보낼 한국어 답변>"}  ← 최종 답변\n'
    "필요하면 툴을 여러 번 순차로 부른다. 각 툴 결과를 받아 다음 행동을 정하고, 충분하면 final로 끝낸다.\n\n"
    "**final 작성법**: 딱딱한 한 줄 말고 맥락 아는 똑똑한 동료처럼 따뜻한 존댓말로. 무엇을 확인했고 "
    "무엇을 할지(또는 뭐가 더 필요한지) 구체적으로. 데이터 답이면 툴 결과를 PD가 바로 읽기 좋게 정리해라. "
    "잡담·의견·인사면 툴 없이 바로 final로 답해도 된다. 이모지는 가볍게.\n\n"
    "툴 목록:\n"
    "- youtube_schedule: 예약/공개 예정 영상의 슬롯·video_id·공개시각·privacy를 YouTube API로 **라이브** "
    "확인. args={date:'YYYY-MM-DD'|생략}. PD가 '예약/배치/슬롯/비디오아이디/언제 올라가/오늘 만든 거' 류를 "
    "물으면 무조건 이걸로 확인해 답해라(DB는 stale일 수 있으니 추측 금지).\n"
    "- get_status: board에 들어온 코드/파이프라인 요청의 처리 큐·진행 현황. args={}. '뭐 하고 있어/진행상황'에.\n"
    "- db_query: agent.db 읽기전용 SELECT(카드/성과/트렌드 등 임의 데이터 질문). args={sql:'SELECT …'}. SELECT/WITH만.\n"
    "- read_log: 최근 로그 확인(디버깅 '왜 실패' 류). args={name:'launch_out'|'launch_err'|'batch_problems'|'slack_err', "
    "contains:'필터문자열'|생략, lines:40}.\n"
    "- list_knowledge: 파이프라인이 PD에게 물은 미답 캐릭터 사실 목록. args={}.\n"
    "- set_concept: 특정 날짜 컨셉 예약(액션). args={date:'YYYY-MM-DD'|null, text:'컨셉 지시문 전체', "
    "lane:'ai_vtuber'|'real_footage'|null}. 연도 생략=올해, 지난 날짜면 내년(과거 연도 추측 금지).\n"
    "- answer_knowledge: 지식질문에 답. args={id:'질문id'|null, answer:'답'}.\n"
    "- escalate: 코드·파이프라인 수정/분석/디버깅 같은 깊은 작업을 자율 실행기에 넘긴다. args={summary:'한 줄 요약'}. "
    "넘긴 뒤엔 final로 무엇을 맡겼는지 알려라.\n"
    "- veto: 영상 내림(되돌리기 어려움 → PD 확인 후 실행). args={video_id:'...'|null, delete:true|false}. "
    "delete는 '완전삭제/영구삭제' 명시 때만 true.\n"
    "- render: 프리셋 1편 즉석 렌더(~$50 → PD 확인 후 실행). args={slug:'hawaii'|'homecam'|'chimipja'|null, "
    "text:'프리셋 아니면 컨셉 지시문'}.\n"
    "- rerender: 배치의 한 슬롯을 다시 만들고 예약영상을 교체(~$50). args={label:'260705_RF2100'} — "
    "파일명(YYMMDD_<AV|RF>HHMM)으로 슬롯을 지목. 기존 예약영상을 비공개로 내리고 같은 시각에 새로 "
    "렌더·재예약하며 **확인 없이 바로 실행**된다(board 봇=최상위 어드민). rerender는 그 슬롯을 **컨셉부터 "
    "새로 뽑아** 다시 그리니, PD가 구체적 방향('다리에 붙는 설정으로', '컨트롤룸 뒤에 상상씬 넣어', "
    "'캡션이 동작이랑 안 맞아')을 줬으면 그냥 rerender만 하면 그 방향이 안 반영된다 — 반드시 **먼저 "
    "set_concept으로 그 방향을 해당 날짜·레인 지시문으로 박고 → rerender** 해라(set_concept 지시문이 "
    "재렌더의 새 컨셉에 최우선으로 들어간다). 방향 없이 '그냥 이거 망했어 다시'면 rerender만.\n\n"
    "원칙: 모르면 툴로 확인하고 추측으로 사실을 지어내지 마라. 애매하면 되묻는 final이 낫다.\n"
    "★역할 분담 — board 봇은 최상위 어드민이라 **렌더·재렌더·재업로드/교체를 직접** 한다(render·rerender "
    "툴이 실제로 돈다). CLI 세션으로 미루지 마라. escalate는 **코드·프롬프트·데이터의 수정/분석/디버깅** 같은 "
    "리포지토리 작업 전용이다(자율 실행기는 안전상 유료키가 없어 렌더/업로드를 못 하고 코드만 고친다). 즉 "
    "버그·로직 수정은 escalate, 영상 다시 만들기는 (필요하면 set_concept→)rerender — 섞지 마라.\n"
    "★한 메시지에 리뷰가 여러 건 온다 — PD는 보통 '이 배치 리뷰 줄게: A는 캡션 고쳐, B는 상상씬 넣어, "
    "C는 다시 만들어'처럼 슬롯 여러 개를 한 번에 준다. 이걸 **슬롯별로 쪼개 각각** 처리해라: 먼저 "
    "youtube_schedule로 그 날짜의 슬롯·파일명을 확인하고, 편마다 위 규칙대로(방향 있으면 set_concept→"
    "rerender, 없으면 rerender). 명확한 리뷰를 받고 '확인할 게 많아요, 좁혀서 다시 물어봐 주세요'로 "
    "떠넘기는 건 실패다 — 할 수 있는 데까지 실행하고 무엇을 했는지 정리해 답하고, 정말 못 끝낸 부분만 "
    "명확히 남겨라(그건 CLI 세션이 이어받는다)."
)


def _board_llm(system: str, user: str, *, max_tokens: int = 2500) -> str:
    """The board is PD's 1:1 conversational surface — low-volume, high-touch — so it runs on
    **Claude Opus** for CLI-level reasoning (dates, context, multi-step tool use). This is the
    ONE intentional Anthropic use: PD's NO-Anthropic rule is a COST guard for the bulk
    caption/concept pipeline, and the board's volume is tiny. Falls back Claude → Gemini →
    cascade so it never goes dead. Override the model with BOARD_MODEL."""
    # 1. Claude Opus (primary — CLI-level)
    try:
        import anthropic
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("no ANTHROPIC_API_KEY")
        client = anthropic.Anthropic(api_key=key, timeout=float(os.getenv("LLM_TIMEOUT_S", "90")))
        resp = client.messages.create(
            model=os.getenv("BOARD_MODEL", "claude-opus-4-8"),
            max_tokens=max_tokens,
            system=system or anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": user}],
        )
        out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not out:
            raise RuntimeError("empty claude response")
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("board Claude failed (%s) — falling back to Gemini", e)
    # 2. Gemini 2.5 Pro (fallback)
    try:
        from google import genai as _genai
        from google.genai import types as _gt
        key = os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("no GOOGLE_API_KEY")
        client = _genai.Client(api_key=key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("LLM_TIMEOUT_S", "90")) * 1000))
        resp = client.models.generate_content(
            model="gemini-2.5-pro", contents=user,
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
    try:
        from agents.progress_log import log_progress
        log_progress("board", f"PD 컨셉 예약 {d.isoformat()}"
                     f"{f'({lane})' if lane else ''}: {text[:50]}")
    except Exception:
        pass
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


def _parse_fname(label: str):
    """'260705_RF2100' → (date(2026,7,5), 'real_footage', '21:00'). None if unparseable.
    Accepts spacing/underscore variants (RF 2100, 260705 rf 2100)."""
    import re as _re
    m = _re.search(r"(\d{6})\D*(AV|RF)\D*(\d{3,4})", (label or "").upper())
    if not m:
        return None
    ymd, lane_tok, hhmm = m.group(1), m.group(2), m.group(3).zfill(4)
    try:
        d = dt.date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError:
        return None
    lane = "ai_vtuber" if lane_tok == "AV" else "real_footage"
    return d, lane, f"{hhmm[:2]}:{hhmm[2:]}"


def _act_rerender(a: dict, db, do_veto) -> str:
    """Re-render ONE launch slot and REPLACE its scheduled video (PD 2026-07-04: the
    board bot is top admin and does this itself — no CLI, no confirm). Unlists the
    currently-scheduled video for the slot (so it's a replace, not a duplicate), then
    spawns a single-slot self-heal render that re-produces + re-uploads at the same
    publish time and posts the result into a fresh batch-summary thread."""
    label = (a.get("label") or a.get("fname") or a.get("slug") or a.get("text") or "").strip()
    parsed = _parse_fname(label)
    if not parsed:
        return (":information_source: 어느 슬롯을 다시 만들지 파일명으로 알려주세요 — "
                "예: `260705_RF2100 다시 만들어` (형식 `YYMMDD_<AV|RF>HHMM`).")
    target, lane, slot = parsed
    lane_lbl = "AV" if lane == "ai_vtuber" else "RF"
    fname = f"{target.strftime('%y%m%d')}_{lane_lbl}{slot.replace(':', '')}"
    # Replace: unlist the current scheduled video for this slot (frees it; no dup).
    replaced = ""
    try:
        from agents.launch import video_id_for_fname
        with db() as con:
            old_vid = video_id_for_fname(con, fname)
        if old_vid and do_veto:
            do_veto(old_vid, delete=False)
            replaced = f" (기존 `{old_vid}` 비공개 처리)"
    except Exception as e:
        log.warning("rerender pre-veto failed (%s): %s", fname, e)
    logp = ROOT / "data" / "logs" / f"rerender_{fname}.log"
    logp.parent.mkdir(parents=True, exist_ok=True)
    fh = open(logp, "a")
    subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "python"), "-m", "agents.launch_selfheal",
         "--date", target.isoformat(), "--lane", lane, "--slot", slot, "--rounds", "1"],
        cwd=str(ROOT), env=dict(os.environ), stdout=fh, stderr=subprocess.STDOUT)
    return (f":arrows_counterclockwise: `{fname}` 재렌더 시작했어요{replaced} "
            f"(백그라운드, ~$50). 완료되면 배치 써머리 쓰레드에 새 영상 올리고 같은 시각에 "
            f"재예약해요. 로그: `{logp.name}`.")


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
            f"_executor가 **원인분석·코드/프롬프트 수정**은 바로 이 스레드에 반영해요. 다만 "
            f"**렌더·재렌더·YouTube 업로드/교체는 executor가 못 해요**(유료키가 빠져 있어 실행이 안 돼요) "
            f"— 그건 CLI 세션(사람이 여는 클로드)에서 처리돼요. `현황` 으로 진행을 볼 수 있어요._")


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


# ── live tools (the agent calls these to read fresh state) ───────────────────
def _live_schedule(date_iso: str | None = None) -> list[dict]:
    """Fresh schedule from `cards` + a LIVE YouTube API verify (DB youtube fields go
    stale after re-uploads). date_iso='YYYY-MM-DD' filters by KST slot date; None =
    upcoming. Each item: slot/kst_date/lane/video_id/title/privacy."""
    import sqlite3 as _sql
    KST = dt.timezone(dt.timedelta(hours=9))
    con = _sql.connect(str(ROOT / "data" / "agent.db")); con.row_factory = _sql.Row
    try:
        rows = con.execute(
            "SELECT render_style, theme, youtube_video_id, youtube_publish_at "
            "FROM cards WHERE uploaded=1 AND youtube_video_id IS NOT NULL "
            "AND youtube_publish_at IS NOT NULL AND youtube_publish_at!='' "
            "ORDER BY youtube_publish_at").fetchall()
    finally:
        con.close()
    now = dt.datetime.now(dt.timezone.utc)
    items, seen = [], set()
    for r in rows:
        try:
            utc = dt.datetime.fromisoformat(r["youtube_publish_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        kst = utc.astimezone(KST)
        if date_iso:
            if kst.date().isoformat() != date_iso:
                continue
        elif utc < now - dt.timedelta(hours=6):
            continue
        key = (kst.isoformat(), r["youtube_video_id"])
        if key in seen:
            continue
        seen.add(key)
        items.append({"slot": kst.strftime("%H:%M"), "kst_date": kst.date().isoformat(),
                      "lane": r["render_style"], "video_id": r["youtube_video_id"],
                      "title": r["theme"] or "", "privacy": None})
    items.sort(key=lambda i: (i["kst_date"], i["slot"]))
    try:
        from youtube.oauth import get_youtube
        svc = get_youtube()
        ids = [i["video_id"] for i in items]
        live = {}
        for j in range(0, len(ids), 50):
            chunk = ids[j:j + 50]
            if not chunk:
                break
            resp = svc.videos().list(part="status", id=",".join(chunk)).execute()
            for v in resp.get("items", []):
                live[v["id"]] = v.get("status", {})
        for i in items:
            st = live.get(i["video_id"])
            if st is None:
                i["privacy"] = "삭제됨/없음"
            else:
                pv = st.get("privacyStatus", "?")
                i["privacy"] = "예약됨" if (pv == "private" and st.get("publishAt")) else pv
    except Exception as e:
        log.warning("live schedule verify failed: %s", e)
        for i in items:
            if i["privacy"] is None:
                i["privacy"] = "DB기준(미검증)"
    return items


def _fmt_schedule(date_iso: str | None) -> str:
    items = [i for i in _live_schedule(date_iso) if i.get("privacy") != "삭제됨/없음"]
    if not items:
        return (f"`{date_iso}` 에 예약된 영상이 없어요 (YouTube 라이브 확인)." if date_iso
                else "다가오는 예약 영상이 없어요 (YouTube 라이브 확인).")
    head = f":calendar: *예약 현황{f' — {date_iso}' if date_iso else ''}* _(YouTube 라이브 확인)_"
    out, cur = [head], None
    for i in items:
        if not date_iso and i["kst_date"] != cur:
            cur = i["kst_date"]; out.append(f"*{cur}*")
        lane = "AV" if i["lane"] == "ai_vtuber" else "RF"
        out.append(f"  • `{i['slot']}` {lane} → `{i['video_id']}` · {i['privacy']} · {i['title'][:30]}")
    return "\n".join(out)


def _safe_db_query(sql: str) -> str:
    """Read-only SELECT against agent.db so the agent can answer arbitrary data
    questions without a hand-coded intent. SELECT/WITH only, single statement."""
    import sqlite3 as _sql
    s = (sql or "").strip().rstrip(";").strip()
    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")):
        return "[거부] 읽기전용 SELECT/WITH 만 허용돼요."
    if ";" in s:
        return "[거부] 한 번에 SELECT 하나만요."
    if any(tok in f" {low} " for tok in (" insert ", " update ", " delete ", " drop ",
                                         " alter ", " attach ", " create ", " replace ")):
        return "[거부] 쓰기/DDL은 안 돼요."
    con = _sql.connect(str(ROOT / "data" / "agent.db")); con.row_factory = _sql.Row
    try:
        cur = con.execute(s)
        cols = [c[0] for c in (cur.description or [])]
        rows = cur.fetchmany(40)
    except Exception as e:
        return f"[쿼리 오류] {e}"
    finally:
        con.close()
    if not rows:
        return "(결과 0행)"
    def cell(v):
        sv = str(v)
        return sv[:60] + ("…" if len(sv) > 60 else "")
    lines = [" | ".join(cols)]
    for r in rows:
        lines.append(" | ".join(cell(r[c]) for c in cols))
    extra = "\n…(40행까지만)" if len(rows) == 40 else ""
    return "\n".join(lines) + extra


_LOGS = {"launch_out": "launch.out.log", "launch_err": "launch.err.log",
         "batch_problems": "batch_problems.jsonl", "slack_err": "slack.err.log"}


def _read_log(name: str, contains: str | None = None, lines: int = 40) -> str:
    fn = _LOGS.get(name)
    if not fn:
        return f"[unknown log: {name}] 가능: {', '.join(_LOGS)}"
    p = ROOT / "data" / "logs" / fn
    if not p.exists():
        return f"({fn} 없음)"
    try:
        tail = p.read_text(errors="ignore").splitlines()[-1500:]
    except Exception as e:
        return f"[읽기 오류] {e}"
    if contains:
        tail = [ln for ln in tail if contains in ln]
    tail = tail[-max(1, min(int(lines or 40), 80)):]
    return "\n".join(tail) or "(해당 라인 없음)"


# ── tool registry + agent loop ───────────────────────────────────────────────
# READ-ONLY/cheap tools run inline in the loop; COSTLY tools (veto/render) are
# returned to handle_board_message as a confirm-pending instead of executing.
def _run_tool(name: str, args: dict, *, db, user: str, channel: str, thread_ts: str,
              do_veto=None) -> str:
    a = args or {}
    if name == "rerender":
        return _act_rerender(a, db, do_veto)
    if name == "youtube_schedule":
        return _fmt_schedule((a.get("date") or "").strip() or None)
    if name == "get_status":
        return _act_status(db)
    if name == "db_query":
        return _safe_db_query(a.get("sql", ""))
    if name == "read_log":
        return _read_log(a.get("name", ""), a.get("contains") or None, int(a.get("lines", 40) or 40))
    if name == "list_knowledge":
        return _act_knowledge_list(db)
    if name == "set_concept":
        return _act_concept(a, db)
    if name == "answer_knowledge":
        return _act_knowledge_answer(a)
    if name == "escalate":
        return _act_escalate(a.get("summary") or "", a, db, user, channel=channel, thread_ts=thread_ts)
    return f"[알 수 없는 툴: {name}]"


# A single PD message can bundle several actions (e.g. "review the 4 slots and
# fix these three"). Each fix is often a 2-tool compose (set_concept → rerender),
# so the budget must fit 3–4 items + lookups, not one Q&A. Board is low-volume
# Opus, so a larger cap is cheap; the real guard is forcing a graceful final on
# the last step (below) rather than clamping the count low.
_AGENT_MAX_STEPS = 10


def _agent_answer(text: str, *, db, user: str, channel: str, thread_ts: str,
                  do_veto=None) -> dict:
    """Tool-using agent. Returns {'text': str} for a final answer, or
    {'costly': d} where d={'intent','params','reply'} to route through the
    confirm flow. The LLM calls live tools and composes the answer itself —
    no per-question intent hand-coding."""
    today = dt.date.today().isoformat()
    try:
        from agents.progress_log import recent_progress
        _prog = recent_progress(12)
    except Exception:
        _prog = "(진행 로그 없음)"
    transcript = (f"오늘은 {today} (KST) 입니다.\n"
                  f"[최근 진행 로그 — board(너)와 CLI(Claude Code)가 함께 한 일. 이 맥락 위에서 이어가라]\n"
                  f"{_prog}\n\n"
                  f"PD 메시지: {text}\n")
    for _step in range(_AGENT_MAX_STEPS):
        if _step == _AGENT_MAX_STEPS - 1:
            # Last step: never call another tool — the loop is about to end. Force a
            # real final so PD gets a substantive answer, and route any unfinished
            # part to the CLI session instead of a dead-end "ask again".
            transcript += ("\n[남은 스텝: 이번이 마지막이야. 더 툴을 부르지 말고 지금 바로 "
                           '{"final":"…"} 로 끝내라. 지금까지 확인/실행한 걸 정리하고, 아직 '
                           "못 끝낸 게 있으면 '나머지는 CLI 세션에 넘겨 이어서 처리할게요'라고 "
                           "명확히 알려라. 절대 '좁혀서 다시 물어봐' 로 떠넘기지 마라.]\n")
        raw = _board_llm(_SYS, transcript, max_tokens=2500).strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        m = re.search(r"\{.*\}", raw, re.S)        # tolerate prose around the JSON
        try:
            d = json.loads(m.group(0) if m else raw)
        except Exception:
            # Broken/truncated JSON (a long list cut off at max_tokens, etc). Recover the
            # final string EVEN when its closing quote/brace got chopped off, and NEVER dump
            # raw {"final": ...} JSON to the PD (that leak is what made the bot look broken).
            fm = (re.search(r'"final"\s*:\s*"(.*?)"\s*[}\n]', raw, re.S)
                  or re.search(r'"final"\s*:\s*"(.+)$', raw, re.S))   # truncated final
            if fm:
                txt = (fm.group(1).rstrip().rstrip('"').rstrip("}")
                       .replace("\\n", "\n").replace('\\"', '"').strip())
                if not raw.rstrip().endswith("}"):
                    txt += "\n_(답변이 길어 일부 잘렸어요 — 더 필요하면 한 번 더 물어봐 주세요)_"
                return {"text": txt or "네! 🐾"}
            if raw.lstrip().startswith("{"):        # broken JSON, no usable final → don't leak it
                return {"text": "앗, 답변 형식이 잠깐 꼬였어요 🙏 한 번만 다시 물어봐 주세요."}
            return {"text": raw or "네! 🐾"}        # genuine plain-prose answer — pass through
        if not isinstance(d, dict):
            return {"text": str(d)}
        if "final" in d:
            return {"text": str(d["final"]).strip() or "네! 🐾"}
        tool = d.get("tool")
        if tool in COSTLY:                          # veto / render → confirm first
            return {"costly": {"intent": tool, "params": d.get("args") or {},
                               "reply": d.get("reply") or ""}}
        try:
            result = _run_tool(tool, d.get("args") or {}, db=db, user=user,
                               channel=channel, thread_ts=thread_ts, do_veto=do_veto)
        except Exception as e:
            log.warning("board tool %s failed: %s", tool, e)
            result = f"[툴 실행 오류: {e}]"
        transcript += (f"\n[너의 호출] {json.dumps(d, ensure_ascii=False)[:300]}\n"
                       f"[결과]\n{result}\n\n위 결과를 보고, 더 확인할 게 있으면 툴을 또 부르고 "
                       f"충분하면 {{\"final\":\"…\"}} 로 답하세요.\n")
    # Safety net (should be unreachable now that the last step forces a final): the
    # model kept calling tools past budget. Don't dead-end PD — queue the original
    # request for the CLI session (shared board↔CLI loop reads board_escalations) and
    # say so honestly, rather than "narrow it down and ask again".
    try:
        with db() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS board_escalations ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT (datetime('now')), "
                "author TEXT, request TEXT, summary TEXT, handled INTEGER DEFAULT 0)")
            con.execute("INSERT INTO board_escalations (author, request, summary) VALUES (?,?,?)",
                        (user or "", text, ("[미완 인계] " + text)[:200]))
    except Exception as e:  # noqa: BLE001
        log.warning("board fallback handoff-record failed: %s", e)
    return {"text": "요청이 여러 건이라 여기서 한 번에 다 처리하진 못했어요 🙏 "
                    "확인/실행한 부분은 위에 정리했고, 나머지는 CLI 세션에 넘겨서 이어 처리할게요."}


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

    # 2) Fresh request — tool-using agent. The LLM calls live tools (youtube_schedule,
    #    db_query, read_log, get_status, …) and composes the answer itself, so novel
    #    questions don't need a new hand-coded intent. Costly tools (veto/render) come
    #    back as a confirm-pending instead of executing.
    reply_thread = thread_ts or ts
    try:
        res = _agent_answer(text, db=db, user=user, channel=channel,
                            thread_ts=reply_thread, do_veto=do_veto)
    except Exception as e:
        log.exception("board agent failed")
        _post(client, channel, reply_thread,
              f":x: 처리 중 문제가 생겼어요: {str(e)[:200]}")
        return
    if res.get("costly"):
        d = res["costly"]
        with _PENDING_LOCK:
            _PENDING[reply_thread] = {"d": d, "text": text, "ts": time.time()}
        _post(client, channel, reply_thread,
              f":pause_button: *{_confirm_preview(d)}* 할까요?\n"
              f"_`응`/`yes` 면 실행, `취소` 면 취소 (3분 후 자동 만료)._")
        return
    _post(client, channel, reply_thread, res.get("text") or "네! 🐾")


def _post(client, channel, thread_ts, text):
    try:
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
    except Exception as e:
        log.warning("board post failed: %s", e)
