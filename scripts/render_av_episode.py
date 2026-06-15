"""Generic AV (ai_vtuber) episode render from Giri-picked stills.

still→Seedance: each cut's <tag>_WIN.png → Seedance i2v (5s, 9:16, no audio) →
burn KO/EN captions → assemble with shared bumpers + BGM. Render held until PD $-OK.

  .venv/bin/python -m scripts.render_av_episode \\
      --concept /tmp/av_pest_concept_patched.json \\
      --stills data/tmp/av_pest_stills_v2 \\
      --workdir data/tmp/pest_render \\
      --bgm assets/bgm/lp-studio-music-background-happy-music-funny-cat-jazz-308988.mp3 \\
      --out data/output/episodes/episode_av_pestcontrol.mp4
"""
import argparse, json, os, subprocess, sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
INTRO = ROOT / "assets/branding/intro_bumper.mp4"
OUTRO = ROOT / "assets/branding/outro_bumper.mp4"
SECONDS = 5  # fast model = 5s only


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def build_caps(concept, out_path: Path) -> Path:
    out = {"_episode_id": concept.get("title", {}).get("en", "av") if isinstance(concept.get("title"), dict) else "av",
           "_caption_position": "bottom"}
    for cut in concept["cuts"]:
        caps = [c for c in (cut.get("captions") or []) if c.get("ko")]
        if not caps:
            continue
        n = len(caps); span = (4.7 - 0.3) / n
        out[cut["tag"]] = {"scenes": [
            {"start": round(0.3 + i * span, 2), "end": round(0.3 + (i + 1) * span, 2),
             "ko": c["ko"], "en": c.get("en", "")} for i, c in enumerate(caps)]}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concept", required=True)
    ap.add_argument("--stills", required=True, help="dir with <tag>_WIN.png per cut")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--bgm", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    concept = json.loads(Path(args.concept).read_text(encoding="utf-8"))
    stills = Path(args.stills); work = Path(args.workdir)
    anim = work / "animated"; captioned = work / "captioned"
    anim.mkdir(parents=True, exist_ok=True); captioned.mkdir(parents=True, exist_ok=True)

    for cut in concept["cuts"]:
        tag = cut["tag"]; win = stills / f"{tag}_WIN.png"; out = anim / f"{tag}.mp4"
        if not win.exists():
            print(f"!! missing WIN still {win}", file=sys.stderr); sys.exit(1)
        if out.exists() and out.stat().st_size > 0:
            print(f"== {tag} already animated, skip", flush=True); continue
        prompt = cut.get("motion_prompt") or cut.get("regen_prompt") or cut.get("scene") or ""
        run([sys.executable, "scripts/animate_seedance_i2v.py", "--mode", "i2v",
             "--image", str(win), "--prompt", prompt, "--seconds", str(SECONDS),
             "--output", str(out)])
        print(f"== {tag} animated", flush=True)

    caps = build_caps(concept, work / "captions.json")
    run([sys.executable, "scripts/burn_captions.py", "--manifest", str(caps),
         "--in-dir", str(anim), "--out-dir", str(captioned)])
    out_final = Path(args.out)
    run([sys.executable, "scripts/assemble_episode.py", "--captions", str(caps),
         "--in-dir", str(captioned), "--music", args.bgm,
         "--intro-bumper", str(INTRO), "--outro-bumper", str(OUTRO), "--out", str(out_final)])
    print("\nFINAL:", out_final, flush=True)


if __name__ == "__main__":
    main()
