"""Generate a HIGH-QUALITY Halloween overlay source image via OpenAI gpt-image-1 (PD: Canva art is
garbage; use OpenAI). Solid green chroma background + realistic flying bat silhouettes + top-corner
cobwebs, bottom two-thirds kept empty green (caption-safe). The green art then goes to Seedance i2v
to animate, then colorkey + composite."""
import base64, os, sys
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
prompt = (
    "A 9:16 vertical Halloween overlay graphic on a COMPLETELY SOLID, FLAT, UNIFORM bright chroma-key "
    "GREEN background (pure #00FF00) filling the entire frame, so it can be chroma-keyed out. "
    "Compose Halloween elements ONLY around the EDGES, leaving the whole CENTER and the BOTTOM-CENTER "
    "pure flat solid green (that area is reserved for subtitles). Elements, all atmospheric and "
    "cinematic: (1) TOP: a dramatic flock of realistic black bat silhouettes of varied sizes flying "
    "with depth — a couple large sharp bats in front, many small ones behind. (2) ALL FOUR CORNERS: "
    "delicate wispy white spider cobwebs. (3) BOTTOM-LEFT and BOTTOM-RIGHT corners ONLY: a couple of "
    "richly glowing orange carved jack-o-lantern pumpkins with warm candlelight, tucked into the "
    "corners — NOTHING in the bottom center between them. A few faint golden sparkles near the edges. "
    "Keep the center and bottom-center a clean empty solid green with nothing on it. No text, no "
    "full ground line, no gradient background. Crisp, high-quality, moody, suitable for chroma keying."
)
import urllib.request
out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/tmp/mv/topics/openai_overlay.png")
resp = client.images.generate(model="dall-e-3", prompt=prompt, size="1024x1792", n=1)
d = resp.data[0]
if getattr(d, "b64_json", None):
    out.write_bytes(base64.b64decode(d.b64_json))
else:
    out.write_bytes(urllib.request.urlopen(d.url, timeout=120).read())
print("OK via dall-e-3 ->", out, out.stat().st_size)
