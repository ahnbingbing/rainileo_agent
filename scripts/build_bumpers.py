"""
scripts/build_bumpers.py
------------------------
Pre-render the reusable channel intro/outro bumper mp4s from the static
banner_card_1080x1920.png. Once built, these become channel-wide branding
assets — every episode prepends/appends the same files.

What it does
------------
1. intro_bumper.mp4 (1.5s by default):
     slow Ken Burns zoom-IN on banner_card, fade-in from black.
     No caption — just brand visual flash.
2. outro_bumper.mp4 (2.5s by default):
     slow Ken Burns zoom-OUT, fade-in caption text ("행복한 부처님 오신날!" or
     custom), fade-out to black at the end.

Both are written to assets/branding/ so they're versioned alongside the
banner source.

Why ffmpeg-only (no Veo / no AI motion)
---------------------------------------
- Bumpers should be 100% deterministic and reproducible — Veo is stochastic.
- They contain trademarked layout (channel name) — we don't want a generative
  model re-rendering "Ryani & Leo" text every time.
- Cost: $0. Pre-rendered once, reused forever.

Usage
-----
    # default — generate both bumpers with default copy
    python3 scripts/build_bumpers.py

    # change the outro caption per episode (rare — usually keep the same)
    python3 scripts/build_bumpers.py \
        --outro-caption "행복한 부처님 오신날!" \
        --outro-caption-en "Have a blessed Buddha's Day!"

    # custom durations
    python3 scripts/build_bumpers.py --intro-sec 2.0 --outro-sec 3.0

    # custom source banner (e.g., per-season variant)
    python3 scripts/build_bumpers.py \
        --banner assets/branding/channel_banner_summer.png

Outputs
-------
    assets/branding/intro_bumper.mp4   — 1.5s, 1080×1920, no audio
    assets/branding/outro_bumper.mp4   — 2.5s, 1080×1920, no audio

Both are silent (no audio track). assemble_episode.py will overlay the BGM
across the full episode duration; bumpers don't need their own audio.

Dependencies
------------
ffmpeg with drawtext + zoompan + fade (all core filters). Pretendard font
expected at ~/Library/Fonts/Pretendard-Bold.otf (matching burn_captions.py).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANNER_DEFAULT = ROOT / "data" / "output" / "capcut_package" / "banner_card_1080x1920.png"
OUT_DIR_DEFAULT = ROOT / "assets" / "branding"

FONT_BOLD = Path.home() / "Library" / "Fonts" / "Pretendard-Bold.otf"
FONT_MEDIUM = Path.home() / "Library" / "Fonts" / "Pretendard-Medium.otf"

# Default outro copy (override via CLI for one-off seasonal variants)
DEFAULT_OUTRO_KO = "행복한 부처님 오신날!"
DEFAULT_OUTRO_EN = "Have a blessed Buddha's Day!"

# Default outro CTA — channel-wide subscribe/like prompt
DEFAULT_OUTRO_HANDLE = "@ryani_n_loe"
DEFAULT_OUTRO_CTA_KO = "구독 좋아요"
DEFAULT_OUTRO_CTA_EN = "Like & Subscribe"

W, H = 1080, 1920          # Shorts vertical
FPS = 30


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _render_bumper(banner: Path, out: Path, dur_s: float, vf: str,
                   music: Path | None) -> None:
    """Run the ffmpeg invocation for a bumper.

    If `music` is provided, that audio is trimmed to `dur_s` with a short
    fade-in/fade-out and baked into the output. Otherwise the output is
    silent (-an). assemble_episode.py inspects the bumper for an audio
    track and routes accordingly: with-audio bumpers retain their audio
    in the final assembly, silent bumpers get the main BGM laid over them.
    """
    total_frames = int(round(dur_s * FPS))
    fade_audio_in = 0.15        # quick — bumper is already short
    fade_audio_out = 0.3        # gentle tail so concat seam is smooth
    fadeout_start = max(0.0, dur_s - fade_audio_out)

    cmd: list[str] = [
        "ffmpeg", "-y", "-nostats", "-loglevel", "error",
        "-loop", "1", "-i", str(banner),
    ]
    if music:
        cmd += ["-i", str(music)]

    cmd += [
        "-frames:v", str(total_frames),
        "-r", str(FPS),
    ]
    if music:
        # filter_complex with explicit labels — avoids ffmpeg's auto-mapping
        # confusion when -loop 1 is on input 0 (it conflates audio stream
        # detection with video looping and truncates the audio output).
        # Order: trim source → reset PTS so fades anchor at t=0 → light
        # volume duck so channel theme isn't deafening → fades.
        af = (
            f"atrim=duration={dur_s:.3f},"
            f"asetpts=PTS-STARTPTS,"
            f"volume=0.85,"
            f"afade=t=in:st=0:d={fade_audio_in},"
            f"afade=t=out:st={fadeout_start:.3f}:d={fade_audio_out}"
        )
        fc = f"[0:v]{vf}[vout];[1:a]{af}[aout]"
        cmd += [
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k",
        ]
    else:
        cmd += ["-vf", vf, "-an"]

    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out),
    ]
    subprocess.run(cmd, check=True)


def build_intro(banner: Path, out: Path, dur_s: float,
                music: Path | None = None) -> None:
    """Slow zoom-IN over `dur_s` seconds + fade-in from black for first 0.4s.

    zoompan params (this is the trickiest filter to read):
      z='zoom+0.0008'   per-frame zoom step; starts at 1.0 and ramps up
      d=1               output one frame per input (we loop a single image)
      s=WxH             output dimensions
      x/y               pan center — we keep banner centered

    With dur_s=1.5 @ 30fps = 45 frames, zoom step 0.0008 → final zoom ≈ 1.036
    (subtle, intentional — too aggressive looks like a jump scare).
    """
    zoom_step = 0.0008  # per-frame; gentle
    vf = (
        f"zoompan=z='zoom+{zoom_step}':d=1:s={W}x{H}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
        f"fade=t=in:st=0:d=0.4"
    )
    _render_bumper(banner, out, dur_s, vf, music)


def build_outro(banner: Path, out: Path, dur_s: float,
                caption_ko: str = "", caption_en: str = "",
                music: Path | None = None,
                handle: str = "", cta_ko: str = "", cta_en: str = "") -> None:
    """Slow zoom-OUT + optional CTA + tail fade-to-black.

    Iteration history:
      v1: plain white drawtext holiday caption — too small, blended in
      v2: gold + dark plate — too busy, off-brand
      v3: no captions, clean brand stamp — too plain, no CTA
      v4 (current): handle + ❤ wrapped CTA on bottom strip ("러블리" tone).
          Hearts use U+2665 (♥) which renders in Pretendard. Pink fontcolor
          + thin black outline so it pops against the warm cream banner.

    Sequence within `dur_s`:
      0.0   banner slightly zoomed-in, no CTA yet
      0.4   handle fades in
      0.7   KO CTA fades in
      1.0   EN CTA fades in
      dur-0.4  whole frame fades to black

    Caption args (caption_ko/en) remain in signature for backward compat —
    ignored. holiday wish lives in cut4's caption.
    """
    del caption_ko, caption_en
    fade_out_start = max(0.0, dur_s - 0.4)
    zoom_step = 0.0006
    z_expr = f"if(eq(on,0),1.05,zoom-{zoom_step})"

    vf = (
        f"zoompan=z='{z_expr}':d=1:s={W}x{H}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
        f"fade=t=out:st={fade_out_start}:d=0.4"
    )

    if handle or cta_ko or cta_en:
        # Heart character U+2665 (♥). Pretendard ships with it. We wrap it
        # in the text itself rather than as a separate drawtext layer so the
        # ❤ stays glued to the message — simpler positioning.
        tmp_dir = out.parent / "_bumper_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        cta_lines: list[tuple[str, str, int, str, int, str, float]] = []
        # tuple: (filename, content, fontsize, fontcolor, borderw, font, fade_start)
        if handle:
            f = tmp_dir / "outro_handle.txt"
            f.write_text(handle, encoding="utf-8")
            cta_lines.append(
                (str(f), handle, 40, "white", 3, str(FONT_MEDIUM), 0.4)
            )
        if cta_ko:
            f = tmp_dir / "outro_cta_ko.txt"
            ko_with_hearts = f"♥  {cta_ko}  ♥"
            f.write_text(ko_with_hearts, encoding="utf-8")
            cta_lines.append(
                (str(f), ko_with_hearts, 60, "#FF6B9D", 4, str(FONT_BOLD), 0.7)
            )
        if cta_en:
            f = tmp_dir / "outro_cta_en.txt"
            en_with_hearts = f"♥  {cta_en}  ♥"
            f.write_text(en_with_hearts, encoding="utf-8")
            cta_lines.append(
                (str(f), en_with_hearts, 44, "#FF6B9D", 3, str(FONT_MEDIUM), 1.0)
            )

        # Position the lines from bottom: last line lowest, walking up. Each
        # line gets a different baseline so they stack cleanly.
        # Layout: handle (top, smaller), KO (middle, hero), EN (bottom).
        # y positions are baseline-from-bottom; subtract text_h at render time.
        y_anchors = [280, 200, 120][:len(cta_lines)]
        # If handle missing, KO becomes top so adjust y to keep stack centered
        if not handle and cta_ko and cta_en:
            y_anchors = [200, 120]
        elif handle and not cta_ko and cta_en:
            y_anchors = [280, 120]

        draw_chain = []
        for (textfile, _content, size, color, bw, font, fs), y_offset in zip(cta_lines, y_anchors):
            alpha = f"if(lt(t,{fs}),0,if(lt(t,{fs+0.3}),(t-{fs})/0.3,1))"
            draw_chain.append(
                f"drawtext=fontfile='{font}':textfile='{textfile}':"
                f"fontsize={size}:fontcolor={color}:"
                f"borderw={bw}:bordercolor=black:"
                f"shadowcolor=#FF6B9D@0.4:shadowx=0:shadowy=3:"
                f"x=(w-text_w)/2:y=h-text_h-{y_offset}:"
                f"alpha='{alpha}'"
            )
        # Insert CTA drawtexts BEFORE the fade-out, so the fade dims them too.
        vf = vf.replace(
            f"fade=t=out:st={fade_out_start}",
            ",".join(draw_chain) + f",fade=t=out:st={fade_out_start}"
        )

    _render_bumper(banner, out, dur_s, vf, music)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--banner", default=str(BANNER_DEFAULT),
                   help=f"banner source png (default: {_rel(BANNER_DEFAULT)})")
    p.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT),
                   help=f"output dir (default: {_rel(OUT_DIR_DEFAULT)})")
    p.add_argument("--intro-sec", type=float, default=1.5,
                   help="intro bumper duration (default 1.5s)")
    p.add_argument("--outro-sec", type=float, default=2.5,
                   help="outro bumper duration (default 2.5s)")
    p.add_argument("--intro-music", default=None,
                   help="optional audio track baked into intro bumper "
                        "(channel theme — upbeat short clip). When set, "
                        "assemble_episode.py picks up this audio in the "
                        "final mix instead of laying main BGM over the intro.")
    p.add_argument("--outro-music", default=None,
                   help="optional audio track baked into outro bumper. "
                        "Usually the same as --intro-music for brand "
                        "consistency, but can differ.")
    p.add_argument("--outro-handle", default=DEFAULT_OUTRO_HANDLE,
                   help=f"channel handle shown on outro (default: '%(default)s'). "
                        f"Pass empty string to hide.")
    p.add_argument("--outro-cta-ko", default=DEFAULT_OUTRO_CTA_KO,
                   help=f"KO CTA shown on outro (default: '%(default)s'). "
                        f"Renders as '♥ <text> ♥'. Empty string to hide.")
    p.add_argument("--outro-cta-en", default=DEFAULT_OUTRO_CTA_EN,
                   help=f"EN CTA shown on outro (default: '%(default)s'). "
                        f"Renders as '♥ <text> ♥'. Empty string to hide.")
    p.add_argument("--outro-caption", default=DEFAULT_OUTRO_KO,
                   help="KO outro caption (default: '%(default)s')")
    p.add_argument("--outro-caption-en", default=DEFAULT_OUTRO_EN,
                   help="EN outro caption (default: '%(default)s')")
    p.add_argument("--intro-only", action="store_true",
                   help="build only intro (skip outro)")
    p.add_argument("--outro-only", action="store_true",
                   help="build only outro (skip intro)")
    args = p.parse_args()

    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found in PATH", file=sys.stderr)
        return 2

    banner = Path(args.banner)
    if not banner.exists():
        print(f"ERROR: banner not found at {_rel(banner)}", file=sys.stderr)
        return 2

    # Resolve optional music args (None if not provided or file missing).
    def _music_or_none(p: str | None, label: str) -> Path | None:
        if not p:
            return None
        mp = Path(p)
        if not mp.exists():
            print(f"ERROR: {label} music not found at {_rel(mp)}",
                  file=sys.stderr)
            sys.exit(2)
        return mp

    intro_music = _music_or_none(args.intro_music, "--intro-music")
    outro_music = _music_or_none(args.outro_music, "--outro-music")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.outro_only:
        intro_path = out_dir / "intro_bumper.mp4"
        print(f"==> intro_bumper ({args.intro_sec}s)"
              + (f" + audio {_rel(intro_music)}" if intro_music else " silent"))
        try:
            build_intro(banner, intro_path, args.intro_sec, music=intro_music)
        except subprocess.CalledProcessError as e:
            print(f"  ! ffmpeg failed (rc={e.returncode})", file=sys.stderr)
            return 1
        size_mb = intro_path.stat().st_size / 1e6
        print(f"  ok ({size_mb:.2f} MB) → {_rel(intro_path)}")

    if not args.intro_only:
        outro_path = out_dir / "outro_bumper.mp4"
        print(f"==> outro_bumper ({args.outro_sec}s)"
              + (f" + audio {_rel(outro_music)}" if outro_music else " silent"))
        try:
            build_outro(banner, outro_path, args.outro_sec,
                        args.outro_caption, args.outro_caption_en,
                        music=outro_music,
                        handle=args.outro_handle,
                        cta_ko=args.outro_cta_ko,
                        cta_en=args.outro_cta_en)
        except subprocess.CalledProcessError as e:
            print(f"  ! ffmpeg failed (rc={e.returncode})", file=sys.stderr)
            return 1
        size_mb = outro_path.stat().st_size / 1e6
        print(f"  ok ({size_mb:.2f} MB) → {_rel(outro_path)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
