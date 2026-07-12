"""Re-caption an already-rendered episode WITHOUT re-rendering ($0): burn a new
caption manifest onto the PRE-caption cuts (work_dir/animated/<tag>.mp4) and
re-assemble with the shared bumpers + the episode's BGM.

Used when the footage is fine but the captions need a rewrite (wrong concept,
caption↔frame mismatch, tone). Reuses burn_captions.build_vf_multi so KO/EN
sizing/placement match the channel standard.

  .venv/bin/python -m scripts.recaption_finish \
      --workdir data/tmp/cameraman_xxx --captions /tmp/newcaps.json \
      --out data/output/episodes/episode_recap.mp4
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from scripts import burn_captions as bc

ROOT = Path(__file__).resolve().parent.parent
INTRO = ROOT / "assets/branding/intro_bumper.mp4"
OUTRO = ROOT / "assets/branding/outro_bumper.mp4"


def _duration(p: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 5.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--captions", required=True, help="new captions.json (cut_tag → {scenes:[...]})")
    ap.add_argument("--out", required=True)
    ap.add_argument("--bgm", default=None)
    args = ap.parse_args()

    wd = Path(args.workdir)
    caps = json.loads(Path(args.captions).read_text(encoding="utf-8"))
    captions_path = Path(args.captions)
    bgm = args.bgm
    if not bgm:
        try:
            bgm = json.loads((wd / "render_meta.json").read_text(encoding="utf-8")).get("bgm")
        except Exception:
            bgm = None

    # CRITICAL: assemble_episode speeds every cut up by its default cut_speed (1.3x)
    # UNLESS the captions manifest carries `_tempo_factors`. A freshly-written
    # recaption manifest usually omits it → the episode silently renders ~23%
    # shorter (the "왜 더 짧아졌어?" bug). The pre-caption cuts in animated/ are at
    # NATIVE speed, so inherit the original render's tempo and default any missing
    # cut to 1.0 (native) — never let assemble fall back to 1.3.
    if "_tempo_factors" not in caps:
        tf = {}
        try:
            orig = json.loads((wd / "captions.json").read_text(encoding="utf-8"))
            tf = dict(orig.get("_tempo_factors") or {})
        except Exception as e:
            print(f"  (could not read original tempo: {e})")
        for tag in [k for k in caps if not k.startswith("_")]:
            tf.setdefault(tag, 1.0)
        caps["_tempo_factors"] = tf
        captions_path = wd / "recap_merged_captions.json"
        captions_path.write_text(json.dumps(caps, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        print(f"  tempo factors for assemble (inherited+native-filled): {tf}")

    out_dir = wd / "recap_captioned"
    out_dir.mkdir(exist_ok=True)
    tags = [k for k in caps if not k.startswith("_")]
    for tag in tags:
        src = wd / "animated" / f"{tag}.mp4"
        if not src.exists():
            print(f"  ! missing {src}; skip"); continue
        entry = caps[tag]
        scenes = entry.get("scenes") if isinstance(entry, dict) else None
        dur = _duration(src)
        cap_vf = bc.build_vf_multi(scenes, tag, dur,
                                   caption_position=entry.get("caption_position", "bottom")) if scenes else ""
        # Scale to the episode's 1080x1920 BEFORE drawtext — calc_fontsize/wrap assume
        # that width, so burning on a smaller native clip (real footage ≠ 1080) makes the
        # text overflow the frame. _scale_prefix pads/letterboxes to the standard size.
        vf = bc._scale_prefix() + ("," + cap_vf if cap_vf else "")
        dst = out_dir / f"{tag}.mp4"
        cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src)]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "19", "-an", str(dst)]
        print(f"  re-captioning {tag} ({dur:.1f}s, {len(scenes or [])} scenes)…")
        subprocess.run(cmd, check=True)

    cmd = [sys.executable, "-m", "scripts.assemble_episode",
           "--captions", str(captions_path), "--in-dir", str(out_dir), "--out", args.out,
           "--intro-bumper", str(INTRO), "--outro-bumper", str(OUTRO)]
    if bgm:
        cmd += ["--music", bgm]
    print("  assembling…")
    # PD 2026-07-12: the assemble ffmpeg intermittently fails under load (an
    # RF2100 + RF1230 recaption each failed once, then succeeded verbatim on retry,
    # leaving a truncated/invalid mp4). Retry a couple times so a transient glitch
    # doesn't strand a $0 caption fix.
    import time as _t
    for _attempt in range(3):
        try:
            subprocess.run(cmd, check=True, cwd=str(ROOT))
            break
        except subprocess.CalledProcessError as e:
            if _attempt == 2:
                raise
            print(f"  assemble failed (attempt {_attempt + 1}/3): {e} — retrying")
            _t.sleep(2)
    print("RECAP_OUT:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
