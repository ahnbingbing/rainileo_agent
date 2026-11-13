"""Comment agent (PD 2026-11-12) — reply to the UN-REPLIED viewer comments on our own
videos (the backlog PD can't keep up with). Batch, not real-time. See
notes/comment_agent_spec.md.

SAFETY: posts NOTHING unless explicitly flagged. Default run = DRY (fetch + draft + print).
- `COMMENT_AGENT_POST=1`  → actually post.
- `COMMENT_AGENT_AUTOPOST_SAFE=1` → auto-post the SAFE category; the rest ALWAYS queue for PD.
Anti-fabrication: replies are grounded in CHARACTER_FACTS + the video; unknown → safe, never a
guess (mirrors the caption discipline). Uses the OpenAI-primary cascade (PD: no Anthropic for
small text gen).
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger("agents.comment_agent")

# ── classification ──────────────────────────────────────────────────────────
# SAFE = short affectionate / emoji / simple praise → low risk of a wrong reply.
# Everything else (questions, sensitive, long, ambiguous, real-people mentions) → PD approval.
_AFFECTION = re.compile(
    r"(귀여|너무.?이뻐|예뻐|사랑|최고|좋아요|잘.?생|천사|복슬|말랑|♥|❤|😍|🥰|😻|🐾|👍|cute|adorable|love(ly)?|precious|sweet)",
    re.I)
_QUESTION = re.compile(r"[?？]|어떻|언제|어디|왜|뭐|무슨|몇|나요\b|까요\b|how |what |why |where |when |is it|does ")
_SENSITIVE = re.compile(r"(아프|병원|죽|무지개|슬프|사료|가격|분양|사고|싸움|학대|엄마|아빠|함미|하비|주인)")


def classify(text: str) -> str:
    """'safe' (auto-postable) vs 'approval' (PD must see). Conservative — err to approval."""
    t = (text or "").strip()
    if not t or len(t) > 40:
        return "approval"
    if _QUESTION.search(t) or _SENSITIVE.search(t):
        return "approval"
    if _AFFECTION.search(t) or re.fullmatch(r"[\W\sㅋㅎㅠㅜ]+", t):
        return "safe"
    return "approval"


# ── fetch ───────────────────────────────────────────────────────────────────
def _channel_id(yt) -> str:
    return yt.channels().list(part="id", mine=True).execute()["items"][0]["id"]


def _recent_videos(con, n: int) -> list[str]:
    # PUBLIC videos only — a future-scheduled upload (uploaded=1, publish_at in the future) is
    # still private and has zero viewer comments, so it just wastes the per-video fetch.
    try:
        return [r[0] for r in con.execute(
            "SELECT youtube_video_id FROM cards WHERE youtube_video_id IS NOT NULL AND uploaded=1 "
            "AND (youtube_publish_at IS NULL OR youtube_publish_at <= strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ORDER BY youtube_publish_at DESC LIMIT ?", (n,)).fetchall() if r[0]]
    except Exception:
        return []


def _recent_public_videos(yt, n: int) -> list[tuple]:
    """Recent PUBLIC videos straight from the channel's uploads playlist — NOT from cards.
    PD 2026-11-12: the card-based source was blind to orphan/uncarded uploads, so their viewer
    comments were never seen (the same orphan-blindness as the RF dedup bug). Walk the uploads
    playlist (ground truth) and keep the public ones. Returns [(video_id, title), ...]."""
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    up = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids: list[str] = []
    tok = None
    while len(ids) < max(n, 50):
        r = yt.playlistItems().list(part="contentDetails", playlistId=up,
                                    maxResults=50, pageToken=tok).execute()
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        tok = r.get("nextPageToken")
        if not tok:
            break
    out: list[tuple] = []
    for i in range(0, len(ids), 50):
        v = yt.videos().list(part="status,snippet", id=",".join(ids[i:i + 50])).execute()
        for it in v.get("items", []):
            if it["status"].get("privacyStatus") == "public":
                out.append((it["id"], it["snippet"]["title"]))
    return out[:n]


def fetch_unreplied(con, yt, max_videos: int = 40, per_video: int = 50) -> list[dict]:
    """Top-level comments on recent PUBLIC uploads with NO reply from our channel = PD's backlog."""
    me = _channel_id(yt)
    out: list[dict] = []
    for vid, title in _recent_public_videos(yt, max_videos):
        try:
            resp = yt.commentThreads().list(
                part="snippet,replies", videoId=vid, maxResults=per_video,
                order="time", textFormat="plainText").execute()
        except Exception as e:  # noqa: BLE001
            log.info("comments for %s skipped: %s", vid, e)
            continue
        for it in resp.get("items", []):
            sn = it["snippet"]
            top = sn["topLevelComment"]
            # already answered by us? (check returned replies for our channel id). If the thread
            # has MORE replies than returned, be conservative and treat as answered.
            replies = (it.get("replies") or {}).get("comments", [])
            total = int(sn.get("totalReplyCount", 0) or 0)
            ours = any((r["snippet"].get("authorChannelId") or {}).get("value") == me for r in replies)
            if ours or total > len(replies):
                continue
            s = top["snippet"]
            txt = (s.get("textDisplay") or "").strip()
            if not txt:
                continue
            out.append({"comment_id": top["id"], "video_id": vid, "video_title": title,
                        "author": s.get("authorDisplayName", ""), "text": txt[:300],
                        "likes": int(s.get("likeCount", 0) or 0), "category": classify(txt)})
    return out


# ── draft ───────────────────────────────────────────────────────────────────
_SYS = (
    "너는 유튜브 채널 @ryani_n_leo의 운영자(함미하비)로서 시청자 댓글에 답하는 보이스다.\n"
    "등장: 랴니=작은 검정 프렌치불독(꼬리 없음, 턱·가슴·발 흰 포인트), 레오=치즈 태비 고양이. "
    "둘은 한 집 식구.\n"
    "목소리(PD 기준): 밝고 다정한 **존댓말**. 감사는 '고마워요'가 아니라 '감사합니다!'. 느낌표로 "
    "생기있게(과하지 않게 1~3개). 끝에 '계속 지켜봐 주세요!' 같은 가벼운 재방문 유도 한 마디는 OK.\n"
    "규칙: ①짧게 1~2문장 ②★답글은 **댓글 쓴 사람의 언어로** — 한글이면 한글, 영어면 영어, 일본어면 "
    "일본어, 스페인어면 스페인어… 되도록 댓글 언어에 맞춰라(PD 2026-11-12) ③이모지는 0~1개(없어도 됨) "
    "④영상/아래 사실에 없는 건 지어내지 마라(모르면 두루뭉술 감사) ⑤하드 홍보·'구독/좋아요 눌러주세요'는 "
    "금지(가벼운 '지켜봐 주세요'는 허용).\n"
    "예시) 댓글 '건강행복하거라 예쁜녀석들 ❤' → 답글 '응원 감사합니다! 랴니와 레오 건강하게 잘 지낼게요! "
    "계속 지켜봐 주세요!'\n"
    "답글 본문만 출력(따옴표·접두사 없이).")


_HAS_TEXT = re.compile(r"[0-9A-Za-z가-힣ぁ-んァ-ヶ一-龥]")
_EMOJI_REPLIES = ["🥰🐾", "🐾💕", "😊🐾", "❤️🐾", "🐶🐱💕", "🥰🥰", "🐾😽"]


def _is_emoji_only(text: str) -> bool:
    """No real letters/digits — just emoji/symbols (incl. ㅋㅋ/ㅠㅠ). Reply in kind (PD 2026-11-12)."""
    return bool((text or "").strip()) and not _HAS_TEXT.search(text or "")


def draft_reply(comment: dict) -> str:
    # Emoji-only comment → emoji-only reply (PD: "이모지만 있는건 이모지만으로 답글"). Deterministic
    # (no LLM → guaranteed no sentence), varied by comment id so it isn't the same every time.
    if _is_emoji_only(comment.get("text", "")):
        return _EMOJI_REPLIES[sum(ord(c) for c in comment["comment_id"]) % len(_EMOJI_REPLIES)]
    from agents import arc
    from agents.llm_cascade import call_text_cascade
    user = (f"[영상 제목] {comment.get('video_title','')}\n"
            f"[캐릭터 사실]\n{arc.CHARACTER_FACTS}\n\n"
            f"[시청자 댓글] {comment['author']}: {comment['text']}\n\n"
            "위 댓글에 운영자로서 짧고 다정한 답글 하나.")
    try:
        return call_text_cascade(_SYS, user, max_tokens=200).strip().strip('"')
    except Exception as e:  # noqa: BLE001
        log.warning("draft_reply failed: %s", e)
        return ""


# ── orchestrate ───────────────────────────────────────────────────────────────
def run(max_videos: int = 8, max_replies: int = 20, dry_run: bool = True) -> dict:
    from agents.producer import _db
    from youtube.oauth import get_youtube
    con = _db()
    yt = get_youtube()
    backlog = fetch_unreplied(con, yt, max_videos=max_videos)
    backlog.sort(key=lambda c: (c["category"] != "safe", -c["likes"]))
    picked = backlog[:max_replies]
    for c in picked:
        c["draft"] = draft_reply(c)
    posted, queued = [], []
    autopost = os.getenv("COMMENT_AGENT_AUTOPOST_SAFE", "0") == "1"
    do_post = os.getenv("COMMENT_AGENT_POST", "0") == "1" and not dry_run
    for c in picked:
        if c["category"] == "safe" and autopost and do_post and c.get("draft"):
            try:
                yt.comments().insert(part="snippet", body={"snippet": {
                    "parentId": c["comment_id"], "textOriginal": c["draft"]}}).execute()
                posted.append(c)
            except Exception as e:  # noqa: BLE001
                log.warning("post failed for %s: %s", c["comment_id"], e)
                queued.append(c)
        else:
            queued.append(c)
    return {"backlog_total": len(backlog), "drafted": len(picked),
            "posted": len(posted), "queued": len(queued), "items": picked}


def main() -> int:
    import argparse
    logging.basicConfig(level="INFO", format="%(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Reply to un-replied viewer comments (dry by default)")
    p.add_argument("--max-videos", type=int, default=8)
    p.add_argument("--max-replies", type=int, default=20)
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    r = run(max_videos=a.max_videos, max_replies=a.max_replies, dry_run=True)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print(f"\n미답변 백로그 {r['backlog_total']}건 · 초안 생성 {r['drafted']}건 "
          f"(게시 {r['posted']} · 대기 {r['queued']})\n" + "─" * 60)
    for c in r["items"]:
        tag = "🟢자동가능" if c["category"] == "safe" else "🟡승인필요"
        print(f"\n[{tag}] on 「{c['video_title'][:24]}」  ♥{c['likes']}")
        print(f"  💬 {c['author']}: {c['text'][:120]}")
        print(f"  ↳ 초안: {c.get('draft','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
