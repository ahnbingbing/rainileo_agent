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
FONT_BLACK = Path.home() / "Library/Fonts/Pretendard-Black.otf"      # punchy / pop / spooky
FONT_PEN = Path.home() / "Library/Fonts/NanumPenScript-Regular.ttf"  # soft handwritten / ballad

# Title-card themes — the M/V title is tuned to the SONG's mood, not one fixed pink card
# (PD 2026-11-01: "타이틀을 노래마다 음악 감성에 맞춰"). Pick by genre/feel; `pink` = legacy default.
# A theme sets: kicker label text/colour, title colour, the accent underline colour, title font.
TITLE_THEMES = {
    "pink":      dict(kicker="♪ 랴니레오 오리지널 M/V", kicker_color="0xFF6B9D",
                      title_color="white",     accent="0xFF6B9D", title_font=str(FONT)),
    "warm":      dict(kicker="♪ 랴니레오 오리지널 M/V", kicker_color="0xF2B65C",
                      title_color="0xFFF3DF",  accent="0xF2B65C", title_font=str(FONT_PEN)),
    "halloween": dict(kicker="♪ 랴니레오 할로윈 M/V",   kicker_color="0x9B4DFF",
                      title_color="0xFF7A18",  accent="0x9B4DFF", title_font=str(FONT_BLACK)),
    "pop":       dict(kicker="♪ 랴니레오 오리지널 M/V", kicker_color="0x00D4FF",
                      title_color="0xFFE14D",  accent="0xFF4FA3", title_font=str(FONT_BLACK)),
}


def _resolve_title_style(title_style):
    """title_style: None (legacy pink) | preset name (str) | dict of overrides → full theme dict."""
    theme = dict(TITLE_THEMES["pink"])
    if isinstance(title_style, str):
        theme.update(TITLE_THEMES.get(title_style, {}))
    elif isinstance(title_style, dict):
        theme.update(title_style)
    return theme
W, H = 1080, 1920
BUMPER_DEFAULT = ROOT / "assets/branding/outro_bumper.mp4"

GEOM = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=30"
HLG_TONEMAP = ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=mobius:param=0.3,"
               "zscale=t=bt709:m=bt709:r=tv,eq=saturation=0.50:brightness=-0.28:contrast=1.3:gamma=0.82,format=yuv420p")
# ^ strong moody darkening — bright daytime phone clips kept reading "too bright"; this pulls them
#   down to match the dim live-bar performance mood (PD repeatedly: "실사 다 밝아").
# ^ HLG→SDR, then pull saturation/brightness back toward the phone's natural look — mobius alone
#   over-saturated real clips (PD: "실사에 채도 넣지 마"). AV cuts are SDR so never hit this path.
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


def _dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return round(float(r.stdout.strip()), 3)


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
            caption_box=True, fontsize=60, title=None, credit="작곡 라레수 · 작사 함미하비",
            title_style=None, title_image=None, outro_hold=0.0,
            bumper_overlap_fade=None, caption_style=None) -> Path:
    """Build a music video.
      song   : path to the song mp3 (its first `total` seconds are used)
      clips  : [{"src": path, "dur": seconds}, ...] in order (real or AV cuts)
      lines  : [{"text","start","end"}, ...] karaoke caption lines (song-relative seconds)
      out    : output mp4 path
      bumper : channel outro bumper (its own audio kept) or None
      title  : song title shown as a title card over the intro (None = no title)
      credit : songwriting credit under the title (our songs are original → default 랴니레오)
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
        ss = float(c.get("ss", 0.1))   # per-clip start offset — land on the action on long source clips
        seg = work / f"seg{i:02d}.mp4"
        # `fill`: slow a SHORT fixed-length source (e.g. a 5s Seedance cut) to exactly `dur` so it
        # fills its slot — same path as the finale. Long real clips omit it and get trimmed instead.
        last = (i == len(clips) - 1) or bool(c.get("fill"))
        grade = ("," + c["grade"]) if c.get("grade") else ""   # per-clip cinematic grade (real cuts)
        if last:   # slow the (≤4.9s) clip to EXACTLY `dur` so body == total → no end-freeze
            vf = f"{_color_vf(src)},setpts=({dur}/4.9)*PTS,{GEOM}{grade}"
            _run(["ffmpeg", "-y", "-ss", ss, "-t", "4.9", "-i", src, "-vf", vf, "-an",
                  "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                  *BT709, "-t", dur, seg])
        else:
            vf = f"{_color_vf(src)},{GEOM}{grade}"
            # non-fill = a long REAL clip → trim to the full requested `dur` (NOT capped at 4.9s,
            # which is only the Seedance-cut length; capping real clips shortened the video and
            # broke the song/outro timing). Short Seedance cuts use fill=True instead.
            _run(["ffmpeg", "-y", "-ss", ss, "-t", dur, "-i", src, "-vf", vf, "-an",
                  "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", *BT709, seg])
        segs.append(seg)

    # 2) concat body
    cl = work / "concat.txt"
    # absolute paths: ffmpeg concat resolves `file` entries relative to the LIST's dir, so a
    # relative seg path (caller passed relative out/work) would double the prefix.
    cl.write_text("".join(f"file '{s.resolve()}'\n" for s in segs), encoding="utf-8")
    body = work / "body.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", cl,
          "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", *BT709, body])

    # 3) karaoke captions (song-synced) + end video fade, 4) song audio + fades → mv
    # Merge CONSECUTIVE identical-text lines: Suno's word alignment sometimes splits one sung
    # line into two back-to-back groups, which would flash the SAME caption twice in a row.
    # (Distant repeats — a chorus sung again later — are NOT merged; only adjacent duplicates.)
    merged = []
    for ln in lines:
        if merged and str(merged[-1]["text"]).strip() == str(ln["text"]).strip():
            merged[-1]["end"] = ln["end"]
        else:
            merged.append(dict(ln))
    lines = merged
    draws = []
    # Music-video TITLE CARD over the intro (fades in/out, upper area so it never clashes with the
    # bottom karaoke captions). Designed like a real M/V title: an accent KICKER label + big title +
    # accent underline + songwriting credit — typography-led (not a subtitle box). Colours/font come
    # from the per-song THEME (title_style) so the card matches the music's mood, not one fixed pink.
    # A pre-rendered PNG title card (full-frame RGBA) wins over drawtext — it lets the caller
    # compose real typography (script fonts, soft shadows, two-tone lockups) that ffmpeg drawtext
    # can't. Overlaid with the same fade envelope in the render step below; drawtext title skipped.
    if title and not title_image:
        ta = "if(lt(t,0.7),(t-0.3)/0.4,if(gt(t,4.4),max(0,(5.0-t)/0.6),1))"
        th = _resolve_title_style(title_style)
        accent, tcolor, kcolor, tfont = th["accent"], th["title_color"], th["kicker_color"], th["title_font"]
        kf = work / "kicker.txt"; kf.write_text(th["kicker"], encoding="utf-8")
        draws.append(f"drawtext=fontfile='{FONT}':textfile='{kf}':fontsize=46:fontcolor={kcolor}:"
                     f"borderw=4:bordercolor=black:shadowcolor=black@0.6:shadowx=3:shadowy=3:"
                     f"x=(w-text_w)/2:y=h*0.135:enable='between(t,0.3,5.0)':alpha='{ta}'")
        tf = work / "title.txt"; tf.write_text(str(title), encoding="utf-8")
        draws.append(f"drawtext=fontfile='{tfont}':textfile='{tf}':fontsize=156:fontcolor={tcolor}:"
                     f"borderw=10:bordercolor=black:shadowcolor=black@0.65:shadowx=7:shadowy=7:"
                     f"x=(w-text_w)/2:y=h*0.135+62:enable='between(t,0.3,5.0)':alpha='{ta}'")
        # Accent underline is OPTIONAL: a theme with accent="" / None skips the bar entirely
        # (a hard rule under the title can read templatey / clash — PD 2026-11-05 on 산책냥's
        # pink line). Typography alone (kicker + title + credit) is the cleaner default.
        if accent:
            draws.append(f"drawbox=x=(iw-300)/2:y=ih*0.135+252:w=300:h=8:color={accent}:t=fill:"
                         f"enable='between(t,0.65,4.6)'")
        if credit:
            cf2 = work / "credit.txt"; cf2.write_text(str(credit), encoding="utf-8")
            draws.append(f"drawtext=fontfile='{FONT}':textfile='{cf2}':fontsize=52:fontcolor=white:"
                         f"borderw=5:bordercolor=black:shadowcolor=black@0.65:shadowx=4:shadowy=4:"
                         f"x=(w-text_w)/2:y=h*0.135+284:enable='between(t,0.3,5.0)':alpha='{ta}'")
    # Caption styles (karaoke lines). Each line may carry an "en" translation for bilingual display:
    #   None/legacy → single KO line at the bottom.
    #   "lyric"     → lyric-video treatment: KO hero (big) + EN below, lower-third, prominent.
    #   "cover"     → cover/subtitle treatment: KO + EN stacked at the very bottom, understated box.
    def _cap(tf_path, size, color, yexpr, bw, box, a, s, e):
        return (f"drawtext=fontfile='{FONT}':textfile='{tf_path}':fontsize={size}:fontcolor={color}:"
                f"borderw={bw}:bordercolor=black:shadowcolor=black@0.5:shadowx=3:shadowy=3:"
                + (f"box=1:boxcolor=black@0.45:boxborderw=14:" if box else "")
                + f"x=(w-text_w)/2:y={yexpr}:enable='between(t,{s},{e})':alpha='{a}'")
    for j, ln in enumerate(lines):
        s, e = float(ln["start"]), float(ln["end"])
        if s >= total:
            continue
        e = min(e, total)
        a = f"if(lt(t,{s+0.2}),(t-{s})/0.2,if(gt(t,{e-0.25}),max(0,({e}-t)/0.25),1))"
        ko = work / f"cap{j}.txt"; ko.write_text(ln["text"], encoding="utf-8")
        en_txt = (ln.get("en") or "").strip()
        en = None
        if en_txt:
            en = work / f"capen{j}.txt"; en.write_text(en_txt, encoding="utf-8")
        if caption_style == "lyric" and en:
            draws.append(_cap(ko, 66, "white",     "h*0.620",     7, False, a, s, e))
            draws.append(_cap(en, 42, "0xFFF0D8",  "h*0.620+92",  5, False, a, s, e))
        elif caption_style == "cover" and en:
            draws.append(_cap(ko, 54, "white",     "h-316",       6, caption_box, a, s, e))
            draws.append(_cap(en, 36, "0xEAEAEA",  "h-236",       5, caption_box, a, s, e))
        else:
            draws.append(_cap(ko, fontsize, "white", "h-text_h-205", 6, caption_box, a, s, e))
    # mv VIDEO only (captions + title), no audio yet, no end fade-to-black — the song carries
    # continuity straight into the bumper so there is no abrupt cut.
    mv = work / "mv_v.mp4"
    vchain = ",".join(draws) if draws else "null"
    if title_image and Path(title_image).exists():
        # Overlay the PNG title card with a soft fade in/out over the intro (same envelope as the
        # drawtext title would have had). -loop 1 makes the still a stream so the alpha fade has a
        # timeline; overlay `enable` + output -t bound it.
        fc = (f"[0:v]{vchain}[bg];"
              f"[1:v]format=rgba,fade=t=in:st=0.3:d=0.4:alpha=1,"
              f"fade=t=out:st=4.4:d=0.6:alpha=1[ttl];"
              f"[bg][ttl]overlay=0:0:enable='between(t,0.3,5.0)'[v]")
        _run(["ffmpeg", "-y", "-i", body, "-loop", "1", "-i", str(title_image),
              "-filter_complex", fc, "-map", "[v]", "-an",
              "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
              "-fps_mode", "cfr", *BT709, "-t", total, "-movflags", "+faststart", mv])
    else:
        _run(["ffmpeg", "-y", "-i", body, "-filter_complex", f"[0:v]{vchain}[v]", "-map", "[v]", "-an",
              "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
              "-fps_mode", "cfr", *BT709, "-t", total, "-movflags", "+faststart", mv])

    # 5) Let the song finish naturally WITHOUT a long BLACK tail or an EARLY bumper (PD): the music
    # cuts play through the instrumental outro — holding the last CUT frame (colourful, not the
    # black bumper) if the song runs longer — and the channel bumper comes ONLY at the very END,
    # video-only (its own theme audio dropped so the song keeps playing over it).
    dur_s = _dur(song)
    bmp, bumper_dur = None, 0.0
    if bumper and Path(bumper).exists():
        bmp = work / "bumper_v.mp4"
        # The channel bumper is a FINISHED asset — do NOT touch its colour. Measured: geometry-only
        # with NO range conversion and NO forced colour tags reproduces the source EXACTLY (patch
        # meanRGB identical); a range conversion (in=full:out=tv) visibly shifted/warmed it. So we
        # scale/pad ONLY and leave colour untagged — the pixels pass through unchanged.
        bumper_geom = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                       f"crop={W}:{H},setsar=1,fps=30,format=yuv420p")
        _run(["ffmpeg", "-y", "-i", bumper, "-vf", bumper_geom, "-map", "0:v:0", "-an",
              "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
              "-fps_mode", "cfr", bmp])
        bumper_dur = _dur(bmp)
    # music section lasts until the song is done minus the short bumper tail at the very end.
    # Base the extension on the ACTUAL cuts-video length (not the requested `total`, which can
    # differ if a clip was shorter than asked) so the song always plays out fully.
    mv_actual = _dur(mv)
    if bumper_overlap_fade is not None and bmp is not None:
        # OVERLAP the bumper over the END of the body (fading in) instead of appending it. The final
        # cut then plays its FULL motion underneath — appending required trimming the cuts, which
        # made the finale dance "end too fast" (PD 2026-11-05) — while the video still ENDS with the
        # bumper at the song's finish. Total = body length, so size the cuts ≈ song (minus nothing).
        ov_start = max(0.0, mv_actual - bumper_dur)
        full = work / "full_v.mp4"
        fc = (f"[1:v]format=yuva420p,fade=t=in:st=0:d={bumper_overlap_fade}:alpha=1,"
              f"setpts=PTS+{ov_start:.3f}/TB[bmp];"
              f"[0:v][bmp]overlay=0:0:enable='gte(t,{ov_start:.3f})':eof_action=pass[v]")
        _run(["ffmpeg", "-y", "-i", mv, "-i", bmp, "-filter_complex", fc, "-map", "[v]", "-an",
              "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
              "-fps_mode", "cfr", *BT709, full])
    else:
        # music section lasts until the song is done minus the short bumper tail at the very end.
        music_target = max(mv_actual + outro_hold, dur_s - bumper_dur)
        mv_music = mv
        if music_target > mv_actual + 0.3:
            ext = work / "mv_ext.mp4"
            _run(["ffmpeg", "-y", "-i", mv, "-vf", f"tpad=stop_mode=clone:stop_duration={music_target-mv_actual:.2f}",
                  "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
                  "-fps_mode", "cfr", *BT709, "-an", ext])
            mv_music = ext
        if bmp is not None:
            cl2 = work / "concat2.txt"
            cl2.write_text(f"file '{Path(mv_music).resolve()}'\nfile '{Path(bmp).resolve()}'\n", encoding="utf-8")
            full = work / "full_v.mp4"
            _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", cl2, "-an",
                  "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
                  "-fps_mode", "cfr", *BT709, full])
        else:
            full = mv_music
    dur = _dur(full)
    # Suno songs tend to have a LONG intro but a SHORT/abrupt outro (PD). Build a proper graceful
    # outro: fade the post-vocal instrumental tail from where the LAST lyric ends down to silence
    # (capped to ≤8s) so the song tapers out instead of hard-stopping — never fades over vocals.
    last_vocal = max((float(l["end"]) for l in lines), default=0.0)
    fstart = min(max(last_vocal, dur - 8.0), dur - 1.5)
    # ★Force SDR bt709 colour TAGS on the final output (even with -c:v copy this writes the mp4 `colr`
    # atom). Recent iPhone source clips are HLG HDR (bt2020 / arib-std-b67); even after HLG_TONEMAP
    # converts the PIXELS to SDR, the HDR colour *tags* can leak through to the output, so players
    # re-tonemap it → the whole video (and the bumper) shows an orange/red cast. Stamping bt709 here
    # makes players read it as plain SDR = correct colour. (CLAUDE.md "orange cast" gotcha, tag form.)
    _run(["ffmpeg", "-y", "-i", full, "-stream_loop", "-1", "-i", song,
          "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *BT709,
          "-af", f"afade=t=in:st=0:d=0.3,afade=t=out:st={fstart:.2f}:d={dur-fstart:.2f}",
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{dur}",
          "-movflags", "+faststart", out])
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
