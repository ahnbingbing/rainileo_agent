"""Home-cam (CCTV) finishing pass for an ai_vtuber episode.

The home-cam concept wants a security-camera FEEL: a slightly bowed/curved frame
(mild barrel distortion at the edges — fisheye-ish, not a full fisheye), softened
image quality with sensor grain + a vignette, and a flat "REC + timestamp" HUD.
The story content is already rendered; this is a burn-stage grade.

Grade goes on the PRE-CAPTION cuts (work_dir/animated/<tag>.mp4); captions and the
HUD are drawn AFTER the lens distortion so all text stays crisp and flat (a real
CCTV overlays its text on top of the bowed footage, it doesn't bow the text). Then
the graded+captioned cuts are concatenated with the shared bumpers + BGM via
assemble_episode.

  .venv/bin/python -m scripts.cctv_finish \
      --workdir data/tmp/cameraman_42d257d8_20260620_170754 \
      --out data/output/episodes/episode_av_homecam_cctv.mp4
"""
import argparse
import json
import subprocess
from pathlib import Path

from scripts import burn_captions as bc

ROOT = Path(__file__).resolve().parent.parent
INTRO = ROOT / "assets/branding/intro_bumper.mp4"
OUTRO = ROOT / "assets/branding/outro_bumper.mp4"
FONT_HUD = Path.home() / "Library" / "Fonts" / "Pretendard-Bold.otf"

# Mild CCTV grade. k1<0 = barrel (edges bow outward, center bulges) — the "굴곡".
# scale down→up (neighbor) softens detail like a cheap sensor; noise = grain;
# vignette = darkened corners; eq = slightly desaturated, punchier contrast.
GRADE = (
    "scale=540:960,scale=1080:1920:flags=neighbor,"
    "lenscorrection=cx=0.5:cy=0.5:k1=-0.16:k2=-0.04,"
    "eq=saturation=0.82:contrast=1.07:brightness=-0.010,"
    "noise=alls=10:allf=t+u,"
    "vignette=a=PI/4.6"
)


def _duration(p: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)],
        capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 5.0


def _hud(ts_text: str) -> str:
    ts_file = bc.TMP_DIR / "_cctv_hud.txt"
    ts_file.write_text(ts_text, encoding="utf-8")
    return (f"drawtext=fontfile='{FONT_HUD}':textfile='{ts_file}':"
            f"fontcolor=white@0.88:fontsize=30:x=30:y=38:"
            f"box=1:boxcolor=black@0.40:boxborderw=9")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bgm", default=None)
    ap.add_argument("--timestamp", default="REC  2026-06-21  13:32:07")
    args = ap.parse_args()

    wd = Path(args.workdir)
    caps = json.loads((wd / "captions.json").read_text(encoding="utf-8"))
    bgm = args.bgm
    if not bgm:
        meta = json.loads((wd / "render_meta.json").read_text(encoding="utf-8"))
        bgm = meta.get("bgm")

    out_dir = wd / "cctv_captioned"
    out_dir.mkdir(exist_ok=True)
    hud = _hud(args.timestamp)

    tags = [k for k in caps if not k.startswith("_")]
    for tag in tags:
        src = wd / "animated" / f"{tag}.mp4"
        if not src.exists():
            print(f"  ! missing {src}; skip")
            continue
        entry = caps[tag]
        scenes = entry.get("scenes") if isinstance(entry, dict) else None
        dur = _duration(src)
        vf = GRADE + "," + hud
        if scenes:
            cap_vf = bc.build_vf_multi(scenes, tag, dur,
                                       caption_position=entry.get("caption_position", "bottom"))
            if cap_vf:
                vf += "," + cap_vf
        dst = out_dir / f"{tag}.mp4"
        cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
               "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-crf", "20", "-an", str(dst)]
        print(f"  grading+captioning {tag} ({dur:.1f}s)…")
        subprocess.run(cmd, check=True)

    # Assemble graded+captioned cuts + bumpers + BGM (reuse assemble_episode).
    cmd = ["python3", "-m", "scripts.assemble_episode",
           "--captions", str(wd / "captions.json"),
           "--in-dir", str(out_dir),
           "--out", args.out,
           "--intro-bumper", str(INTRO),
           "--outro-bumper", str(OUTRO)]
    if bgm:
        cmd += ["--music", bgm]
    print("  assembling…", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    print("CCTV_OUT:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
