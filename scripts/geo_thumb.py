"""One small representative frame per asset, written at INGEST time (PD 2026-11-26).

The GPS-less geo pipeline (clustering + scene-model water class) needs one 224px frame per
clip. Most GPS-less clips are iCloud-optimized originals NOT on this Mac, so a cold run had
to GCS-fetch thousands of full files. Saving a thumbnail WHEN the file is first ingested
(it is local at that instant) means future clustering/scene-classification — and the nightly
recurring backfill — read a tiny cached jpg instead of refetching. Intentionally dependency-
light (PIL + ffmpeg only) so icloud.sync can call it on the ingest hot path.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAME_DIR = ROOT / "data" / "tmp" / "geo_frames"


def save_thumb(aid: str, kind: str, src: str | Path, dur: float | None) -> Path | None:
    """Extract/cache a 224×224 representative frame for asset `aid`. Video → mid-point frame,
    photo → downscaled thumbnail. Idempotent (returns the cached path if already present)."""
    out = FRAME_DIR / f"{aid}.jpg"
    if out.exists():
        return out
    src = Path(src)
    if not src.exists():
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "video":
            ss = max(0.0, (float(dur or 2.0)) / 2.0)
            subprocess.run(
                ["ffmpeg", "-nostdin", "-ss", f"{ss:.2f}", "-i", str(src), "-frames:v", "1",
                 "-vf", "scale=224:224:force_original_aspect_ratio=increase,crop=224:224",
                 "-y", str(out)],
                capture_output=True, timeout=60)
        else:
            from PIL import Image
            im = Image.open(src).convert("RGB")
            im.thumbnail((224, 224))
            im.save(out, "JPEG")
    except Exception:
        return None
    return out if out.exists() else None
