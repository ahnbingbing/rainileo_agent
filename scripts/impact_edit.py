#!/usr/bin/env python3
"""scripts/impact_edit.py — REAL impact re-editor from raw clips (not a filter).

Unlike scripts/edit_style.py (a post-filter on a finished episode — which PD
correctly rejected as "just a filter"), this rebuilds a Short from RAW source
clips with actual editing: high-motion window selection, beat-synced cuts,
per-cut club-light color, speed ramps, zoom punches, kinetic captions, sound
design. Length targets escape the 25-40s dead zone.

Grammars (= PD's "3 lanes", each a bandit edit_style arm once approved):
  • velocity  (= PD's "club")   15-20s  beat-cut montage, per-cut COLOR CAST
                                        (club lighting), slow→snap drop ramp.
  • meme      (reaction)        15-20s  jump cuts, ZOOM PUNCH, freeze frame,
                                        big kinetic captions + SFX stings.
  • story     (payoff-first)    ~35-45s COLD-OPEN the climax ("이게 무슨
                                        일이냐면…") → rewind → build → return
                                        to the payoff. Retention hook grammar.
                                        (Extends to YouTube long-form later;
                                         TikTok stays Shorts, YouTube goes long.)

PD note on color: full hue-rotation turned the *subject* odd colors (a green
dog) — awkward. Club look = colored LIGHT cast (colorbalance) so the subject
stays readable while the scene bathes in neon, and it changes boldly per cut.

Phase-0 PROOF: hand-driven clip plans, run locally for fast iteration. Once a
look is approved it graduates into assemble_episode.py as a bandit edit_style arm.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
import shutil as _shutil


def _pick_ff(name: str) -> str:
    """Host-portable ffmpeg/ffprobe. Prefer the stable system binary at /usr/bin (VM's
    ffmpeg 5.1 supports our arg set incl. -vsync), THEN PATH, THEN Mac Homebrew. The VM
    ALSO has a ~/.local/bin static build that dropped -vsync ("Option not found") and can
    win a bare PATH lookup — so /usr/bin is checked first."""
    for c in (f"/usr/bin/{name}", f"/opt/homebrew/bin/{name}"):
        if os.path.exists(c):
            return c
    return _shutil.which(name) or f"/opt/homebrew/bin/{name}"


FF = _pick_ff("ffmpeg")
FP = _pick_ff("ffprobe")
DB = ROOT / "data" / "agent.db"


def _font(*names: str) -> str:
    """First existing Pretendard weight from `names`. The Mac dev box has every weight
    (Homebrew cask) but the VM only ships Bold/ExtraBold/Medium — a hardcoded
    Pretendard-Black.otf there is a MISSING fontfile, so drawtext renders Korean as tofu
    (□□□). Fall back to an installed heavier weight so KO burns correctly on both hosts."""
    for n in names:
        p = os.path.expanduser(f"~/Library/Fonts/{n}")
        if os.path.exists(p):
            return p
    return os.path.expanduser(f"~/Library/Fonts/{names[0]}")


FONT_BLACK = _font("Pretendard-Black.otf", "Pretendard-ExtraBold.otf", "Pretendard-Bold.otf")
FONT_XBOLD = _font("Pretendard-ExtraBold.otf", "Pretendard-Bold.otf")
W, H, FPS = 1080, 1920, 30
BGM = ROOT / "assets" / "bgm"
# Channel bumpers — the grammar path renders its OWN body (concat+caption+music) and so
# bypasses assemble_episode.py, which is what prepends/appends the bumpers on the standard
# RF path. Without wrapping here, every grammar RF episode ships with NO intro/outro
# (the D_grammarlive lesson: a bypass inherits the bypassed path's hidden contracts).
INTRO_BUMPER = ROOT / "assets" / "branding" / "intro_bumper.mp4"
OUTRO_BUMPER = ROOT / "assets" / "branding" / "outro_bumper.mp4"


def _run(cmd: list[str]):
    # use -v warning (not error) so real diagnostics survive; some failures print nothing under -v error
    cmd = [c if c != "error" else "warning" for c in cmd]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed (rc=%d):\n%s\n---STDERR---\n%s\n---STDOUT---\n%s"
                           % (r.returncode, " ".join(cmd), r.stderr[-2500:], r.stdout[-800:]))
    return r


def probe_dur(f: str) -> float:
    out = subprocess.run([FP, "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nk=1:nw=1", f], capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def resolve(asset_id: str) -> str:
    con = sqlite3.connect(str(DB))
    r = con.execute("select file_path from assets where asset_id=?", (asset_id,)).fetchone()
    con.close()
    f = r[0]
    if not os.path.isabs(f):
        f = str(ROOT / f)
    return f


def _resolve_clip(v: str) -> str:
    """Accept either an asset_id (DB lookup) OR a direct file path — so production
    (Phase B) can hand the grammar already-selected clip paths without a DB round-trip."""
    if os.path.exists(v):
        return v
    p = ROOT / v
    return str(p) if p.exists() else resolve(v)


# ──────────────────────────────────────────────────────────────────────
# Motion analysis — find the most kinetic windows (what a velocity edit wants)
# ──────────────────────────────────────────────────────────────────────
def motion_curve(clip: str, fps: int = 10, edge: int = 64) -> tuple[np.ndarray, float]:
    """Mean abs frame-difference over time on a tiny grayscale copy."""
    raw = subprocess.run(
        [FF, "-v", "error", "-i", clip, "-vf", f"fps={fps},scale={edge}:{edge},format=gray",
         "-f", "rawvideo", "-"], capture_output=True).stdout
    n = len(raw) // (edge * edge)
    if n < 3:
        return np.array([0.0]), fps
    a = np.frombuffer(raw[: n * edge * edge], np.uint8).astype(np.float32).reshape(n, edge * edge)
    d = np.abs(np.diff(a, axis=0)).mean(axis=1)
    return d, float(fps)


def clip_motion_peak(clip: str, win: float = 1.2) -> float:
    """Mean frame-motion in the clip's single most kinetic `win`-second window — a scalar
    'how energetic is this clip at its peak' used by the footage-fit gate. Calibrated scale
    (64×64 gray abs-frame-diff): a running/swimming/playing pet peaks ~23-30, a calm nap ~6,
    a sniff/walk ~8-14. So a velocity edit (which NEEDS a genuine kinetic climax) can require
    the cast to clear a floor, and fall back to standard RF instead of hue-strobing a sniff."""
    d, efps = motion_curve(clip)
    if d.size < 2:
        return 0.0
    wlen = max(1, int(win * efps))
    if d.size <= wlen:
        return float(d.mean())
    csum = np.cumsum(np.insert(d, 0, 0))
    return float(((csum[wlen:] - csum[:-wlen]) / wlen).max())


def top_motion_windows(clip: str, n: int, win: float, *, guard: float = 0.3) -> list[tuple[float, float]]:
    """Pick `n` non-overlapping HIGH-motion windows of length `win` seconds."""
    d, efps = motion_curve(clip)
    dur = probe_dur(clip)
    wlen = max(1, int(win * efps))
    if d.size <= wlen or d.max() <= 1e-6:
        k = max(1, n)
        starts = np.linspace(guard, max(guard, dur - win - guard), k)
        return [(round(float(s), 2), win) for s in starts]
    csum = np.cumsum(np.insert(d, 0, 0))
    score = csum[wlen:] - csum[:-wlen]
    order = np.argsort(score)[::-1]
    chosen: list[int] = []
    gap = wlen
    for i in order:
        t = i / efps
        if t < guard or t + win > dur - guard * 0.5:
            continue
        if all(abs(i - j) >= gap for j in chosen):
            chosen.append(int(i))
        if len(chosen) >= n:
            break
    chosen.sort()
    return [(round(i / efps, 2), win) for i in chosen]


def best_motion_window(clip: str, win: float, *, guard: float = 0.3) -> tuple[float, float]:
    """The single most kinetic window — used as the story COLD-OPEN payoff."""
    w = top_motion_windows(clip, 1, win, guard=guard)
    return w[0] if w else (guard, win)


def motion_field(clip: str, fps: int = 10, edge: int = 64, block: int = 8) -> tuple[np.ndarray, float]:
    """Per-frame, block-pooled abs frame-difference: (T, block*block) — the SPATIAL layout of
    motion over time (motion_curve is this averaged over space). Used to tell a localized moving
    subject (a few hot blocks) from diffuse full-frame motion (water splash, wind, rain)."""
    raw = subprocess.run(
        [FF, "-v", "error", "-i", clip, "-vf", f"fps={fps},scale={edge}:{edge},format=gray",
         "-f", "rawvideo", "-"], capture_output=True).stdout
    n = len(raw) // (edge * edge)
    if n < 3:
        return np.zeros((1, block * block), np.float32), float(fps)
    a = np.frombuffer(raw[: n * edge * edge], np.uint8).astype(np.float32).reshape(n, edge, edge)
    d = np.abs(np.diff(a, axis=0))                       # (n-1, edge, edge)
    s = edge // block
    d = d.reshape(d.shape[0], block, s, block, s).mean(axis=(2, 4))   # pool → (n-1, block, block)
    return d.reshape(d.shape[0], block * block), float(fps)


def salient_motion_window(clip: str, win: float, *, guard: float = 0.3) -> tuple[float, float]:
    """The window maximizing motion × spatial CONCENTRATION — favors a localized moving SUBJECT
    over diffuse full-frame motion, so a story hook/payoff frame actually contains the pet rather
    than empty splashing water. A running pet scores high on BOTH (energy + concentration) so it
    still wins on normal clips (no regression); on a fountain clip it picks the leap-in moment
    (dog + splash) instead of the pure-splash motion max where the dog is out of frame. Falls back
    to best_motion_window when the field is uninformative (very calm clip)."""
    f, efps = motion_field(clip)
    dur = probe_dur(clip)
    wlen = max(1, int(win * efps))
    if f.shape[0] <= wlen or f.max() <= 1e-6:
        return best_motion_window(clip, win, guard=guard)
    csum = np.cumsum(np.insert(f, 0, 0, axis=0), axis=0)      # (T+1, blocks)
    winsum = csum[wlen:] - csum[:-wlen]                       # (Twin, blocks)
    energy = winsum.sum(axis=1)
    k = max(1, winsum.shape[1] // 8)                          # top ~1/8 of blocks = the "subject"
    top = np.sort(winsum, axis=1)[:, -k:].sum(axis=1)
    conc = top / (energy + 1e-6)                              # ~1 = energy in few blocks (localized)
    en = energy / (energy.max() + 1e-6)                       # normalize so the product is balanced
    score = en * conc
    for i in np.argsort(score)[::-1]:
        t = i / efps
        if t < guard or t + win > dur - guard * 0.5:
            continue
        return (round(float(i) / efps, 2), win)
    return best_motion_window(clip, win, guard=guard)


def even_windows(clip: str, n: int, win: float, *, guard: float = 0.4) -> list[tuple[float, float]]:
    """Evenly spaced windows (calm build beats — not motion-peaked)."""
    dur = probe_dur(clip)
    if dur < win + 2 * guard:
        return [(round(max(0.0, (dur - win) / 2), 2), min(win, max(0.5, dur - 0.1)))]
    starts = np.linspace(guard, dur - win - guard, max(1, n))
    return [(round(float(s), 2), win) for s in starts]


# ──────────────────────────────────────────────────────────────────────
# Beat grid (velocity only)
# ──────────────────────────────────────────────────────────────────────
def beat_grid(music: str) -> tuple[float, float]:
    import sys
    sys.path.insert(0, str(ROOT))
    from scripts.edit_style import estimate_tempo_phase
    return estimate_tempo_phase(Path(music))


# ──────────────────────────────────────────────────────────────────────
# Color: club-light casts (colorbalance shadows/mids/highlights toward a hue)
# Bold enough to read as LIGHTING, not a broken tint. Changes per cut.
# (rs,gs,bs, rm,gm,bm, rh,gh,bh)
# ──────────────────────────────────────────────────────────────────────
CLUB = [
    (.40, -.18, .40,  .22, -.10, .26,  .26, -.05, .22),   # magenta
    (-.28, .16, .42,  -.16, .10, .28,  -.10, .10, .24),   # cyan
    (.44, .14, -.34,   .26, .06, -.22,  .20, .05, -.16),  # amber
    (.28, -.22, .46,   .16, -.12, .32,  .10, -.06, .26),  # violet
    (-.30, .40, -.22,  -.18, .26, -.12,  -.10, .22, -.06),  # green
    (.46, -.06, .24,   .32, .00, .16,   .26, .04, .10),   # hot pink
    (-.34, -.06, .46,  -.22, .00, .32,  -.10, .04, .26),  # electric blue
    (.50, -.24, -.16,  .32, -.12, -.10,  .22, -.06, -.05),  # red
]


def render_segment(clip: str, src_start: float, src_dur: float, target: float,
                   tmp: Path, idx: int, *, grade: str = "club",
                   hue_speed: float = 460.0, phase: float = 0.0,
                   sat: float = 1.35, flash: bool = False, zoom_crop: float = 1.0,
                   hflip: bool = False, zoom_ramp: str | None = None,
                   rotate_amp: float = 0.0, club_cast: tuple | None = None) -> Path:
    """Motion FX (PD 2026-09-10, velocity punch-up): hflip (좌우반전), zoom_ramp
    ('in'=wide→tight / 'out'=tight→wide, a live push over the cut), rotate_amp (radians —
    a rhythmic dutch wobble). Applied on top of the club color cycle."""
    out = tmp / f"seg_{idx:02d}.mp4"
    speed = src_dur / target                    # >1 speeds up, <1 slow-mo

    def _build(fx: bool) -> str:
        # setsar=1 right after the normalize so a variable-SAR/rotated source can't make a
        # downstream filter re-init ("Failed to inject frame") mid-stream.
        c = [f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"]
        if fx and hflip:
            c.append("hflip")
        # zoom PUNCH per cut (static level — a time-VARYING crop changes the frame size mid-stream
        # → "reinitializing filters"; a smooth zoompan ramp is a follow-up). Alternate cuts
        # wide(1.0)↔tight(≈1.26) for an in/out push cut-to-cut.
        _zc = 1.26 if zoom_ramp in ("in", "out") else (zoom_crop if zoom_crop else 1.0)
        if fx and _zc and abs(_zc - 1.0) > 1e-3:
            c.append(f"crop=iw/{_zc:.4f}:ih/{_zc:.4f},scale={W}:{H}")
        c.append(f"setpts=PTS/{speed:.4f}")
        if fx and rotate_amp > 1e-3:
            # prescale so the rotated frame still fills 9:16 (no black corners), rhythmic dutch
            # wobble, crop back to CONSTANT WxH so the frame size never changes.
            c.append(f"scale=iw*1.25:ih*1.25,rotate=a='{rotate_amp:.3f}*sin(2*PI*1.6*t)':c=black,"
                     f"crop={W}:{H}")
        c.append(f"fps={FPS}")
        if grade == "club":
            # Club look = a bold colored LIGHT cast per cut (colorbalance shifts shadows/mids/
            # highlights toward one hue) so the SUBJECT stays readable while the scene bathes in
            # neon — and the cast changes BOLDLY cut-to-cut (velocity cuts fast), which reads as
            # club strobe. This is deliberately NOT a full `hue` rotation: rotating every hue
            # turned the dog itself green/magenta (an awkward broken-filter look PD rejected on
            # 9/8-9). `club_cast` = one CLUB[] tuple assigned per segment by build_velocity; the
            # saturation lift + contrast curve give the neon punch the old hue-cycle provided.
            rs, gs, bs, rm, gm, bm, rh, gh, bh = club_cast or CLUB[0]
            c.append(f"colorbalance=rs={rs}:gs={gs}:bs={bs}:rm={rm}:gm={gm}:bm={bm}"
                     f":rh={rh}:gh={gh}:bh={bh}")
            c.append(f"eq=contrast=1.20:brightness=0.006:saturation={sat:.3f}")
            c.append("curves=preset=lighter,eq=brightness=0.10" if flash
                     else "curves=preset=increase_contrast")
        elif grade == "cinematic":
            c.append(f"eq=contrast=1.06:brightness=-0.004:saturation={sat:.3f}")
            c.append("curves=r='0/0.02 1/0.98':b='0/0.03 1/0.95'")   # gentle teal-warm filmic
            c.append("vignette=PI/4.5")
        else:  # natural (meme) — punchy but true color
            c.append(f"eq=contrast=1.10:brightness=0.008:saturation={sat:.3f}")
        c.append("format=yuv420p")
        return ",".join(c)

    # Per-segment resilience: try the full FX chain; if a specific clip trips ffmpeg (variable
    # res/rotation metadata → filter re-init), retry THIS cut without the motion FX so one bad
    # clip degrades to a plain cut instead of killing the whole grammar episode.
    for _fx in (True, False):
        try:
            _run([FF, "-y", "-v", "error", "-ss", f"{src_start}", "-t", f"{src_dur}", "-i", clip,
                  "-vf", _build(_fx), "-an", "-r", str(FPS), "-c:v", "libx264", "-crf", "18",
                  "-preset", "medium", str(out)])
            return out
        except Exception as e:
            if _fx:
                print(f"  [seg {idx}] FX render failed → retry plain: {str(e)[:100]}", flush=True)
                continue
            raise
    return out


def render_freeze(clip: str, at: float, dur: float, tmp: Path, idx: int, *,
                  grade: str = "natural", zoom_crop: float = 1.0) -> Path:
    """Freeze-frame beat: grab one frame, hold it (meme punchline)."""
    out = tmp / f"seg_{idx:02d}.mp4"
    img = tmp / f"frz_{idx:02d}.png"
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if zoom_crop and abs(zoom_crop - 1.0) > 1e-3:
        vf += f",crop=iw/{zoom_crop:.4f}:ih/{zoom_crop:.4f},scale={W}:{H}"
    _run([FF, "-y", "-v", "error", "-ss", f"{at}", "-i", clip, "-frames:v", "1", "-vf", vf, str(img)])
    _run([FF, "-y", "-v", "error", "-loop", "1", "-t", f"{dur}", "-i", str(img),
          "-vf", f"fps={FPS},eq=contrast=1.12:saturation=1.08,format=yuv420p",
          "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-an", str(out)])
    return out


# ──────────────────────────────────────────────────────────────────────
# Shared assembler: concat CFR → captions (separate passes) → music + SFX
# ──────────────────────────────────────────────────────────────────────
_SFX_DIR = ROOT / "assets" / "sfx"
# Meme SFX. A REAL CC0 sample at assets/sfx/<kind>.{wav,mp3,ogg,m4a} wins — a genuine vine-boom /
# air-horn / ding reads far punchier than a synth tone, and a pure sine/noise burst is the "허접"
# tell PD flagged. Drop samples in (Mixkit/Pixabay CC0) and they override, mirroring the BGM dir.
# The synthesized fallbacks below are tuned to be punchier than a bare sine (decaying transient +
# an octave/second partial) so the edit still lands with no samples installed.
_SFX_CAP = {"boom": 0.8, "ding": 0.5, "riser": 0.9, "whoosh": 0.5}


def _sfx_sample(kind: str) -> Path | None:
    for ext in ("wav", "mp3", "ogg", "m4a"):
        p = _SFX_DIR / f"{kind}.{ext}"
        if p.exists():
            return p
    return None


def _gen_sfx(tmp: Path, kind: str) -> Path:
    p = tmp / f"sfx_{kind}.wav"
    sample = _sfx_sample(kind)
    if sample:                                   # real CC0 sting — trim short, gentle out-fade
        cap = _SFX_CAP.get(kind, 0.8)
        _run([FF, "-y", "-v", "error", "-i", str(sample), "-t", f"{cap}",
              "-af", f"afade=t=out:st={max(0.0, cap - 0.12):.2f}:d=0.12,aresample=48000",
              "-ac", "2", str(p)])
        return p
    if kind == "boom":                           # vine-boom: DOWNWARD pitch chirp 190→~40Hz + a
        # click transient, exp decay. sin(2π(f0·t − k·t²)) has instantaneous freq f0−2k·t, so
        # k=(f0−f1)/(2T) sweeps f0→f1 over T=0.55 (190→~20). A sub octave under it thickens the
        # body; the leading 0.5·exp(−90t) burst is the "thud" attack. This reads as a real boom,
        # not the static tone a bare sine gives.
        _run([FF, "-y", "-v", "error", "-f", "lavfi",
              "-i", "aevalsrc='exp(-6*t)*sin(2*PI*(190*t-155*t*t))"
                    "+0.45*exp(-5*t)*sin(2*PI*(95*t-77*t*t))"
                    "+0.5*exp(-90*t)*sin(2*PI*140*t)':d=0.55:s=48000",
              "-af", "volume=2.3", str(p)])
    elif kind == "ding":                         # bright bell: INHARMONIC partials, each decaying
        # at its own rate (higher = faster) — the inharmonicity + differential decay is what makes
        # a sine sound like a struck bell instead of a test tone.
        _run([FF, "-y", "-v", "error", "-f", "lavfi",
              "-i", "aevalsrc='exp(-6*t)*sin(2*PI*1150*t)+0.5*exp(-9*t)*sin(2*PI*2560*t)"
                    "+0.22*exp(-13*t)*sin(2*PI*5300*t)':d=0.45:s=48000",
              "-af", "volume=1.5", str(p)])
    elif kind == "riser":                        # UPWARD chirp + amplitude swell into the drop
        _run([FF, "-y", "-v", "error", "-f", "lavfi", "-i",
              "aevalsrc='min(1,t/0.6)*sin(2*PI*(180*t+650*t*t))':d=0.8",
              "-af", "afade=t=out:st=0.66:d=0.12,volume=1.2", str(p)])
    elif kind == "whoosh":                        # airy noise transition: band-limited pink noise
        # with a fast in-swell and out-fade. (bandpass `f` takes no time-expression, so the sweep
        # feel comes from the sharp amplitude envelope rather than a moving centre.)
        _run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=0.34:c=pink:a=0.6",
              "-af", "highpass=f=900,lowpass=f=6000,afade=t=in:d=0.08,"
              "afade=t=out:st=0.19:d=0.15,volume=1.2", str(p)])
    else:
        raise ValueError(kind)
    return p


def _tts(tmp: Path, idx: int, text: str, *, rate: int = 188) -> Path:
    """Korean narration. OpenAI neural TTS (natural) first — macOS `say` (robotic) fallback."""
    try:
        from openai import OpenAI
        out = tmp / f"tts_{idx:02d}.mp3"
        client = OpenAI(timeout=40, max_retries=1)
        resp = client.audio.speech.create(
            model="gpt-4o-mini-tts", voice="nova", input=text,
            instructions="밝고 장난기 있는 반려동물 유튜브 내레이터 톤. 자연스러운 한국어 구어체, 또박또박하되 리듬감 있게.")
        resp.stream_to_file(str(out))
        return out
    except Exception as e:
        print(f"  [tts] OpenAI failed ({type(e).__name__}: {e}); falling back to macOS say")
        aiff = tmp / f"tts_{idx:02d}.aiff"
        subprocess.run(["say", "-v", "Yuna", "-r", str(rate), "-o", str(aiff), text], check=True)
        return aiff


def _sanitize_caption(text: str) -> str:
    """Strip glyphs Pretendard can't render (they burn as □□□ tofu). The grammar-copy Writer
    likes to sprinkle emoji (🐾⚡🐱😺) into captions; Pretendard has no emoji/pictograph glyphs,
    so drawtext renders them as tofu. Remove emoji / pictographs / dingbats / variation selectors,
    keep Hangul + Latin + normal punctuation + ♥ (which Pretendard DOES have). Deterministic
    backstop so no Writer output can leak tofu, regardless of the prompt."""
    keep = []
    for ch in text or "":
        o = ord(ch)
        if ch == "♥":
            keep.append(ch); continue
        if (0x1F000 <= o <= 0x1FFFF or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF
                or o in (0x200D, 0xFE0E, 0xFE0F, 0x2122, 0x2139) or 0x1F1E6 <= o <= 0x1F1FF):
            continue
        keep.append(ch)
    return " ".join("".join(keep).split())


def _has_audio(path) -> bool:
    r = subprocess.run([FP, "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=index", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def _wrap_bumpers(body: Path, out: Path, tmp: Path,
                  intro: Path = INTRO_BUMPER, outro: Path = OUTRO_BUMPER) -> Path:
    """Prepend the intro bumper + append the outro bumper to a finished grammar body,
    PRESERVING the body's own mixed audio (music/TTS). Each segment is normalized to the
    channel WxH/FPS/yuv420p/setsar=1 and guaranteed an audio track before concat
    (gotchas #8 SAR/res mismatch, #9 bumper theme audio). Bumper-less hosts (files absent)
    just get the body back untouched."""
    segs = [p for p in (intro, body, outro) if p and Path(p).exists()]
    if len(segs) <= 1:            # no bumpers present → nothing to wrap
        if Path(body) != Path(out):
            _shutil.copy(str(body), str(out))
        return out
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p")
    norm = []
    for i, seg in enumerate(segs):
        np_ = tmp / f"bwrap_{i}.mp4"
        if _has_audio(seg):
            _run([FF, "-y", "-v", "error", "-i", str(seg), "-vf", vf,
                  "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                  "-shortest", str(np_)])
        else:                     # silent bumper → synthesize a matched silent track
            dur = probe_dur(str(seg)) or 1.5
            _run([FF, "-y", "-v", "error", "-i", str(seg),
                  "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                  "-vf", vf, "-map", "0:v", "-map", "1:a",
                  "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                  "-c:a", "aac", "-b:a", "192k", "-shortest", str(np_)])
        norm.append(np_)
    inputs = []
    for f in norm:
        inputs += ["-i", str(f)]
    n = len(norm)
    fc = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([FF, "-y", "-v", "error", *inputs, "-filter_complex", fc,
          "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18",
          "-preset", "medium", "-c:a", "aac", "-b:a", "192k",
          "-movflags", "+faststart", str(out)])
    return out


def assemble(seq: list[dict], caps: list[tuple], music_id: str, out: Path, *,
             music_start: float = 6.0, music_vol: float = 0.85,
             sfx: list[tuple] | None = None, voices: list[tuple] | None = None,
             bumpers: bool = True) -> Path:
    """seq: segment dicts. caps: (st,en,txt,fontsize,y[,font,box]). sfx: (t,kind,vol).
    voices: (t,text,vol) — TTS narration mixed over ducked music."""
    tmp = Path(tempfile.mkdtemp(prefix="impact_"))
    seg_files, t = [], 0.0
    for i, sg in enumerate(seq):
        if sg.get("freeze"):
            f = render_freeze(sg["clip"], sg["start"], sg["target"], tmp, i,
                              zoom_crop=sg.get("zoom_crop", 1.0))
        else:
            f = render_segment(sg["clip"], sg["start"], sg["dur"], sg["target"], tmp, i,
                              grade=sg.get("grade", "club"), hue_speed=sg.get("hue_speed", 460.0),
                              phase=sg.get("phase", 0.0), sat=sg.get("sat", 1.35),
                              flash=sg.get("flash", False), zoom_crop=sg.get("zoom_crop", 1.0),
                              hflip=sg.get("hflip", False), zoom_ramp=sg.get("zoom_ramp"),
                              rotate_amp=sg.get("rotate_amp", 0.0), club_cast=sg.get("club_cast"))
        seg_files.append(f)
        t += sg["target"]
    total = t
    print(f"  segments={len(seq)}  total≈{total:.1f}s")

    # concat — RE-ENCODE to clean CFR yuv420p (copy-concat's irregular timebase segfaults drawtext here)
    concat_txt = tmp / "list.txt"
    concat_txt.write_text("".join(f"file '{f}'\n" for f in seg_files))
    vcat = tmp / "vcat.mp4"
    _run([FF, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
          "-vf", "fps=30,setsar=1,format=yuv420p", "-vsync", "cfr",
          "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-an", str(vcat)])

    # captions — chained in small GROUPS over the CFR-normalized concat (the old segfault
    # was VFR-timebase-specific; grouping keeps many captions cheap without one pass each).
    def _fit_fs(text: str, fs: int) -> int:
        """Shrink fontsize until the widest line fits ~88% of frame width — drawtext does NOT
        wrap, so a long KO line at a fixed fs overflows and clips at BOTH side edges (PD 9/10)."""
        maxw = W * 0.88

        def _wpx(s: str, f: int) -> float:  # CJK ~0.98*fs wide, Latin/punct ~0.56*fs
            return sum((0.98 if ord(c) > 0x2000 else 0.56) * f for c in s)
        longest = max(text.split("\n"), key=len) if text else text
        while fs > 34 and _wpx(longest, fs) > maxw:
            fs -= 2
        return fs

    def _one(text, fs, y, font, box, st, en, tag):
        text = _sanitize_caption(text)          # strip emoji/tofu glyphs
        if not text:
            return ""
        fs = _fit_fs(text, fs)                  # auto-fit so it never clips the sides
        tf = tmp / f"cap_{tag}.txt"
        tf.write_text(text)
        boxpart = ":box=1:boxcolor=black@0.55:boxborderw=22" if box else ""
        return (f"drawtext=fontfile='{font}':textfile='{tf}':fontcolor=white:"
                f"fontsize={fs}:borderw=8:bordercolor=black@0.9{boxpart}:"
                f"x=(w-text_w)/2:y={y:.0f}:enable='between(t,{st:.2f},{en:.2f})'")

    def _draw(cap, i):
        st, en, txt, fs, y = cap[0], cap[1], cap[2], cap[3], cap[4]
        font = cap[5] if len(cap) > 5 else FONT_BLACK
        box = cap[6] if len(cap) > 6 else False
        eng = cap[7] if len(cap) > 7 else None          # optional English line, below KO
        draws = [_one(txt, fs, y, font, box, st, en, f"{i}k")]
        if eng:
            en_fs = max(34, int(fs * 0.5))
            # clear BOTH boxes' borders (22px each) + a visible gap, else EN hugs KO on top rows
            en_y = y + fs + (2 * 22 + 16 if box else 22)
            draws.append(_one(eng, en_fs, en_y, FONT_XBOLD, box, st, en, f"{i}e"))
        return ",".join(d for d in draws if d)

    cur = vcat
    GROUP = 4
    for g in range(0, len(caps), GROUP):
        chunk = caps[g:g + GROUP]
        vf = ",".join(d for j, cap in enumerate(chunk) if (d := _draw(cap, g + j)))
        if not vf:
            continue
        nxt = tmp / f"vtxt_{g}.mp4"
        _run([FF, "-y", "-v", "error", "-i", str(cur), "-vf", vf,
              "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-an", str(nxt)])
        cur = nxt

    # audio: music (trimmed into the energy) + SFX stings + optional TTS narration
    out.parent.mkdir(parents=True, exist_ok=True)
    music = str(BGM / music_id)
    extra: list[tuple] = []   # (delay_s, path, vol)
    for (tt, kind, vol) in (sfx or []):
        extra.append((tt, _gen_sfx(tmp, kind), vol))
    # narration: SEQUENTIAL — each line starts no earlier than the previous line's audio ENDS, so
    # voices can never overlap. The story ENGINE now sizes each scene to fit its narration up front
    # (see _prerender_and_size), so lines already fit their windows and the atempo below is a GENTLE
    # safety only (cap 1.15 — a big speed-up sounds jarring; a hair of drift reads far better). A
    # voice item may carry a 4th element = a pre-rendered TTS path (skip re-synthesis). The dur
    # fallback estimates from text length (~7 KO chars/sec) instead of a flat 1.5 so a failed probe
    # can't under-count and cause an overlap.
    vlist = voices or []
    _cursor = 0.0
    for j, item in enumerate(vlist):
        tt, text, vol = item[0], item[1], item[2]
        pre = item[3] if len(item) > 3 else None
        p = Path(pre) if pre else _tts(tmp, j, text)
        dur = probe_dur(str(p)) or max(1.2, len(text) / 7.0)
        start = max(tt, _cursor)                     # never begin before the prior line ends
        nxt = vlist[j + 1][0] if j + 1 < len(vlist) else total
        window = max(0.7, min(nxt, total) - start - 0.12)
        if dur > window * 1.03:                       # only if genuinely over — scenes are pre-fit
            tempo = min(1.15, dur / window)
            fitted = tmp / f"tts_fit_{j:02d}.mp3"
            _run([FF, "-y", "-v", "error", "-i", str(p), "-filter:a", f"atempo={tempo:.3f}",
                  "-c:a", "libmp3lame", "-q:a", "3", str(fitted)])
            p = fitted
            dur = probe_dur(str(p)) or (dur / tempo)
        extra.append((start, p, vol))
        _cursor = start + dur + 0.08                  # next line waits for this one to finish

    # LOOP the BGM so it always covers the whole body: generated Lyria tracks are only ~32.8s, but
    # a narrated story body runs 40s+, and atrim + the final -shortest would otherwise truncate the
    # VIDEO to the music length (a story cut off mid-payoff). -stream_loop -1 repeats the track; the
    # atrim below bounds it to exactly the body length. Short bodies (velocity/meme ~15s) never loop.
    inputs = ["-stream_loop", "-1", "-i", music]
    for (_, pth, _) in extra:
        inputs += ["-i", str(pth)]
    video_idx = 1 + len(extra)
    inputs += ["-i", str(cur)]

    parts = [f"[0:a]atrim={music_start}:{music_start + total},asetpts=PTS-STARTPTS,"
             f"volume={music_vol},afade=t=out:st={max(0.0, total - 1.2):.2f}:d=1.2[m]"]
    labels = ["[m]"]
    for k, (tt, _, vol) in enumerate(extra, start=1):
        ms = max(0, int(tt * 1000))
        parts.append(f"[{k}:a]adelay={ms}|{ms},volume={vol}[e{k}]")
        labels.append(f"[e{k}]")
    if len(labels) > 1:
        parts.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first"
                     f":dropout_transition=0:normalize=0[a]")
        amap = "[a]"
    else:
        amap = "[m]"
    body = (tmp / "body.mp4") if bumpers else out
    _run([FF, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(parts),
          "-map", f"{video_idx}:v", "-map", amap, "-c:v", "copy", "-c:a", "aac",
          "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(body)])
    if bumpers:
        _wrap_bumpers(body, out, tmp)     # intro/outro channel bumpers (D_grammarlive fix)
    print(f"→ {out}  ({total:.1f}s)")
    return out


def _times(seq: list[dict]) -> list[float]:
    ts, t = [], 0.0
    for sg in seq:
        ts.append(t)
        t += sg["target"]
    return ts


# ──────────────────────────────────────────────────────────────────────
# Source pool (shared across grammars) — proven real-footage clips
# ──────────────────────────────────────────────────────────────────────
SOCCER = "med_2021_03_08_094114_icloud_e25ca954"   # Ryani soccer dribble (the payoff/climax)
PLAY1  = "med_2026_06_28_031839_slack_8bea9489"    # Leo+Ryani play
SWIM   = "med_2026_06_28_032719_slack_d4f82512"    # Ryani swim
PLAY2  = "med_2026_06_28_031930_slack_85d20838"    # Leo+Ryani play 2
BELLY  = "med_2026_07_05_072016_slack_f144f67b"    # Leo belly (slow beauty)

# Default clip set = the PD-approved proof footage. Production (Phase B) passes its own
# `clips` dict (SAME role keys) so the grammar runs on dynamically-selected RF footage.
# Roles are ENERGY/function slots, not literal content: soccer=climax/high-motion payoff,
# play1/play2=mid-energy interaction, swim=high-motion, belly=slow "beauty"/calm anchor.
DEFAULT_CLIPS = {"soccer": SOCCER, "play1": PLAY1, "swim": SWIM, "play2": PLAY2, "belly": BELLY}


# ──────────────────────────────────────────────────────────────────────
# VELOCITY (club)
# ──────────────────────────────────────────────────────────────────────
def build_velocity(music_id: str, out: Path, clips: dict | None = None, copy: dict | None = None):
    """copy (B4): {"captions": [{"ko": <hook>}, {"ko": <drop>}]} — 2 KO-only slots; None = proof."""
    c = clips or DEFAULT_CLIPS
    velocity_plan = [
        (c["soccer"], 4, 1.1, "run"), (c["play1"], 3, 1.0, "play"),
        (c["swim"], 3, 1.1, "swim"), (c["play2"], 3, 1.0, "play"),
        (c["belly"], 1, 1.4, "beauty"),
    ]
    music = str(BGM / music_id)
    period, _ = beat_grid(music)
    print(f"velocity  {os.path.basename(music)}  {60/period:.1f}bpm  beat={period:.3f}s")

    pool = []
    for aid, nwin, win, role in velocity_plan:
        clip = _resolve_clip(aid)
        for (s, d) in top_motion_windows(clip, nwin, win):
            pool.append((clip, s, d, role))

    runs = [p for p in pool if p[3] == "run"]
    plays = [p for p in pool if p[3] == "play"]
    swims = [p for p in pool if p[3] == "swim"]
    beauty = [p for p in pool if p[3] == "beauty"]
    energetic = [x for trip in zip(runs + [None] * 4, plays + [None] * 4, swims + [None] * 4)
                 for x in trip if x]
    if not energetic:
        raise SystemExit("no energetic windows")
    pick = lambda i: energetic[i % len(energetic)]

    seq: list[dict] = []
    def add(p, beats, **kw):
        clip, s, d, role = p
        kw.setdefault("role", role)
        seq.append(dict(clip=clip, start=s, dur=d, target=beats * period, **kw))

    ei = 0
    add(runs[0] if runs else pick(0), 2, flash=True); ei += 1            # HOOK
    for _ in range(7):                                                    # BUILD
        add(pick(ei), 2); ei += 1
    if beauty:                                                            # PRE-DROP slow beauty
        b = beauty[0]
        seq.append(dict(clip=b[0], start=b[1], dur=b[2], target=3 * period, role="slow"))
    for _ in range(6):                                                    # DROP burst
        add(pick(ei), 1, role="drop"); ei += 1
    for _ in range(2):                                                    # POST
        add(pick(ei), 2); ei += 1
    add(runs[0] if runs else pick(0), 2, flash=True)                      # PAYOFF/LOOP

    # Color + MOTION FX (PD 9/9 색 / 9/10 모션). Color: mix ORIGINAL-color scenes with club
    # strobe (DROP = full ~900°/s, BUILD alternates natural↔club, slow beauty stays true color).
    # Motion (PD "줌인/줌아웃·회전·좌우반전 막"): every cut gets an alternating zoom push (in↔out);
    # a dutch wobble (subtle on build, hard on the drop burst); a left-right flip every 3rd cut
    # for variety. The HOOK/PAYOFF flash hits stay a clean zoom-in punch (no wobble/flip) so the
    # bookends read strong; the slow beauty anchor stays still.
    # Color casts cycle through CLUB[] so consecutive club cuts land on DIFFERENT neon hues
    # (the strobe now comes from bold per-cut CHANGE, not a within-cut hue spin). The drop
    # burst strides across the wheel (×3) so its faster cuts jump further per cut = harder
    # strobe; build/bookends step by 1.
    ci = 0
    for sg in seq:
        role = sg.get("role")
        if role == "slow":
            sg["grade"] = "natural"; sg["sat"] = 1.10
            continue
        if ci % 2 == 1:                                    # alternate tight(punch)↔wide → in/out push
            sg["zoom_ramp"] = "in"
        if sg.get("flash"):                                # hook / payoff — clean strong punch
            sg["grade"] = "club"; sg["club_cast"] = CLUB[ci % len(CLUB)]; sg["sat"] = 1.4
            sg["zoom_ramp"] = "in"
        elif role == "drop":                               # DROP burst — bold cast jumps + hard spin-wobble + flips
            sg["grade"] = "club"; sg["club_cast"] = CLUB[(ci * 3) % len(CLUB)]; sg["sat"] = 1.4
            sg["rotate_amp"] = 0.11
            sg["hflip"] = (ci % 2 == 0)
        elif ci % 2 == 0:                                  # BUILD — original color + gentle wobble
            sg["grade"] = "natural"; sg["sat"] = 1.08
            sg["rotate_amp"] = 0.05
            if ci % 3 == 0:
                sg["hflip"] = True
        else:                                              # BUILD — club color + gentle wobble
            sg["grade"] = "club"; sg["club_cast"] = CLUB[ci % len(CLUB)]; sg["sat"] = 1.4
            sg["rotate_amp"] = 0.05
        ci += 1

    ts = _times(seq)
    total = sum(s["target"] for s in seq)
    drop_t = ts[9] if len(ts) > 9 else total * 0.55
    caps = [
        (0.10, 2.4, "우리집 국가대표", 96, H * 0.15),
        (drop_t, drop_t + 2.2, "풀 파워 가동", 108, H * 0.15),
        (total - 2.2, total, "@ryani_n_leo", 64, H * 0.82),
    ]
    if copy and copy.get("captions"):                 # B4: swap hook/drop text, keep timing/handle
        for i, cp in enumerate(copy["captions"][:2]):
            st, en, _ko, fs, y = caps[i]
            caps[i] = (st, en, cp.get("ko", _ko), fs, y)
    assemble(seq, caps, music_id, out, music_start=8.0, music_vol=0.85,
             sfx=[(max(0.0, drop_t - 0.62), "riser", 1.2), (drop_t, "boom", 2.2)])


# ──────────────────────────────────────────────────────────────────────
# MEME (reaction) — jump cuts, zoom punch, freeze, big captions, SFX
# ──────────────────────────────────────────────────────────────────────
def build_meme(music_id: str, out: Path, clips: dict | None = None, copy: dict | None = None):
    """copy (B4): {"captions": [{"ko":..,"en":..} × 7]} — 7 punch beats (hook, ?!?!, 포착, 잠깐만,
    레오 등장, 레오:나 아닌데, 또?!) in order; SFX/timing/handle kept. None = proof."""
    c = clips or DEFAULT_CLIPS
    print(f"meme  {music_id}")
    soccer, play1, swim, play2, belly = (_resolve_clip(c[k]) for k in ("soccer", "play1", "swim", "play2", "belly"))
    p_soc = top_motion_windows(soccer, 4, 1.2)
    p_p1 = top_motion_windows(play1, 4, 1.1)
    p_sw = top_motion_windows(swim, 4, 1.1)
    p_p2 = top_motion_windows(play2, 4, 1.1)
    b1 = best_motion_window(belly, 1.1)
    b2 = top_motion_windows(belly, 2, 1.1)

    def S(clip, ws, i, target, **kw):
        s, d = ws[i % len(ws)]
        return dict(clip=clip, start=s, dur=d, target=target, grade="natural", **kw)

    def FR(clip, at, target, **kw):
        return dict(clip=clip, start=at, target=target, freeze=True, **kw)

    # many fast jump cuts, 4 zoom punches, 2 freezes → ~17s
    seq = [
        S(play1, p_p1, 0, 1.8, sat=1.10),                # hook
        S(soccer, p_soc, 0, 1.0),
        S(swim, p_sw, 0, 0.9, zoom_crop=1.5),            # ZOOM PUNCH 1
        S(play2, p_p2, 0, 1.0),
        FR(belly, b1[0], 0.7, zoom_crop=1.5),            # FREEZE 1
        S(soccer, p_soc, 1, 0.9),
        S(play1, p_p1, 1, 0.85, zoom_crop=1.6),          # ZOOM PUNCH 2
        S(swim, p_sw, 1, 1.0),
        S(play2, p_p2, 1, 0.9),
        S(soccer, p_soc, 2, 0.8, zoom_crop=1.7),         # ZOOM PUNCH 3
        S(play1, p_p1, 2, 1.0),
        FR(belly, b2[1][0], 0.7, zoom_crop=1.55),        # FREEZE 2
        S(swim, p_sw, 2, 0.9),
        S(play2, p_p2, 2, 0.8, zoom_crop=1.6),           # ZOOM PUNCH 4
        S(soccer, p_soc, 3, 1.0),
        S(play1, p_p1, 3, 1.9),                          # payoff hold
    ]
    ts = _times(seq)
    total = sum(s["target"] for s in seq)
    F = FONT_BLACK
    # KO + EN bilingual (8th tuple = English, drawn below). No Hanja — 甲 renders as tofu in ffmpeg.
    caps = [
        (0.10, 1.8, "우리집 텐션 미쳤다", 94, H * 0.13, F, True, "our house energy: chaos"),
        (ts[2], ts[2] + 0.9, "?!?!", 150, H * 0.38, F, True, "wait WHAT"),
        (ts[4], ts[4] + 0.7, "포착.jpg", 100, H * 0.13, F, True, "caught in 4K"),
        (ts[6], ts[6] + 0.85, "잠깐만", 128, H * 0.38, F, True, "hold up"),
        (ts[9], ts[9] + 0.8, "레오 등장", 94, H * 0.13, F, True, "enter: Leo"),
        (ts[11], ts[11] + 0.7, "레오: 나 아닌데", 82, H * 0.13, F, True, "Leo: wasn't me"),
        (ts[13], ts[13] + 0.8, "또?!", 140, H * 0.38, F, True, "AGAIN?!"),
        (total - 1.8, total, "@ryani_n_leo", 64, H * 0.82, F, False),
    ]
    sfx = [(ts[2] - 0.08, "whoosh", 1.0), (ts[2] + 0.02, "ding", 1.3), (ts[4], "boom", 2.6),
           (ts[6] - 0.08, "whoosh", 1.0), (ts[6] + 0.02, "ding", 1.3), (ts[9] + 0.02, "ding", 1.3),
           (ts[11], "boom", 2.5), (ts[13] - 0.08, "whoosh", 1.0), (ts[13] + 0.02, "boom", 2.2)]
    if copy and copy.get("captions"):                 # B4: swap the 7 punch captions (KO+EN), keep timing/sfx/handle
        for i, cp in enumerate(copy["captions"][:len(caps) - 1]):   # leave the @handle (last)
            st, en, _ko, fs, y, Ff, box, *rest = caps[i]
            _en = cp.get("en", rest[0] if rest else None)
            caps[i] = (st, en, cp.get("ko", _ko), fs, y, Ff, box, _en)
    assemble(seq, caps, music_id, out, music_start=2.0, music_vol=0.5, sfx=sfx)


# ──────────────────────────────────────────────────────────────────────
# STORY (payoff-first) — climax cold-open → rewind → build → return
# ──────────────────────────────────────────────────────────────────────
_NARR_PAD = 0.7   # lead-in + tail around a narration line so a scene isn't wall-to-wall speech


def _prerender_and_size(seq: list[dict], narr_by_idx: dict) -> dict:
    """TTS each scene's narration UP FRONT and grow that scene's target to fit the natural speech
    (dur + _NARR_PAD), so the voice is never sped up and never overruns into the next scene's
    caption. Returns {scene_idx: (tts_path, dur)}; scenes without narration are untouched. This is
    the fix for the three story-voice bugs (caption↔story desync, sudden fast speech, overlap):
    size the PICTURE to the words, instead of cramming the words into a pre-cut picture."""
    pre: dict = {}
    if not narr_by_idx:
        return pre
    d = Path(tempfile.mkdtemp(prefix="narr_"))
    for idx, text in narr_by_idx.items():
        p = _tts(d, idx, text)
        dur = probe_dur(str(p)) or max(1.2, len(text) / 7.0)
        pre[idx] = (p, dur)
        if 0 <= idx < len(seq):
            seq[idx]["target"] = max(seq[idx]["target"], dur + _NARR_PAD)
    return pre


def _build_story_from_beats(c: dict, beats: list[dict], music_id: str, out: Path) -> Path:
    """B4 beat-driven story: the Writer supplies ordered beats — each a role (soccer/play1/
    swim/play2/belly, cast by the Writer), an optional kind (cold_open|payoff|calm), KO caption
    line(s), and optional narration. The ENGINE owns motion-window pick + timing + slow-mo; the
    Writer owns which clip plays when and what it says (grounded to that clip). Mirrors the proof
    structure but data-driven. (Generalizes the hand-authored _phaseb_story_grounded proof.)"""
    winpool: dict = {}

    def _win(role: str, kind: str):
        clip = _resolve_clip(c[role])
        if kind in ("cold_open", "payoff"):
            # subject-aware: the hook & climax MUST show the pet, not empty splashing water
            return clip, salient_motion_window(clip, 2.6)
        wp = winpool.setdefault(role, {"clip": clip, "ws": even_windows(clip, 3, 3.6), "i": 0})
        s, d = wp["ws"][wp["i"] % len(wp["ws"])]
        wp["i"] += 1
        return wp["clip"], (s, d)

    seq = []
    for b in beats:
        kind = b.get("kind", "normal")
        clip, (s, d) = _win(b["role"], kind)
        mult = {"cold_open": 1.15, "payoff": 1.5, "calm": 1.2}.get(kind, 1.0)
        seq.append(dict(clip=clip, start=s, dur=d, target=d * mult, grade="cinematic",
                        sat=1.05 if kind in ("cold_open", "payoff") else 0.97))
    # size each narration-bearing scene to fit its natural TTS length (fixes fast/overlap/desync)
    narr = {i: b["narration"] for i, b in enumerate(beats) if b.get("narration")}
    pre = _prerender_and_size(seq, narr)
    ts = _times(seq)
    total = sum(sg["target"] for sg in seq)
    F, Y = FONT_XBOLD, H * 0.15
    caps, voices = [], []
    for i, b in enumerate(beats):
        seg_end = ts[i + 1] if i + 1 < len(seq) else total
        ko, ko2, box = b.get("ko"), b.get("ko2"), b.get("box", False)
        if ko and ko2:
            mid = ts[i] + min(2.0, (seg_end - ts[i]) * 0.45)
            caps.append((ts[i] + 0.10, mid - 0.05, ko, b.get("fs", 74), Y, F, box))
            caps.append((mid, seg_end - 0.1, ko2, b.get("fs2", b.get("fs", 74)), Y, F, box))
        elif ko:
            caps.append((ts[i] + 0.10, seg_end - 0.1, ko, b.get("fs", 74), Y, F, box))
        if b.get("narration"):
            voices.append((ts[i] + 0.15, b["narration"], 1.45, str(pre[i][0])))
    caps.append((total - 2.0, total, "@ryani_n_leo", 58, H * 0.82, F, False))
    assemble(seq, caps, music_id, out, music_start=4.0, music_vol=0.40, voices=voices)
    return out


def build_story(music_id: str, out: Path, clips: dict | None = None, copy: dict | None = None):
    """copy (B4): {"beats": [{"role","kind"?,"ko","ko2"?,"narration"?,"box"?} ...]} — the Writer's
    grounded payoff-first arc. None = the hardcoded PROOF (soccer story) below, unchanged."""
    c = clips or DEFAULT_CLIPS
    print(f"story  {music_id}")
    if copy and copy.get("beats"):
        return _build_story_from_beats(c, copy["beats"], music_id, out)
    soccer, play1, swim, play2, belly = (_resolve_clip(c[k]) for k in ("soccer", "play1", "swim", "play2", "belly"))
    climax = best_motion_window(soccer, 2.6)          # the payoff moment
    soc_build = top_motion_windows(soccer, 2, 2.4)
    b_belly = even_windows(belly, 2, 4.4)
    b_play1 = even_windows(play1, 1, 3.6)
    b_swim = even_windows(swim, 2, 3.6)
    b_play2 = even_windows(play2, 1, 3.4)

    def C(clip, ws, i, *, slow=1.0, sat=0.95):
        s, d = ws[i % len(ws)]
        return dict(clip=clip, start=s, dur=d, target=d * slow, grade="cinematic", sat=sat)

    seq = [
        # 0 ── COLD OPEN: the RESULT, first (the chaos) ──
        dict(clip=soccer, start=climax[0], dur=climax[1], target=climax[1] * 1.15,
             grade="cinematic", sat=1.05),
        # 1 ── REWIND: peace before ──
        C(belly, b_belly, 0, slow=1.05),
        # 2 ── CAUSE: Ryani bored ──
        C(play1, b_play1, 0),
        # 3 ── CAUSE: energy overload ──
        C(swim, b_swim, 0),
        # 4 ── CAUSE: pokes Leo, ignored ──
        C(play2, b_play2, 0),
        # 5 ── CAUSE: decides to go solo ──
        C(swim, b_swim, 1),
        # 6 ── TRIGGER: finds the ball ──
        C(soccer, soc_build, 0),
        # 7 ── RETURN to the payoff (extended slow-mo) ──
        dict(clip=soccer, start=climax[0], dur=climax[1], target=climax[1] * 1.5,
             grade="cinematic", sat=1.06),
        # 8 ── RESOLVE: Leo unbothered ──
        C(belly, b_belly, 1, slow=1.2),
    ]
    # one narration line per scene; pre-render + size each scene to fit before timing the captions
    narr_lines = {
        0: "결론부터 말할게요. 랴니가 왜 혼자 밖에서 축구를 하고 있냐면.",
        1: "세 시간 전. 레오는 방에서 배를 까고 자고 있었어요.",
        2: "그런데 랴니가, 심심했어요.",
        3: "에너지는 이미 만렙.",
        4: "레오를 툭툭 건드려 봤지만, 무관심.",
        5: "그래서 랴니는 밖으로 나갔어요.",
        6: "그러다, 마당에서 축구공을 발견합니다.",
        7: "그래서 지금, 혼자 신나게 축구 중이에요.",
        8: "레오는, 자긴 모르는 일이래요.",
    }
    pre = _prerender_and_size(seq, narr_lines)
    ts = _times(seq)
    total = sum(s["target"] for s in seq)
    F = FONT_XBOLD
    Y = H * 0.15
    def cap(seg, a, b, txt, fs=74, box=False):
        return (ts[seg] + a, ts[seg] + b, txt, fs, Y, F, box)
    # dense, causal, a little funny — a real cause→effect chain, payoff shown FIRST
    caps = [
        cap(0, 0.10, 1.5, "결론부터 말할게요", 82, True),
        cap(0, 1.5, ts[1] - ts[0] - 0.1, "랴니가 왜 혼자 밖에서 축구를?", 70, True),
        cap(1, 0.10, 2.2, "세 시간 전, 방 안", 74),
        cap(1, 2.3, ts[2] - ts[1] - 0.1, "레오는 배 까고 숙면 중", 74),
        cap(2, 0.10, 1.9, "근데 랴니가… 심심했다", 76),
        cap(2, 1.9, ts[3] - ts[2] - 0.1, "(모든 일의 시작)", 66),
        cap(3, 0.10, 1.8, "에너지 200% 완충", 80),
        cap(3, 1.8, ts[4] - ts[3] - 0.1, "누가 좀 말렸어야 했다", 70),
        cap(4, 0.10, 1.7, "레오를 툭툭 건드려 보지만", 72),
        cap(4, 1.7, ts[5] - ts[4] - 0.1, "레오: 관심 없음", 78),
        cap(5, 0.10, 1.7, "그래서 결심한다", 76),
        cap(5, 1.7, ts[6] - ts[5] - 0.1, "밖으로 나갔어요", 86, True),
        cap(6, 0.10, 1.5, "마당에서 축구공 발견", 76, True),
        cap(6, 1.5, ts[7] - ts[6] - 0.1, "스위치 ON", 92, True),
        cap(7, 0.10, 2.0, "그래서 지금, 혼자 신나게 축구 중", 74, True),
        cap(7, 2.0, ts[8] - ts[7] - 0.1, "아무도 못 말림", 84, True),
        cap(8, 0.10, 2.4, "레오: …나는 모르는 일이다", 74),
        (total - 2.0, total, "@ryani_n_leo", 58, H * 0.82, F, False),
    ]
    # OpenAI neural TTS narration reading the causal arc — pre-rendered above, placed at each
    # scene start (scenes already sized to fit), music ducked underneath.
    V = 1.45
    voices = [(ts[i] + 0.15, narr_lines[i], V, str(pre[i][0])) for i in sorted(narr_lines)]
    assemble(seq, caps, music_id, out, music_start=4.0, music_vol=0.40, voices=voices)


# ──────────────────────────────────────────────────────────────────────
GRAMMARS = {
    "velocity": (build_velocity, "9jackjack8-fading-summer-ibiza-chill-house-499753.mp3",
                 "data/output/style_demo/velocity_proof.mp4"),
    "meme":     (build_meme, "music_for_videos-fun-amp-quirky-jazz-123607.mp3",
                 "data/output/style_demo/meme_proof.mp4"),
    "story":    (build_story, "hunzalaawanarts75-cinematic-cozy-vibes-421335.mp3",
                 "data/output/style_demo/story_proof.mp4"),
}


def _grammar_music(grammar: str, default: str) -> str:
    """Convention override so PD can swap a grammar's music with NO code edit and NO env (both
    are hard to change on the VM): drop `assets/bgm/<grammar>_music.{mp3,wav,m4a}` and it wins.
    This is how a real club/EDM banger gets under `velocity` — the 93-track library has none, so
    velocity currently rides a chill-house default that reads too calm for the club edit. Falls
    back to `<GRAMMAR>_MUSIC` env, then the hardcoded default."""
    for ext in ("mp3", "wav", "m4a"):
        p = BGM / f"{grammar}_music.{ext}"
        if p.exists():
            return p.name
    return os.getenv(f"{grammar.upper()}_MUSIC") or default


def render_grammar(grammar: str, out, *, clips: dict | None = None, music: str | None = None,
                   copy: dict | None = None):
    """Production entry (Phase B): render one grammar to `out` from an injected `clips`
    dict (role→asset_id or file path; roles: soccer/play1/swim/play2/belly) and an injected
    `copy` object (B4 Writer output, grammar-specific — see each build_* docstring). With
    `copy=None` the hardcoded PROOF text is used (standalone proof runs unchanged)."""
    fn, default_music, _ = GRAMMARS[grammar]
    return fn(music or _grammar_music(grammar, default_music), Path(out), clips=clips, copy=copy) or Path(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grammar", default="velocity", choices=list(GRAMMARS) + ["all"])
    ap.add_argument("--music", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    grammars = list(GRAMMARS) if args.grammar == "all" else [args.grammar]
    for g in grammars:
        fn, music, out = GRAMMARS[g]
        # honor the convention override (assets/bgm/<g>_music.mp3) so the CLI matches the live
        # render path (render_grammar) instead of always using the hardcoded default.
        fn(args.music or _grammar_music(g, music), Path(args.out or out))


if __name__ == "__main__":
    main()
