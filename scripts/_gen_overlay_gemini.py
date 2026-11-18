"""Generate a HIGH-QUALITY Halloween overlay source image via Google's image model (nano-banana /
gemini-3-pro-image-preview). Solid green chroma background + atmospheric bats / cobwebs / corner pumpkins,
center + bottom-center kept empty green (caption-safe). Then Seedance i2v animates it, colorkey, composite.

Prompt = nano-banana style (notes/model_prompting_guides.md §nano-banana): positive phrasing over
"NO/NOT" (nano renders the negated noun — "no text" can summon text; assert "one pure uniform flat green"
instead), and lean on positive quality descriptors rather than spam. 3-pro tolerates length but the
negation rule still holds."""
import base64, json, os, ssl, sys, urllib.request
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass
try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    CTX = ssl.create_default_context()

KEY = os.getenv("GOOGLE_API_KEY")
MODEL = os.getenv("OVERLAY_GEMINI_MODEL", "gemini-3-pro-image-preview")
PROMPT = (
    "A premium cinematic Halloween video-filter overlay, 9:16 vertical, at the quality of a top paid "
    "CapCut filter — rich, atmospheric, photorealistic with real depth of field, soft glow and subtle "
    "motion blur. Render it on one pure, uniform, flat chroma-key green background (#00FF00) so it keys "
    "out cleanly, and place every Halloween element ONLY around the edges, leaving the whole center and "
    "bottom-center as clean bare flat green (the subtitle-safe zone). Elements: "
    "(1) TOP: a dramatic swarm of photorealistic bats with detailed membrane wings and true depth — a "
    "few large crisp bats in front, a cloud of smaller soft-focus bats behind, mid-flight. "
    "(2) FOUR CORNERS: finely detailed wispy spider cobwebs with thin silk threads catching light. "
    "(3) BOTTOM-LEFT and BOTTOM-RIGHT corners only: a beautifully carved, glowing jack-o-lantern with "
    "warm volumetric candlelight, with clean bare green between them in the bottom center. Scatter a few "
    "glowing embers and golden sparkle particles near the edges. Moody, magazine-grade Halloween art. "
    "The entire keyable area stays one pure uniform flat green, free of lettering, ground line or gradient."
)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/tmp/mv/topics/gemini_overlay.png")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={KEY}"
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "9:16"}},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180, context=CTX) as r:
        data = json.loads(r.read())
    for part in data["candidates"][0]["content"]["parts"]:
        inline = part.get("inline_data") or part.get("inlineData")
        if inline and str(inline.get("mime_type") or inline.get("mimeType", "")).startswith("image/"):
            out.write_bytes(base64.b64decode(inline["data"]))
            print("OK", MODEL, "->", out, out.stat().st_size)
            return 0
    print("NO IMAGE in response:", json.dumps(data)[:400])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
