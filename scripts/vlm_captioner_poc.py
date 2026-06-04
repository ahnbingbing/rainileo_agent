"""
scripts/vlm_captioner_poc.py — POC: Gemini Vision reads rendered video, writes captions.

Built 2026-05-31 to evaluate caption-pipeline pivot (PD option 2: replace
the entire Writer→Director→Polisher caption chain with a single VLM pass
that watches the actual rendered video and writes captions matching what
viewers will see).

What it does:
1. Take a rendered cut mp4.
2. Extract N keyframes (default 5 evenly spaced).
3. Send keyframes + prompt to Gemini 2.5 Flash with frame timestamps.
4. Gemini returns captions[] array with start/end/ko/en matching action_beats
   in the actual frames.
5. Output: standalone captions.json matching the v17/v18 schema.

Why this is better than the current Writer→Polisher chain:
- Grounded in actual rendered visuals (no spoiler timing, no hallucinated actions).
- Single LLM call (no 5-pass drift).
- Captions match what viewers literally see.

Run:
    python3 scripts/vlm_captioner_poc.py \\
        --in data/output/episodes/episode_av_20260531_*.mp4 \\
        --concept-context "랴니와 레오의 같이 놀자 신호" \\
        --out /tmp/vlm_captions.json

Cost: ~$0.01-0.03 per 30s video on Gemini Flash.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def extract_keyframes(mp4: Path, out_dir: Path, n_frames: int = 8) -> list[tuple[float, Path]]:
    """Extract evenly-spaced keyframes. Returns [(timestamp_s, frame_path), ...]."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(mp4)],
        capture_output=True, text=True, check=True,
    )
    duration = float(result.stdout.strip())
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(n_frames):
        t = duration * (i + 0.5) / n_frames
        frame_path = out_dir / f"frame_{i:02d}_{t:.2f}s.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(mp4),
             "-frames:v", "1", "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
             "-q:v", "5", str(frame_path)],
            check=True, capture_output=True,
        )
        frames.append((t, frame_path))
    return frames


SYSTEM_PROMPT = """\
You are the Caption Writer for the Ryani & Leo YouTube Shorts channel.
You have just watched ONE rendered cut (the keyframes are provided below
with their timestamps). Write 동물농장 narrator-style captions matching
what is actually on screen at each moment.

**Caption rules (NON-NEGOTIABLE):**
- 종결 어미 = "해요/아요/어요/네요/죠/거든요" 체 only. NEVER "습니다/입니다".
- ko ≤ 14 chars per scene, en ≤ 28 chars.
- ko = Korean only; en = English only. No \\n line breaks.
- Each scene gets BOTH ko and en populated separately.
- TV동물농장 톤: "오늘도 어김없이...", "과연...", "아니나 다를까...", "그 순간...", "~네요", "~거든요"
- NO abstract "이 둘이에요" patterns. Use concrete verb.
- caption_position: "top" if pets fill the lower half of the frame, "bottom" otherwise.
- 액션 reveal과 캡션 spoiler 분리: 액션이 일어나기 BEFORE 캡션은 setup ("...을까요?"),
  액션이 일어나는 시점 캡션은 reveal, 액션 AFTER 캡션은 reaction.

**Output schema:**
```json
{
  "captions": [
    {"start": 0.0, "end": 1.5, "ko": "짧은 마디", "en": "Short line."},
    ...
  ],
  "caption_position": "bottom"
}
```

Output ONLY the JSON. No prose. Match scene timing to actual visual beats.
"""


def caption_via_vlm(frames: list[tuple[float, Path]],
                     concept_context: str = "") -> dict:
    """Send keyframes to Gemini 2.5 Flash, get back captions JSON."""
    import google.generativeai as genai
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        "gemini-2.5-flash",
        system_instruction=SYSTEM_PROMPT,
        generation_config={
            "temperature": 0.7,
            "response_mime_type": "application/json",
        },
    )
    parts: list = []
    if concept_context:
        parts.append(f"Concept context: {concept_context}\n")
    parts.append("Keyframes from the rendered cut (in chronological order):\n")
    from PIL import Image
    for t, fp in frames:
        parts.append(f"\n[t={t:.2f}s]")
        parts.append(Image.open(fp))
    parts.append(
        "\nNow write captions matching what you saw, following all rules. "
        "The cut's full duration is the max timestamp above plus ~0.5s."
    )
    resp = model.generate_content(parts)
    text = resp.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def main() -> int:
    p = argparse.ArgumentParser(description="VLM post-render captioner POC")
    p.add_argument("--in", dest="in_mp4", required=True, help="Input mp4")
    p.add_argument("--out", required=True, help="Output captions JSON")
    p.add_argument("--concept-context", default="",
                   help="Short story summary to help Gemini understand intent")
    p.add_argument("--frames", type=int, default=8,
                   help="Number of keyframes to extract")
    args = p.parse_args()

    in_mp4 = Path(args.in_mp4).resolve()
    out = Path(args.out).resolve()
    if not in_mp4.exists():
        print(f"ERROR: input not found: {in_mp4}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        print(f"→ extracting {args.frames} keyframes from {in_mp4.name}...")
        frames = extract_keyframes(in_mp4, td_path, args.frames)
        print(f"  ✓ {len(frames)} frames")
        print(f"→ sending to Gemini 2.5 Flash...")
        result = caption_via_vlm(frames, args.concept_context)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"  ✓ {out}")
        print("\n=== captions preview ===")
        for sc in result.get("captions", []):
            print(f"  {sc.get('start'):.1f}-{sc.get('end'):.1f}: "
                  f"ko={sc.get('ko')!r} | en={sc.get('en')!r}")
        print(f"\ncaption_position: {result.get('caption_position')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
