"""Image-edit still generation via fal.ai FLUX.1 Kontext — preserves the subject in an input
image while applying an instructed edit (pose, prop, costume, framing). Better anatomy /
character consistency than nano-banana for MV interlude cuts. Needs FAL_KEY in .env.

Prompt spec (FLUX Kontext) — see notes/model_prompting_guides.md §FLUX. The `--prompt` is the
EDIT instruction; write it as: (1) name the target precisely, (2) state the change, (3) ★name
what to PRESERVE explicitly ("keep the black French bulldog exactly — same face, same white
chin/chest/paw markings, same tailless rear"). Preservation is a prompt duty, not automatic —
without it Kontext redraws the whole image and identity drifts.
  ⚠ FOOTGUN: these fal endpoints expose NO negative_prompt and NO strength knob, and FLUX
  handles negation poorly — "no tail" backfires. Describe the positive opposite + lean on the
  preserve clause; control change amount via prompt scope + guidance_scale/seed, not strength.

Usage:
  python scripts/gen_still_fal.py --image <in.png> --prompt "<edit>" --out <out.png> \
      [--model max|pro] [--aspect 9:16]
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

MODELS = {
    "max": "fal-ai/flux-pro/kontext/max",
    "pro": "fal-ai/flux-pro/kontext",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="input image to edit (subject preserved)")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="max", choices=list(MODELS))
    ap.add_argument("--aspect", default="9:16")
    args = ap.parse_args()

    url = fal_client.upload_file(args.image)
    result = fal_client.subscribe(
        MODELS[args.model],
        arguments={
            "prompt": args.prompt,
            "image_url": url,
            "aspect_ratio": args.aspect,
            "num_images": 1,
            "guidance_scale": 3.5,
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
