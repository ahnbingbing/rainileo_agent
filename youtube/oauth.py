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


def authorize() -> Credentials:
    creds: Credentials | None = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        return creds
    if not CLIENT_SECRETS.exists():
        raise FileNotFoundError(
            f"client_secret.json not found at {CLIENT_SECRETS}. "
            "Download from Google Cloud Console (Desktop app OAuth) and place it here."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
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
