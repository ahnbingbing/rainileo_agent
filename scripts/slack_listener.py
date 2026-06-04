"""
scripts/slack_listener.py — long-running Slack Socket Mode listener.

Built 2026-05-31 as the event-driven counterpart to scripts/slack_sync.py.
PD pushed back on cron polling: "유저가 각 채널에 내용 올릴 때 그걸 trigger로
하면 되잖아." Yes — Slack Socket Mode lets us react in real time without
opening a public webhook URL.

Architecture:
- Socket Mode client subscribes to `events_api` envelopes using the
  app-level token (SLACK_APP_TOKEN, xapp-…). No public URL needed.
- Routes only `message` events from the three watched channels into the
  same per-channel handlers used by `slack_sync.py`. Single source of truth
  for routing rules.
- After dispatching a real-time event, the channel's `slack_sync_state`
  last_ts is bumped so a later catch-up `slack_sync.py` run skips it.
- Handles `subtype` correctly: skips bot_message / message_deleted /
  channel_join. Only ingests human messages and file_share.

Run (foreground for now — wrap in launchd/tmux/pm2 for daemonisation later):
    .venv/bin/python scripts/slack_listener.py

Required env: SLACK_BOT_TOKEN (xoxb-…) + SLACK_APP_TOKEN (xapp-…).

Complement to `scripts/slack_sync.py`:
- listener = real-time (new uploads)
- sync = catch-up (downtime, bootstrap, manual replay)
Run both. Idempotency keeps them safe.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import slack_sync  # noqa: E402 — reuse routing + handlers + state helpers

log = logging.getLogger("slack_listener")

# Skip non-content message subtypes (bots, joins, edits to existing rows)
SKIP_SUBTYPES = {
    "bot_message",
    "message_deleted",
    "message_changed",
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "pinned_item",
    "unpinned_item",
}


def _build_channel_map() -> dict[str, str]:
    """channel_id → route name (background/episode/photos)."""
    out: dict[str, str] = {}
    for name, route in slack_sync.CHANNEL_ROUTES.items():
        cid = os.environ.get(route["env"])
        if cid:
            out[cid] = name
        else:
            log.warning("%s not set — listener will ignore that channel", route["env"])
    return out


def handle_message(client_web, message: dict, channel_map: dict[str, str]) -> None:
    """Dispatch one inbound message event to its channel handler."""
    channel_id = message.get("channel")
    if not channel_id or channel_id not in channel_map:
        return  # silently ignore other channels
    subtype = message.get("subtype")
    if subtype in SKIP_SUBTYPES:
        log.debug("skip subtype=%s in %s", subtype, channel_id)
        return
    name = channel_map[channel_id]
    handler_name = slack_sync.CHANNEL_ROUTES[name]["handler"]
    handler = slack_sync.HANDLERS[handler_name]

    con = slack_sync._db()
    try:
        result = handler(con, client_web, message, dry_run=False)
        log.info("[%s] ts=%s → %s", name, message.get("ts", ""), result)
        # Bump last_ts so a later sync.py catch-up skips this
        ts = message.get("ts", "")
        if ts:
            prev = slack_sync.get_last_ts(con, channel_id)
            if ts > prev:
                slack_sync.set_last_ts(con, channel_id, name, ts)
    except Exception:
        log.exception("handler failed for %s ts=%s", name, message.get("ts", ""))
    finally:
        con.close()


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not bot_token or not app_token:
        log.error("SLACK_BOT_TOKEN and SLACK_APP_TOKEN both required for Socket Mode")
        return 2

    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    web = WebClient(token=bot_token)
    sm = SocketModeClient(app_token=app_token, web_client=web)
    channel_map = _build_channel_map()
    log.info("listening on channels: %s", channel_map)

    def on_request(client: SocketModeClient, req: SocketModeRequest):
        # ACK immediately; Slack requires <3s
        client.send_socket_mode_response(
            SocketModeResponse(envelope_id=req.envelope_id)
        )
        if req.type != "events_api":
            return
        event = (req.payload or {}).get("event") or {}
        if event.get("type") != "message":
            return
        try:
            handle_message(web, event, channel_map)
        except Exception:
            log.exception("on_request dispatch error")

    sm.socket_mode_request_listeners.append(on_request)

    # Catch up anything missed before opening the live socket
    try:
        log.info("running catch-up sync before going live…")
        con = slack_sync._db()
        for cid, name in channel_map.items():
            try:
                stats = slack_sync.sync_channel(con, web, name, cid)
                log.info("catch-up %s: %s", name, stats)
            except Exception:
                log.exception("catch-up failed for %s", name)
        con.close()
    except Exception:
        log.exception("catch-up phase failed (continuing to live mode)")

    log.info("connecting Socket Mode…")
    sm.connect()
    # Block forever; Socket Mode runs in its own thread
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("shutting down…")
        sm.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
