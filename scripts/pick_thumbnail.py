"""
scripts/pick_thumbnail.py — choose the channel thumbnail for a rendered episode
(PD 2026-06-24: best frame auto-selected, set on YouTube at upload).

Pipeline: sample N frames from the CONTENT region (skip the intro/outro bumpers),
drop near-black / low-detail frames, then a VLM judge (Gemini vision, audience/grid
lens) picks the single most click-worthy frame for the Shorts channel grid. The
winner is saved as a 9:16 JPEG ready for youtube thumbnails().set().

Degrades safely: if the VLM fails, falls back to a mid-content frame (never blocks
the upload).

    python scripts/pick_thumbnail.py --video EP.mp4 --out thumb.jpg
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTRO_DUR = 1.5   # assets/branding/intro_bumper.mp4
OUTRO_DUR = 2.5   # assets/branding/outro_bumper.mp4


def _duration(p: Path | str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def _mean_luma(p: Path) -> float:
    """Rough brightness 0-255 to drop near-black / blank frames."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-vf",
         "format=gray,scale=32:32", "-frames:v", "1", "-f", "rawvideo", "-"],
        capture_output=True)
    data = r.stdout
    return (sum(data) / len(data)) if data else 0.0


def extract_candidate_frames(video: Path | str, n: int = 9, *, workdir: Path | None = None,
                             intro: float = INTRO_DUR, outro: float = OUTRO_DUR) -> list[Path]:
    """Sample n frames across the content region (between the bumpers), dropping
    near-black frames. Returns the kept frame paths in time order."""
    video = Path(video)
    workdir = workdir or (ROOT / "data" / "tmp" / f"thumb_{video.stem}")
    workdir.mkdir(parents=True, exist_ok=True)
    total = _duration(video)
    lo, hi = intro + 0.3, max(intro + 1.0, total - outro - 0.2)
    frames: list[Path] = []
    for i in range(n):
        t = lo + (hi - lo) * (i + 0.5) / n
        out = workdir / f"cand_{i:02d}_{t:.2f}.jpg"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}",
                        "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out), "-y"],
                       check=False)
        if out.exists() and out.stat().st_size > 0 and _mean_luma(out) > 22:
            frames.append(out)
    return frames


def pick_best_frame(frames: list[Path], *, concept: dict | None = None) -> dict:
    """VLM judge: pick the most click-worthy thumbnail for the Shorts channel grid.
    Returns {"winner": idx, "winner_path": Path, "reason": str}. Falls back to the
    middle frame on any failure."""
    if not frames:
        raise ValueError("no candidate frames")
    mid = len(frames) // 2
    if len(frames) == 1:
        return {"winner": 0, "winner_path": frames[0], "reason": "sole frame"}
    try:
        from io import BytesIO

        from dotenv import load_dotenv
        from google import genai
        from google.genai import types as t
        from PIL import Image
        load_dotenv(str(ROOT / ".env"))

        parts = []
        for i, fp in enumerate(frames):
            img = Image.open(fp).convert("RGB")
            if max(img.size) > 768:
                r = 768 / max(img.size)
                img = img.resize((int(img.width * r), int(img.height * r)))
            buf = BytesIO(); img.save(buf, format="JPEG", quality=82)
            parts.append(t.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
            parts.append(f"[프레임 {i}]")
        theme = (concept or {}).get("theme") or (concept or {}).get("title") or ""
        parts.append(
            "위 프레임들은 한 'Ryani(랴니=강아지)와 Leo(레오=고양이)' 펫 숏츠의 장면들이다. "
            f"회차 주제: {theme or '일상'}. "
            "유튜브 쇼츠 채널 그리드(작은 썸네일)에서 시청자가 가장 '누르고 싶게' 만드는 1장을 골라라. "
            "기준(중요도순): ①펫 얼굴/표정이 크고 살아있음(눈 마주침·귀여움·웃긴 순간·생동감) "
            "②선명하고 흐림/눈감음/모션블러 없음 ③색감이 밝고 눈에 띔 ④작은 그리드에서도 한눈에 "
            "무슨 영상인지 읽힘. "
            "★절대 배제: 펫의 엉덩이/항문/생식기가 정면으로 크게 보이는 뒷태 클로즈업 등 "
            "'적나라한/민망한' 프레임은 아무리 선명해도 고르지 마라 — 썸네일은 캐주얼한 채널 "
            "얼굴이 되므로 품위가 중요하다. 같은 장면이라도 얼굴이 보이는 앞모습/옆모습을 택하라. "
            "하단 검은 자막 바는 평가에서 무시한다. "
            "JSON만: {\"winner\": 프레임번호, \"reason\": \"한 줄 이유\"}")

        client = genai.Client()
        last = None
        for model in ("gemini-2.5-flash", "gemini-flash-latest"):
            try:
                resp = client.models.generate_content(
                    model=model, contents=parts,
                    config=t.GenerateContentConfig(
                        response_mime_type="application/json",
                        thinking_config=t.ThinkingConfig(thinking_budget=0)))
                txt = (resp.text or "").strip()
                if not txt:
                    continue
                txt = re.sub(r"^```(?:json)?\s*", "", txt); txt = re.sub(r"\s*```$", "", txt)
                d = json.loads(txt)
                idx = int(d.get("winner", mid))
                idx = idx if 0 <= idx < len(frames) else mid
                return {"winner": idx, "winner_path": frames[idx],
                        "reason": d.get("reason", "")}
            except Exception as e:  # noqa: BLE001
                last = e
        raise last or RuntimeError("vlm empty")
    except Exception:
        return {"winner": mid, "winner_path": frames[mid], "reason": "fallback:mid-frame"}


def make_thumbnail(video: Path | str, out: Path | str, *, concept: dict | None = None,
                   n: int = 9) -> dict:
    """End-to-end: extract → judge → write the winning frame as a JPEG thumbnail
    (re-encoded ≤2MB for the YouTube thumbnails API). Returns the pick dict + out."""
    frames = extract_candidate_frames(video, n=n)
    if not frames:
        raise RuntimeError(f"no usable frames in {video}")
    pick = pick_best_frame(frames, concept=concept)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # re-encode to a clean JPEG (YouTube thumbnail: jpg/png, <2MB)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i",
                    str(pick["winner_path"]), "-q:v", "2", str(out), "-y"], check=True)
    pick["out"] = str(out)
    return pick


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("-n", type=int, default=9)
    a = ap.parse_args()
    res = make_thumbnail(a.video, a.out, n=a.n)
    print(json.dumps({k: (str(v) if isinstance(v, Path) else v) for k, v in res.items()},
                     ensure_ascii=False))
