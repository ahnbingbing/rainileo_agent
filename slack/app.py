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

# Global stop flag — set by "중지/stop" command, checked by all background work
import threading
_stop_flag = threading.Event()

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
@app.command("/veto")
def veto_cmd(ack, body, respond):
    ack()
    parts = (body.get("text") or "").strip().split()
    if not parts:
        respond("usage: `/veto <card_id_prefix | youtube_video_id> [delete]`")
        return
    ident = parts[0]
    do_delete = len(parts) > 1 and parts[1].lower() in ("delete", "del", "삭제")
    with db() as con:
        row = con.execute(
            "SELECT card_id, youtube_video_id, theme FROM cards "
            "WHERE youtube_video_id=? OR card_id LIKE ? || '%' "
            "ORDER BY updated_at DESC LIMIT 1",
            (ident, ident),
        ).fetchone()
    if not row or not row["youtube_video_id"]:
        respond(f":x: `{ident}` 에 매칭되는 업로드 영상이 없어요 (youtube_video_id 없음)")
        return
    card_id = row["card_id"]
    vid = row["youtube_video_id"]
    respond(f":no_entry: veto `{card_id[:8]}` → {vid} "
            f"({'삭제' if do_delete else '비공개 전환'})...")
    try:
        from youtube.upload import veto_video
        action = veto_video(vid, delete=do_delete)
        with db() as con:
            # revoke uploaded flag so the clip cooldown / arc no longer counts it,
            # and mark state so it won't be re-treated as live.
            con.execute(
                "UPDATE cards SET uploaded=0, state=?, updated_at=datetime('now') "
                "WHERE card_id=?",
                ("vetoed_deleted" if do_delete else "vetoed", card_id),
            )
        respond(f":white_check_mark: veto 완료 — `{card_id[:8]}` {action} "
                f"(쿨다운/아크 카운트 회수됨)")
    except Exception as e:
        log.exception("veto failed for %s", card_id[:8])
        respond(f":x: veto 실패: {str(e)[:600]}")


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
        target = _dt.datetime.now(_ZI("Asia/Seoul")).date()

    respond(f":rocket: 런칭 4슬롯 시작 — {target.isoformat()}"
            + (" (dry-run)" if dry_run else "")
            + ("" if do_upload else " (no-upload)"))

    import threading

    def _run():
        from agents.launch import launch_pipeline
        # open a thread root so all 4 episodes + progress live together
        root = None
        try:
            r = client.chat_postMessage(
                channel=WORKROOM,
                text=f":clapper: *런칭 데이* {target.isoformat()} — 4슬롯 생산 시작")
            root = r.get("ts")
        except Exception:
            pass

        def _progress(msg):
            if _stop_flag.is_set():
                raise InterruptedError("중지 명령으로 중단됨")
            try:
                client.chat_postMessage(channel=WORKROOM, text=msg, thread_ts=root)
            except Exception:
                pass

        def _video(path):
            try:
                client.files_upload_v2(
                    channel=WORKROOM, thread_ts=root,
                    file=str(path), title=Path(path).name,
                    initial_comment=f":movie_camera: {Path(path).name} — 문제 있으면 `/veto`")
            except Exception as e:
                log.warning("launch video upload failed: %s", e)

        try:
            results = launch_pipeline(target, progress_cb=_progress,
                                      video_cb=_video, do_upload=do_upload,
                                      dry_run=dry_run)
            # summary line with veto hints
            lines = [":checkered_flag: *런칭 요약*"]
            for r in results:
                vid = r.get("video_id")
                tag = (f"<https://youtube.com/shorts/{vid}|{vid}>" if vid
                       else "_미업로드_")
                lines.append(f"  • {r['slot']} {r['lane']} → {tag}")
            client.chat_postMessage(channel=WORKROOM, text="\n".join(lines),
                                    thread_ts=root)
        except Exception as e:
            log.exception("launch pipeline failed")
            client.chat_postMessage(
                channel=WORKROOM, thread_ts=root,
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
            "SELECT asset_id FROM assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if existing:
            return None

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

    # Insert into DB
    captured_iso = ts.isoformat()
    with db() as con:
        con.execute(
            """
            INSERT INTO assets
                (asset_id, source, kind, file_path, captured_iso,
                 duration_sec, width, height, phash, subjects_csv)
            VALUES (?, 'slack', ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (asset_id, kind, str(dest), captured_iso,
             duration, width, height, phash),
        )

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

    # Only process files in photos channel (or workroom as fallback)
    allowed_channels = {PHOTOS_CHANNEL, WORKROOM}
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
    if channel in {PHOTOS_CHANNEL, WORKROOM} and files:
        for f in files:
            mimetype = f.get("mimetype", "")
            if mimetype.startswith("image/") or mimetype.startswith("video/"):
                try:
                    file_info = client.files_info(file=f["id"])["file"]
                    asset = _ingest_file(file_info, client)
                    if asset:
                        log.info("Ingested file_share %s → %s", f["id"], asset["asset_id"])
                except Exception as e:
                    log.warning("file_share ingest failed: %s", e)


@app.message(re.compile(r".*"))
def handle_thread_replies(message, client, context):
    """Handle all thread replies: subject tagging + remake commands."""
    event = message
    # Skip bot messages to avoid loops
    if event.get("bot_id") or event.get("subtype"):
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

    try:
        handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
        log.info("Ryani & Leo Slack workroom — starting (db=%s, pid=%d)", DB_PATH, os.getpid())
        handler.start()
    finally:
        pidfile.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
