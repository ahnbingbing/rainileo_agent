"""
scripts/assemble_episode.py
---------------------------
Stitch the 5 captioned cuts into a single episode mp4 + add background music
with a fade-in at the start and a fade-out at the end. Replaces the manual
CapCut assembly step.

Pipeline
--------
    data/output/animated_captioned/cut1..5_*.mp4   (in CUT_ORDER below)
    + assets/music/<track>.mp3                      (user-provided)
    →  data/output/episodes/episode_<ts>.mp4

What this does
--------------
1. ffprobe each cut for exact duration.
2. ffmpeg `concat` filter chains the 5 video streams (audio stripped — Veo
   lite audio is unreliable and we're laying our own BGM anyway).
3. Music gets:
     • `aloop` so it repeats if shorter than the episode
     • `atrim` to exact episode length
     • `volume` ducking so BGM is present but not overpowering
     • `afade=t=in`  — soft start (avoids harsh feed-start)
     • `afade=t=out` — tail fade matching the last cut's visual close
4. Encodes H.264 + AAC, +faststart, ready to upload as a Short.

Where to drop the music
-----------------------
Default expected path: `assets/music/bgm.mp3`. Override with --music PATH.

Free, YouTube-cleared music sources (cute/playful for pet content):
  • Pixabay Music   https://pixabay.com/music/   (direct mp3, no account)
       tags worth searching: "cute", "ukulele", "happy", "whistle"
  • Bensound        https://www.bensound.com/   (free with attribution;
       try "buddy", "ukulele", "cute" — all under 30 sec or loopable)
  • YouTube Audio Library  (inside YT Studio — cleanest license, auto-cleared
       for monetization; search "Children" / "Happy" categories)

Usage
-----
    # default — uses assets/music/bgm.mp3, writes data/output/episodes/episode_<ts>.mp4
    python3 scripts/assemble_episode.py

    # different track
    python3 scripts/assemble_episode.py --music assets/music/buddy.mp3

    # fixed output filename
    python3 scripts/assemble_episode.py --out data/output/episodes/v1.mp4

    # tweak audio mix
    python3 scripts/assemble_episode.py --volume 0.7 --fadeout 3.0 --fadein 0.8

    # just print the ffmpeg command, don't run it (for sanity-checking)
    python3 scripts/assemble_episode.py --dry-run

Exit codes
----------
    0   ok
    1   missing cuts or ffmpeg failed
    2   bad setup (music missing, ffmpeg missing)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPTIONED_DIR = ROOT / "data" / "output" / "animated_captioned"
EPISODE_DIR_DEFAULT = ROOT / "data" / "output" / "episodes"
MUSIC_DEFAULT = ROOT / "assets" / "music" / "bgm.mp3"
# Default captions manifest = Episode 01. Episode 02+ pass --captions to point
# at their own JSON. Cut order is derived from the manifest's dict iteration
# (Python 3.7+ preserves insertion order, so the JSON's ordering is the arc).
CAPTIONS_DEFAULT = ROOT / "scripts" / "prompts" / "captions_bilingual.json"


def load_cut_order(captions_path: Path) -> list[str]:
    """Read the captions manifest, return cut tags in declared order.

    Skips meta keys (starting with `_`) and any entries whose value isn't a
    dict (Episode 02's `_outro_caption` is a dict but the underscore filter
    catches it — that one is rendered by build_bumpers.py, not assembly).
    """
    raw = json.loads(captions_path.read_text(encoding="utf-8"))
    return [k for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict)]


def load_tempo_factors(captions_path: Path) -> dict[str, float]:
    """Read optional `_tempo_factors` meta from captions manifest.

    Maps cut tag → setpts speed (e.g., 0.85 = slow, 1.3 = default, 1.6 = fast).
    Cameraman embeds these from Director's per-cut `tempo_factor` field.
    Missing or unknown → caller falls back to the global cut_speed default.
    """
    raw = json.loads(captions_path.read_text(encoding="utf-8"))
    tf = raw.get("_tempo_factors") or {}
    if not isinstance(tf, dict):
        return {}
    out: dict[str, float] = {}
    for tag, speed in tf.items():
        try:
            s = float(speed)
            if 0.5 <= s <= 2.0:
                out[tag] = s
        except (TypeError, ValueError):
            continue
    return out

# Audio mix defaults (overridable via CLI)
MUSIC_VOLUME = 0.55      # 55% — present but not overpowering. If you later
                         # add narration/SFX, leave more headroom (~0.4).
MUSIC_FADEIN_S = 0.5     # gentle ramp-in so the music doesn't snap on at t=0
MUSIC_FADEOUT_S = 2.0    # tail fade — last 2s of the episode dip to silence

# Episode output dimensions (YouTube Shorts spec). All input clips get
# scaled+padded to this in the filter graph so concat sees uniform streams —
# bumpers may be 1080×1920 (rendered by build_bumpers.py) while content cuts
# can be 720×1280 (Veo default) or 1080×1920 (Veo standard). Without this
# normalization, concat fails with "input link parameters do not match".
EPISODE_W = 1080
EPISODE_H = 1920


def _rel(p: Path) -> str:
    """Repo-relative path for logging; falls back to absolute."""
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def ffprobe_duration(mp4: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(mp4)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def probe_mean_luma(mp4: Path) -> float:
    """Mean Y (luminance) of the clip via ffmpeg signalstats. 0-255 scale.

    Used by build_cmd to balance brightness across cuts — without this, AI-
    generated cuts can swing wildly (one cut feels pre-dawn, the next noon).
    We probe each cut, compute the median, and apply per-cut `eq=brightness`
    offsets to pull each toward that median.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(mp4),
        "-vf", "signalstats,metadata=mode=print:key=lavfi.signalstats.YAVG",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return 128.0
    import re as _re
    vals = _re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", proc.stderr)
    if not vals:
        return 128.0  # mid-gray fallback
    return sum(float(v) for v in vals) / len(vals)


def has_audio_stream(mp4: Path) -> bool:
    """True if the mp4 has at least one audio stream. Used to decide whether
    a bumper carries its own (channel-theme) audio that should pass through
    to the final mix, or whether the main BGM should cover it instead."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type",
         "-of", "default=nw=1:nk=1", str(mp4)],
        capture_output=True, text=True,
    )
    return "audio" in r.stdout


def build_cmd(clips: list[Path], cut_durations: list[float],
              intro_idx: int | None, outro_idx: int | None,
              music: Path, out: Path,
              volume: float, fadein: float, fadeout: float,
              cut_speed: float = 1.3,
              per_cut_speeds: dict[int, float] | None = None,
              xfade_into: set[int] | None = None,
              xfade_dur: float = 0.2) -> list[str]:
    """Build the full ffmpeg invocation.

    `clips` is the FULL ordered list of video inputs in the final episode.
    `intro_idx` and `outro_idx` mark which positions (if any) are bumpers —
    we probe them for their own audio. If a bumper has audio (channel-theme
    music), that audio is preserved in the final mix. The main BGM covers
    only the content-cuts portion. Silent bumpers fall back to BGM covering
    them too (legacy behavior).

    Video graph (always the same):
      • Each input normalized to EPISODE_W × EPISODE_H + uniform SAR + 30fps.
      • Concatenated into [vout]. a=0 because audio is handled separately.

    Audio graph (one of three shapes):
      A. No bumpers or all silent bumpers:
           main BGM trimmed to total_duration → faded → [aout]
      B. One bumper has audio:
           [bumper_audio] + [BGM_for_other_sections] → concat → [aout]
      C. Both bumpers have audio:
           [intro_a] + [bgm_for_cuts] + [outro_a] → concat → [aout]
    """
    n = len(clips)
    music_idx = n   # music is appended as last input, zero-indexed
    bumper_indices = {idx for idx in (intro_idx, outro_idx) if idx is not None}

    # Probe each cut's mean luminance (skip bumpers — those are channel-fixed).
    # Compute the median target so per-cut brightness deltas pull each cut
    # toward a common middle, smoothing the dawn/noon swings the user noted.
    cut_indices = [i for i in range(n) if i not in bumper_indices]
    cut_lumas: dict[int, float] = {}
    for i in cut_indices:
        cut_lumas[i] = probe_mean_luma(clips[i])
    if cut_lumas:
        sorted_lumas = sorted(cut_lumas.values())
        target_luma = sorted_lumas[len(sorted_lumas) // 2]
    else:
        target_luma = 128.0

    # ─── video chain (uniform across all audio shapes) ────────────────────
    # Cuts get a per-cut setpts speedup. Director can pick slower (0.85x for
    # 살랑살랑 observation/emotion) or faster (1.6x for action) per cut via
    # `tempo_factor` in concept_cuts; falls back to `cut_speed` default.
    # Bumpers stay at 1.0x so channel-theme CTA timing is intact.
    # Cuts also get an `eq=brightness` offset to align their median luma.
    norm_chains = []
    per_cut_speeds = per_cut_speeds or {}
    for i in range(n):
        if i in bumper_indices:
            speed = 1.0
        else:
            speed = per_cut_speeds.get(i, cut_speed)
        setpts_filter = f"setpts=PTS/{speed}," if speed != 1.0 else ""
        # PD 2026-06-04: brightness normalization DISABLED. PD wants original
        # footage preserved as-is — "갑자기 루미넌스 체인지 있는데 그냥
        # 되도록이면 원본 그대로 가도록할래?". The per-cut eq=brightness was
        # creating visible luminance jumps between cuts. Keeping source
        # exposure untouched. Re-enable via ASSEMBLE_LUMA_NORMALIZE=1 only
        # if a future episode genuinely needs cross-cut exposure matching.
        eq_filter = ""
        if os.getenv("ASSEMBLE_LUMA_NORMALIZE", "0") == "1" and i in cut_lumas:
            delta = (target_luma - cut_lumas[i]) / 100.0
            delta = max(-0.5, min(0.5, delta))
            if abs(delta) > 0.05:  # only correct large exposure gaps
                eq_filter = f"eq=brightness={delta:.3f},"
        norm_chains.append(
            f"[{i}:v]{setpts_filter}{eq_filter}"
            f"scale={EPISODE_W}:{EPISODE_H}:force_original_aspect_ratio=decrease,"
            f"pad={EPISODE_W}:{EPISODE_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps=30[v{i}]"
        )
    v_inputs = "".join(f"[v{i}]" for i in range(n))
    # PD 2026-06-09 보강 옵션: between CHAINED one-take cuts, a short crossfade
    # (dissolve) instead of a hard cut — smooths any minor chain discontinuity
    # WITHOUT the fade-to-black flash. `xfade_into` = the set of clip indices
    # whose transition INTO them should dissolve (a chained cut). All other
    # boundaries (bumpers, true scene cuts) stay hard concat. Default (empty
    # set) = the existing single concat, so nothing changes unless enabled.
    xfade_into = xfade_into or set()
    if xfade_into:
        def _post_dur(idx: int) -> float:
            raw = ffprobe_duration(clips[idx])
            sp = 1.0 if idx in bumper_indices else per_cut_speeds.get(idx, cut_speed)
            return raw / sp if sp else raw
        # xfade requires BOTH inputs at an identical timebase, but `concat`
        # emits tb=1/1000000 while a raw fps=30 input is tb=1/30 — feeding a
        # concat result straight into xfade fails ("timebase do not match").
        # Stamp every transition output with settb=1/30 so the running `acc`
        # label always matches the next raw [vi] input.
        TB = "settb=1/30"
        seg = []
        acc = "[v0]"
        acc_dur = _post_dur(0)
        for i in range(1, n):
            lbl = "[vout]" if i == n - 1 else f"[vx{i}]"
            if i in xfade_into:
                off = max(0.0, acc_dur - xfade_dur)
                seg.append(f"{acc}[v{i}]xfade=transition=fade:"
                           f"duration={xfade_dur}:offset={off:.3f},{TB}{lbl}")
                acc_dur = off + _post_dur(i)
            else:
                seg.append(f"{acc}[v{i}]concat=n=2:v=1:a=0,{TB}{lbl}")
                acc_dur = acc_dur + _post_dur(i)
            acc = lbl
        video_chain = ";".join(norm_chains) + ";" + ";".join(seg)
        xfade_total = xfade_dur * len(xfade_into)
    else:
        video_chain = ";".join(norm_chains) + ";" + \
                      f"{v_inputs}concat=n={n}:v=1:a=0[vout]"
        xfade_total = 0.0

    # Adjust cut_durations for BGM timing — each cut shrinks by its own speed.
    if cut_durations:
        new_durations = []
        # cut_durations is in clip-index order EXCLUDING bumpers (per caller),
        # but we need to keep this list aligned with how the caller uses it.
        # Currently assemble's callers pass cut_durations covering all the
        # non-bumper cuts. We apply each cut's actual speed.
        cut_idx = 0
        for i in range(n):
            if i in bumper_indices:
                continue
            sp = per_cut_speeds.get(i, cut_speed)
            if cut_idx < len(cut_durations):
                new_durations.append(cut_durations[cut_idx] / sp)
                cut_idx += 1
        cut_durations = new_durations

    # ─── audio chain ──────────────────────────────────────────────────────
    intro_has_audio = intro_idx is not None and has_audio_stream(clips[intro_idx])
    outro_has_audio = outro_idx is not None and has_audio_stream(clips[outro_idx])
    intro_dur = ffprobe_duration(clips[intro_idx]) if intro_idx is not None else 0.0
    outro_dur = ffprobe_duration(clips[outro_idx]) if outro_idx is not None else 0.0
    # Crossfades overlap adjacent cuts, shrinking the cuts block by xfade_dur
    # per crossfade — subtract so BGM/section timing stays aligned to video.
    cuts_total = max(0.0, sum(cut_durations) - xfade_total)
    total = intro_dur + cuts_total + outro_dur

    # BGM segments — we may need it for the whole episode, for cuts-only,
    # or with prefix/suffix coverage of silent bumpers.
    # bgm_start_offset = where in the BGM clock we begin (0 = from the
    # track's start). bgm_segment_dur = how long of BGM we use.
    bgm_start = 0.0 if intro_has_audio else 0.0
    if intro_has_audio and outro_has_audio:
        bgm_segment_dur = cuts_total
    elif intro_has_audio and not outro_has_audio:
        bgm_segment_dur = cuts_total + outro_dur
    elif not intro_has_audio and outro_has_audio:
        bgm_segment_dur = intro_dur + cuts_total
    else:
        bgm_segment_dur = total

    # Within the BGM segment, fade-out should still land near the end of the
    # OVERALL episode, so the listener perceives a clean wrap.
    # If outro has its own audio, no BGM fade-out needed (bumper audio's
    # internal fade handles tail). If outro is silent (BGM covers it),
    # fade-out at total - fadeout.
    bgm_fadeout_start_in_segment = (
        bgm_segment_dur - fadeout if not outro_has_audio
        else max(0.0, bgm_segment_dur - 0.3)   # gentle dip if BGM ends mid-clip
    )
    bgm_fadein_dur = fadein if not intro_has_audio else 0.05  # tiny if pre-faded

    bgm_chain = (
        f"[{music_idx}:a]"
        f"aloop=loop=-1:size=2000000000,"
        f"atrim=duration={bgm_segment_dur:.3f},"
        f"asetpts=N/SR/TB,"
        f"volume={volume},"
        f"afade=t=in:st=0:d={bgm_fadein_dur},"
        f"afade=t=out:st={bgm_fadeout_start_in_segment:.3f}:d={fadeout}"
        f"[bgm_a]"
    )

    # Build the audio concat (3-section, 2-section, or 1-section).
    audio_segments: list[str] = []
    extra_chains: list[str] = []
    if intro_has_audio:
        # Pass through the bumper's own audio. We resample to a consistent
        # rate so concat doesn't choke.
        extra_chains.append(
            f"[{intro_idx}:a]aformat=channel_layouts=stereo:sample_rates=48000[intro_a]"
        )
        audio_segments.append("[intro_a]")
    audio_segments.append("[bgm_a]")
    if outro_has_audio:
        extra_chains.append(
            f"[{outro_idx}:a]aformat=channel_layouts=stereo:sample_rates=48000[outro_a]"
        )
        audio_segments.append("[outro_a]")

    if len(audio_segments) == 1:
        # BGM-only path — relabel for downstream map
        audio_chain = bgm_chain.replace("[bgm_a]", "[aout]")
    else:
        audio_chain = (
            ";".join(extra_chains + [bgm_chain]) + ";"
            + "".join(audio_segments)
            + f"concat=n={len(audio_segments)}:v=0:a=1[aout]"
        )

    fc = video_chain + ";" + audio_chain

    cmd = ["ffmpeg", "-y", "-nostats", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", str(c)]
    cmd += ["-i", str(music)]
    cmd += [
        "-filter_complex", fc,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    return cmd


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--captions", default=str(CAPTIONS_DEFAULT),
                   help=f"captions manifest JSON — defines cut order "
                        f"(default: {_rel(CAPTIONS_DEFAULT)})")
    p.add_argument("--in-dir", default=str(CAPTIONED_DIR),
                   help=f"captioned cuts dir (default: {_rel(CAPTIONED_DIR)})")
    p.add_argument("--music", default=str(MUSIC_DEFAULT),
                   help=f"BGM file (default: {_rel(MUSIC_DEFAULT)})")
    p.add_argument("--out", default=None,
                   help="output path (default: data/output/episodes/episode_<ts>.mp4)")
    p.add_argument("--intro-bumper", default=None,
                   help="optional intro bumper mp4 prepended to the episode "
                        "(e.g., assets/branding/intro_bumper.mp4)")
    p.add_argument("--outro-bumper", default=None,
                   help="optional outro bumper mp4 appended to the episode "
                        "(e.g., assets/branding/outro_bumper.mp4)")
    p.add_argument("--volume", type=float, default=MUSIC_VOLUME,
                   help=f"music volume 0..1 (default {MUSIC_VOLUME})")
    p.add_argument("--fadein", type=float, default=MUSIC_FADEIN_S,
                   help=f"fade-in seconds (default {MUSIC_FADEIN_S})")
    p.add_argument("--fadeout", type=float, default=MUSIC_FADEOUT_S,
                   help=f"fade-out seconds (default {MUSIC_FADEOUT_S})")
    p.add_argument("--xfade-tags", default="",
                   help="comma-separated cut tags whose transition INTO them "
                        "is a crossfade/dissolve (chained one-take cuts). "
                        "Empty = all hard cuts (default).")
    p.add_argument("--xfade-dur", type=float, default=0.2,
                   help="crossfade duration in seconds (default 0.2)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the ffmpeg command, do not run it")
    args = p.parse_args()

    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found in PATH", file=sys.stderr)
        return 2

    captions_path = Path(args.captions)
    if not captions_path.exists():
        print(f"ERROR: captions manifest {_rel(captions_path)} not found",
              file=sys.stderr)
        return 2
    cut_order = load_cut_order(captions_path)
    if not cut_order:
        print(f"ERROR: no cuts in {_rel(captions_path)} "
              f"(every key starts with _ or value isn't a dict)", file=sys.stderr)
        return 2

    in_dir = Path(args.in_dir).resolve()
    music = Path(args.music).resolve()
    if not music.exists():
        print(f"ERROR: music file not found at {_rel(music)}", file=sys.stderr)
        print("       Drop an mp3 there, or pass --music PATH.",
              file=sys.stderr)
        print("       Free sources:", file=sys.stderr)
        print("         • https://pixabay.com/music/  (no account, direct mp3)",
              file=sys.stderr)
        print("         • https://www.bensound.com/   (free w/ attribution)",
              file=sys.stderr)
        return 2

    cuts = [in_dir / f"{tag}.mp4" for tag in cut_order]
    missing = [c for c in cuts if not c.exists()]
    if missing:
        print("ERROR: missing captioned cuts:", file=sys.stderr)
        for m in missing:
            print(f"   ! {_rel(m)}", file=sys.stderr)
        print("   Run burn_captions.py first.", file=sys.stderr)
        return 1

    # Optional bumpers — checked & probed before assembly so missing files
    # are caught early rather than mid-ffmpeg.
    intro = Path(args.intro_bumper).resolve() if args.intro_bumper else None
    outro = Path(args.outro_bumper).resolve() if args.outro_bumper else None
    for label, p in (("--intro-bumper", intro), ("--outro-bumper", outro)):
        if p is not None and not p.exists():
            print(f"ERROR: {label} {_rel(p)} not found", file=sys.stderr)
            return 2

    cut_durations = [ffprobe_duration(c) for c in cuts]
    intro_dur = ffprobe_duration(intro) if intro else 0.0
    outro_dur = ffprobe_duration(outro) if outro else 0.0
    total = intro_dur + sum(cut_durations) + outro_dur

    # Build the ordered clip list: [intro?] + cuts + [outro?]
    # Track which positions are bumpers so build_cmd can probe them for
    # their own (channel-theme) audio.
    clips: list[Path] = []
    intro_idx: int | None = None
    outro_idx: int | None = None
    if intro:
        intro_idx = len(clips)
        clips.append(intro)
    clips.extend(cuts)
    if outro:
        outro_idx = len(clips)
        clips.append(outro)

    if args.out:
        out = Path(args.out).resolve()
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = EPISODE_DIR_DEFAULT / f"episode_{ts}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    # Director may have set per-cut tempo (살랑살랑 0.85 / default 1.3 / fast 1.6).
    # Map cut tag → clip index, then build per_cut_speeds dict by clip index.
    tempo_by_tag = load_tempo_factors(captions_path)
    per_cut_speeds: dict[int, float] = {}
    if tempo_by_tag:
        for i, tag in enumerate(cut_order):
            if tag in tempo_by_tag:
                # Clip index = (intro_idx is not None and present) + i
                clip_i = i + (1 if intro_idx is not None else 0)
                per_cut_speeds[clip_i] = tempo_by_tag[tag]
        if per_cut_speeds:
            print(f"Per-cut tempo  : {per_cut_speeds}")

    # Map crossfade cut tags → clip indices (same offset logic as tempo).
    xfade_into: set[int] = set()
    xfade_tags = {t.strip() for t in args.xfade_tags.split(",") if t.strip()}
    if xfade_tags:
        for i, tag in enumerate(cut_order):
            if tag in xfade_tags and i > 0:  # i==0 has no prev cut to dissolve from
                clip_i = i + (1 if intro_idx is not None else 0)
                xfade_into.add(clip_i)
        if xfade_into:
            print(f"Crossfade into : {sorted(xfade_into)} (dur={args.xfade_dur}s)")

    cmd = build_cmd(clips, cut_durations, intro_idx, outro_idx, music, out,
                    args.volume, args.fadein, args.fadeout,
                    per_cut_speeds=per_cut_speeds,
                    xfade_into=xfade_into, xfade_dur=args.xfade_dur)

    print(f"Captions manifest: {_rel(captions_path)}")
    if intro:
        print(f"Intro bumper   : {_rel(intro)}  {intro_dur:.2f}s")
    print("Cuts (in order):")
    for tag, c, d in zip(cut_order, cuts, cut_durations):
        print(f"  {tag:28s} {d:5.2f}s")
    if outro:
        print(f"Outro bumper   : {_rel(outro)}  {outro_dur:.2f}s")
    print(f"Total duration : {total:.2f}s")
    print(f"Music          : {_rel(music)}")
    print(f"  volume       : {args.volume}")
    print(f"  fade-in      : {args.fadein}s")
    print(f"  fade-out     : {args.fadeout}s "
          f"(starts at t={max(0, total - args.fadeout):.2f}s)")
    print(f"Output         : {_rel(out)}")

    if args.dry_run:
        print("\n[dry-run] ffmpeg command:")
        print("  " + " ".join(repr(a) if (" " in a or ";" in a) else a
                              for a in cmd))
        return 0

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"! ffmpeg failed (rc={e.returncode})", file=sys.stderr)
        return 1

    size_mb = out.stat().st_size / 1e6
    print(f"\nok ({size_mb:.2f} MB) → {_rel(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
