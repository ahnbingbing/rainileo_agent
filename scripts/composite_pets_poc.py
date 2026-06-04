"""
scripts/composite_pets_poc.py — POC: pets-only render + PD photo background.

Built 2026-05-31 to evaluate Seedance-background-failure mitigation. Premise:
Seedance hallucinates rooms (apartment cliché, fake curtains) no matter how
hard we prompt. Instead, render pets on any background, segment them out,
composite onto PD's real 충주집 photo. Background becomes 100% photo-accurate.

What it does:
1. Take a rendered cut mp4 (any background).
2. Extract frames.
3. For each frame: use `rembg` (U2Net-based) to remove background → pets RGBA.
4. Composite pets onto a real PD background photo (resized to match).
5. Re-encode as mp4.

Tradeoffs:
- ✅ Background = 100% photo-accurate. No more apartment cliché.
- ❌ Pets appear "cut-out" — edge artifacts, lighting mismatch.
- ❌ Pet shadows missing (no contact shadows on the new floor).
- ⚠️ rembg is slow (~0.5s per frame) — 6s clip × 30fps = 180 frames × 0.5s = 90s per cut.

Mitigations for cut-out look:
- Use rembg `u2net_human_seg` or `isnet-general-use` model (better edges).
- Add soft 5-10px feather to alpha mask.
- Add fake contact shadow (Gaussian blur of mask, dark, alpha 0.4).

Run:
    pip install rembg pillow
    python3 scripts/composite_pets_poc.py \\
        --in data/tmp/cameraman_cc_*/animated/cut1_intro.mp4 \\
        --bg assets/backgrounds/bg_b969c2ad.jpg \\
        --out /tmp/cut1_composite.mp4

If rembg too slow: try `--frames 5` to subsample for static-frame composite test.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_video_frames(mp4: Path, out_dir: Path,
                          fps: int = 24) -> tuple[list[Path], float]:
    """Extract all frames at given fps. Returns (frame_paths, actual_fps)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(mp4),
        "-vf", f"fps={fps}",
        "-q:v", "2",
        str(out_dir / "f_%04d.jpg"),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    frames = sorted(out_dir.glob("f_*.jpg"))
    return frames, float(fps)


def remove_bg_frame(input_jpg: Path, output_png: Path, session=None) -> Path:
    """Use rembg to extract foreground (pets). Output RGBA PNG."""
    from PIL import Image
    from rembg import remove
    img = Image.open(input_jpg)
    if session:
        out = remove(img, session=session, alpha_matting=True,
                     alpha_matting_foreground_threshold=240,
                     alpha_matting_background_threshold=20,
                     alpha_matting_erode_size=5)
    else:
        out = remove(img, alpha_matting=False)
    out.save(output_png)
    return output_png


def composite_onto_bg(pets_rgba_png: Path, bg_jpg: Path, out_jpg: Path,
                       add_shadow: bool = True) -> Path:
    """Composite pets RGBA over background. Optional contact shadow."""
    from PIL import Image, ImageFilter
    bg = Image.open(bg_jpg).convert("RGB")
    pets = Image.open(pets_rgba_png).convert("RGBA")
    if bg.size != pets.size:
        bg = bg.resize(pets.size, Image.LANCZOS)
    if add_shadow:
        alpha = pets.split()[-1]
        shadow_alpha = alpha.filter(ImageFilter.GaussianBlur(radius=18))
        shadow = Image.new("RGBA", pets.size, (0, 0, 0, 0))
        for x in range(0, pets.size[0]):
            pass
        shadow_layer = Image.new("RGBA", pets.size, (0, 0, 0, 100))
        shadow_layer.putalpha(shadow_alpha)
        offset_layer = Image.new("RGBA", pets.size, (0, 0, 0, 0))
        offset_layer.paste(shadow_layer, (0, 12))
        bg = Image.alpha_composite(bg.convert("RGBA"), offset_layer).convert("RGB")
    bg.paste(pets, (0, 0), pets)
    bg.convert("RGB").save(out_jpg, quality=90)
    return out_jpg


def frames_to_mp4(frame_dir: Path, fps: float, out_mp4: Path) -> Path:
    cmd = [
        "ffmpeg", "-y", "-framerate", f"{fps}",
        "-i", str(frame_dir / "out_%04d.jpg"),
        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
        "-vf", "fps={}".format(fps),
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_mp4


def main() -> int:
    p = argparse.ArgumentParser(description="Background composite POC")
    p.add_argument("--in", dest="in_mp4", required=True)
    p.add_argument("--bg", required=True, help="Background photo (jpg)")
    p.add_argument("--out", required=True, help="Output mp4")
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--model", default="isnet-general-use",
                   help="rembg model — isnet-general-use / u2net / silueta")
    p.add_argument("--no-shadow", action="store_true", help="skip contact shadow")
    p.add_argument("--max-frames", type=int, default=0,
                   help="cap frames (for fast POC); 0 = all")
    args = p.parse_args()

    try:
        from rembg import new_session  # noqa: F401
    except ImportError:
        print("ERROR: rembg not installed. Run: pip install rembg pillow",
              file=sys.stderr)
        return 2

    in_mp4 = Path(args.in_mp4).resolve()
    bg = Path(args.bg).resolve()
    out = Path(args.out).resolve()
    for p_ in (in_mp4, bg):
        if not p_.exists():
            print(f"ERROR: {p_} not found", file=sys.stderr)
            return 2

    from rembg import new_session
    session = new_session(args.model)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        in_frames_dir = td_path / "in_frames"
        out_frames_dir = td_path / "out_frames"
        out_frames_dir.mkdir(parents=True)

        print(f"→ extracting frames @ {args.fps}fps...")
        frames, fps = extract_video_frames(in_mp4, in_frames_dir, args.fps)
        if args.max_frames and len(frames) > args.max_frames:
            step = len(frames) // args.max_frames
            frames = frames[::step][:args.max_frames]
        print(f"  ✓ {len(frames)} frames to process")

        print(f"→ rembg ({args.model}) + composite (this is the slow step)...")
        from PIL import Image
        bg_img = Image.open(bg).convert("RGB")
        for i, f in enumerate(frames):
            rgba = td_path / f"rgba_{i:04d}.png"
            out_jpg = out_frames_dir / f"out_{i+1:04d}.jpg"
            remove_bg_frame(f, rgba, session=session)
            # Resize bg to match frame size on first call
            tmp_bg = td_path / "bg_resized.jpg"
            if not tmp_bg.exists():
                first_frame = Image.open(f)
                resized = bg_img.resize(first_frame.size, Image.LANCZOS)
                resized.save(tmp_bg)
            composite_onto_bg(rgba, tmp_bg, out_jpg,
                              add_shadow=not args.no_shadow)
            if (i + 1) % 10 == 0:
                print(f"  ... {i+1}/{len(frames)}")
        print("→ encoding mp4...")
        frames_to_mp4(out_frames_dir, fps, out)
        print(f"  ✓ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
