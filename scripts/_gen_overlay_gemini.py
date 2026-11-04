"""Generate a HIGH-QUALITY Halloween overlay source image via Google's image model (nano-banana /
gemini-3-pro-image-preview) — the local OpenAI key lacks image access; Gemini (GOOGLE_API_KEY) works
and is high quality. Solid green chroma background + atmospheric bats / cobwebs / corner pumpkins,
center + bottom-center kept empty green (caption-safe). Then Seedance i2v animates it, colorkey, composite."""
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
    "Ultra high-quality, PREMIUM cinematic Halloween video-FILTER overlay graphic, 9:16 vertical, the "
    "quality level of a top TikTok / CapCut paid Halloween filter — rich, polished, atmospheric, "
    "professionally designed, absolutely NOT flat clipart. Render it on a COMPLETELY SOLID, FLAT, "
    "UNIFORM bright chroma-key GREEN background (pure #00FF00) so it can be keyed out. Compose the "
    "Halloween elements ONLY around the EDGES; keep the whole CENTER and BOTTOM-CENTER as clean empty "
    "flat solid green (reserved for subtitles — nothing there). Elements, all photorealistic and "
    "cinematic with real depth of field, soft atmospheric glow and subtle motion blur: "
    "(1) TOP: a dramatic swarm of PHOTOREALISTIC bats with detailed membrane wings, varied sizes and "
    "true depth — a few large crisp bats in the foreground, a cloud of smaller soft-focus bats behind, "
    "mid-flight, dynamic. (2) FOUR CORNERS: finely detailed realistic wispy spider cobwebs with thin "
    "silk threads catching light. (3) BOTTOM-LEFT and BOTTOM-RIGHT corners ONLY: a beautifully carved, "
    "richly GLOWING jack-o-lantern pumpkin with warm volumetric candlelight and realistic texture in "
    "each corner — nothing in the bottom center between them. Scatter a few tasteful glowing embers "
    "and golden sparkle particles near the edges. Moody, premium, magazine-grade Halloween art. No "
    "text, no ground line, no background gradient; the keyable area stays pure solid green."
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
