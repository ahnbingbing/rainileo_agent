"""
scripts/tts_narration_poc.py — POC: TTS narration on one cut.

Built 2026-05-31 to evaluate caption-pipeline pivot (PD option 1: replace
text captions with 동물농장-style audio narration + minimal visual emphasis).

What it does:
1. Take one Cameraman cut mp4 + narrator text.
2. Generate Korean narration audio via OpenAI TTS (model=tts-1, voice=nova).
3. Mix the narration audio onto the cut mp4 (existing audio kept or dropped).
4. Output: <input>_narrated.mp4

Run:
    python3 scripts/tts_narration_poc.py \\
        --in data/tmp/cameraman_cc_20260_*/animated/cut1_intro.mp4 \\
        --text "오늘 오후, 랴니에게 목표가 생겼어요. 레오를 발견한 순간이었어요." \\
        --out /tmp/cut1_narrated.mp4

Cost: ~$0.015 per minute of narration (OpenAI tts-1).

Voices to try: nova (warm female, recommended for 동물농장 tone), alloy (neutral),
shimmer (bright). Korean TTS quality is best on tts-1-hd (~$0.030/min).
"""
from __future__ import annotations

import argparse
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


def synthesize(text: str, out_path: Path, voice: str = "nova",
               model: str = "tts-1-hd", speed: float = 0.95) -> Path:
    """Call OpenAI TTS, save MP3 to out_path."""
    import openai
    client = openai.OpenAI()
    with client.audio.speech.with_streaming_response.create(
        model=model, voice=voice, input=text, speed=speed,
        response_format="mp3",
    ) as resp:
        resp.stream_to_file(out_path)
    return out_path


def mix_audio_onto_video(video: Path, narration_mp3: Path, out: Path,
                          keep_original_audio: bool = False,
                          narration_volume: float = 1.2) -> Path:
    """ffmpeg: replace video's audio with narration (or mix). Output to out."""
    if keep_original_audio:
        # amix: -1 = both inputs, narration slightly boosted
        filter_complex = (
            f"[0:a]volume=0.4[a0];"
            f"[1:a]volume={narration_volume}[a1];"
            f"[a0][a1]amix=inputs=2:duration=longest[aout]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", str(video), "-i", str(narration_mp3),
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(out),
        ]
    else:
        # Drop original, use narration only
        cmd = [
            "ffmpeg", "-y", "-i", str(video), "-i", str(narration_mp3),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out),
        ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="TTS narration POC")
    p.add_argument("--in", dest="in_mp4", required=True,
                   help="Input video mp4")
    p.add_argument("--text", required=True,
                   help="Narration text (Korean)")
    p.add_argument("--out", required=True, help="Output mp4")
    p.add_argument("--voice", default="nova",
                   choices=["alloy", "echo", "fable", "onyx", "nova", "shimmer"])
    p.add_argument("--model", default="tts-1-hd",
                   choices=["tts-1", "tts-1-hd"])
    p.add_argument("--speed", type=float, default=0.95,
                   help="Speech speed (0.25-4.0); 0.9-1.0 for narrator tone")
    p.add_argument("--keep-original", action="store_true",
                   help="Mix narration with original video audio (default: replace)")
    args = p.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    in_mp4 = Path(args.in_mp4).resolve()
    out = Path(args.out).resolve()
    if not in_mp4.exists():
        print(f"ERROR: input not found: {in_mp4}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as td:
        tts_mp3 = Path(td) / "narration.mp3"
        print(f"→ TTS ({args.model}, voice={args.voice}, speed={args.speed})...")
        synthesize(args.text, tts_mp3, voice=args.voice,
                   model=args.model, speed=args.speed)
        size_kb = tts_mp3.stat().st_size / 1024
        print(f"  ✓ narration {size_kb:.1f}KB")
        print("→ mixing onto video...")
        mix_audio_onto_video(in_mp4, tts_mp3, out,
                              keep_original_audio=args.keep_original)
        print(f"  ✓ output: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
