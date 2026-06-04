"""
scripts/qa_review.py — Automated QA review of rendered episodes.

Extracts 1 frame per cut from the final video, sends them + the original
storyboard concept to Gemini VLM, and gets a structured quality report.

Checks:
  1. Does each cut match its storyboard description?
  2. Are the right subjects (ryani/leo) visible?
  3. Are there unwanted humans visible?
  4. Is the caption readable and correct?
  5. Are stickers/decorations appropriate (not overdone)?
  6. Overall quality and coherence score

Usage:
    python3 scripts/qa_review.py <video.mp4> --concept <concept.json>
    python3 scripts/qa_review.py <video.mp4> --card-id <card_id_prefix>
    python3 scripts/qa_review.py <video.mp4> --storyboard "cut1: 랴니 인사, cut2: 레오 등장, ..."

Output: JSON report + human-readable summary to stdout.
Returns exit code 0 if PASS, 1 if FAIL.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("qa_review")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()

QA_PROMPT = """\
You are the QA reviewer for "Ryani & Leo" YouTube Shorts channel.
- Ryani (랴니): black French Bulldog, white markings on chin/chest/paws
- Leo (레오): orange tabby cat, young

You are reviewing a rendered 20-second Short. I'm showing you FRAMES EXTRACTED from the video — these are screenshots, NOT still images. The actual video has motion.
Do NOT penalize for "lack of motion" — judge composition, subjects, captions, storyboard matching.
For "real_footage" style: AI-generated images ARE OK if made for THIS episode. Only reject AI images from a DIFFERENT context.
One frame per ~4-second cut, plus the original
storyboard that describes what each cut SHOULD show.

Compare each frame to its storyboard description and evaluate:

Return JSON:
{
  "cuts": [
    {
      "cut_number": 1,
      "storyboard": "what it should show",
      "actual": "what the frame actually shows",
      "match_score": 0.0-1.0,
      "issues": ["list of problems"],
      "has_correct_subject": true/false,
      "has_unwanted_human": true/false,
      "caption_visible": true/false,
      "caption_readable": true/false,
      "caption_position": "top" | "bottom" | "center" | "none",
      "caption_overflow": false,
      "caption_text_seen": "actual text visible in the frame (if readable)",
      "caption_blocks_subject": false,
      "decoration_appropriate": true/false
    }
  ],
  "overall": {
    "score": 0.0-1.0,
    "pass": true/false,
    "visual_coherence": 0.0-1.0,
    "style_consistency": 0.0-1.0,
    "caption_quality": {
      "all_visible": true/false,
      "all_readable": true/false,
      "any_overflow": false,
      "any_blocks_subject": false,
      "position_consistent": true/false,
      "font_appropriate": true/false,
      "notes": "캡션 배치/가독성 관련 메모"
    },
    "summary_ko": "한국어로 2-3줄 종합 평가",
    "fix_suggestions": ["구체적 수정 제안 리스트"]
  }
}

Caption checking rules:
- caption_overflow: true if text extends beyond frame edges or is cut off
- caption_blocks_subject: true if caption text covers the pet's face or body
- caption_readable: false if text is too small, blurry, or blends into background
- position_consistent: all cuts should have captions in the same position area
- font_appropriate: should look clean and match the channel style (handwritten for real_footage, modern for ai_vtuber)

Scoring guide:
- match_score >= 0.7: cut is acceptable
- overall score >= 0.6: episode is publishable
- pass = true if overall score >= 0.6 AND no cut has unwanted human AND no cut match_score < 0.3 AND caption_quality.any_overflow == false

Be strict but fair. The channel targets warm, cute content.
"""


def _check_audio(video: Path) -> dict:
    """Check if video has audio stream and measure volume levels."""
    result = {
        "has_audio": False,
        "has_bgm": False,
        "mean_volume_db": None,
        "max_volume_db": None,
        "silent": True,
        "issues": [],
    }

    # Check for audio stream
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type",
         "-of", "json", str(video)],
        capture_output=True, text=True, timeout=10,
    )
    streams = json.loads(probe.stdout).get("streams", [])
    if not streams:
        result["issues"].append("오디오 스트림 없음 — BGM이 빠졌습니다")
        return result

    result["has_audio"] = True

    # Measure volume
    try:
        vol = subprocess.run(
            ["ffmpeg", "-i", str(video), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
        stderr = vol.stderr
        mean_match = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", stderr)
        max_match = re.search(r"max_volume:\s*([-\d.]+)\s*dB", stderr)

        if mean_match:
            result["mean_volume_db"] = float(mean_match.group(1))
        if max_match:
            result["max_volume_db"] = float(max_match.group(1))

        if result["mean_volume_db"] is not None:
            if result["mean_volume_db"] > -50:
                result["silent"] = False
                result["has_bgm"] = True
            else:
                result["issues"].append(f"오디오가 거의 무음 (mean={result['mean_volume_db']:.1f}dB)")

            # Check if too loud
            if result["mean_volume_db"] > -5:
                result["issues"].append(f"BGM이 너무 큼 (mean={result['mean_volume_db']:.1f}dB)")
    except Exception as e:
        result["issues"].append(f"볼륨 측정 실패: {str(e)[:100]}")

    return result


def _extract_cut_frames(video: Path, n_cuts: int = 4, bumper_sec: float = 1.5) -> list[Path]:
    """Extract one frame from the middle of each cut."""
    tmpdir = Path(tempfile.mkdtemp(prefix="qa_"))

    # Get total duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(video)],
        capture_output=True, text=True,
    )
    duration = float(json.loads(probe.stdout)["format"]["duration"])

    # Calculate cut boundaries (skip intro/outro bumpers)
    content_dur = duration - bumper_sec - 2.5  # intro 1.5s, outro 2.5s
    cut_dur = content_dur / n_cuts

    frames = []
    for i in range(n_cuts):
        # Middle of each cut
        t = bumper_sec + (i * cut_dur) + (cut_dur / 2)
        out = tmpdir / f"cut{i+1}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "2", str(out)],
            capture_output=True, timeout=10,
        )
        if out.exists():
            frames.append(out)
    return frames


def _call_gemini_qa(frames: list[Path], storyboard: list[dict]) -> dict:
    """Send frames + storyboard to Gemini for QA review."""
    import google.generativeai as genai
    from PIL import Image

    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    model = genai.GenerativeModel(os.getenv("VLM_MODEL", "gemini-2.5-flash"))

    parts = []

    # Add frames as images
    for i, fp in enumerate(frames):
        img = Image.open(fp)
        max_dim = 1024
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        parts.append({"mime_type": "image/jpeg", "data": buf.getvalue()})

    # Build storyboard text
    sb_text = "## Storyboard (what each cut SHOULD show):\n"
    for i, cut in enumerate(storyboard):
        desc = cut.get("description", cut.get("ko", ""))
        beat = cut.get("beat", f"cut{i+1}")
        sb_text += f"  Cut {i+1} ({beat}): {desc}\n"

    parts.append(QA_PROMPT + "\n\n" + sb_text)

    response = model.generate_content(
        parts,
        generation_config={"response_mime_type": "application/json"},
    )
    text = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def review_episode(video: Path, storyboard: list[dict],
                   n_cuts: int = 4) -> dict:
    """Full QA review pipeline: visual (VLM) + audio checks."""
    # Audio check
    log.info("Checking audio for %s", video.name)
    audio = _check_audio(video)

    # Visual check
    log.info("Extracting %d frames from %s", n_cuts, video.name)
    frames = _extract_cut_frames(video, n_cuts=n_cuts)
    if len(frames) < n_cuts:
        log.warning("Only extracted %d/%d frames", len(frames), n_cuts)

    log.info("Sending to VLM for review...")
    report = _call_gemini_qa(frames, storyboard)

    # Merge audio results into report
    overall = report.setdefault("overall", {})
    overall["audio"] = audio
    if not audio["has_bgm"]:
        overall.setdefault("fix_suggestions", []).insert(0, "BGM이 없습니다 — 배경음악을 추가하세요")
        # Penalize score for missing BGM
        if overall.get("score"):
            overall["score"] = max(0, overall["score"] - 0.2)
        overall["pass"] = False

    if audio.get("issues"):
        for issue in audio["issues"]:
            overall.setdefault("fix_suggestions", []).append(f"오디오: {issue}")

    # Penalize for caption overflow
    caption_q = overall.get("caption_quality", {})
    if caption_q.get("any_overflow"):
        overall.setdefault("fix_suggestions", []).append("캡션이 화면을 넘칩니다 — 텍스트를 줄이거나 폰트 크기를 줄이세요")
        if overall.get("score"):
            overall["score"] = max(0, overall["score"] - 0.1)
    if caption_q.get("any_blocks_subject"):
        overall.setdefault("fix_suggestions", []).append("캡션이 펫을 가립니다 — 위치를 조정하세요")

    # Cleanup temp frames
    for f in frames:
        f.unlink(missing_ok=True)
        try:
            f.parent.rmdir()
        except OSError:
            pass

    return report


def print_report(report: dict) -> None:
    """Pretty-print the QA report."""
    overall = report.get("overall", {})
    score = overall.get("score", 0)
    passed = overall.get("pass", False)

    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n{'='*50}")
    print(f"QA Review: {status} (score: {score:.1f})")
    print(f"{'='*50}\n")

    # Audio status
    audio = overall.get("audio", {})
    bgm_icon = "🎵" if audio.get("has_bgm") else "🔇"
    vol_info = f" ({audio['mean_volume_db']:.0f}dB)" if audio.get("mean_volume_db") is not None else ""
    print(f"  BGM: {bgm_icon} {'있음' if audio.get('has_bgm') else '없음'}{vol_info}")

    # Caption quality
    cap_q = overall.get("caption_quality", {})
    if cap_q:
        cap_issues = []
        if cap_q.get("any_overflow"):
            cap_issues.append("넘침")
        if cap_q.get("any_blocks_subject"):
            cap_issues.append("펫 가림")
        if not cap_q.get("all_readable"):
            cap_issues.append("가독성↓")
        if not cap_q.get("position_consistent"):
            cap_issues.append("위치 불일치")
        cap_icon = "✓" if not cap_issues else "⚠"
        print(f"  캡션: {cap_icon} {', '.join(cap_issues) if cap_issues else '양호'}")
        if cap_q.get("notes"):
            print(f"    {cap_q['notes']}")
    print()

    # Per-cut results
    for cut in report.get("cuts", []):
        n = cut.get("cut_number", "?")
        ms = cut.get("match_score", 0)
        match_icon = "✓" if ms >= 0.7 else "△" if ms >= 0.4 else "✗"
        human = " 👤" if cut.get("has_unwanted_human") else ""
        cap_pos = cut.get("caption_position", "")
        cap_overflow = " 📏넘침" if cut.get("caption_overflow") else ""
        cap_text = cut.get("caption_text_seen", "")
        print(f"  Cut {n}: {match_icon} {ms:.1f}{human}{cap_overflow}")
        print(f"    기대: {cut.get('storyboard', '?')[:60]}")
        print(f"    실제: {cut.get('actual', '?')[:60]}")
        if cap_text:
            print(f"    캡션: \"{cap_text[:50]}\" ({cap_pos})")
        if cut.get("issues"):
            for issue in cut["issues"]:
                print(f"    ⚠ {issue}")
        print()

    print(f"종합: {overall.get('summary_ko', '')}")
    if overall.get("fix_suggestions"):
        print("\n수정 제안:")
        for s in overall["fix_suggestions"]:
            print(f"  • {s}")
    print()


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(name)s %(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="QA review of rendered episode")
    p.add_argument("video", help="path to rendered .mp4")
    p.add_argument("--concept", default=None, help="concept JSON file")
    p.add_argument("--card-id", default=None, help="card_id to look up storyboard from DB")
    p.add_argument("--storyboard", default=None, help="inline storyboard text")
    p.add_argument("--cuts", type=int, default=4, help="number of content cuts")
    p.add_argument("--json", action="store_true", help="output raw JSON only")
    args = p.parse_args()

    video = Path(args.video)
    if not video.exists():
        print(f"Video not found: {video}", file=sys.stderr)
        return 2

    # Load storyboard
    storyboard = []
    if args.concept:
        concept = json.loads(Path(args.concept).read_text(encoding="utf-8"))
        storyboard = concept.get("cuts", [])
    elif args.card_id:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT dp.finalized_json, dp.proposal_json FROM daily_proposals dp "
            "ORDER BY dp.id DESC LIMIT 1"
        ).fetchone()
        if row:
            concepts = json.loads(row["finalized_json"] or row["proposal_json"])
            # Find matching concept
            for c in concepts:
                if args.card_id.lower() in c.get("title", "").lower():
                    storyboard = c.get("cuts", [])
                    break
            if not storyboard and concepts:
                storyboard = concepts[0].get("cuts", [])
    elif args.storyboard:
        # Parse "cut1: desc, cut2: desc" format
        for part in args.storyboard.split(","):
            part = part.strip()
            storyboard.append({"description": part})

    if not storyboard:
        print("WARNING: No storyboard provided, using generic check", file=sys.stderr)
        storyboard = [{"beat": f"cut{i}", "description": "펫 영상 컷"} for i in range(1, args.cuts + 1)]

    report = review_episode(video, storyboard, n_cuts=args.cuts)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    passed = report.get("overall", {}).get("pass", False)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
