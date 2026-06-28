"""
slack/app.py — Slack Bolt (Socket Mode) workroom for Ryani & Leo agents.

Slash commands (register them in api.slack.com/apps -> Slash Commands):
    /writer-run         Trigger Writer agent now (default: tomorrow's card)
    /writer-show <date> Show today's draft card (or specified YYYY-MM-DD)
    /pd-approve <id>    PD approves a draft card
    /pd-reject <id>     PD rejects with reason (use as: /pd-reject <id> reason text)
    /post <id>          Push approved card to Cameraman pipeline
    /status             Pipeline status snapshot

Run:
    python -m slack.app
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("slack.app")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
WORKROOM = os.getenv("SLACK_WORKROOM_CHANNEL")
PHOTOS_CHANNEL = os.getenv("SLACK_PHOTOS_CHANNEL", "C0B5TEX2LQZ")
EPISODE_CHANNEL = os.getenv("SLACK_EPISODE_CHANNEL", "C0B6Q1TDYCQ")
BACKGROUND_CHANNEL = os.getenv("SLACK_BACKGROUND_CHANNEL", "C0B5TGGB9J9")
REFERENCES_CHANNEL = os.getenv("SLACK_REFERENCES_CHANNEL", "C0B60EC81NX")
# PD 2026-06-17: 할머니·할아버지 에셋 업로드 채널. 여기 올린 사진/영상은 펫 에셋
# 라이브러리로 바로 ingest(= PHOTOS_CHANNEL과 같은 경로). 비-기술 사용자라 확인
# 메시지는 따뜻하고 단순하게(asset_id 같은 용어 금지).
GRANDMOMPAPA_CHANNEL = os.getenv("SLACK_GRANDMOMPAPA_CHANNEL", "C0BASN221UL")
# 할머니·할아버지용 따뜻한 확인 문구 (subject 태깅 요청을 쉬운 말로).
GRANDMA_THANKS = "💛 잘 받았어요! 무슨 영상이에요? 🐾"
# PD 2026-06-12: live agent channel — PD chats here and a headless Claude Code
# (`claude -p`) runs against the repo with full tools and replies (full perms).
BOARD_CHANNEL = os.getenv("SLACK_BOARD_CHANNEL")
BOARD_PD_USER = os.getenv("SLACK_PD_USER_ID", "U0B166M9C9F")

# Global stop flag — set by "중지/stop" command, checked by all background work
import threading
import time as _time
from collections import deque
_stop_flag = threading.Event()


# --- Socket-Mode wedge watchdog -------------------------------------------
# After a network/DNS blip, slack_bolt's Socket Mode can get permanently stuck:
# it keeps establishing new WebSocket sessions, but every send fails with
# [Errno 32] Broken pipe, and it never self-recovers. The process stays ALIVE
# (just looping), so launchd KeepAlive — which only restarts on exit — can't
# rescue it, and the bot goes silent until a manual restart. This watchdog
# detects the wedge (a burst of BrokenPipe errors in a short window) and hard-
# exits, letting launchd bring up a fresh, healthy process.
#
# Keyed specifically on BrokenPipe (not URLError/TimeoutError): those signal the
# network is genuinely down, where exiting would just cause a restart storm.
# BrokenPipe is the *post-recovery wedged* state — a transient drop produces
# only a handful and self-heals; the wedge produces them unboundedly.
# PD 2026-06-28: even 12/120s missed it — the wedge emits only ~1 BrokenPipe per
# reconnect cycle (~15-30s), so 12-in-120s was never reached and the bot stayed silent
# (하미 got no replies, twice). A HEALTHY bot emits ZERO BrokenPipes, and a process
# restart is cheap+harmless (fresh socket), so go aggressive: any 4 in 120s == wedged.
# A single transient drop self-heals in 1-2; 4+ means it is NOT recovering → restart.
_BROKENPIPE_WINDOW_S = 120      # rolling window
_BROKENPIPE_THRESHOLD = 4       # this many BrokenPipe within the window == wedged


class _WedgeWatchdog(logging.Handler):
    def __init__(self, pidfile: "Path | None" = None):
        super().__init__(level=logging.ERROR)
        self._hits: "deque[float]" = deque()
        self._lock = threading.Lock()
        self._pidfile = pidfile

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        if "BrokenPipeError" not in msg and "Broken pipe" not in msg:
            return
        now = _time.monotonic()
        with self._lock:
            self._hits.append(now)
            while self._hits and now - self._hits[0] > _BROKENPIPE_WINDOW_S:
                self._hits.popleft()
            n = len(self._hits)
        if n >= _BROKENPIPE_THRESHOLD:
            logging.critical(
                "Socket-Mode wedge detected: %d BrokenPipe errors in %ds — "
                "exiting so launchd restarts a fresh process.",
                n, _BROKENPIPE_WINDOW_S,
            )
            try:
                if self._pidfile is not None:
                    self._pidfile.unlink(missing_ok=True)
            except Exception:
                pass
            os._exit(1)
# Serialize board agent runs (one claude session at a time) + persist session id.
_board_lock = threading.Lock()
_BOARD_SESSION_FILE = ROOT / "data" / "board_session.txt"

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _event_dedup_key(event: dict) -> str:
    """Stable per-message id. client_msg_id is set on real user messages; fall back
    to channel:ts for events that lack it (edits, app messages)."""
    return event.get("client_msg_id") or f"{event.get('channel','')}:{event.get('ts','')}"


def _already_processed(event: dict) -> bool:
    """Idempotency guard for message events (returns True = skip).

    Slack redelivers any event we don't ack within ~3s (up to 3 retries) AND
    redelivers still-unacked events after a listener restart. Both make the SAME
    message get handled twice — PD saw the bot replay an old message + re-answer it.
    We record each event's stable id once and treat a repeat as a duplicate. DB-backed
    so it also survives a restart (which is exactly when the redelivery storm hits).
    The real retries are prevented upstream by acking fast (heavy work runs in a
    background thread); this guard is the belt-and-suspenders that makes handling
    idempotent regardless."""
    key = _event_dedup_key(event)
    if not key or key.endswith(":"):
        return False
    try:
        with db() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS slack_processed_events ("
                "  event_key TEXT PRIMARY KEY,"
                "  seen_at   TEXT DEFAULT (datetime('now')))")
            cur = con.execute(
                "INSERT OR IGNORE INTO slack_processed_events(event_key) VALUES (?)",
                (key,))
            con.commit()
            return cur.rowcount == 0  # 0 rows inserted → key already existed → dup
    except Exception as e:  # never block real handling on a dedup hiccup
        log.warning("dedup check failed (%s) — processing anyway", e)
        return False


def fmt_card_summary(row: sqlite3.Row) -> str:
    payload = json.loads(row["payload_json"])
    lane = payload.get("memory_lane")
    lane_str = f" / memory_lane={lane['variant']}" if lane else ""
    return (
        f"*{row['date']}* — `{row['card_id'][:8]}` "
        f"({row['card_type']}{lane_str}, tone={row['tone_primary']}/{row['tone_intensity']:.1f})\n"
        f"> {payload.get('narrative_oneliner', '(no oneliner)')}\n"
        f"state=`{row['state']}` ask_pd={'Y' if row['ask_pd'] else 'N'}"
    )


# ──────────────────────────────────────────────────────────────────────
# /writer-run
# ──────────────────────────────────────────────────────────────────────
@app.command("/writer_run")
def writer_run(ack, body, respond):
    ack()
    target_date = (body.get("text") or "").strip() or None
    # Hand off to the Writer agent runner — invoke as subprocess to keep Bolt thread tight.
    import subprocess
    cmd = ["python", "-m", "agents.writer"]
    if target_date:
        cmd += ["--date", target_date]
    log.info("writer-run: %s", cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if proc.returncode == 0:
        respond(f":white_check_mark: writer ran ok\n```{proc.stdout[-1200:]}```")
    else:
        respond(f":x: writer failed\n```{proc.stderr[-1200:]}```")


# ──────────────────────────────────────────────────────────────────────
# /writer-show
# ──────────────────────────────────────────────────────────────────────
@app.command("/writer_show")
def writer_show(ack, body, respond):
    ack()
    date = (body.get("text") or "").strip()
    sql = """
        SELECT * FROM cards
        WHERE (?='' OR date=?)
        ORDER BY date DESC, updated_at DESC
        LIMIT 5
    """
    with db() as con:
        rows = con.execute(sql, (date, date)).fetchall()
    if not rows:
        respond(f"_no cards found for_ `{date or 'recent'}`")
        return
    body_text = "\n\n".join(fmt_card_summary(r) for r in rows)
    respond(body_text)


# ──────────────────────────────────────────────────────────────────────
# /pd-approve, /pd-reject
# ──────────────────────────────────────────────────────────────────────
@app.command("/pd_approve")
def pd_approve(ack, body, respond):
    ack()
    card_id_prefix = (body.get("text") or "").strip()
    if not card_id_prefix:
        respond("usage: `/pd-approve <card_id_prefix>`")
        return
    with db() as con:
        row = con.execute(
            "SELECT card_id, state FROM cards WHERE card_id LIKE ? || '%' LIMIT 1",
            (card_id_prefix,),
        ).fetchone()
        if not row:
            respond(f":x: no card matching `{card_id_prefix}`")
            return
        if row["state"] not in ("draft", "pd_review"):
            respond(f":warning: card `{row['card_id'][:8]}` state is `{row['state']}` — already past review")
            return
        con.execute(
            "UPDATE cards SET state='approved', updated_at=datetime('now') WHERE card_id=?",
            (row["card_id"],),
        )
    respond(f":white_check_mark: approved `{row['card_id'][:8]}` — ready for `/post`")


@app.command("/pd_reject")
def pd_reject(ack, body, respond):
    ack()
    parts = (body.get("text") or "").strip().split(maxsplit=1)
    if not parts:
        respond("usage: `/pd-reject <card_id_prefix> <reason>`")
        return
    card_id_prefix = parts[0]
    reason = parts[1] if len(parts) > 1 else "(no reason given)"
    with db() as con:
        row = con.execute(
            "SELECT card_id FROM cards WHERE card_id LIKE ? || '%' LIMIT 1",
            (card_id_prefix,),
        ).fetchone()
        if not row:
            respond(f":x: no card matching `{card_id_prefix}`")
            return
        con.execute(
            "UPDATE cards SET state='rejected', ask_reason=?, updated_at=datetime('now') WHERE card_id=?",
            (reason, row["card_id"]),
        )
    respond(f":no_entry: rejected `{row['card_id'][:8]}` — {reason}")


# ──────────────────────────────────────────────────────────────────────
# /post
# ──────────────────────────────────────────────────────────────────────
@app.command("/post")
def post(ack, body, respond, client):
    ack()
    card_id_prefix = (body.get("text") or "").strip()
    if not card_id_prefix:
        respond("usage: `/post <card_id_prefix>`")
        return
    with db() as con:
        row = con.execute(
            "SELECT card_id, state FROM cards WHERE card_id LIKE ? || '%' LIMIT 1",
            (card_id_prefix,),
        ).fetchone()
        if not row:
            respond(f":x: no card matching `{card_id_prefix}`")
            return
        if row["state"] != "approved":
            respond(f":warning: card state is `{row['state']}` — must be `approved` before `/post`")
            return

    card_id = row["card_id"]
    respond(f":movie_camera: Starting Cameraman for `{card_id[:8]}`...")

    import threading

    def _render():
        _render_and_report(card_id, client)

    threading.Thread(target=_render, daemon=True).start()


def _render_and_report(card_id: str, client) -> None:
    from agents.cameraman import render_card

    def _progress(msg: str) -> None:
        try:
            client.chat_postMessage(channel=WORKROOM, text=msg)
        except Exception:
            log.warning("Failed to post progress: %s", msg)

    try:
        out = render_card(card_id, progress_cb=_progress)
        size_mb = out.stat().st_size / 1e6 if out.exists() else 0
        client.chat_postMessage(
            channel=WORKROOM,
            text=(
                f":white_check_mark: Rendered `{card_id[:8]}` → `{out.name}` ({size_mb:.1f} MB)\n"
                f"Use `/upload {card_id[:8]}` to publish to YouTube."
            ),
        )
    except Exception as e:
        log.exception("Cameraman failed for %s", card_id[:8])
        client.chat_postMessage(
            channel=WORKROOM,
            text=f":x: Cameraman failed for `{card_id[:8]}`:\n```{str(e)[:1200]}```",
        )


# ──────────────────────────────────────────────────────────────────────
# /upload
# ──────────────────────────────────────────────────────────────────────
@app.command("/upload")
def upload_cmd(ack, body, respond):
    ack()
    card_id_prefix = (body.get("text") or "").strip()
    if not card_id_prefix:
        respond("usage: `/upload <card_id_prefix>`")
        return
    with db() as con:
        row = con.execute(
            "SELECT card_id, state, output_video_path, payload_json FROM cards WHERE card_id LIKE ? || '%' LIMIT 1",
            (card_id_prefix,),
        ).fetchone()
        if not row:
            respond(f":x: no card matching `{card_id_prefix}`")
            return
        if row["state"] != "rendered":
            respond(f":warning: card state is `{row['state']}` — must be `rendered` before `/upload`")
            return
        if not row["output_video_path"]:
            respond(":x: no output video path — run `/post` first")
            return

    card_id = row["card_id"]
    video_path = row["output_video_path"]
    payload = json.loads(row["payload_json"])
    draft = payload.get("draft", {})

    respond(f":arrow_up: Uploading `{card_id[:8]}` to YouTube...")
    try:
        from youtube.upload import upload_short

        tags = draft.get("hashtags", [])
        # Strip '#' prefix from hashtags for YouTube tags
        tags = [t.lstrip("#") for t in tags]
        result = upload_short(
            video_path=video_path,
            title=draft.get("title", payload.get("theme", "Ryani & Leo")),
            description=draft.get("description", ""),
            tags=tags,
        )
        video_id = result.get("id", "unknown")
        with db() as con:
            # PD 2026-06-07: mark uploaded=1 (gates the arc + clip cooldown,
            # which only count UPLOADED episodes) and persist the YouTube
            # video_id (for analytics / upload-feedback-driven content later).
            cols = [r[1] for r in con.execute("PRAGMA table_info(cards)")]
            if "uploaded" not in cols:
                con.execute("ALTER TABLE cards ADD COLUMN uploaded INTEGER DEFAULT 0")
            if "youtube_video_id" not in cols:
                con.execute("ALTER TABLE cards ADD COLUMN youtube_video_id TEXT")
            con.execute(
                "UPDATE cards SET state='published', uploaded=1, "
                "youtube_video_id=?, updated_at=datetime('now') WHERE card_id=?",
                (video_id, card_id),
            )
        respond(
            f":white_check_mark: Uploaded `{card_id[:8]}` → `{video_id}`\n"
            f"https://youtube.com/shorts/{video_id}"
        )
    except Exception as e:
        log.exception("Upload failed for %s", card_id[:8])
        respond(f":x: Upload failed: {str(e)[:800]}")


# ──────────────────────────────────────────────────────────────────────
# /veto — 런칭 모드 취소 (PD 2026-06-07): 자동 발행된 회차를 내림.
# 기리 게이트 + PD 스팟체크 워크플로의 PD 개입 수단. 기본 = 비공개 전환
# (예약 publishAt 해제 → 공개 안 됨, 되돌릴 수 있음). `/veto <id> delete` = 완전 삭제.
# 인자: card_id prefix 또는 youtube_video_id.
# ──────────────────────────────────────────────────────────────────────
def _do_veto(vid: str, delete: bool = False) -> str:
    """Shared veto core (PD 2026-06-09): take down a scheduled/published video
    (private by default, since launch videos are scheduled publishAt) + revoke
    cards.uploaded so arc/cooldown drop it. Returns a status string."""
    from youtube.upload import veto_video
    try:
        action = veto_video(vid, delete=delete)
    except Exception as e:
        log.exception("veto failed for %s", vid)
        return f":x: veto 실패 ({vid}): {str(e)[:300]}"
    try:
        with db() as con:
            # state='archived' (the cards.state CHECK constraint does NOT allow
            # 'vetoed' — PD 2026-06-09 bug: the old 'vetoed' value silently failed
            # the UPDATE so uploaded stayed 1). uploaded=0 is the load-bearing flag
            # (arc/cooldown only count uploaded=1).
            _row = con.execute(
                "SELECT render_style, theme FROM cards WHERE youtube_video_id=?",
                (vid,)).fetchone()
            con.execute(
                "UPDATE cards SET uploaded=0, state='archived', "
                "updated_at=datetime('now') WHERE youtube_video_id=?", (vid,))
            try:
                con.execute("UPDATE launch_threads SET vetoed=1 WHERE video_id=?", (vid,))
            except Exception:
                pass
            # PD 2026-06-15: a veto is the strongest selection signal — record it so the
            # selectors (concept ranker / clip gate) learn what PD rejects. [[pd_taste]]
            try:
                from agents import pd_taste as _pt
                _lane = (_row[0] if _row else None) or "both"
                _title = (_row[1] if _row else "") or vid
                _pt.log_selection(con, lane=_lane, kind=_pt.K_CONCEPT, decision=_pt.VETO,
                                  subject=vid, rejected=_title,
                                  reason=f"PD veto: '{_title}' — 이런 회차는 내림(다음 선택에서 유사 회피).")
            except Exception as _e:
                log.warning("pd_taste veto log failed: %s", _e)
    except Exception as e:
        log.warning("veto db update failed for %s: %s", vid, e)
    return f":white_check_mark: veto 완료 — `{vid}` {action} (쿨다운/아크 회수됨)"


@app.command("/veto")
def veto_cmd(ack, body, respond):
    ack()
    parts = (body.get("text") or "").strip().split()
    if not parts:
        # No arg: list today's + tomorrow's launch videos so PD can /veto <id>.
        import datetime as _dt
        try:
            with db() as con:
                rows = con.execute(
                    "SELECT slot, lane, video_id, title FROM launch_threads "
                    "WHERE vetoed=0 AND target >= ? ORDER BY target, slot",
                    (_dt.date.today().isoformat(),)).fetchall()
        except Exception:
            rows = []
        if not rows:
            respond("usage: `/veto <youtube_video_id | card_id_prefix> [delete]`\n"
                    "_또는 각 영상 쓰레드에 `veto` 라고 답글._\n"
                    "_(취소할 예약 런칭 영상이 없어요.)_")
            return
        lines = [":no_entry: *취소 가능한 런칭 영상* — `/veto <video_id>` 또는 쓰레드에 `veto`"]
        for r in rows:
            lane_lbl = "AV" if r["lane"] == "ai_vtuber" else "RF"
            lines.append(f"  • {r['slot']} {lane_lbl} `{r['video_id']}` — "
                         f"{(r['title'] or '')[:40]}")
        respond("\n".join(lines))
        return
    ident = parts[0].strip("`<>")
    do_delete = len(parts) > 1 and parts[1].lower() in ("delete", "del", "삭제")
    with db() as con:
        row = con.execute(
            "SELECT card_id, youtube_video_id FROM cards "
            "WHERE youtube_video_id=? OR card_id LIKE ? || '%' "
            "ORDER BY updated_at DESC LIMIT 1",
            (ident, ident),
        ).fetchone()
    # Accept a raw youtube_video_id even if no card row matches (launch path).
    vid = (row["youtube_video_id"] if row and row["youtube_video_id"] else ident)
    if not vid:
        respond(f":x: `{ident}` 에 매칭되는 영상이 없어요")
        return
    respond(f":no_entry: veto → {vid} ({'삭제' if do_delete else '비공개 전환'})...")
    respond(_do_veto(vid, delete=do_delete))


# ──────────────────────────────────────────────────────────────────────
# /bgm-fix <video_id | card_id_prefix> [track.mp3] — 저작권 침해 복구 (PD 2026-06-24).
#   YouTube Content-ID가 한 회차의 메인 BGM을 클레임하면: 범퍼는 그대로 두고
#   메인 BGM만 안전한 다른 트랙으로 교체 → 클레임된 업로드 내리고 → 같은 일정으로
#   다시 업로드 → 카드 갱신. 클레임된 트랙은 원장에 기록돼 picker가 다시 안 고른다.
#   (재렌더 없음, 오디오만 재mux. 범퍼 공통 트랙은 한 영상만 걸릴 리 없으니 무죄.)
#   /bgm-fix                       → 사용법
#   /bgm-fix ptppVGHjltg           → 자동으로 안전한 트랙 골라 교체+재업로드
#   /bgm-fix e67a9d8b sonican-sweet-moments-232318.mp3  → 트랙 지정
# ──────────────────────────────────────────────────────────────────────
@app.command("/bgm-fix")
def bgm_fix_cmd(ack, body, respond):
    ack()
    parts = (body.get("text") or "").strip().split()
    if not parts:
        respond("usage: `/bgm-fix <youtube_video_id | card_id_prefix> [교체할_트랙.mp3]`\n"
                "_저작권 클레임된 회차의 BGM을 안전한 트랙으로 바꿔 같은 일정으로 다시 올려요._")
        return
    ident = parts[0].strip("`<>")
    track = parts[1].strip("`<>") if len(parts) > 1 else None
    respond(f":musical_note: BGM 교체 시작 → `{ident}` (백그라운드 처리 중, 잠시만요…)")

    def _work():
        import importlib
        try:
            sb = importlib.import_module("scripts.swap_bgm")
            res = sb.reupload(ident, new_bgm=track)
            respond(
                f":white_check_mark: BGM 교체 완료 — `{res['card_id'][:8]}`\n"
                f"  • 클레임된 BGM: `{res.get('claimed_bgm')}` (원장 등록 → 재선택 차단)\n"
                f"  • 새 BGM: `{res.get('new_bgm')}`\n"
                f"  • 새 영상: `{res.get('new_video_id')}` "
                f"(공개예정 {res.get('publish_at')})\n"
                f"  https://youtube.com/shorts/{res.get('new_video_id')}"
            )
        except Exception as e:  # noqa: BLE001
            log.exception("bgm-fix failed for %s", ident)
            respond(f":x: BGM 교체 실패 ({ident}): {str(e)[:400]}")

    import threading
    threading.Thread(target=_work, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# /bgm-claim <video_id...> — Content-ID 클레임 원장 동기화 (PD 2026-06-27).
#   YouTube Data API는 일반(비-MCN) 채널의 Content-ID 클레임을 노출하지 않는다
#   (youtubePartner는 403). 그래서 PD가 Studio "콘텐츠→저작권"에서 클레임된
#   영상 id를 보면 여기로 넘겨주면, 그 회차의 메인 BGM을 원장(bgm_claimed.json)에
#   기록 → picker가 다시 안 고른다. (영상은 건드리지 않음. 교체+재업로드는 /bgm-fix.)
#   인자 없이 호출하면 --auto 스캔(클레임으로 차단된 업로드만 자동 탐지)도 같이 돈다.
#   /bgm-claim                     → auto 스캔만
#   /bgm-claim QlgbeyqkpDI jfyqT-7SqAU  → 해당 영상들 BGM 원장 등록(+auto 스캔)
# ──────────────────────────────────────────────────────────────────────
@app.command("/bgm-claim")
def bgm_claim_cmd(ack, body, respond):
    ack()
    vids = [p.strip("`<>") for p in (body.get("text") or "").strip().split() if p.strip()]
    respond(":mag: BGM 클레임 동기화 중… (영상은 안 건드리고 원장만 기록)")

    def _work():
        import importlib
        try:
            sc = importlib.import_module("scripts.sync_bgm_claims")
            auto = sc.auto_detect_blocked()
            for v in auto:
                if v not in vids:
                    vids.append(v)
            if not vids:
                respond(":information_source: 클레임으로 *차단된* 업로드 없음. "
                        "공개 상태로 남은 false-AdRev 클레임은 API로 안 보이니 "
                        "Studio에서 본 video_id를 `/bgm-claim <id>`로 넘겨주세요.")
                return
            res = sc.sync_claims_from_videos(vids)
            lines = [f":white_check_mark: BGM 클레임 동기화 완료 (원장 {res['claimed_total']}곡)"]
            if auto:
                lines.append(f"  • auto 차단탐지: `{auto}`")
            if res["newly_claimed"]:
                lines.append(f"  • 새로 등록(재선택 차단): `{res['newly_claimed']}`")
            else:
                lines.append("  • 새로 등록된 트랙 없음 (이미 원장에 있거나 중복)")
            if res["unresolved"]:
                lines.append(f"  • :warning: BGM 미해결: `{res['unresolved']}` "
                             "(과거 업로드라 트랙 기록이 없음 — 트랙명을 알려주면 직접 등록)")
            respond("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            log.exception("bgm-claim failed")
            respond(f":x: BGM 클레임 동기화 실패: {str(e)[:400]}")

    import threading
    threading.Thread(target=_work, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# /thumb <video_id | card_id_prefix> — 채널 썸네일 재선택 (PD 2026-06-24). 업로드 시
#   best 프레임이 자동 설정되지만, 마음에 안 들면 이걸로 다시 골라 적용. (채널 전화인증
#   필요 — 미인증이면 YouTube가 403.)
# ──────────────────────────────────────────────────────────────────────
@app.command("/thumb")
def thumb_cmd(ack, body, respond):
    ack()
    ident = (body.get("text") or "").strip().split()[0:1]
    if not ident:
        respond("usage: `/thumb <youtube_video_id | card_id_prefix>`")
        return
    ident = ident[0].strip("`<>")
    with db() as con:
        row = con.execute(
            "SELECT card_id, youtube_video_id, output_video_path, theme FROM cards "
            "WHERE youtube_video_id=? OR card_id LIKE ? || '%' ORDER BY updated_at DESC LIMIT 1",
            (ident, ident)).fetchone()
    if not row or not row["youtube_video_id"] or not row["output_video_path"]:
        respond(f":x: `{ident}` — 영상/파일을 못 찾았어요")
        return
    vid, vpath, theme = row["youtube_video_id"], row["output_video_path"], row["theme"]
    respond(f":frame_with_picture: 썸네일 재선택 중 → `{vid}` …")

    def _work():
        try:
            import scripts.pick_thumbnail as _pt
            from youtube.upload import set_thumbnail
            from pathlib import Path as _P
            thumb = _P(vpath).with_suffix(".thumb.jpg")
            pk = _pt.make_thumbnail(vpath, thumb, concept={"theme": theme})
            set_thumbnail(vid, thumb)
            respond(f":white_check_mark: 썸네일 설정 완료 — {pk.get('reason','')[:80]}\n"
                    f"https://youtube.com/shorts/{vid}")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "thumbnail" in msg and "permission" in msg.lower() or "403" in msg:
                respond(":x: 채널이 커스텀 썸네일 권한이 없어요 — youtube.com/verify 에서 "
                        "전화 인증 1회 하면 풀려요.")
            else:
                respond(f":x: 썸네일 실패: {msg[:300]}")

    import threading
    threading.Thread(target=_work, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# /trend — 시의성 훅 (PD 2026-06-24). 캘린더(절기·메가이벤트)는 자동, 여기선 PD가
#   본 유행 밈/챌린지를 즉석 추가. 컨셉 브레인스토밍이 이걸 읽어 시의성 회차를 만든다.
#   /trend                      → 지금 활성 훅 목록
#   /trend 주술회전 소환 챌린지   → 밈으로 추가(기본 14일)
# ──────────────────────────────────────────────────────────────────────
@app.command("/trend")
def trend_cmd(ack, body, respond):
    ack()
    import datetime as _dt
    import scripts.trend_feed as tf
    text = (body.get("text") or "").strip()
    con = tf._conn()
    today = _dt.date.today()
    if not text:
        rows = con.execute(
            "SELECT category, title, fit_score, expiry_date, source FROM trends "
            "WHERE expiry_date IS NULL OR expiry_date >= ? ORDER BY fit_score DESC LIMIT 12",
            (today.isoformat(),)).fetchall()
        con.close()
        if not rows:
            respond("활성 시의성 훅이 없어요. `/trend <유행 밈/챌린지>` 로 추가하거나 "
                    "런치 배치가 자동 갱신해요.")
            return
        lines = [":satellite: *지금 활성 시의성 훅* (브레인스토밍이 이걸 탑니다)"]
        for c, ti, f, ex, src in rows:
            lines.append(f"  • [{src}/{c}] {f:.2f}  {ti}  _(만료 {ex})_")
        lines.append("\n_추가:_ `/trend <유행 밈/챌린지 한 줄>`")
        respond("\n".join(lines))
        return
    import hashlib as _h
    tid = "manual_" + _h.sha1(text.encode()).hexdigest()[:10]
    tf._upsert(con, tid, "manual", "meme", text, 0.78,
               (today + _dt.timedelta(days=14)).isoformat(), {"why": "PD 수동 추가"})
    con.commit(); con.close()
    respond(f":white_check_mark: 시의성 훅 추가 — `{text}` (14일간 브레인스토밍에 반영)")


# ──────────────────────────────────────────────────────────────────────
# /concept <YYYY-MM-DD> <내용> — 특정 날짜 컨셉 예약 (PD 2026-06-07).
#   그 날 제작 시 PD가 적은 내용을 최우선 방향으로 컨셉을 만든다.
#   /concept                    → 다가오는 예약 목록
#   /concept 2026-06-15 비오는날 창밖 보는 레오 + 랴니 실내놀이
# ──────────────────────────────────────────────────────────────────────
@app.command("/concept")
def concept_cmd(ack, body, respond):
    ack()
    import datetime as _dt
    from agents import arc
    text = (body.get("text") or "").strip()
    if not text:
        with db() as con:
            rows = arc.list_concept_directives(con, _dt.date.today().isoformat())
        if not rows:
            respond("예약된 컨셉 없음.\nusage: `/concept <YYYY-MM-DD> <내용>`")
            return
        lines = [":calendar: *예약된 컨셉*"]
        for r in rows:
            lines.append(f"  • `{r['target_date']}` — {r['directive'][:120]}")
        respond("\n".join(lines)); return
    parts = text.split(maxsplit=1)
    try:
        d = _dt.date.fromisoformat(parts[0])
    except ValueError:
        respond("날짜 형식은 `YYYY-MM-DD` 예요. 예: `/concept 2026-06-15 비오는날 컨셉`")
        return
    if len(parts) < 2 or not parts[1].strip():
        respond("내용을 적어주세요. 예: `/concept 2026-06-15 비오는날 창밖 레오 + 랴니 실내놀이`")
        return
    directive = parts[1].strip()
    try:
        with db() as con:
            arc.set_concept_directive(con, d.isoformat(), directive)
        warn = "" if d >= _dt.date.today() else " :warning: (과거 날짜)"
        respond(f":white_check_mark: `{d.isoformat()}` 컨셉 예약됨{warn}\n  → {directive}\n"
                f"_그날 4슬롯 모두 이 방향을 최우선으로 제작합니다._")
    except Exception as e:
        log.exception("concept cmd failed")
        respond(f":x: concept 저장 실패: {str(e)[:300]}")


# ──────────────────────────────────────────────────────────────────────
# /knowledge — 컨셉 생성이 PD에게 물은 '모르는 캐릭터/세계 사실' 대기열 (PD 2026-06-07).
#   /knowledge          → 대기 질문 + 저장된 사실 보기
#   /answer <id> <답>    → 그 질문에 답 저장(영구 — 다음 컨셉부터 반영, 다시 안 물음)
# ──────────────────────────────────────────────────────────────────────
@app.command("/knowledge")
def knowledge_cmd(ack, body, respond):
    ack()
    try:
        from agents import knowledge as kn
        con = kn._db()
        pend = kn.pending_questions(con)
        lines = []
        if pend:
            lines.append(":grey_question: *대기 중 질문* (`/answer <id> <답>`)")
            for q in pend:
                sub = f"[{q['subject']}] " if q["subject"] else ""
                lines.append(f"  • `{q['id']}` {sub}{q['question']}")
        else:
            lines.append(":white_check_mark: 대기 중 질문 없음")
        fb = kn.facts_block(con)
        if fb:
            lines.append("\n" + fb)
        respond("\n".join(lines))
    except Exception as e:
        log.exception("knowledge cmd failed")
        respond(f":x: knowledge 실패: {str(e)[:500]}")


@app.command("/answer")
def answer_cmd(ack, body, respond):
    ack()
    parts = (body.get("text") or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        respond("usage: `/answer <id> <답변>`  (id는 `/knowledge`에서 확인)")
        return
    qid, fact = parts[0], parts[1]
    try:
        from agents import knowledge as kn
        con = kn._db()
        row = con.execute("SELECT question, subject FROM character_facts WHERE id=?",
                          (qid,)).fetchone()
        if not row:
            respond(f":x: id `{qid}` 질문 없음 — `/knowledge`로 확인")
            return
        kn.add_answer(con, row["question"], fact, subject=row["subject"] or "")
        respond(f":white_check_mark: 저장됨 — \"{row['question']}\" → {fact}\n"
                f"_다음 컨셉부터 반영되고 다시 묻지 않습니다._")
    except Exception as e:
        log.exception("answer cmd failed")
        respond(f":x: answer 실패: {str(e)[:500]}")


# ──────────────────────────────────────────────────────────────────────
# /bandit — av-vs-rf A/B 현황 (PD 2026-06-07). `/bandit` = 리포트,
# `/bandit collect` = 48h 지표 수집 후 리포트, `/bandit choose` = 다음 레인/시각 추천.
# ──────────────────────────────────────────────────────────────────────
@app.command("/bandit")
def bandit_cmd(ack, body, respond):
    ack()
    arg = (body.get("text") or "").strip().lower()
    try:
        from agents import bandit as B
        if arg.startswith("collect"):
            got = B.collect()
            respond(f":arrows_counterclockwise: {len(got)}편 지표 수집/갱신\n\n" + B.report())
        elif arg.startswith("choose"):
            respond(f":game_die: 다음 추천\n  lane → *{B.choose_lane()}*\n"
                    f"  timeslot → *{B.choose_timeslot()}*\n\n" + B.report())
        else:
            respond(B.report())
    except Exception as e:
        log.exception("bandit cmd failed")
        respond(f":x: bandit 실패: {str(e)[:600]}")


# ──────────────────────────────────────────────────────────────────────
# /sync — iCloud 수동 싱크
# ──────────────────────────────────────────────────────────────────────
@app.command("/sync")
def sync_cmd(ack, body, respond, client):
    ack()
    respond(":arrows_counterclockwise: iCloud 싱크 시작...")

    import threading as _th

    def _run():
        channel = body["channel_id"]
        try:
            import subprocess
            proc = subprocess.run(
                [str(ROOT / ".venv" / "bin" / "python"), "-m", "icloud.sync", "--album", "Ryani & Leo"],
                capture_output=True, text=True, cwd=str(ROOT), timeout=300,
            )
            output = proc.stdout[-500:] if proc.stdout else ""
            if proc.returncode == 0:
                client.chat_postMessage(channel=channel, text=f":white_check_mark: iCloud 싱크 완료\n```{output}```")
            else:
                client.chat_postMessage(channel=channel, text=f":x: 싱크 실패\n```{proc.stderr[-500:]}```")
        except Exception as e:
            client.chat_postMessage(channel=channel, text=f":x: 싱크 실패: {str(e)[:200]}")

    _th.Thread(target=_run, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# /test — 진단 테스트 (상세 로그)
# ──────────────────────────────────────────────────────────────────────
@app.command("/test")
def test_cmd(ack, body, respond, client):
    ack()
    channel = body["channel_id"]

    # Post initial message — this becomes the thread parent
    resp = client.chat_postMessage(
        channel=channel,
        text=":test_tube: *테스트 시작* (컨펌 없이 자동 진행)",
    )
    thread_ts = resp["ts"]

    import threading as _th

    def _run():
        # All messages go into this thread
        _handle_test({"channel": channel}, client, thread_ts)

        # After diagnostics, run daily pipeline WITHOUT PD confirmation
        try:
            import datetime as _dt
            from zoneinfo import ZoneInfo as _ZI
            from agents.producer import daily_pipeline

            target = (_dt.datetime.now(_ZI("Asia/Seoul")) + _dt.timedelta(days=1)).date()

            def _progress(msg):
                if _stop_flag.is_set():
                    raise InterruptedError("중지")
                client.chat_postMessage(channel=channel, text=msg, thread_ts=thread_ts)

            def _on_thread(ts):
                pass  # ignore — we use our own thread_ts

            # PD 2026-06-07: the RESULT VIDEO must be posted to the thread
            # (regressed to filenames-only). Upload each rendered mp4.
            def _video(path):
                try:
                    client.files_upload_v2(
                        channel=channel, thread_ts=thread_ts,
                        file=str(path), title=Path(path).name,
                        initial_comment=f":movie_camera: {Path(path).name}",
                    )
                except Exception as e:
                    log.warning("video upload failed: %s", e)
                    client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                        text=f":warning: 영상 업로드 실패: {Path(path).name} ({str(e)[:80]})")

            # PD 2026-06-06: /test runs BOTH lanes (ai_vtuber + real_footage).
            # Optional arg narrows it: "/test rf" or "/test ai".
            text_arg = (body.get("text") or "").strip().lower()
            if text_arg in ("rf", "real_footage", "real"):
                style_filter = "real_footage"
            elif text_arg in ("ai", "ai_vtuber", "vtuber"):
                style_filter = "ai_vtuber"
            else:
                style_filter = None  # both lanes
            lane_label = style_filter or "ai_vtuber + real_footage"
            client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                text=f":rocket: 테스트 렌더 시작 — {target.isoformat()} "
                     f"({lane_label}, PD 컨펌 스킵)")

            daily_pipeline(target, timeout_sec=0, progress_cb=_progress,
                           on_thread_created=_on_thread, video_cb=_video,
                           style_filter=style_filter)

        except InterruptedError:
            client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                text=":octagonal_sign: 테스트 중단됨")
        except Exception as e:
            log.exception("test pipeline failed")
            client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                text=f":x: 테스트 실패:\n```{str(e)[:800]}```")

    _th.Thread(target=_run, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# /daily — 일일 2편 파이프라인
# ──────────────────────────────────────────────────────────────────────
@app.command("/daily")
def daily_cmd(ack, body, respond, client):
    ack()
    text = (body.get("text") or "").strip()
    # Parse optional date and flags
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    target = None
    dry_run = False
    for part in text.split():
        if part == "dry":
            dry_run = True
        else:
            try:
                target = _dt.date.fromisoformat(part)
            except ValueError:
                pass
    if not target:
        target = (_dt.datetime.now(_ZI("Asia/Seoul")) + _dt.timedelta(days=1)).date()

    respond(f":calendar: 일일 파이프라인 시작 — {target.isoformat()}" + (" (dry-run)" if dry_run else ""))

    import threading

    def _run():
        from agents.producer import daily_pipeline

        # First message goes to channel; after proposal is posted, switch to thread
        thread_ref = [None]  # mutable container for closure

        def _progress(msg):
            if _stop_flag.is_set():
                raise InterruptedError("중지 명령으로 중단됨")
            try:
                if thread_ref[0]:
                    client.chat_postMessage(channel=WORKROOM, text=msg, thread_ts=thread_ref[0])
                else:
                    client.chat_postMessage(channel=WORKROOM, text=msg)
            except Exception:
                pass

        def _on_thread(ts):
            thread_ref[0] = ts

        def _video(path):
            # PD 2026-06-07: post the rendered mp4 into the proposal thread.
            try:
                client.files_upload_v2(
                    channel=WORKROOM, thread_ts=thread_ref[0],
                    file=str(path), title=Path(path).name,
                    initial_comment=f":movie_camera: {Path(path).name}")
            except Exception as e:
                log.warning("video upload failed: %s", e)

        try:
            daily_pipeline(target, progress_cb=_progress,
                           on_thread_created=_on_thread, video_cb=_video,
                           dry_run=dry_run)
        except Exception as e:
            log.exception("daily pipeline failed")
            kwargs = {"channel": WORKROOM, "text": f":x: 일일 파이프라인 실패:\n```{str(e)[:1200]}```"}
            if thread_ref[0]:
                kwargs["thread_ts"] = thread_ref[0]
            client.chat_postMessage(**kwargs)

    threading.Thread(target=_run, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# /launch — 런칭 모드 4슬롯 (PD 2026-06-07): 하루 4편(2 AV + 2 RF) 라틴스퀘어.
# 기리 통과분만 슬롯 시각에 예약 발행, 4편 모두 스레드 포스팅 → PD는 `/veto`로만 개입.
# `/launch [YYYY-MM-DD] [dry] [noupload]`
# ──────────────────────────────────────────────────────────────────────
@app.command("/launch")
def launch_cmd(ack, body, respond, client):
    ack()
    text = (body.get("text") or "").strip()
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    target = None
    dry_run = False
    do_upload = True
    for part in text.split():
        if part == "dry":
            dry_run = True
        elif part in ("noupload", "no-upload"):
            do_upload = False
        else:
            try:
                target = _dt.date.fromisoformat(part)
            except ValueError:
                pass
    if not target:
        # PD 2026-06-07: default to TOMORROW, consistent with /daily and /test
        # (production runs ahead of publish; gives a veto buffer).
        target = (_dt.datetime.now(_ZI("Asia/Seoul")) + _dt.timedelta(days=1)).date()

    respond(f":rocket: 런칭 4슬롯 시작 — {target.isoformat()}"
            + (" (dry-run)" if dry_run else "")
            + ("" if do_upload else " (no-upload)"))

    import threading

    def _run():
        from agents.launch import launch_pipeline
        # PD 2026-06-09: per-slot threads (4 threads/day) — launch_pipeline opens
        # each slot's own thread via slack_client/slack_channel. Day-level progress
        # posts to the channel (un-threaded summary).
        def _progress(msg):
            if _stop_flag.is_set():
                raise InterruptedError("중지 명령으로 중단됨")
            try:
                client.chat_postMessage(channel=WORKROOM, text=msg)
            except Exception:
                pass

        try:
            results = launch_pipeline(target, progress_cb=_progress,
                                      video_cb=None, do_upload=do_upload,
                                      dry_run=dry_run, ask_cb=None,
                                      slack_client=client, slack_channel=WORKROOM)
            # day-level summary in the channel, with per-video veto hints
            lines = [":checkered_flag: *런칭 요약* — 취소는 각 영상 쓰레드에 `veto` "
                     "(또는 `/veto <video_id>`)"]
            for r in results:
                vid = r.get("video_id")
                lane_lbl = "AV" if r.get("lane") == "ai_vtuber" else "RF"
                tag = (f"<https://youtube.com/shorts/{vid}|{vid}>" if vid
                       else "_미업로드_")
                lines.append(f"  • {r['slot']} {lane_lbl} → {tag}")
            client.chat_postMessage(channel=WORKROOM, text="\n".join(lines))
        except Exception as e:
            log.exception("launch pipeline failed")
            client.chat_postMessage(
                channel=WORKROOM,
                text=f":x: 런칭 파이프라인 실패:\n```{str(e)[:1200]}```")

    threading.Thread(target=_run, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# /status
# ──────────────────────────────────────────────────────────────────────
@app.command("/bot_status")
def status(ack, body, respond):
    ack()
    with db() as con:
        counts = dict(con.execute(
            "SELECT state, count(*) FROM cards GROUP BY state"
        ).fetchall())
        recent = con.execute(
            "SELECT date, card_id, card_type, state FROM cards ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        runs = con.execute(
            "SELECT agent, status, count(*) FROM runs WHERE started_at > datetime('now', '-7 days') GROUP BY agent, status"
        ).fetchall()
    body_text = "*Pipeline status*\n"
    body_text += "_cards by state:_ " + ", ".join(f"{k}={v}" for k, v in counts.items()) + "\n"
    body_text += "_recent (5):_\n" + "\n".join(
        f"• `{r['card_id'][:8]}` {r['date']} {r['card_type']} → {r['state']}" for r in recent
    )
    if runs:
        body_text += "\n_runs (7d):_ " + ", ".join(f"{a}/{s}={c}" for a, s, c in runs)
    respond(body_text)


# ──────────────────────────────────────────────────────────────────────
# Photo ingestion — file_shared event
# ──────────────────────────────────────────────────────────────────────
import datetime as dt
import hashlib
import re
import urllib.request

PHOTOS_DIR = ROOT / "data" / "assets" / "photos"
CLIPS_DIR = ROOT / "data" / "assets" / "clips"


def _ingest_file(file_info: dict, client) -> dict | None:
    """Download a Slack file, extract metadata, insert into assets DB.
    Returns the asset dict or None on skip/error."""
    mimetype = file_info.get("mimetype", "")
    filename = file_info.get("name", "unknown")

    if mimetype.startswith("image/"):
        kind = "photo"
    elif mimetype.startswith("video/"):
        kind = "video"
    else:
        return None

    # Generate asset_id
    ts_epoch = file_info.get("created", 0)
    ts = dt.datetime.fromtimestamp(ts_epoch, tz=dt.timezone.utc)
    ts_str = ts.strftime("%Y_%m_%d_%H%M%S")
    file_hash = hashlib.sha256(file_info["id"].encode()).hexdigest()[:8]
    asset_id = f"med_{ts_str}_slack_{file_hash}"

    # Check if already ingested
    with db() as con:
        existing = con.execute(
            "SELECT asset_id, file_path, kind FROM assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if existing:
            # A re-post of the same file is NOT an error — return the existing asset so
            # the caller still proceeds. (The grandmompapa bot was going SILENT on
            # duplicate uploads because this returned None → n_ok=0 → reply gate skipped.)
            return {"asset_id": existing[0], "kind": existing[2] or kind,
                    "file_path": existing[1], "width": None, "height": None,
                    "duration_sec": None, "already_ingested": True}

    # Destination path
    year = ts.strftime("%Y")
    ext = Path(filename).suffix.lower() or (".jpg" if kind == "photo" else ".mp4")
    if kind == "photo":
        dest_dir = PHOTOS_DIR / year
    else:
        dest_dir = CLIPS_DIR / year
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{asset_id}{ext}"

    # Download
    url = file_info.get("url_private_download") or file_info.get("url_private")
    if not url:
        log.warning("No download URL for file %s", file_info["id"])
        return None

    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"
    })
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
        f.write(resp.read())

    # Extract metadata
    width, height, duration = None, None, None
    phash = None
    try:
        if kind == "photo":
            from PIL import Image
            img = Image.open(dest)
            width, height = img.size
            try:
                import imagehash
                phash = str(imagehash.phash(img))
            except ImportError:
                pass
        elif kind == "video":
            import subprocess
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-show_entries", "format=duration",
                 "-of", "json", str(dest)],
                capture_output=True, text=True,
            )
            if probe.returncode == 0:
                info = json.loads(probe.stdout)
                streams = info.get("streams", [{}])
                if streams:
                    width = streams[0].get("width")
                    height = streams[0].get("height")
                fmt = info.get("format", {})
                dur = fmt.get("duration")
                if dur:
                    duration = float(dur)
    except Exception as e:
        log.warning("Metadata extraction failed for %s: %s", asset_id, e)

    # Insert into DB. Use INSERT OR IGNORE + tolerate IntegrityError: the slack_sync
    # cron can ingest the same file in the gap between the existing-check above and
    # this insert (the "UNIQUE constraint failed: assets.asset_id" seen in logs).
    # That race must NOT make the caller think ingest failed — the file is on disk and
    # in the DB either way, so we still return the asset (else grandma goes silent).
    captured_iso = ts.isoformat()
    try:
        with db() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO assets
                    (asset_id, source, kind, file_path, captured_iso,
                     duration_sec, width, height, phash, subjects_csv)
                VALUES (?, 'slack', ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (asset_id, kind, str(dest), captured_iso,
                 duration, width, height, phash),
            )
    except sqlite3.IntegrityError as e:
        log.info("asset %s already present (concurrent ingest race) — reusing: %s",
                 asset_id, e)

    return {
        "asset_id": asset_id, "kind": kind, "file_path": str(dest),
        "width": width, "height": height, "duration_sec": duration,
    }


def _handle_background_upload(event: dict, client, file_id: str) -> None:
    """Download background reference image and save to DB."""
    try:
        file_info = client.files_info(file=file_id)["file"]
        mimetype = file_info.get("mimetype", "")
        if not mimetype.startswith("image/"):
            return

        filename = file_info.get("name", "bg.jpg")
        url = file_info.get("url_private_download") or file_info.get("url_private")
        if not url:
            return

        # Download
        bg_dir = ROOT / "assets" / "backgrounds"
        bg_dir.mkdir(parents=True, exist_ok=True)

        import hashlib
        ts = file_info.get("created", 0)
        h = hashlib.sha256(file_id.encode()).hexdigest()[:8]
        ext = Path(filename).suffix.lower() or ".jpg"
        dest = bg_dir / f"bg_{h}{ext}"

        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"
        })
        with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
            f.write(resp.read())

        # Get description from file title/comment
        desc = file_info.get("title", "") or file_info.get("initial_comment", {}).get("comment", "") or filename

        with db() as con:
            con.execute(
                "INSERT INTO background_refs (file_path, space_name, description, slack_ts) VALUES (?, ?, ?, ?)",
                (str(dest), desc, desc, event.get("event_ts", "")),
            )

        try:
            ch = event.get("channel_id") or event.get("channel", "")
            ts = event.get("ts") or event.get("event_ts", "")
            if ch and ts:
                client.reactions_add(channel=ch, timestamp=ts, name="art")
        except Exception:
            pass
        log.info("Background ref saved: %s → %s", desc, dest)

    except Exception as e:
        log.warning("Background upload failed: %s", e)


def _handle_reference_upload(event: dict, client, file_id: str, message_text: str) -> None:
    """Download object reference photo and save to DB with description.

    PD uploads a photo (toy, basket, chives, etc.) to #references channel
    with a text description. This becomes available to Producer for accurate
    veo_prompt writing.
    """
    try:
        file_info = client.files_info(file=file_id)["file"]
        mimetype = file_info.get("mimetype", "")
        if not mimetype.startswith("image/"):
            return

        filename = file_info.get("name", "ref.jpg")
        url = file_info.get("url_private_download") or file_info.get("url_private")
        if not url:
            return

        # Download
        ref_dir = ROOT / "assets" / "references"
        ref_dir.mkdir(parents=True, exist_ok=True)

        import hashlib
        h = hashlib.sha256(file_id.encode()).hexdigest()[:8]
        ext = Path(filename).suffix.lower() or ".jpg"
        dest = ref_dir / f"ref_{h}{ext}"

        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"
        })
        with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
            f.write(resp.read())

        # Parse description from message text or file title
        desc = message_text.strip() if message_text.strip() else (
            file_info.get("title", "") or filename
        )
        # Extract short name (first line or first ~30 chars)
        name = desc.split("\n")[0][:50].strip() or filename

        # Auto-detect category from keywords
        cat = "object"
        cat_keywords = {
            "toy": ["장난감", "toy", "낚싯대", "쥐", "mouse", "폼폼"],
            "food": ["부추", "간식", "사료", "밥", "풀", "chive", "treat", "food", "나물"],
            "furniture": ["소파", "침대", "캣타워", "sofa", "bed", "chair", "의자"],
            "clothing": ["하네스", "옷", "harness", "collar", "목줄", "넥카라"],
        }
        desc_lower = desc.lower()
        for c, kws in cat_keywords.items():
            if any(kw in desc_lower for kw in kws):
                cat = c
                break

        # Auto-detect related subjects
        subjects = None
        if "레오" in desc or "leo" in desc_lower:
            subjects = "leo"
        if "랴니" in desc or "ryani" in desc_lower:
            subjects = "both" if subjects == "leo" else "ryani"

        with db() as con:
            con.execute(
                "INSERT INTO object_refs (file_path, name, description, category, subjects, slack_ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(dest), name, desc, cat, subjects, event.get("event_ts", "")),
            )

        # React with camera emoji
        try:
            ch = event.get("channel_id") or event.get("channel", "")
            ts = event.get("ts") or event.get("event_ts", "")
            if ch and ts:
                client.reactions_add(channel=ch, timestamp=ts, name="camera_with_flash")
        except Exception:
            pass
        log.info("Reference saved: %s [%s] → %s", name, cat, dest)

    except Exception as e:
        log.warning("Reference upload failed: %s", e)


def _grandma_history(client, channel, limit=28):
    """Recent channel dialogue for CONTINUOUS conversation (PD 2026-06-24: the bot must
    keep chatting with context, video-related or not — not one-shot replies). Returns a
    list of {role, text} oldest→newest, excluding the just-arrived message."""
    try:
        resp = client.conversations_history(channel=channel, limit=limit + 1)
        msgs = list(reversed(resp.get("messages", [])))  # oldest → newest
    except Exception:
        return []
    out = []
    for m in msgs:
        if m.get("subtype") in ("channel_join", "channel_leave"):
            continue
        t = (m.get("text") or "").strip()
        if not t:
            continue
        out.append({"role": "bot" if m.get("bot_id") else "family", "text": t[:300]})
    return out[-limit:]


def _grandma_converse(client, channel, user, text, thread_ts, asset_id=None):
    """할머니·할아버지와 '계속' 대화하는 따뜻한 가족 비서 (PD 2026-06-24): 영상 얘기든 일상
    잡담이든 이전 대화 맥락을 이어 자연스럽게 답한다. 대화 중 나온 펫 일화·영상 아이디어는
    컨셉 소재로 저장 — 일화/설명 → episode_stories(브레인스토밍 재료), '영상 만들어줘' 요청 →
    [요청] 태그(우선 가중), 함께 올린 에셋이 있으면 설명을 그 에셋 notes에 보강."""
    text = (text or "").strip()
    if not text:
        return
    reply, intent, summary, concept = "", "chat", text[:80], ""
    try:
        from agents.llm_cascade import call_text_cascade
        import json as _json, re as _re
        hist = _grandma_history(client, channel)
        convo = "\n".join(
            ("나(비서): " if h["role"] == "bot" else "가족: ") + h["text"] for h in hist)
        import datetime as _dt
        try:
            from zoneinfo import ZoneInfo
            _now = _dt.datetime.now(ZoneInfo("Asia/Seoul"))
        except Exception:
            _now = _dt.datetime.now()
        _h = _now.hour
        _part = ("새벽" if _h < 6 else "이른 아침" if _h < 8 else "아침" if _h < 11
                 else "점심때" if _h < 14 else "오후" if _h < 18 else "저녁" if _h < 21
                 else "밤")
        now_label = _now.strftime("%Y년 %m월 %d일 %H시 %M분") + f" ({_part})"
        sys_p = (
            "너는 'Ryani(랴니=강아지, 꼬리 없음)와 Leo(레오=고양이)' 펫 숏츠 채널의 따뜻한 "
            "가족 비서다. 할머니·할아버지와 '계속' 대화한다 — 펫 영상 얘기든, 안부·날씨·일상 "
            "잡담이든 무엇이든 끊지 말고 다정하게 받아준다. 아래 '최근 대화'의 맥락을 이어서 "
            "자연스럽게 답하라(반복 금지, 이전에 한 질문 또 묻지 말 것). "
            "★맥락 기억: 최근 대화를 정확히 기억하고 이어가라. 가족이 앞서 말한 사실(펫 상황, 일정, "
            "기분, 사건 등)을 반영하고, 방금 한 얘기를 까먹거나 모순되는 답을 하지 마라. 가족의 새 "
            "메시지가 짧거나 모호하면 바로 앞 맥락에 비추어 해석하라(예: 내 질문에 대한 대답으로). "
            "대화 중 펫 일화나 "
            "'이런 영상 만들면 좋겠다'는 아이디어가 나오면 컨셉 소재로 기억한다.\n"
            "★중요: 메시지에 이미 적힌 정보(누가/무엇을/어떤 상황인지)는 절대 다시 묻지 마라. "
            "사진/영상에 설명이 함께 왔으면 '설명 적어달라'고 하지 말고 그 내용을 이해했다고 "
            "확인하라. 단 이미 적힌 걸 되묻는 게 아니라 '대화를 이어가는' 새 질문(오늘 하루·다음 "
            "이야기 등)은 자주 던져 어르신이 계속 답하시게 하라.\n"
            "★영상/사진을 직접 보지 못한다: 너에게는 가족이 '글로 적어준 설명'만 전달된다. 영상·사진의 "
            "실제 장면은 볼 수 없다. 그러니 절대 '영상에서 봤어요/잘 봤어요' 같이 본 척하지 말고, 영상 속 "
            "장면을 멋대로 묘사하거나, 영상에 이미 담겼을 법한 디테일(누가 어떤 반응을 했는지 등)을 되묻지 "
            "마라(가족이 '아까 보여줬잖아' 하게 된다). 글로 적힌 내용에만 반응하고, 영상 얘기는 '담아주셔서 "
            "고마워요' 정도로 받아라.\n"
            "★이름/호칭 정확히: 펫은 랴니(강아지)와 레오(고양이) 둘이다. 그리고 **'하비'=할아버지, "
            "'함미'=할머니**(이 채널을 쓰시는 그 어르신들 본인의 애칭). 예: '하비를 기다린다'=할아버지를 "
            "기다린다, '하비가 못살게 한다'=할아버지가 장난친다, '함미가~'=할머니가~. 이분들은 사람(어르신)"
            "이니 펫이나 '아이들'·'세 친구'로 절대 뭉뚱그리지 마라. 그 외 처음 보는 이름은 사람인지 동물인지 "
            "함부로 단정 말고 가족이 부른 그대로 받아라.\n"
            "★최우선 목표 = 컨텐츠(영상 소재) 끌어내기: 어르신 대화에서 펫의 일화·습관·사건을 얻는 게 "
            "가장 중요하다(그 답이 우리 영상 소재가 된다). 그래서 처음엔 그 영상/얘기의 내용을 끌어내는 "
            "가벼운 질문을 한두 번 한다 — '뭐 하는 모습이에요?' '그때 무슨 일이 있었어요?'처럼 답하기 쉬운 것. "
            "**단, 끝없이 캐묻지 마라.** 쓸 만한 내용(무슨 일/언제/누가)이 어느 정도 나오면 더 묻지 말고 "
            "'고마워요! 영상에 잘 담을게요~' 식으로 따뜻하게 닫아라. 한 번에 질문 하나만, 취조 금지, 이미 "
            "나온 건 다시 묻지 마라. 어르신이 '글쎄/없어요'처럼 더 없다는 신호를 주면 즉시 질문을 멈추고 "
            "감사로 마무리하라(질문보다 마무리가 나을 때가 많다).\n"
            "★어르신 말투(충청도): 할머니·할아버지는 충청도 사투리·구어체·줄임말·받아쓰기 오타를 "
            "자주 쓴다(예: '글씨/글쎄'=글쎄요·잘 모르겠네, '~혀/~여'=~해, '겨/기여'=그래, "
            "'~유/~슈'=~요(왔슈=왔어요), '워디'=어디, '거시기'=그거, '쪼끔'=조금). 맥락으로 찰떡같이 "
            "알아듣고 **절대 '무슨 뜻이에요?' '다시 말씀해 주세요' 같이 뜻을 되묻지 마라.** 정말 "
            "모호해도 되묻지 말고 가장 자연스러운 해석으로 따뜻하게 맞장구치며 이어가라(어르신이 "
            "무안하지 않게). 특히 내 질문에 '글씨'/'글쎄'라고만 답하시면 '글쎄요, 잘 모르겠네요'라는 "
            "뜻이다 — 더 말해달라 하지 말고 '그러게요~ 다음에 같이 봐요' 식으로 가볍게 공감하며 받아라. "
            "단, 이해만 그렇게 하고 **답변은 반드시 깔끔한 표준어 존댓말**로 하라 "
            "— 사투리를 흉내내 답하지 마라(예: '~유', '~혀', '~겨' 금지).\n"
            f"★지금 한국 시각: {now_label}. 이 '실제' 시각에 분위기를 맞춰라(아침=상쾌한 인사 등). 시각을 "
            "추측·환각하지 말고 위 실제 시각만 기준. 매 답을 시간 얘기로 시작하거나 끼니·취침을 반복 잔소리하진 "
            "마라. **밤 10시 이후 등 늦은 시각이면** 대화를 길게 끌지 말고 '늦었는데 오늘은 이만 주무세요~ "
            "내일 또 봬요' 식으로 따뜻하게 마무리하라(늦은 밤엔 새 질문 자제).\n"
            "★분류 시키지 마라: '랴니예요? 레오예요? 둘 다예요?'처럼 누가 나왔는지 골라 달라고 "
            "절대 묻지 마라(어르신껜 어렵다). 영상 얘기를 더 듣고 싶으면 '무슨 영상이에요?' '뭐 하는 "
            "모습이에요?'처럼 **내용(무슨 일이 일어나는지)**을 물어라. 누가 나왔는지는 네가 알아서 짐작한다.\n"
            "JSON만 답하라: {\"reply\": 정감있는 한국어 존댓말 — **딱 한 문장, 아주 짧게**(할머니·할아버지가 "
            "큰 글씨로 읽으셔서 길면 못 읽으신다). 두 문장 이상·장황한 설명·여러 질문 나열 절대 금지. "
            "한 문장 안에 따뜻한 맞장구 + 대체로 가벼운 후속 질문 하나(대화를 이어가게), 이모지 한 개 정도. "
            "\"intent\": \"request\"(영상 제작 요청)|\"story\"(펫 일화·설명)|\"chat\"(인사·안부·잡담), "
            "\"subjects\": \"ryani\"|\"leo\"|\"ryani,leo\"|\"\", "
            "\"concept\": 대화에서 건질 만한 펫 영상 컨셉이 있으면 한 줄로(없으면 \"\"), "
            "\"summary\": 한 줄 요약}"
        )
        usr = (("최근 대화:\n" + convo + "\n\n") if convo else "") + \
            f"가족의 새 메시지: {text}" + (" (사진/영상도 같이 올렸어요)" if asset_id else "")
        raw = call_text_cascade(sys_p, usr, max_tokens=500).strip()
        raw = _re.sub(r"^```(?:json)?\s*", "", raw); raw = _re.sub(r"\s*```$", "", raw)
        d = _json.loads(raw)
        reply = (d.get("reply") or "").strip()
        intent = (d.get("intent") or "chat").strip()
        summary = (d.get("summary") or text[:80]).strip()
        subjects = (d.get("subjects") or "").strip()
        concept = (d.get("concept") or "").strip()
    except Exception as e:
        log.warning("grandma LLM failed: %s", e)
        reply = "💛 감사합니다! 잘 받았어요. 영상 만들 때 꼭 참고할게요 🐾"
        subjects = ""
    # 설명에서 펫을 알아냈고 함께 올린 에셋이 있으면 클릭 없이 자동 subject 태깅.
    if asset_id and subjects in ("ryani", "leo", "ryani,leo"):
        try:
            with db() as con:
                con.execute("UPDATE assets SET subjects_csv = ? WHERE asset_id LIKE ? || '%'",
                            (subjects, asset_id[:30]))
        except Exception as e:
            log.warning("grandma subject tag failed: %s", e)
    # 일화/요청/대화에서 건진 컨셉 → episode_stories (브레인스토밍이 자동으로 읽음).
    #   요청=[요청] 태그(우선 가중), 잡담 중 떠오른 영상 아이디어=[컨셉] 태그로도 별도 저장.
    saved = []
    if intent in ("request", "story"):
        saved.append(("[요청] " if intent == "request" else "") + text)
    if concept and concept not in ("없음", "-"):
        saved.append("[컨셉] " + concept)
    for store in saved:
        try:
            with db() as con:
                con.execute(
                    "INSERT INTO episode_stories (text, author, slack_ts) VALUES (?, ?, ?)",
                    (store, user or "", thread_ts or ""))
            log.info("grandma saved (%s): %s", intent, store[:60])
        except Exception as e:
            log.warning("grandma story save failed: %s", e)
    # 함께 올린 에셋이 있으면 설명을 그 에셋에 붙임(자막/소재에 활용).
    if asset_id and intent != "chat":
        try:
            with db() as con:
                con.execute(
                    "UPDATE assets SET notes = COALESCE(notes,'') || ? WHERE asset_id LIKE ? || '%'",
                    (f" [할머니설명] {summary}", asset_id[:30]))
        except Exception as e:
            log.warning("grandma asset note failed: %s", e)
    # Reply in the main channel (not threaded) so it reads as a flowing conversation
    # (PD 2026-06-24: "계속 이야길 해야해"). Continuity comes from _grandma_history.
    try:
        client.chat_postMessage(channel=channel, text=reply or GRANDMA_THANKS)
    except Exception:
        pass


def _grandma_catchup(client) -> None:
    """PD 2026-06-28: the Mac's flaky network keeps wedging Socket Mode, and Slack does
    NOT replay events that arrived while we were disconnected — so 할머니·할아버지 messages
    sent during the gap are silently lost (the bot never replies). On (re)start, read the
    channel's recent history and process every FAMILY message that came AFTER our last bot
    reply (i.e. we never answered it), so a brief outage self-heals on reconnect.

    Heuristic: anything newer than our last bot message is unanswered. Skip the most-recent
    ~15s so a message arriving exactly at startup is left to the live event path (no
    double-reply)."""
    ch = GRANDMOMPAPA_CHANNEL
    if not ch:
        return
    try:
        resp = client.conversations_history(channel=ch, limit=30)
    except Exception as e:
        log.warning("grandma catchup history failed: %s", e)
        return
    msgs = list(reversed(resp.get("messages", [])))  # oldest → newest
    last_bot_ts = 0.0
    for m in msgs:
        if m.get("bot_id"):
            try:
                last_bot_ts = max(last_bot_ts, float(m.get("ts", 0)))
            except Exception:
                pass
    cutoff = _time.time() - 15
    pending = []
    for m in msgs:
        if m.get("bot_id") or m.get("subtype") in ("channel_join", "channel_leave", "bot_message"):
            continue
        try:
            ts = float(m.get("ts", 0))
        except Exception:
            continue
        if ts <= last_bot_ts or ts > cutoff:
            continue
        text = (m.get("text") or "").strip()
        has_files = bool(m.get("files"))
        # Process unanswered TEXT messages AND file uploads (a video/photo with no reply is
        # exactly the "할머니가 영상 올렸는데 반응 없네" case — file_share carries a subtype so
        # it must NOT be skipped above).
        if text or has_files:
            pending.append((m, text, has_files))
    if not pending:
        return
    log.info("grandma catchup: %d unanswered message(s) since last reply — processing",
             len(pending))
    for m, text, has_files in pending:
        try:
            if text:
                _grandma_converse(client, ch, m.get("user", ""), text,
                                  m.get("thread_ts") or m.get("ts"), asset_id=None)
            elif has_files:
                # File upload with no caption → warm in-channel acknowledgment (the file was
                # already ingested live or by slack_sync; we just owe a reply).
                client.chat_postMessage(channel=ch, text=GRANDMA_THANKS)
        except Exception as e:
            log.warning("grandma catchup process failed: %s", e)


@app.event("file_shared")
def handle_file_shared(event, client, say):
    file_id = event.get("file_id")
    channel_id = event.get("channel_id")

    # Handle background channel separately
    if channel_id == BACKGROUND_CHANNEL:
        _handle_background_upload(event, client, file_id)
        return

    # Handle references channel
    if channel_id == REFERENCES_CHANNEL:
        _handle_reference_upload(event, client, file_id, "")
        return

    # Only process files in photos channel, grandparents' channel, or workroom
    allowed_channels = {PHOTOS_CHANNEL, WORKROOM, GRANDMOMPAPA_CHANNEL}
    if channel_id not in allowed_channels:
        return

    try:
        file_info = client.files_info(file=file_id)["file"]
    except Exception as e:
        log.warning("files_info failed for %s: %s", file_id, e)
        return

    mimetype = file_info.get("mimetype", "")
    if not (mimetype.startswith("image/") or mimetype.startswith("video/")):
        return

    asset = _ingest_file(file_info, client)
    if not asset:
        return

    log.info("Ingested Slack file %s → %s", file_id, asset["asset_id"])

    size_info = ""
    if asset.get("width") and asset.get("height"):
        size_info = f" | {asset['width']}×{asset['height']}"
    if asset.get("duration_sec"):
        size_info += f" | {asset['duration_sec']:.1f}s"

    if channel_id == GRANDMOMPAPA_CHANNEL:
        # PD 2026-06-24: do NOT ack here. The message+file_share handler owns
        # grandmompapa — it has the post's caption text and runs the LLM conversation
        # (which already understands the description). Acking here too caused a
        # duplicate generic "설명 적어주세요" even when the description was already written.
        return
    else:
        say(
            text=(
                f":white_check_mark: `{asset['asset_id'][:30]}`{size_info}\n"
                f"subject 태깅: 이 스레드에 `랴니` / `레오` / `랴니레오` 로 답글 달아주세요."
            ),
            thread_ts=event.get("event_ts"),
        )


@app.event({"type": "message", "subtype": "file_share"})
def handle_file_share_message(event, client):
    """Handle file uploads via message subtype (multiple files at once)."""
    channel = event.get("channel", "")
    files = event.get("files", [])
    log.info("file_share message: ch=%s files=%d", channel[:8], len(files))

    if channel == BACKGROUND_CHANNEL and files:
        for f in files:
            _handle_background_upload(event, client, f["id"])
        return

    # Photos channel
    if channel in {PHOTOS_CHANNEL, WORKROOM, GRANDMOMPAPA_CHANNEL} and files:
        n_ok = 0
        last_asset_id = None
        for f in files:
            mimetype = f.get("mimetype", "")
            if mimetype.startswith("image/") or mimetype.startswith("video/"):
                try:
                    file_info = client.files_info(file=f["id"])["file"]
                    asset = _ingest_file(file_info, client)
                    if asset:
                        n_ok += 1
                        last_asset_id = asset["asset_id"]
                        log.info("Ingested file_share %s → %s", f["id"], asset["asset_id"])
                except Exception as e:
                    log.warning("file_share ingest failed: %s", e)
        # 할머니·할아버지: 함께 적은 설명이 있으면 LLM이 이해+대화(+에셋 연결), 없으면 따뜻한 확인.
        # NEVER stay silent: respond whenever they posted a file OR a comment. (Going
        # silent on a duplicate/already-ingested upload is exactly the "답이 없는데?"
        # complaint — duplicates now return an asset so n_ok>0, but keep the OR as a belt.)
        if channel == GRANDMOMPAPA_CHANNEL and (n_ok or (event.get("text") or "").strip()):
            ts = event.get("ts") or event.get("event_ts", "")
            comment = (event.get("text") or "").strip()
            try:
                if ts:
                    client.reactions_add(channel=channel, timestamp=ts, name="heart")
            except Exception:
                pass
            if comment:
                _grandma_converse(client, channel, event.get("user", ""), comment,
                                  ts, asset_id=last_asset_id)
            elif n_ok:
                # Reply in the MAIN channel, NEVER in a thread (PD 2026-06-28: 어르신들이
                # 쓰레드를 헷갈려 함 — grandmompapa 대화는 전부 채널 top-level이어야 한다).
                # Don't ask them to reply-in-thread either; just warmly acknowledge in-channel.
                try:
                    client.chat_postMessage(
                        channel=channel, text="💛 잘 받았어요! 무슨 영상이에요? 🐾")
                except Exception:
                    pass


@app.message(re.compile(r".*"))
def handle_thread_replies(message, client, context):
    """Handle all thread replies: subject tagging + remake commands."""
    event = message
    # Skip bot messages to avoid loops
    if event.get("bot_id") or event.get("subtype"):
        return
    # Idempotency: Slack redelivers unacked events (slow listener → 3s retry, or a
    # listener restart replaying the unacked backlog). Skip anything already handled
    # so the bot never replays/re-answers an old message.
    if _already_processed(event):
        log.info("skip duplicate/redelivered event key=%s", _event_dedup_key(event))
        return
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts")
    text = (event.get("text") or "").strip()
    text_lower = text.lower()
    log.info("message event: ch=%s thread_ts=%s text=%r", channel[:8], thread_ts, text[:50])

    # ── Background channel: save reference images ──
    if channel == BACKGROUND_CHANNEL:
        files = event.get("files", [])
        if files:
            for f in files:
                _handle_background_upload(event, client, f["id"])
            return
        return

    # ── References channel: save object/item reference photos ──
    if channel == REFERENCES_CHANNEL:
        files = event.get("files", [])
        if files:
            for f in files:
                _handle_reference_upload(event, client, f["id"], text)
            return
        # Text-only message in references channel — save as description update
        if text:
            log.info("References channel text (no image): %s", text[:100])
        return

    # ── grandmompapa: 할머니·할아버지와 대화 + 설명/요청 기억 (LLM) ──
    if channel == GRANDMOMPAPA_CHANNEL and text:
        # Run the LLM conversation off-thread so the event handler returns immediately
        # and Bolt acks within Slack's 3s window (a slow inline LLM call is what made
        # Slack retry → duplicate replies).
        threading.Thread(
            target=_grandma_converse,
            args=(client, channel, event.get("user", ""), text, thread_ts or event.get("ts")),
            kwargs=dict(asset_id=None), daemon=True).start()
        return

    # ── rayleo_board: 자연어 운영 비서 (PD 2026-06-21). PD가 채널에 말하면 LLM이
    # 의도를 파싱해 실제 액션(컨셉예약/현황/지식/veto/렌더)을 실행하거나 CLI로
    # 에스컬레이션. 돈/되돌리기 어려운 건 실행 전 확인. 슬롯 쓰레드는 WORKROOM이라
    # 충돌 없음 → 보드의 모든 텍스트(탑레벨+쓰레드)를 비서로 보낸다. ──
    if BOARD_CHANNEL and channel == BOARD_CHANNEL and text:
        from slack import board_agent
        # Off-thread: the assistant parses intent + may call an LLM / render, which
        # takes well over Slack's 3s ack window. Returning now lets Bolt ack fast so
        # Slack doesn't retry the event (the cause of the repeated replies PD saw).
        threading.Thread(
            target=board_agent.handle_board_message,
            args=(client, event), kwargs=dict(db=db, do_veto=_do_veto),
            daemon=True).start()
        return

    # ── Episode channel: save stories to DB ──
    if channel == EPISODE_CHANNEL and text:
        try:
            with db() as con:
                con.execute(
                    "INSERT INTO episode_stories (text, author, slack_ts) VALUES (?, ?, ?)",
                    (text, event.get("user", ""), event.get("ts", "")),
                )
            log.info("Episode story saved: %s", text[:50])
            from slack_sdk import WebClient
            _c = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
            _c.reactions_add(channel=channel, timestamp=event["ts"], name="memo")
        except Exception as e:
            log.warning("Episode save failed: %s", e)
        return

    if not thread_ts:
        return

    # ── Stop handler: "중지", "stop" ──
    stop_keywords = {"중지", "stop", "스탑", "멈춰", "취소", "cancel"}
    if text_lower in stop_keywords:
        _handle_stop(event, client)
        return

    # ── Test handler: "test", "테스트" ──
    test_keywords = {"test", "테스트"}
    if text_lower in test_keywords:
        _handle_test(event, client, thread_ts)
        return

    # ── Remake handler: "다시", "remake", "1번 다시", etc. ──
    remake_keywords = {"다시", "remake", "리메이크", "재생산"}
    if any(kw in text_lower for kw in remake_keywords):
        _handle_remake(event, client, thread_ts, text_lower)
        return

    # ── In-thread veto (PD 2026-06-09): reply "veto" inside a slot's thread to
    # cancel THAT video. (Slash commands can't carry thread context, so the
    # in-thread trigger is a plain message — it does carry thread_ts.) Optional
    # "veto delete" / "veto 삭제" to fully delete instead of unlist.
    veto_keywords = {"veto", "베토", "비토", "거부"}
    if text_lower.split()[0] in veto_keywords if text_lower.split() else False:
        from agents.launch import video_id_for_thread
        with db() as con:
            vid = video_id_for_thread(con, thread_ts)
        if not vid:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=":warning: 이 쓰레드에 연결된 업로드 영상이 없어요 "
                     "(아직 예약 전이거나 런칭 슬롯 쓰레드가 아님). 메인에서 "
                     "`/veto <video_id>` 로도 취소할 수 있어요.")
            return
        do_delete = any(w in text_lower for w in ("delete", "del", "삭제"))
        action = _do_veto(vid, delete=do_delete)
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=action)
        return

    # ── Subject tagging handler ──
    text_lower_stripped = text_lower
    alias_map = {
        "랴니": "ryani", "라니": "ryani", "rayni": "ryani", "리아니": "ryani",
        "레오": "leo",
        "랴니레오": "ryani,leo", "레오랴니": "leo,ryani",
        "랴니,레오": "ryani,leo", "레오,랴니": "leo,ryani",
        "랴니 레오": "ryani,leo", "레오 랴니": "leo,ryani",
    }
    text_lower_stripped = alias_map.get(text_lower_stripped, text_lower_stripped)
    valid_tags = {"ryani", "leo", "ryani,leo", "leo,ryani"}
    if text_lower_stripped not in valid_tags:
        return

    # Normalize
    subjects = ",".join(sorted(text.split(",")))

    # Find the asset from the parent message's thread
    # The parent message contains the asset_id in its text
    try:
        replies = client.conversations_replies(
            channel=event["channel"], ts=thread_ts, limit=1
        )
        parent_text = replies["messages"][0].get("text", "")
        # Extract asset_id (starts with med_)
        match = re.search(r"(med_\S+)", parent_text)
        if not match:
            return
        asset_id_prefix = match.group(1).rstrip("`")

        with db() as con:
            con.execute(
                "UPDATE assets SET subjects_csv = ? WHERE asset_id LIKE ? || '%'",
                (subjects, asset_id_prefix),
            )
        client.reactions_add(
            channel=event["channel"],
            timestamp=event["ts"],
            name="white_check_mark",
        )
    except Exception as e:
        log.warning("Subject tagging failed: %s", e)


# ──────────────────────────────────────────────────────────────────────
# Test handler — detailed debug mode
# ──────────────────────────────────────────────────────────────────────
def _handle_test(event: dict, client, thread_ts: str) -> None:
    """Run a diagnostic test with verbose logging in the thread."""
    import threading as _threading

    def _say(msg):
        if _stop_flag.is_set():
            raise InterruptedError("중지")
        kwargs = {"channel": event["channel"], "text": msg}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        client.chat_postMessage(**kwargs)

    def _run():
      try:
        import sqlite3 as _sql
        import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI

        _say(":test_tube: *테스트 모드 시작*")

        # 1. DB status
        con = _sql.connect(str(DB_PATH))
        con.row_factory = _sql.Row
        total = con.execute("SELECT count(*) FROM assets").fetchone()[0]
        tagged = con.execute("SELECT count(*) FROM assets WHERE vlm_analyzed_at IS NOT NULL").fetchone()[0]
        videos = con.execute("SELECT count(*) FROM assets WHERE kind='video' AND duration_sec IS NOT NULL").fetchone()[0]
        photos_clean = con.execute("SELECT count(*) FROM assets WHERE kind='photo' AND (decoration_level IS NULL OR decoration_level='none') AND file_path NOT LIKE '%.heic'").fetchone()[0]
        _say(f":card_file_box: DB: 에셋 {total}개 (태깅 {tagged}, 비디오 {videos}, 깨끗한사진 {photos_clean})")

        # 2. Location distribution
        locs = {r[0]: r[1] for r in con.execute("SELECT location_type, count(*) FROM assets WHERE location_type IS NOT NULL GROUP BY location_type").fetchall()}
        _say(f":round_pushpin: 위치: {locs}")

        # 3. Photo Selector test
        _say(":camera: Photo Selector 테스트 (10장 후보)...")
        try:
            from agents.photo_selector import search_candidates, vlm_select
            import time
            concept = {"title": "테스트", "render_style": "ai_vtuber", "subjects": ["ryani", "leo"],
                       "cuts": [{"beat": "hook", "description": "정면 응시"}]}

            t0 = time.time()
            candidates = search_candidates(concept, limit=10)
            _say(f"  후보 {len(candidates)}개 검색 ({time.time()-t0:.1f}s)")

            for i, c in enumerate(candidates, 1):
                _say(f"  {i}. `{c['asset_id'][:25]}` | {c.get('activity', '?')} | "
                     f"loc={c.get('location_type', '?')} | q={c.get('quality_score', '?')} | "
                     f"{(c.get('scene_description') or '')[:40]}")

            t1 = time.time()
            selected = vlm_select(candidates, concept, n_select=4)
            sel_time = time.time() - t1
            _say(f"  VLM 선정 {len(selected.get('selected', []))}장 ({sel_time:.1f}s)")

            for s in selected.get("selected", []):
                _say(f"  → #{s.get('photo_number')} {s.get('asset_id', '?')[:25]} | "
                     f"{s.get('beat', '?')} | {s.get('reason', '')[:50]}")
        except Exception as e:
            _say(f"  :x: Photo Selector 실패: {str(e)[:200]}")

        # 4. Character ref check
        from pathlib import Path as _P
        refs = list((_P(ROOT) / "assets" / "character_ref").glob("*.png"))
        _say(f":art: 캐릭터 레퍼런스: {len(refs)}개 — {[r.name for r in refs]}")

        # 5. Caption wrap test
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(str(_P.home() / "Library/Fonts/NanumPenScript-Regular.ttf"), 72)
            test_text = "랴니 & 레오 — 나란히 앉아서 뭘 보나?"
            w = font.getlength(test_text)
            _say(f":pencil: 캡션 '{test_text}' → {w:.0f}px (980px 이하면 OK)")
        except Exception as e:
            _say(f"  :x: 캡션 테스트 실패: {e}")

        # 6. API connectivity
        _say(":globe_with_meridians: API 연결 테스트...")
        # OpenAI
        try:
            from openai import OpenAI
            oc = OpenAI()
            oc.models.list()
            _say("  OpenAI: :white_check_mark:")
        except Exception as e:
            _say(f"  OpenAI: :x: {str(e)[:100]}")

        # Gemini
        try:
            import google.generativeai as genai
            from dotenv import load_dotenv; load_dotenv()
            import os
            genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
            m = genai.GenerativeModel("gemini-2.5-flash")
            r = m.generate_content("say hi", request_options={"timeout": 15})
            _say("  Gemini: :white_check_mark:")
        except Exception as e:
            _say(f"  Gemini: :x: {str(e)[:100]}")

        # Vertex Veo
        try:
            import subprocess as _sp
            r = _sp.run(["gcloud", "auth", "application-default", "print-access-token"],
                        capture_output=True, text=True, timeout=10)
            _say(f"  Vertex AI: {'✅' if r.returncode == 0 else '❌'}")
        except Exception as e:
            _say(f"  Vertex AI: :x: {str(e)[:100]}")

        _say(":white_check_mark: *테스트 완료!*")
      except InterruptedError:
        return  # stop 명령으로 중단

    _threading.Thread(target=_run, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# Stop handler
# ──────────────────────────────────────────────────────────────────────
def _handle_stop(event: dict, client) -> None:
    """Kill all running work — subprocesses + background threads via stop flag."""
    import subprocess as _sp

    # Set global stop flag — all background threads check this
    _stop_flag.set()

    # Kill subprocesses
    result = _sp.run(
        ["pgrep", "-f", "animate_hero_veo3|cameraman|producer|burn_captions|assemble_episode|extract_clips|generate_character|preprocess_for_i2v|build_bumpers|qa_review|tag_assets"],
        capture_output=True, text=True,
    )
    pids = result.stdout.strip().split("\n") if result.stdout.strip() else []
    my_pid = str(os.getpid())
    pids = [p for p in pids if p and p != my_pid]

    for pid in pids:
        try:
            os.kill(int(pid), 9)
        except (ProcessLookupError, ValueError):
            pass

    killed = len(pids)
    client.chat_postMessage(
        channel=event["channel"],
        thread_ts=event.get("thread_ts") or event["ts"],
        text=f":octagonal_sign: 전체 중지! 프로세스 {killed}개 종료 + 백그라운드 작업 중단 플래그 설정.",
    )

    # Clear flag after 5 seconds so new work can start
    def _clear():
        import time; time.sleep(5)
        _stop_flag.clear()
    threading.Thread(target=_clear, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# Remake handler
# ──────────────────────────────────────────────────────────────────────
def _handle_remake(event: dict, client, thread_ts: str, text: str) -> None:
    """Re-produce videos from a previous proposal thread."""
    import threading

    # Find proposal by thread_ts
    with db() as con:
        row = con.execute(
            "SELECT id, target_date, finalized_json, proposal_json FROM daily_proposals WHERE thread_ts = ?",
            (thread_ts,),
        ).fetchone()

    if not row:
        client.chat_postMessage(
            channel=event["channel"],
            thread_ts=thread_ts,
            text=":x: 이 스레드에 연결된 제안을 찾을 수 없어요.",
        )
        return

    concepts = json.loads(row["finalized_json"] or row["proposal_json"])
    target_date = row["target_date"]

    # Parse which episodes to remake: "1번 다시", "1번 3번 다시", "다시" (= all)
    import re as _re
    nums = [int(n) for n in _re.findall(r"(\d+)", text)]
    if nums:
        # Filter to requested indices (1-based)
        selected = [c for i, c in enumerate(concepts, 1) if i in nums]
        label = ", ".join(f"{n}번" for n in nums)
    else:
        selected = concepts
        label = "전체"

    client.chat_postMessage(
        channel=event["channel"],
        thread_ts=thread_ts,
        text=f":arrows_counterclockwise: {label} 재생산 시작 ({len(selected)}편)...",
    )

    def _run():
        import datetime as _dt
        from agents.producer import produce_and_render

        def _progress(msg):
            try:
                client.chat_postMessage(channel=event["channel"], thread_ts=thread_ts, text=msg)
            except Exception:
                pass

        try:
            target = _dt.date.fromisoformat(target_date)
            outputs = produce_and_render(selected, target, progress_cb=_progress)
            client.chat_postMessage(
                channel=event["channel"],
                thread_ts=thread_ts,
                text=(
                    f":white_check_mark: {len(outputs)}편 재생산 완료!\n"
                    + "\n".join(f"  • `{o.name}`" for o in outputs)
                    + "\n`/upload`로 YouTube에 업로드하세요."
                ),
            )
        except Exception as e:
            log.exception("Remake failed")
            client.chat_postMessage(
                channel=event["channel"],
                thread_ts=thread_ts,
                text=f":x: 재생산 실패: {str(e)[:800]}",
            )

    threading.Thread(target=_run, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────────────────────────────
def main() -> None:
    # Single-process guard via PID file
    pidfile = ROOT / "data" / "slack.pid"
    import signal, sys

    def _cleanup(*_):
        pidfile.unlink(missing_ok=True)
        sys.exit(0)

    if pidfile.exists():
        old_pid = int(pidfile.read_text().strip())
        try:
            os.kill(old_pid, 0)  # check if alive
            log.warning("Another slack.app is running (pid=%d). Killing it.", old_pid)
            os.kill(old_pid, signal.SIGTERM)
            import time; time.sleep(2)
        except (ProcessLookupError, ValueError):
            pass  # already dead

    pidfile.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    # Watchdog: detect the BrokenPipe wedge loop and exit so launchd restarts us.
    logging.getLogger("slack_bolt.App").addHandler(_WedgeWatchdog(pidfile))

    # Catch up on grandma messages missed while disconnected (network-wedge recovery):
    # Slack doesn't replay events from the offline gap, so on startup we backfill any
    # family message that arrived after our last reply. Runs off-thread after the socket
    # has had a few seconds to connect.
    def _catchup_later():
        _time.sleep(8)
        try:
            _grandma_catchup(app.client)
        except Exception as e:
            log.warning("grandma catchup thread failed: %s", e)
    threading.Thread(target=_catchup_later, daemon=True).start()

    try:
        handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
        log.info("Ryani & Leo Slack workroom — starting (db=%s, pid=%d)", DB_PATH, os.getpid())
        handler.start()
    finally:
        pidfile.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
