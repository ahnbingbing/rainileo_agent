"""
youtube/oauth.py — bootstrap YouTube OAuth credentials.

Steps to run once:
    1. https://console.cloud.google.com/ -> APIs & Services -> Credentials -> Create OAuth client ID
       Application type: Desktop app
    2. Download the JSON, save as youtube/client_secret.json
    3. Enable: YouTube Data API v3, YouTube Analytics API
    4. python -m youtube.oauth
       (browser opens, sign in to the channel's Google account, grant scopes)
    5. Resulting token is saved to youtube/token.json (auto-refreshing)

Subsequent runs of get_youtube() just load token.json.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    # Read viewer comments for the macro reviewer's audience-context. commentThreads.list
    # is gated SPECIFICALLY behind force-ssl — verified empirically: a token carrying
    # youtube + youtube.readonly STILL 403s "insufficient scopes" on commentThreads, so
    # neither the manage nor the readonly scope unlocks comments (a well-known YouTube API
    # quirk — comments are moderatable data). We only fetch, never moderate. Adding a scope
    # requires a FRESH consent (a refresh keeps the OLD scope set): delete token.json then
    # `python -m youtube.oauth`.
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

CLIENT_SECRETS = Path(os.getenv("YOUTUBE_CLIENT_SECRETS", str(ROOT / "youtube" / "client_secret.json")))
TOKEN_PATH = Path(os.getenv("YOUTUBE_TOKEN", str(ROOT / "youtube" / "token.json")))


_BAK_PATH = TOKEN_PATH.parent / (TOKEN_PATH.name + ".bak")
_TMP_PATH = TOKEN_PATH.parent / (TOKEN_PATH.name + ".tmp")


def _save_token(creds: Credentials) -> None:
    """Persist creds atomically so a concurrent refresh can never leave a 0-byte / half-written
    token. 8/21 incident: two processes refreshed at once and truncated token.json to 0 bytes →
    every upload failed with 'Expecting value: line 1 column 1' and the whole day's batch would
    have gone unpublished. Guard against empty content, keep a .bak of the last good token, and
    write-temp-then-os.replace (atomic on POSIX) so readers only ever see a complete file."""
    data = creds.to_json()
    if not data or not data.strip():
        return  # never overwrite a working token with empty content
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_PATH.exists() and TOKEN_PATH.stat().st_size > 0:
        try:
            shutil.copy2(TOKEN_PATH, _BAK_PATH)
        except Exception:
            pass
    _TMP_PATH.write_text(data, encoding="utf-8")
    os.replace(_TMP_PATH, TOKEN_PATH)  # atomic


def _load_creds() -> Credentials | None:
    """Load creds from token.json, falling back to token.json.bak if the primary is missing,
    empty, or unparseable — auto-recovery from a truncated token instead of a hard failure."""
    for p in (TOKEN_PATH, _BAK_PATH):
        try:
            if p.exists() and p.stat().st_size > 0:
                return Credentials.from_authorized_user_file(str(p), SCOPES)
        except Exception:
            continue
    return None


def authorize() -> Credentials:
    creds = _load_creds()
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)
        return creds
    if not CLIENT_SECRETS.exists():
        raise FileNotFoundError(
            f"client_secret.json not found at {CLIENT_SECRETS}. "
            "Download from Google Cloud Console (Desktop app OAuth) and place it here."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _save_token(creds)
    return creds


def get_youtube():
    creds = authorize()
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def get_analytics():
    creds = authorize()
    return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)


def main() -> None:
    creds = authorize()
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    me = yt.channels().list(part="snippet,statistics", mine=True).execute()
    if not me.get("items"):
        print("[warn] auth ok but no channel found for this account")
        return
    ch = me["items"][0]
    s = ch["snippet"]
    st = ch["statistics"]
    print(f"[ok] authorized as channel: {s['title']} (id={ch['id']})")
    print(f"     subscribers={st.get('subscriberCount')}, videos={st.get('videoCount')}")
    print(f"     token saved at: {TOKEN_PATH}")


if __name__ == "__main__":
    main()
