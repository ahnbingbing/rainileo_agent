"""
scripts/export_clips_for_capcut.py
----------------------------------
Export each Episode 1 cut as a standalone 1080x1920 H.264 MP4 with the
visual treatment baked in (ken-burns for portraits, blur-bg letterbox
for landscapes, auto-rotate for iPhone videos) but WITHOUT captions or
stickers.  Drop the resulting clips into CapCut and add Korean text +
cute decorative stickers there.

Outputs
-------
    data/output/capcut_package/clips/01_intro_ryani.mp4
    data/output/capcut_package/clips/02_intro_leo.mp4
    data/output/capcut_package/clips/03_age_gap.mp4
    data/output/capcut_package/clips/04_best_buds.mp4
    data/output/capcut_package/clips/05_play_together.mp4
    data/output/capcut_package/clips/06_one_family.mp4
    data/output/capcut_package/clips/07_see_you_at_9.mp4
    data/output/capcut_package/clips/_bgm_30s.m4a    (ready-trimmed BGM)

Run
---
    cd ~/code/rianileo-agent
    python3 scripts/export_clips_for_capcut.py
    # sandbox dry-run (skips HEIC if no decoder available)
    python3 scripts/export_clips_for_capcut.py --skip-heic
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
PKG_DIR = ROOT / "data" / "output" / "capcut_package"
CLIPS_DIR = PKG_DIR / "clips"
TMP = ROOT / "data" / "tmp" / "ep1_capcut"

CANVAS_W, CANVAS_H = 1080, 1920
FPS = 30
CRF = 18
PRESET = "fast"
log = logging.getLogger("capcut_export")


# ─────────────────────────────────────────────────────────────────────
# Storyboard — same 7 cuts as render_episode_1.py, but with friendly
# filenames so they sort correctly in CapCut's media bin.
# ─────────────────────────────────────────────────────────────────────
@dataclass
class Cut:
    idx: int
    name: str           # filename slug, e.g. 'intro_ryani'
    asset_id: str
    dur: float
    video_start: float = 0.0


CUTS: list[Cut] = [
    Cut(1, "intro_ryani",     "med_2026_05_06_203421_icloud_331110de", 2.5),
    Cut(2, "intro_leo",       "med_2026_05_06_203433_icloud_57e3500d", 2.5),
    Cut(3, "age_gap",         "med_2025_11_21_112556_icloud_11fe4ba7", 5.0),
    Cut(4, "best_buds",       "med_2025_12_12_193926_icloud_6a1268c0", 5.0),
    Cut(5, "play_together",   "med_2025_12_14_152903_icloud_ad7fb05a", 5.5),
    Cut(6, "one_family",      "med_2026_02_07_111144_icloud_77fa65d8", 5.0),
    Cut(7, "see_you_at_9",    "med_2026_03_01_163302_icloud_5d2836d5", 4.5),
]
TOTAL_DUR = sum(c.dur for c in CUTS)  # 30.0

BGM_FILE = "geoffharvey-playdate-427890.mp3"
BGM_START = 8.0
BGM_FADE_IN = 0.6
BGM_FADE_OUT = 1.2
BGM_VOLUME = 0.8


# ─────────────────────────────────────────────────────────────────────
# Helpers (path remap, HEIC decode, EXIF orientation bake, ffprobe)
# ─────────────────────────────────────────────────────────────────────
def resolve(p: str | Path) -> Path:
    p = Path(p)
    if p.is_absolute() and "rianileo-agent" in p.parts:
        idx = p.parts.index("rianileo-agent")
        return (ROOT / Path(*p.parts[idx + 1:])).resolve()
    if not p.is_absolute():
        return (ROOT / p).resolve()
    return p


def heic_to_jpeg(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("sips"):
        r = subprocess.run(
            ["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
            capture_output=True,
        )
        if r.returncode == 0 and dst.exists():
            return True
    if shutil.which("heif-convert"):
        r = subprocess.run(
            ["heif-convert", "-q", "92", str(src), str(dst)],
            capture_output=True,
        )
        if r.returncode == 0 and dst.exists():
            return True
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-q:v", "2", str(dst)],
        capture_output=True,
    )
    return r.returncode == 0 and dst.exists()


def normalize_photo(src: Path, dst: Path) -> Path:
    from PIL import Image, ImageOps
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.save(dst, "JPEG", quality=92, optimize=True)
    return dst


def ffprobe_dims(path: Path) -> tuple[int, int]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = r.stdout.strip().split("x")[:2]
    return int(w), int(h)


def ffprobe_rotate(path: Path) -> str:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream_tags=rotate", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    return r.stdout.strip() or "0"


# ─────────────────────────────────────────────────────────────────────
# Per-clip rendering — clean (no overlays). Same visual treatment as
# render_episode_1 so timing matches the captions in the shot-list.
# ─────────────────────────────────────────────────────────────────────
def render_photo_clip(cut: Cut, src: Path, out: Path) -> None:
    try:
        w, h = ffprobe_dims(src)
    except Exception:
        w, h = (4284, 5712)
    is_portrait = h >= w
    dur_frames = max(int(cut.dur * FPS), 2)
    z_expr = f"1+0.06*on/{dur_frames}"

    if is_portrait:
        graph = (
            f"[0:v]scale=1188:2112:force_original_aspect_ratio=increase,"
            f"crop=1188:2112,setsar=1,"
            f"zoompan=z='{z_expr}':d={dur_frames}:s={CANVAS_W}x{CANVAS_H}:fps={FPS}[outv]"
        )
    else:
        fg_h = max(int(CANVAS_W * h / w), 2)
        graph = (
            f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{CANVAS_H},gblur=sigma=24,setsar=1[bg];"
            f"[0:v]scale=1188:-2:force_original_aspect_ratio=decrease,setsar=1,"
            f"zoompan=z='{z_expr}':d={dur_frames}:s={CANVAS_W}x{fg_h}:fps={FPS}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[outv]"
        )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-loop", "1", "-framerate", str(FPS), "-t", str(cut.dur), "-i", str(src),
        "-filter_complex", graph,
        "-map", "[outv]", "-t", str(cut.dur),
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF),
        "-an", str(out),
    ]
    log.info("[cut %d] photo -> %s", cut.idx, out.name)
    subprocess.run(cmd, check=True)


def render_video_clip(cut: Cut, src: Path, out: Path) -> None:
    try:
        w, h = ffprobe_dims(src)
    except Exception:
        w, h = (1920, 1080)
    rotate = ffprobe_rotate(src)
    auto_portrait = rotate in ("90", "270") or h >= w

    if auto_portrait:
        graph = (
            f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{CANVAS_H},setsar=1,fps={FPS}[outv]"
        )
    else:
        graph = (
            f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{CANVAS_H},gblur=sigma=24,setsar=1,fps={FPS}[bg];"
            f"[0:v]scale={CANVAS_W}:-2,setsar=1,fps={FPS}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[outv]"
        )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-ss", str(cut.video_start), "-t", str(cut.dur), "-i", str(src),
        "-filter_complex", graph,
        "-map", "[outv]", "-t", str(cut.dur),
        "-r", str(FPS), "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF),
        "-an", str(out),
    ]
    log.info("[cut %d] video -> %s (rotate=%s)", cut.idx, out.name, rotate)
    subprocess.run(cmd, check=True)


# ─────────────────────────────────────────────────────────────────────
# BGM — single trimmed AAC track for CapCut (.m4a, common-friendly)
# ─────────────────────────────────────────────────────────────────────
def export_bgm(out: Path) -> None:
    src = resolve(Path("assets/bgm") / BGM_FILE)
    fade_out_st = max(0.0, TOTAL_DUR - BGM_FADE_OUT)
    chain = (
        f"[0:a]atrim=duration={TOTAL_DUR},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={BGM_FADE_IN},"
        f"afade=t=out:st={fade_out_st}:d={BGM_FADE_OUT},"
        f"volume={BGM_VOLUME},aresample=44100[mout]"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-ss", str(BGM_START), "-i", str(src),
        "-filter_complex", chain,
        "-map", "[mout]",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        str(out),
    ]
    log.info("BGM -> %s (%s, %.1fs)", out.name, BGM_FILE, TOTAL_DUR)
    subprocess.run(cmd, check=True)


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def lookup_assets(con: sqlite3.Connection) -> dict[str, dict]:
    ids = [c.asset_id for c in CUTS]
    qmarks = ",".join(["?"] * len(ids))
    rows = con.execute(
        f"SELECT asset_id, kind, file_path FROM assets WHERE asset_id IN ({qmarks})",
        ids,
    ).fetchall()
    return {r[0]: {"kind": r[1], "file_path": r[2]} for r in rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export per-clip MP4s for CapCut.")
    ap.add_argument("--skip-heic", action="store_true",
                    help="skip HEIC cuts that can't be decoded (sandbox preview)")
    ap.add_argument("--skip-bgm", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(DB_PATH))
    try:
        assets = lookup_assets(con)
    finally:
        con.close()

    for c in CUTS:
        if c.asset_id not in assets:
            log.error("asset_id not in DB: %s", c.asset_id)
            return 2

    skipped: list[int] = []
    for c in CUTS:
        meta = assets[c.asset_id]
        src = resolve(meta["file_path"])
        if not src.exists():
            log.error("[cut %d] source missing: %s", c.idx, src)
            return 3

        out = CLIPS_DIR / f"{c.idx:02d}_{c.name}.mp4"
        if meta["kind"] == "photo":
            work_src = src
            heic_jpeg = None
            if src.suffix.lower() in (".heic", ".heif"):
                heic_jpeg = TMP / f"{src.stem}.jpg"
                if not heic_jpeg.exists() and not heic_to_jpeg(src, heic_jpeg):
                    if args.skip_heic:
                        log.warning("[cut %d] skipping (no HEIC decoder)", c.idx)
                        skipped.append(c.idx)
                        continue
                    log.error("[cut %d] HEIC decode failed: %s", c.idx, src)
                    return 4
            normalized = TMP / f"norm_{src.stem}.jpg"
            try:
                normalize_photo(heic_jpeg or src, normalized)
                work_src = normalized
            except Exception as e:
                log.warning("[cut %d] normalize failed (%s); using raw source",
                            c.idx, e)
                work_src = heic_jpeg or src
            render_photo_clip(c, work_src, out)
        else:
            render_video_clip(c, src, out)

    if not args.skip_bgm:
        export_bgm(CLIPS_DIR / "_bgm_30s.m4a")

    if skipped:
        log.warning("skipped cuts: %s", skipped)
    log.info("done. clips at %s", CLIPS_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
