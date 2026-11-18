"""Text-to-image still via fal.ai FLUX.1 [pro] v1.1 — generates a whole SCENE from a prompt
(no input image). Use for distinct LOCATIONS / wardrobe the Kontext edit path can't reach
(it preserves composition). Identity of specific pets is loose → storyboard/wide use; lock
identity at the cut-render stage (Seedance ref-mode). Needs FAL_KEY in .env.  (MV §8.11)

Prompt spec (FLUX v1.1 t2i) — see notes/model_prompting_guides.md §FLUX: write the `--prompt`
as specific NATURAL-LANGUAGE prose (medium → subject+action → setting → light → framing), NOT
comma-separated tags or quality spam ("4k/masterpiece"). ⚠ FOOTGUN: no negative_prompt and
FLUX handles negation poorly — describe the positive opposite ("empty, deserted"), never "no X".

Usage:
  python scripts/gen_still_t2i.py --prompt "<scene>" --out <out.png> [--aspect 9:16]
"""
import sys, ssl, argparse, urllib.request
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass
try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _CTX = ssl.create_default_context()
import fal_client


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--aspect", default="9:16")
    ap.add_argument("--model", default="fal-ai/flux-pro/v1.1")
    args = ap.parse_args()

    result = fal_client.subscribe(
        args.model,
        arguments={
            "prompt": args.prompt,
            "aspect_ratio": args.aspect,
            "num_images": 1,
            "safety_tolerance": "5",
            "output_format": "png",
        },
        with_logs=False,
    )
    imgs = result.get("images") or []
    if not imgs:
        print("NO IMAGE:", str(result)[:300]); return 1
    out = Path(args.out)
    out.write_bytes(urllib.request.urlopen(imgs[0]["url"], timeout=240, context=_CTX).read())
    print("wrote", out, out.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
