"""scripts/make_mv.py — reusable Ryani×Leo MUSIC-VIDEO pipeline (PD 2026-10-29).

One parameterized builder that replaces the per-song scratch assemblers (_mv_assemble_*). Give
it a song, karaoke lyric lines, and an ordered list of clips (real GCS footage OR AV/Seedance
cuts) and it produces a finished 9:16 MV: per-clip colour-correct → concat → karaoke captions →
optional channel bumper. All the hard-won fixes are baked in:

  • HDR handling   — HLG/PQ clips are tone-mapped to SDR (mobius, natural); SDR clips pass
                     through; the whole output is tagged bt709 SDR (so players don't render the
                     HLG-tagged 8-bit as orange).  → see get_timestamped_lyrics / commit e760abb
  • CFR            — every segment resampled to 30fps (phone clips are VFR; concat of VFR breaks
                     the PTS timeline and drifts the karaoke captions).
  • Karaoke        — captions are burned at the SONG's word-aligned timestamps (Suno
                     get-timestamped-lyrics), decoupled from cut boundaries, with a soft box.
  • No end-freeze  — the final cut is slowed to fill its slot exactly so body == total (a short
                     body + `-t total` padding is what caused the "멈추다가 f/o" freeze).
  • Bumper         — colour-normalized to bt709 (NOT tone-mapped — it's SDR), its OWN channel-
                     theme audio kept, resampled to 48 kHz so the concat doesn't drop it.

Karaoke lines come from Suno: `karaoke_lines(task_id, music_index)` fetches + groups them, or
pass your own list of {"text","start","end"}.

CLI (clips/lines via a JSON config):
  python3 scripts/make_mv.py --config mv.json
  # mv.json = {"song": "...mp3", "out": "...mp4", "bumper": "assets/branding/outro_bumper.mp4",
  #            "lines": [{"text","start","end"},...] OR {"suno_task":"...","music_index":1},
  #            "clips": [{"src": "...mp4", "dur": 5.5}, ...]}
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT = Path.home() / "Library/Fonts/Pretendard-Bold.otf"
W, H = 1080, 1920
BUMPER_DEFAULT = ROOT / "assets/branding/outro_bumper.mp4"

GEOM = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=30"
HLG_TONEMAP = ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=mobius:param=0.3,"
               "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")   # natural HLG→SDR (not blown)
SDR_NORM = "format=yuv420p"                                     # SDR clips: just 8-bit, tag bt709
BT709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


# ── ffmpeg helpers ────────────────────────────────────────────────────
def _run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n  " + " ".join(str(c) for c in cmd)[:200] + "\n" + r.stderr[-800:])


def _probe(path, entry):
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                           "-show_entries", f"stream={entry}", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True).stdout.strip()


def _color_vf(src) -> str:
    """HLG/PQ clip → tone-map to SDR; any SDR clip → just ensure 8-bit. Either way → bt709 (via tags)."""
    return HLG_TONEMAP if _probe(src, "color_transfer") in ("arib-std-b67", "smpte2084") else SDR_NORM


# ── Suno karaoke timing ───────────────────────────────────────────────
def karaoke_lines(suno_task: str, music_index: int = 0) -> list[dict]:
    """Fetch Suno word-aligned timestamps for a generated clip and group them into caption lines
    [{text,start,end}] by the lyric newlines. Needs SUNO_API_KEY (same as gen_song.py)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    base = os.getenv("SUNO_API_BASE", "https://api.sunoapi.org").rstrip("/")
    req = urllib.request.Request(
        base + "/api/v1/generate/get-timestamped-lyrics",
        data=json.dumps({"taskId": suno_task, "audioId": None, "musicIndex": music_index}).encode(),
        method="POST", headers={"Authorization": f"Bearer {os.getenv('SUNO_API_KEY')}",
                                 "Content-Type": "application/json", "Accept": "application/json",
                                 "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"})
    out = json.loads(urllib.request.urlopen(req, timeout=60, context=ctx).read())
    words = (out.get("data") or {}).get("alignedWords") or []
    lines, cur, start = [], [], None
    for w in words:
        t = w["word"]
        for tag in ("[Verse]", "[Chorus]", "[Bridge]", "[Outro]", "[Pre-Chorus]"):
            t = t.replace(tag, "")
        if start is None and t.strip():
            start = w["startS"]
        cur.append(t)
        if "\n" in w["word"]:
            txt = "".join(cur).replace("\n", " ").strip()
            if txt:
                lines.append({"text": txt, "start": round(start, 2), "end": round(w["endS"], 2)})
            cur, start = [], None
    if cur and start is not None:
        txt = "".join(cur).replace("\n", " ").strip()
        if txt:
            lines.append({"text": txt, "start": round(start, 2), "end": round(words[-1]["endS"], 2)})
    # drop immediate duplicate lines (Suno repeats a sustained line across two windows)
    dedup = []
    for ln in lines:
        if dedup and dedup[-1]["text"] == ln["text"]:
            dedup[-1]["end"] = ln["end"]
        else:
            dedup.append(ln)
    return dedup


# ── the pipeline ──────────────────────────────────────────────────────
def make_mv(song, clips, lines, out, *, bumper=BUMPER_DEFAULT, work=None,
            caption_box=True, fontsize=60) -> Path:
    """Build a music video.
      song   : path to the song mp3 (its first `total` seconds are used)
      clips  : [{"src": path, "dur": seconds}, ...] in order (real or AV cuts)
      lines  : [{"text","start","end"}, ...] karaoke caption lines (song-relative seconds)
      out    : output mp4 path
      bumper : channel outro bumper (its own audio kept) or None
    Returns the output path. total = sum of clip durs; the final clip is slowed to fill its slot
    so there is no end-freeze."""
    song, out = Path(song), Path(out)
    work = Path(work) if work else out.parent / f"_mvwork_{out.stem}"
    work.mkdir(parents=True, exist_ok=True)
    total = round(sum(c["dur"] for c in clips), 2)

    # 1) per-clip: colour-correct + geometry → CFR bt709 segment. Last clip slowed to fill its slot.
    segs = []
    for i, c in enumerate(clips):
        src, dur = Path(c["src"]), float(c["dur"])
        seg = work / f"seg{i:02d}.mp4"
        last = (i == len(clips) - 1)
        if last:   # slow the (≤4.9s) clip to EXACTLY `dur` so body == total → no end-freeze
            vf = f"{_color_vf(src)},setpts=({dur}/4.9)*PTS,{GEOM}"
            _run(["ffmpeg", "-y", "-ss", "0.1", "-t", "4.9", "-i", src, "-vf", vf, "-an",
                  "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                  *BT709, "-t", dur, seg])
        else:
            vf = f"{_color_vf(src)},{GEOM}"
            _run(["ffmpeg", "-y", "-ss", "0.1", "-t", min(dur, 4.9), "-i", src, "-vf", vf, "-an",
                  "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", *BT709, seg])
        segs.append(seg)

    # 2) concat body
    cl = work / "concat.txt"
    cl.write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")
    body = work / "body.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", cl,
          "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", *BT709, body])

    # 3) karaoke captions (song-synced) + end video fade, 4) song audio + fades → mv
    draws = []
    for j, ln in enumerate(lines):
        s, e = float(ln["start"]), float(ln["end"])
        if s >= total:
            continue
        e = min(e, total)
        cf = work / f"cap{j}.txt"
        cf.write_text(ln["text"], encoding="utf-8")
        a = f"if(lt(t,{s+0.2}),(t-{s})/0.2,if(gt(t,{e-0.25}),max(0,({e}-t)/0.25),1))"
        draws.append(f"drawtext=fontfile='{FONT}':textfile='{cf}':fontsize={fontsize}:fontcolor=white:"
                     f"borderw=6:bordercolor=black:shadowcolor=black@0.5:shadowx=4:shadowy=4:"
                     + (f"box=1:boxcolor=black@0.42:boxborderw=18:" if caption_box else "")
                     + f"x=(w-text_w)/2:y=h-text_h-205:enable='between(t,{s},{e})':alpha='{a}'")
    mv = work / "mv_nobumper.mp4"
    vchain = (",".join(draws) + "," if draws else "") + f"fade=t=out:st={total-1.2:.2f}:d=1.2"
    _run(["ffmpeg", "-y", "-i", body, "-ss", "0", "-t", total, "-i", song,
          "-filter_complex", f"[0:v]{vchain}[v]", "-map", "[v]", "-map", "1:a:0",
          "-af", f"afade=t=in:st=0:d=0.3,afade=t=out:st={total-2.2:.2f}:d=2.2",
          "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
          "-fps_mode", "cfr", *BT709, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
          "-t", total, "-movflags", "+faststart", mv])

    # 5) append channel bumper (bt709 colour-normalize, keep its OWN audio @48k so concat keeps it)
    if bumper and Path(bumper).exists():
        bmp = work / "bumper.mp4"
        _run(["ffmpeg", "-y", "-i", bumper, "-vf", f"{SDR_NORM},{GEOM}", "-map", "0:v:0", "-map", "0:a:0",
              "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", *BT709,
              "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", bmp])
        cl2 = work / "concat2.txt"
        cl2.write_text(f"file '{mv}'\nfile '{bmp}'\n", encoding="utf-8")
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", cl2,
              "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", *BT709,
              "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", out])
    else:
        out.write_bytes(mv.read_bytes())
    print(f"DONE → {out} ({total}s + bumper, {len(clips)} cuts, {len(lines)} caption lines)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a Ryani×Leo music video")
    ap.add_argument("--config", required=True, help="JSON: {song, out, clips[], lines[] or {suno_task,music_index}, bumper?}")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    lines = cfg["lines"]
    if isinstance(lines, dict):   # {suno_task, music_index}
        lines = karaoke_lines(lines["suno_task"], lines.get("music_index", 0))
    make_mv(cfg["song"], cfg["clips"], lines, cfg["out"],
            bumper=cfg.get("bumper", BUMPER_DEFAULT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
