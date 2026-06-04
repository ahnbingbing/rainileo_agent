"""
scripts/burn_captions.py
------------------------
Burn bilingual (KO/EN) captions into the 5 animated mp4 cuts using ffmpeg's
drawtext filter. Replaces the manual CapCut text-overlay step.

Pipeline
--------
    data/output/animated/<tag>.mp4
    + scripts/prompts/captions_bilingual.json
    →  data/output/animated_captioned/<tag>.mp4

Why drawtext (not subtitles+libass)
-----------------------------------
Homebrew ffmpeg 8.1.x (current default) ships without libass support, so the
`subtitles` filter isn't registered ("No such filter: 'subtitles'"). The
drawtext filter is built into every ffmpeg build and takes a fontfile path
directly via freetype, so we get reliable Korean glyph rendering without
fighting libass/fontconfig family-name matching (which was producing tofu
boxes for "Apple SD Gothic Neo" via the system TTC).

Layout (matches CAPCUT_SHOT_LIST.md style block):
  KO line — fontsize 68, white + 4px black outline + soft shadow
  EN line — fontsize 50, white + 3px black outline + soft shadow
  Position — bottom-center, KO above EN, KO bottom ~320 px / EN bottom ~235 px
             above screen bottom (drawtext y is top-of-text, so we subtract
             text_h at render time to anchor by the bottom edge).
  Fade-in — 0.3s linear ramp starting at t=0.25s; held until 0.25s before clip
            end, then `enable` hides the text (no tail fade — Shorts pacing).

Font
----
~/Library/Fonts/NotoSansKR[wght].ttf — Noto Sans KR (variable font, installed
via `brew install --cask font-noto-sans-kr`). drawtext doesn't pick a weight
axis from variable fonts, so both lines use the default instance (Regular).
Visual hierarchy comes from fontsize + borderw, not weight. If you want true
Bold for KO later, drop a non-variable Bold TTF in and point KO_FONT_PATH
at it.

The `[wght]` brackets in the filename are filter-graph special chars (label
syntax), so the path is wrapped in single quotes inside the drawtext arg.

Usage
-----
    # all 5 cuts
    python3 scripts/burn_captions.py

    # one cut (handy for iterating)
    python3 scripts/burn_captions.py --cut cut1_ryani_hook

    # dry-run — print the filter chain, no ffmpeg call
    python3 scripts/burn_captions.py --dry-run

    # custom input/output dirs (e.g. on a re-rolled batch)
    python3 scripts/burn_captions.py --in-dir data/output/animated_v2 \
                                     --out-dir data/output/animated_v2_captioned

Exit codes
----------
    0   all requested cuts succeeded
    1   one or more cuts failed
    2   bad setup (caption file missing, font missing, no ffmpeg)

Dependencies
------------
ffmpeg with drawtext (built-in, libass not needed) + Noto Sans KR variable
font at the path above. Verify drawtext:
    ffmpeg -filters 2>/dev/null | grep drawtext
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANIM_DIR = ROOT / "data" / "output" / "animated"
OUT_DIR_DEFAULT = ROOT / "data" / "output" / "animated_captioned"
TMP_DIR = ROOT / "data" / "tmp" / "captions"
# Default manifest = Episode 01. Episode 02+ pass --manifest to point at
# their own JSON (so episodes don't trample each other's captions).
CAPTIONS_FILE_DEFAULT = ROOT / "scripts" / "prompts" / "captions_bilingual.json"

# ─── font + style constants ──────────────────────────────────────────────
# Pretendard — modern Korean sans serif, installed via
#     brew install --cask font-pretendard
# Cask drops individual weight files in ~/Library/Fonts/. We use Bold for KO
# (primary, needs emphasis) and Medium for EN (secondary, softer). If the
# cask installs .ttf instead of .otf, flip the extension below.
_FONT_DIR = Path.home() / "Library" / "Fonts"

# Default: 손글씨 스타일 (NanumPenScript) — real_footage 느낌의 따뜻한 캡션
# 특별 컨셉(부처님 오신날 등)은 Producer/Director가 font_override로 다른 폰트 지정 가능
HANDWRITING_FONT = _FONT_DIR / "NanumPenScript-Regular.ttf"
MODERN_FONT_KO = _FONT_DIR / "Pretendard-Bold.otf"
MODERN_FONT_EN = _FONT_DIR / "Pretendard-Medium.otf"

# 2026-05-31: default → NanumPenScript handwriting (per PD preference).
# Re-test showed NanumPenScript covers all v23 caption syllables — earlier
# "letters dropping" complaint was likely a different rendering issue
# (border edge, force-wrap split, or font-size mismatch). Switching back to
# handwriting as default; if specific chars actually fail to render, build
# a per-glyph fallback rather than swap the whole font.
# PD 2026-06-02: switched default to "modern" (Pretendard) so the entire
# episode uses a consistent typeface — and Pretendard has heart ♥ glyphs.
# Earlier default was "handwriting" (NanumPen) but mid-cut font switching
# looked awkward, and NanumPen lacks heart glyphs.
_FONT_STYLE = os.getenv("FONT_STYLE", "modern").lower()
if _FONT_STYLE == "handwriting" and HANDWRITING_FONT.exists():
    KO_FONT_PATH = HANDWRITING_FONT
    EN_FONT_PATH = HANDWRITING_FONT
else:
    KO_FONT_PATH = MODERN_FONT_KO
    EN_FONT_PATH = MODERN_FONT_EN
FONT_PATH = KO_FONT_PATH


def _font_paths_for_tag_and_text(tag: str, text: str) -> tuple[Path, Path]:
    """PD 2026-06-02: NanumPen doesn't have ♥/♡/❤ glyphs but Pretendard does.
    Auto-switch to Pretendard for any cut tagged wink_ending OR any text
    containing heart symbols. Other cuts keep the default (handwriting)."""
    needs_modern = "wink_ending" in (tag or "").lower() or any(
        c in (text or "") for c in "♥♡❤❥💕💖💗💓"
    )
    if needs_modern:
        return MODERN_FONT_KO, MODERN_FONT_EN
    return KO_FONT_PATH, EN_FONT_PATH

KO_SIZE_DEFAULT = 72  # px — Pretendard default (PD 2026-06-02: shrunk from 84 when switching to sans-serif which reads tighter)
EN_SIZE_DEFAULT = 48  # px — EN sub line (shrunk from 56)
PADDING_X = 60        # px left/right padding from screen edge
SCREEN_WIDTH = 1080   # 9:16 Shorts
EPISODE_HEIGHT = 1920 # final episode height (we now burn at this resolution)
# burn_captions upscales the source to 1080x1920 BEFORE drawtext so the
# rendered text appears at its true on-screen size (no later assemble scale-up
# of the text). PIL under-measures NanumPenScript by ~25-30%; wrap budget
# accounts for that. PIL_width × 1.3 ≤ 920 (screen 1080 - 80 margin each side).
USABLE_WIDTH = 820    # generous wrap budget on the 1080 canvas (bumped 720→820 for fewer line breaks)
KO_BORDER = 7         # px black outline — 손글씨 배경 위 가독성 (bumped for larger font)
EN_BORDER = 5         # px (bumped from 4)
SHADOW_X = 4          # px drop shadow X offset (bumped 3→4)
SHADOW_Y = 4          # px drop shadow Y offset (bumped 3→4)
KO_Y_FROM_BOTTOM = 220  # KO line bottom edge from screen bottom (bumped 180→220 — bigger KO font needs more clearance above EN)
EN_Y_FROM_BOTTOM = 100  # EN line bottom edge from screen bottom (bumped 90→100)
FADE_S = 0.30         # fade-in duration in seconds
SHOW_START_S = 0.25   # delay before captions appear
TAIL_OFFSET_S = 0.25  # hide captions this many seconds before clip end

COLOR_FG = "white"
COLOR_BORDER = "black"
COLOR_SHADOW = "black@0.5"   # 50% alpha black


def _rel(p: Path) -> str:
    """Best-effort: show p as a repo-relative path, else absolute."""
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def ffprobe_duration(mp4: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(mp4)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def wrap_for_render(text: str, font_path: Path, font_size: int,
                    max_width: int) -> str:
    """Re-wrap text so every line fits within max_width at the actual render
    font + size. drawtext renders \\n as a new line, so we just insert breaks.

    Why this exists: cameraman.py pre-wraps using a *different* font
    (Pretendard) than burn_captions.py renders with (NanumPenScript handwriting,
    much wider). A line that "fits" per cameraman can overflow at render. We
    re-measure here with the truth font and break on word boundaries.

    Korean has no word spaces between particles, so the word-split fallback
    can still produce one ultra-long token. For that case we fall back to
    grapheme-level breaks so nothing exceeds the screen.
    """
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(str(font_path), font_size)
    except Exception:
        return text  # caller will see clipping but we can't measure

    out_lines: list[str] = []
    for src_line in text.split("\n"):
        if font.getlength(src_line) <= max_width:
            out_lines.append(src_line)
            continue
        # Try word wrap first (space-delimited)
        words = src_line.split(" ")
        cur = ""
        for w in words:
            test = (cur + " " + w).strip() if cur else w
            if font.getlength(test) > max_width and cur:
                out_lines.append(cur)
                cur = w
            else:
                cur = test
        if cur:
            out_lines.append(cur)
        # If any post-wrap line is STILL too long (e.g. one Korean clause with
        # no spaces), break it character by character.
        rewrapped: list[str] = []
        for line in out_lines[-(len(words) if words else 0):]:
            if font.getlength(line) <= max_width:
                rewrapped.append(line)
                continue
            cur = ""
            for ch in line:
                test = cur + ch
                if font.getlength(test) > max_width and cur:
                    rewrapped.append(cur)
                    cur = ch
                else:
                    cur = test
            if cur:
                rewrapped.append(cur)
        if rewrapped:
            # Replace the just-added word-wrap lines with character-wrap lines
            out_lines[-(len(words) if words else 0):] = rewrapped
    return "\n".join(out_lines)


def calc_fontsize(text: str, font_path: Path, default_size: int, max_width: int) -> int:
    """Pick the largest font size where (after wrap_for_render at that size)
    every line fits within max_width. This means we PREFER big fonts that
    wrap to multiple lines over small fonts that fit on one line.

    Floor: default_size // 2 (don't shrink past readable).
    """
    try:
        from PIL import ImageFont
        floor = max(28, default_size // 2)
        for size in range(default_size, floor - 1, -2):
            font = ImageFont.truetype(str(font_path), size)
            wrapped = wrap_for_render(text, font_path, size, max_width)
            lines = wrapped.split("\n")
            if not lines:
                continue
            max_line_w = max(font.getlength(line) for line in lines)
            if max_line_w <= max_width:
                return size
        return floor
    except Exception:
        return max(28, int(default_size * 0.7))


def build_drawtext(font: Path, textfile: Path, fontsize: int, borderw: int,
                   y_from_bottom: int, start: float, end: float,
                   fade: float, position: str = "bottom") -> str:
    """Build one drawtext filter for a single caption line.

    Uses `textfile=` rather than `text=` so we never have to escape Korean,
    commas, apostrophes, etc. in the filter argument — the file is read as
    UTF-8 verbatim.

    position: "bottom" (default — y_from_bottom px above screen bottom) or
    "top" (y_from_bottom px below screen top). Used when pets are in lower
    half of frame and bottom captions would occlude them.

    text_align=T+L = top-anchor + left-align. Center-per-line (the default
    T+C) makes short wrapped lines look indented when sandwiched between
    longer lines. Left-align keeps every line's left edge at the block's
    x position, eliminating the "padded middle line" visual artifact.
    """
    fade_end = start + fade
    alpha = f"if(lt(t,{fade_end}),(t-{start})/{fade},1)"
    if position == "top":
        y_expr = f"{y_from_bottom}"  # treat as y_from_top in top mode
    else:
        y_expr = f"h-text_h-{y_from_bottom}"
    parts = [
        f"fontfile='{font}'",
        f"textfile='{textfile}'",
        f"fontsize={fontsize}",
        f"fontcolor={COLOR_FG}",
        f"borderw={borderw}",
        f"bordercolor={COLOR_BORDER}",
        f"shadowcolor={COLOR_SHADOW}",
        f"shadowx={SHADOW_X}",
        f"shadowy={SHADOW_Y}",
        f"text_align=T+L",
        f"x=(w-text_w)/2",
        f"y={y_expr}",
        f"enable='between(t,{start},{end})'",
        f"alpha='{alpha}'",
    ]
    return "drawtext=" + ":".join(parts)


def build_vf(ko_file: Path, en_file: Path, duration: float) -> str:
    """Chain KO + EN drawtext filters into a -vf string.
    If EN is empty, only KO drawtext is used (KO may contain merged KO+EN text).
    Re-wraps the text in-place so render-font overflow becomes a new line."""
    start = SHOW_START_S
    end = max(start + 0.5, duration - TAIL_OFFSET_S)

    ko_text = ko_file.read_text(encoding="utf-8").strip() if ko_file.exists() else ""
    ko_size = calc_fontsize(ko_text, KO_FONT_PATH, KO_SIZE_DEFAULT, USABLE_WIDTH)
    ko_wrapped = wrap_for_render(ko_text, KO_FONT_PATH, ko_size, USABLE_WIDTH)
    if ko_wrapped != ko_text:
        ko_file.write_text(ko_wrapped, encoding="utf-8")
    # Border ALWAYS uses KO_BORDER. No conditional shrink — past bug let
    # smaller-font KO captions render border-less, making KO illegible
    # against busy backgrounds.
    ko_dt = build_drawtext(KO_FONT_PATH, ko_file, ko_size, KO_BORDER,
                           KO_Y_FROM_BOTTOM, start, end, FADE_S)

    en_text = en_file.read_text(encoding="utf-8").strip() if en_file.exists() else ""
    if en_text:
        en_size = calc_fontsize(en_text, EN_FONT_PATH, EN_SIZE_DEFAULT, USABLE_WIDTH)
        en_wrapped = wrap_for_render(en_text, EN_FONT_PATH, en_size, USABLE_WIDTH)
        if en_wrapped != en_text:
            en_file.write_text(en_wrapped, encoding="utf-8")
        en_dt = build_drawtext(EN_FONT_PATH, en_file, en_size, EN_BORDER,
                               EN_Y_FROM_BOTTOM, start, end, FADE_S)
        return f"{ko_dt},{en_dt}"
    return ko_dt


def build_vf_multi(scenes: list[dict], tag: str, duration: float,
                   caption_position: str = "bottom") -> str:
    """Build drawtext filters for multiple timed caption scenes.

    Each scene has {start, end, ko, en, [position]}. Creates a separate
    drawtext per scene, each with its own enable=between(t,start,end) window.

    `caption_position` arg is the cut-level default ("bottom" or "top").
    A scene can override per-scene with `position` key.
    """
    # Top-mode anchor: place captions Y_FROM_TOP px from screen top.
    KO_Y_FROM_TOP = 220   # symmetric to KO_Y_FROM_BOTTOM
    EN_Y_FROM_TOP = 320   # below KO when at top

    filters = []
    for i, sc in enumerate(scenes):
        ko = sc.get("ko", "").strip()
        en = sc.get("en", "").strip()
        if not ko and not en:
            continue
        start = float(sc.get("start", 0.2))
        end = float(sc.get("end", duration - TAIL_OFFSET_S))
        end = min(end, duration - 0.1)
        pos = sc.get("position") or caption_position

        # PD 2026-06-02: per-scene font selection — switch to Pretendard
        # when text contains heart symbols or for wink_ending cuts (handwriting
        # font has no heart glyphs).
        ko_font, en_font = _font_paths_for_tag_and_text(tag, ko + " " + en)

        if ko:
            ko_sz = calc_fontsize(ko, ko_font, KO_SIZE_DEFAULT, USABLE_WIDTH)
            ko_wrapped = wrap_for_render(ko, ko_font, ko_sz, USABLE_WIDTH)
            ko_file = TMP_DIR / f"{tag}_s{i}.ko.txt"
            ko_file.write_text(ko_wrapped, encoding="utf-8")
            ko_y = KO_Y_FROM_TOP if pos == "top" else KO_Y_FROM_BOTTOM
            filters.append(build_drawtext(
                ko_font, ko_file, ko_sz, KO_BORDER,
                ko_y, start, end, FADE_S, position=pos))
        if en:
            en_sz = calc_fontsize(en, en_font, EN_SIZE_DEFAULT, USABLE_WIDTH)
            en_wrapped = wrap_for_render(en, en_font, en_sz, USABLE_WIDTH)
            en_file = TMP_DIR / f"{tag}_s{i}.en.txt"
            en_file.write_text(en_wrapped, encoding="utf-8")
            en_y = EN_Y_FROM_TOP if pos == "top" else EN_Y_FROM_BOTTOM
            filters.append(build_drawtext(
                en_font, en_file, en_sz, EN_BORDER,
                en_y, start, end, FADE_S, position=pos))

    return ",".join(filters) if filters else ""


def _scale_prefix() -> str:
    """ffmpeg filter chain prefix that upscales the source to 1080x1920
    (with letterbox if needed) before drawtext runs. Keeping the burn on
    the final episode resolution means fontsize values render at their true
    on-screen size — no surprise shrink from a later assemble-time scale-up.
    """
    return (
        f"scale={SCREEN_WIDTH}:{EPISODE_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={SCREEN_WIDTH}:{EPISODE_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1"
    )


def burn_one(src: Path, ko_file: Path, en_file: Path, duration: float,
             out: Path) -> None:
    """Run ffmpeg with the two drawtext filters chained.

    Audio is optional via `-map 0:a?` (Veo lite sometimes has no audio).
    Video is re-encoded at crf=18 (visually lossless) since text is burned in.
    """
    vf = _scale_prefix() + "," + build_vf(ko_file, en_file, duration)
    subprocess.run(
        ["ffmpeg", "-y", "-nostats", "-loglevel", "error",
         "-i", str(src),
         "-vf", vf,
         "-map", "0:v", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-c:a", "copy",
         "-movflags", "+faststart",
         str(out)],
        check=True,
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cut", default=None,
                   help="process a single tag (default: all in the JSON)")
    p.add_argument("--in-dir", default=str(ANIM_DIR),
                   help=f"source mp4 dir (default: {_rel(ANIM_DIR)})")
    p.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT),
                   help=f"output dir (default: {_rel(OUT_DIR_DEFAULT)})")
    p.add_argument("--manifest", default=str(CAPTIONS_FILE_DEFAULT),
                   help=f"captions JSON path (default: {_rel(CAPTIONS_FILE_DEFAULT)})")
    p.add_argument("--font", default=None,
                   help="override font path (Producer/Director가 특별 컨셉용 폰트 지정)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the drawtext filter chain without running ffmpeg")
    args = p.parse_args()

    # Font override: Director가 특별 컨셉(부처님 오신날 등)용 폰트 지정
    if args.font:
        font_path = Path(args.font)
        if font_path.exists():
            global KO_FONT_PATH, EN_FONT_PATH, FONT_PATH
            KO_FONT_PATH = font_path
            EN_FONT_PATH = font_path
            FONT_PATH = font_path
            print(f"  font override: {font_path.name}")
        else:
            print(f"  font override not found: {args.font}, using default", file=sys.stderr)

    captions_file = Path(args.manifest)
    if not captions_file.exists():
        print(f"ERROR: caption file {captions_file} not found", file=sys.stderr)
        return 2
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found in PATH", file=sys.stderr)
        return 2
    # Font check is skipped on dry-run so the user can preview the filter
    # chain even on a machine without the font (e.g. a Linux sandbox).
    if not args.dry_run and not FONT_PATH.exists():
        print(f"ERROR: font not found at {FONT_PATH}", file=sys.stderr)
        print("       Install via: brew install --cask font-pretendard",
              file=sys.stderr)
        return 2

    raw = json.loads(captions_file.read_text(encoding="utf-8"))
    # filter out metadata keys (_episode_id, _comment, _outro_caption, etc.)
    # AND any values that aren't dicts with ko/en
    captions = {k: v for k, v in raw.items()
                if not k.startswith("_") and isinstance(v, dict)}

    in_dir = Path(args.in_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    tags = [args.cut] if args.cut else list(captions.keys())
    failures = 0
    for tag in tags:
        if tag not in captions:
            print(f"  ! {tag} missing from captions JSON; skipping",
                  file=sys.stderr)
            failures += 1
            continue
        entry = captions[tag]

        src = in_dir / f"{tag}.mp4"
        if not src.exists():
            print(f"  ! {src} not found; skipping", file=sys.stderr)
            failures += 1
            continue

        try:
            dur = ffprobe_duration(src)
        except subprocess.CalledProcessError as e:
            print(f"  ! ffprobe failed for {src.name}: {e}", file=sys.stderr)
            failures += 1
            continue

        out = out_dir / f"{tag}.mp4"
        print(f"==> {tag}")
        print(f"    dur = {dur:.2f}s")

        # Multi-scene support: each scene gets its own timed drawtext
        scenes = entry.get("scenes", []) if isinstance(entry.get("scenes"), list) else []
        # PD 2026-06-02: intentionally empty captions (e.g. wink cut) →
        # copy source through unmodified, do NOT mark as failure.
        if not scenes and not entry.get("ko") and not entry.get("en"):
            print(f"    (no captions — passthrough copy)")
            if args.dry_run:
                print(f"    [dry-run] would copy → {_rel(out)}")
                continue
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                     "-i", str(src), "-c", "copy",
                     "-movflags", "+faststart", str(out)],
                    check=True,
                )
                size_mb = out.stat().st_size / 1e6
                print(f"    ok ({size_mb:.2f} MB, passthrough) → {_rel(out)}")
            except subprocess.CalledProcessError as e:
                print(f"    ! passthrough copy failed (rc={e.returncode})",
                      file=sys.stderr)
                failures += 1
            continue
        # PD 2026-06-02: even single-scene captions must respect start/end
        # timing (was bug: single scene path burned full clip duration,
        # ignoring the scene's start/end). Use build_vf_multi for any
        # scenes-based input.
        if scenes:
            # Multiple timed captions — use build_vf_multi
            for i, sc in enumerate(scenes):
                s_ko = sc.get("ko", "")
                s_en = sc.get("en", "")
                print(f"    scene[{i}] ({sc.get('start',0)}-{sc.get('end','?')}s): ko={s_ko!r}")
            cut_caption_pos = entry.get("caption_position", "bottom")
            vf = build_vf_multi(scenes, tag, dur, caption_position=cut_caption_pos)
            if not vf:
                print(f"  ! {tag} all scenes empty; skipping", file=sys.stderr)
                failures += 1
                continue
            if args.dry_run:
                for i, dt in enumerate(vf.split(",")):
                    print(f"    [dry-run] filter[{i}]:\n      {dt}")
                print(f"    [dry-run] would write → {_rel(out)}")
                continue
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                     "-i", str(src),
                     "-vf", _scale_prefix() + "," + vf,
                     "-map", "0:v", "-map", "0:a?",
                     "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                     "-c:a", "copy", "-movflags", "+faststart", str(out)],
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                print(f"    ! ffmpeg failed (rc={e.returncode})", file=sys.stderr)
                failures += 1
                continue
        else:
            # Single caption (legacy or scenes[0] only)
            if scenes:
                first = scenes[0]
                ko = first.get("ko", "").strip()
                en = first.get("en", "").strip()
            else:
                ko = entry.get("ko", "").strip()
                en = entry.get("en", "").strip()
            if not ko and not en:
                print(f"  ! {tag} has empty ko/en; skipping", file=sys.stderr)
                failures += 1
                continue
            print(f"    ko  = {ko!r}")
            print(f"    en  = {en!r}")
            ko_file = TMP_DIR / f"{tag}.ko.txt"
            en_file = TMP_DIR / f"{tag}.en.txt"
            ko_file.write_text(ko, encoding="utf-8")
            en_file.write_text(en, encoding="utf-8")
            if args.dry_run:
                vf = build_vf(ko_file, en_file, dur)
                for i, dt in enumerate(vf.split(",")):
                    tag_lbl = "KO" if i == 0 else "EN"
                    print(f"    [dry-run] {tag_lbl}:\n      {dt}")
                print(f"    [dry-run] would write → {_rel(out)}")
                continue
            try:
                burn_one(src, ko_file, en_file, dur, out)
            except subprocess.CalledProcessError as e:
                print(f"    ! ffmpeg failed (rc={e.returncode})", file=sys.stderr)
                failures += 1
                continue

        size_mb = out.stat().st_size / 1e6
        print(f"    ok ({size_mb:.2f} MB) → {_rel(out)}")

    print()
    if failures:
        print(f"done — {failures} failure(s)")
        return 1
    print("done — all cuts burned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
