#!/usr/bin/env python3
"""scripts/edit_style.py — post-assembly "impact edit" style layer (Phase 0 demo).

Takes a finished episode mp4 and re-renders it in one of three high-impact edit
styles so PD can eyeball the LOOK before we wire edit_style as a bandit arm:

  • punch_club        vibrant teal push, beat-synced zoom pulses, grain+vignette
  • premium_cinematic teal-orange grade, slow continuous push-in, thin bars, film grain
  • meme_comedy       bright punchy grade, big snappy beat zoom-punches

The beat grid is estimated from the mp4's own audio (BGM) with a lightweight
scipy onset+tempo autocorrelation — no librosa needed. Steady BGM → a constant
tempo/phase pulse, which is robust and cheap. If detection fails we fall back to
120 BPM / phase 0.

This is a POST pass on the finished mp4 (captions already burned, cuts fixed), so
it demonstrates color / zoom-punch / texture — the dominant perceived "style".
Structural elements (cold-open hook, seamless loop, kinetic captions, SFX) come in
Phase 1 when this becomes a real bandit arm wired into the assembler.

Usage:
  scripts/edit_style.py --in EP.mp4 --style punch_club --out OUT.mp4
  scripts/edit_style.py --in EP.mp4 --all --outdir data/output/style_demo
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

STYLES = ("punch_club", "premium_cinematic", "meme_comedy")


# ──────────────────────────────────────────────────────────────────────
# Beat / tempo estimation (scipy-free: numpy STFT flux + autocorrelation)
# ──────────────────────────────────────────────────────────────────────
def onset_envelope(mp4: Path, sr: int = 22050) -> tuple[np.ndarray, float]:
    """Extract a normalized spectral-flux onset envelope + its sample rate.

    Returns (flux, fps_env). flux is empty on failure."""
    try:
        raw = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(mp4), "-ac", "1", "-ar", str(sr),
             "-f", "f32le", "-"],
            capture_output=True, check=True).stdout
        x = np.frombuffer(raw, dtype=np.float32)
        if x.size < sr:  # < 1s of audio
            return np.array([]), 0.0
        hop, win = 512, 1024
        n_frames = 1 + (x.size - win) // hop
        if n_frames < 8:
            return np.array([]), 0.0
        window = np.hanning(win).astype(np.float32)
        idx = np.arange(win)[None, :] + hop * np.arange(n_frames)[:, None]
        frames = x[idx] * window
        mag = np.abs(np.fft.rfft(frames, axis=1))
        flux = np.maximum(0.0, np.diff(mag, axis=0)).sum(axis=1)
        flux = flux - flux.mean()
        flux = np.maximum(0.0, flux)
        if flux.max() <= 0:
            return np.array([]), 0.0
        return flux / flux.max(), sr / hop
    except Exception:
        return np.array([]), 0.0


def detect_accents(mp4: Path, *, k: int = 10, min_gap: float = 1.1) -> list[float]:
    """Top-`k` loud audio accents (reaction/punchline moments), spaced ≥min_gap
    apart. These are the moments a comedy edit should punch — not every beat."""
    flux, fps_env = onset_envelope(mp4)
    if flux.size == 0:
        return []
    # smooth a touch so we pick peaks not single-frame spikes
    kern = np.ones(3) / 3.0
    sm = np.convolve(flux, kern, mode="same")
    order = np.argsort(sm)[::-1]
    picked: list[int] = []
    gap = int(min_gap * fps_env)
    for i in order:
        if sm[i] < 0.25:
            break
        if all(abs(i - j) >= gap for j in picked):
            picked.append(int(i))
        if len(picked) >= k:
            break
    return sorted(t / fps_env for t in picked)


def detect_cuts(mp4: Path, thresh: float = 0.30) -> list[float]:
    """Hard-cut timestamps via ffmpeg scene detection. Comedy punches on the cut."""
    try:
        out = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(mp4), "-vf",
             f"select='gt(scene,{thresh})',metadata=print:file=-",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True, check=True).stdout
        times = []
        for line in out.splitlines():
            if "pts_time:" in line:
                try:
                    times.append(float(line.split("pts_time:")[1].split()[0]))
                except Exception:
                    pass
        return sorted(times)
    except Exception:
        return []


def merge_events(*groups: list[float], min_gap: float = 0.4) -> list[float]:
    """Merge event-time lists, dropping any within min_gap of an earlier one."""
    allt = sorted(t for g in groups for t in g if t is not None)
    out: list[float] = []
    for t in allt:
        if not out or t - out[-1] >= min_gap:
            out.append(round(t, 3))
    return out


def estimate_tempo_phase(mp4: Path, sr: int = 22050) -> tuple[float, float]:
    """Return (period_seconds, phase_seconds) of the dominant beat.

    autocorrelation peak of the onset envelope in a plausible BPM band → constant
    tempo for the club pulse. Falls back to 120 BPM / 0 phase on any failure.
    """
    default = (60.0 / 120.0, 0.0)
    try:
        flux, fps_env = onset_envelope(mp4)
        if flux.size == 0:
            return default
        # autocorrelation
        ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
        # search 70–170 BPM
        lo = int(fps_env * 60.0 / 170.0)
        hi = int(fps_env * 60.0 / 70.0)
        hi = min(hi, len(ac) - 1)
        if hi <= lo + 1:
            return default
        lag = lo + int(np.argmax(ac[lo:hi]))
        period = lag / fps_env
        # phase: correlate a period-spaced pulse train against flux, pick best offset
        best_off, best_score = 0, -1.0
        step = max(1, lag // 12)
        for off in range(0, lag, step):
            score = flux[off::lag].sum()
            if score > best_score:
                best_score, best_off = score, off
        phase = best_off / fps_env
        # sanity clamp
        if not (0.3 <= period <= 1.0):
            return default
        return (period, phase)
    except Exception:
        return default


# ──────────────────────────────────────────────────────────────────────
# Filtergraph per style
# ──────────────────────────────────────────────────────────────────────
def _beat_zoom(amp: float, decay: float, period: float, phase: float,
               fps: int = 30) -> str:
    """zoompan beat pulse: zoom in by up to `amp`, decaying each beat.

    z(on) = 1 + amp*exp(-decay * frac), frac = position within the beat, using the
    output frame index `on` (on/fps = seconds) which every ffmpeg build supports.
    crop's w/h are init-time only (no per-frame `t`), so zoompan is the reliable
    way to animate zoom on continuous video."""
    tt = f"(on/{fps})"
    z = f"(1+{amp}*exp(-{decay}*mod({tt}-{phase:.3f}\\,{period:.3f})/{period:.3f}))"
    return (f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:fps={fps}:s=1080x1920")


def build_filter(style: str, period: float, phase: float, dur: float) -> str:
    if style == "punch_club":
        grade = ("eq=contrast=1.14:saturation=1.36:brightness=0.010:gamma=0.98,"
                 "colorbalance=rs=-0.06:gs=0.0:bs=0.08:rh=0.05:gh=0.0:bh=-0.04,"
                 "curves=preset=increase_contrast")
        motion = _beat_zoom(amp=0.065, decay=6.0, period=period, phase=phase)
        texture = "vignette=PI/4.5,noise=alls=5"
        return f"{grade},{motion},{texture},format=yuv420p"

    if style == "premium_cinematic":
        grade = ("eq=contrast=1.06:saturation=1.05:brightness=-0.006:gamma=1.02,"
                 "colorbalance=rs=-0.08:gs=-0.02:bs=0.09:rh=0.10:gh=0.02:bh=-0.06,"
                 "curves=preset=lighter")
        # slow continuous push-in over the whole clip (Ken Burns), no beat pulse
        fps = 30
        z = f"(1+0.045*min((on/{fps})/{max(dur,0.1):.2f}\\,1))"
        push = (f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d=1:fps={fps}:s=1080x1920")
        bars = ("drawbox=x=0:y=0:w=iw:h=96:color=black@1.0:t=fill,"
                "drawbox=x=0:y=ih-96:w=iw:h=96:color=black@1.0:t=fill")
        texture = "vignette=PI/5,noise=alls=3"
        return f"{grade},{push},{texture},{bars},format=yuv420p"

    if style == "meme_comedy":
        grade = ("eq=contrast=1.10:saturation=1.28:brightness=0.020:gamma=0.96,"
                 "colorbalance=rs=0.03:gs=0.0:bs=-0.02:rh=0.04:gh=0.02:bh=-0.02,"
                 "curves=preset=increase_contrast")
        # bigger, snappier beat punches (sharper decay, more amp)
        motion = _beat_zoom(amp=0.11, decay=9.0, period=period, phase=phase)
        texture = "vignette=PI/6"
        return f"{grade},{motion},{texture},format=yuv420p"

    raise SystemExit(f"unknown style {style}")


def probe_duration(mp4: Path) -> float:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(mp4)],
            capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except Exception:
        return 25.0


def render(src: Path, style: str, out: Path) -> Path:
    period, phase = estimate_tempo_phase(src)
    dur = probe_duration(src)
    vf = build_filter(style, period, phase, dur)
    out.parent.mkdir(parents=True, exist_ok=True)
    bpm = round(60.0 / period, 1)
    print(f"[{style}] tempo≈{bpm}bpm period={period:.3f}s phase={phase:.3f}s dur={dur:.1f}s")
    cmd = [FFMPEG, "-y", "-i", str(src), "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print(f"[{style}] → {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--style", choices=STYLES)
    ap.add_argument("--all", action="store_true", help="render all 3 styles")
    ap.add_argument("--out")
    ap.add_argument("--outdir", default="data/output/style_demo")
    args = ap.parse_args()
    src = Path(args.src)
    if not src.exists():
        raise SystemExit(f"missing {src}")
    if args.all:
        stem = src.stem
        for st in STYLES:
            render(src, st, Path(args.outdir) / f"{stem}__{st}.mp4")
    else:
        if not args.style:
            raise SystemExit("--style or --all required")
        out = Path(args.out) if args.out else Path(args.outdir) / f"{src.stem}__{args.style}.mp4"
        render(src, args.style, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
