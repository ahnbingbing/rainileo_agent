"""
scripts/generate_character_scene.py — Character-based scene generation.

Generates illustration scenes of Ryani & Leo as virtual influencer characters.
Supports two modes:
  1. character_only (text-to-image): Generate from character description + scene prompt only
  2. pose_reference (image-to-image): Use a photo as pose/composition reference,
     but generate a fully new illustration (not a photo filter)

Uses Gemini 2.5 Flash Image API (same as regen_vtuber_style.py).

Usage:
    # Character-only (no source photo)
    python3 scripts/generate_character_scene.py \
        --prompt "Leo sitting at a tiny cafe table, curious expression, warm afternoon" \
        --subjects leo \
        --output data/tmp/test_scene.png

    # With pose reference
    python3 scripts/generate_character_scene.py \
        --prompt "Leo and Ryani nose-to-nose in a cherry blossom garden" \
        --reference data/assets/photos/2026/med_2026_01_01_141833.jpeg \
        --subjects both \
        --output data/tmp/test_scene.png

    # Batch from prompts manifest
    python3 scripts/generate_character_scene.py \
        --manifest data/tmp/cameraman_xxx/regen_prompts.json \
        --in-dir data/tmp/cameraman_xxx/input/ \
        --out-dir data/tmp/cameraman_xxx/regen/

    # Dry-run
    python3 scripts/generate_character_scene.py --prompt "test" --dry-run
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from agents import canon as _canon  # central character canon (single source)
CHARACTER_SHEET = ROOT / "agents" / "prompts" / "character_sheets.md"
log = logging.getLogger("generate_character_scene")

# Model — same as regen_vtuber_style.py
MODEL = os.getenv("REGEN_MODEL", "gemini-2.5-flash-image")
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def load_character_sheet() -> str:
    """Load the character design sheet for prompt injection."""
    if CHARACTER_SHEET.exists():
        return CHARACTER_SHEET.read_text(encoding="utf-8")
    return ""


def build_character_prompt(scene_prompt: str, subjects: str = "both",
                           overall_style: str = "", extra_rules: str = "") -> str:
    """Build a complete prompt with character sheet + scene direction.

    Args:
        scene_prompt: The per-cut scene/art direction
        subjects: "ryani", "leo", or "both"
        overall_style: Episode-wide style (from regen_direction.overall_style)
        extra_rules: Any additional per-cut rules
    """
    sheet = load_character_sheet()

    # Character identity canon — central source of truth (agents/canon.py).
    # PD 2026-06-08: image gen MUST NOT destroy the characters' traits
    # (Ryani = SPAYED FEMALE, THIN blaze, NO tail; Leo = MALE, chartreuse eyes,
    # NOT amber/gold). PD 2026-06-09: de-duplicated — edit agents/canon.py only.
    if subjects == "leo":
        char_focus = "Focus on Leo (레오) — the orange tabby cat."
    elif subjects == "ryani":
        char_focus = "Focus on Ryani (랴니) — the black French Bulldog."
    else:
        char_focus = "Both Leo (orange tabby cat) and Ryani (black French Bulldog) appear."
    canon = _canon.image_canon(subjects)

    prompt = f"""PHOTOREALISTIC image that looks like a REAL PHOTOGRAPH taken with a professional camera.

{char_focus}

CHARACTER IDENTITY — preserve EXACTLY, do not alter the markings or proportions:
{canon}

ABSOLUTE REQUIREMENTS:
- REAL fur textures, REAL lighting, REAL environment. Shallow depth of field, natural bokeh.
- This MUST look like an actual photograph. NEVER cartoon, NEVER illustration, NEVER anime, NEVER digital art.
- Pets are bare-furred — NO clothing/hanbok/costumes (unless the scene explicitly says a harness).
- Vertical 9:16 composition.
- Do NOT add any text, captions, watermarks, or logos to the image.

Style: {overall_style}

Scene: {scene_prompt}

{extra_rules}
"""
    return prompt


def _get_reference_image(subjects: str = "both", segment: str = "S4") -> Path | None:
    """Get the appropriate reference photo based on subjects and Leo's growth segment.

    Priority: official character reference (hanbok) > photo reference > segment-specific.
    The official reference ensures consistent character design across all episodes.
    """
    # PD 2026-06-13: CLEAN base ref first — the old official refs have a hanbok /
    # cafe styling that bled accessories (beret, bow tie) into the regen. A plain
    # white-background both-sitting photo (no clothing) fixes that. `base_both.png`
    # = PD's hand-tuned version (preferred if present); `base_both_clean.png` = the
    # OpenAI-generated clean fallback.
    refs = [
        ROOT / "assets" / "character_ref" / "base_both.png",          # PD's clean base
        ROOT / "assets" / "character_ref" / "base_both_clean.png",    # OpenAI clean base
        ROOT / "assets" / "character_ref" / "official_ryani_leo.png",       # hanbok (legacy)
        ROOT / "assets" / "character_ref" / "official_ryani_leo_cafe.png",  # cafe (legacy)
    ]
    # Prefer the first CLEAN base that exists (no alternation — consistency).
    for _r in refs:
        if _r.exists():
            return _r
    # Pick based on subject or just use first available
    existing = [r for r in refs if r.exists()]
    if existing:
        # Alternate: use hash of subjects+segment to pick
        idx = hash(f"{subjects}_{segment}") % len(existing)
        return existing[idx]

    # Fallback to photo reference
    photo_ref = ROOT / "assets" / "character_ref" / "photo_ref_both.png"
    if photo_ref.exists():
        return photo_ref

    # Legacy fallback
    ref_dir = ROOT / "data" / "tmp"
    if subjects == "ryani":
        ref = ref_dir / "ref_ryani_main.png"
    elif subjects == "leo":
        seg_name = {"S1": "baby", "S2": "junior", "S3": "teen", "S4": "adult"}.get(segment, "adult")
        ref = ref_dir / f"ref_leo_{segment}_{seg_name}.png"
    else:
        ref = ref_dir / "ref_ryani_main.png"
    return ref if ref.exists() else None


def generate_scene(prompt: str, reference_image: Path | None = None,
                   subjects: str = "both", segment: str = "S4") -> bytes:
    """Generate a character scene via OpenAI gpt-image-1 with Gemini fallback.

    PD 2026-06-02: "openAI가 안될땐 gemini API 중 하나를 쓰도록." Whenever
    OpenAI fails (rate limit, content policy reject, network), transparently
    retry with Gemini 2.5 Flash Image. Gemini path needs a reference image —
    text-only fallback would lose marking fidelity.
    """
    if reference_image is None:
        reference_image = _get_reference_image(subjects, segment)
    try:
        return _generate_scene_openai(prompt, reference_image)
    except Exception as e:
        log.warning("OpenAI image gen failed (%s) — falling back to Gemini",
                    str(e)[:120])
        if not (reference_image and reference_image.exists()):
            raise RuntimeError(
                "OpenAI failed and Gemini fallback requires a reference image"
            ) from e
        return _generate_scene_gemini(prompt, reference_image)


# PD 2026-06-08: image gen must use gpt-image-2 (newer, better fidelity).
_OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")


def _generate_scene_openai(prompt: str, reference_image: Path | None) -> bytes:
    from openai import OpenAI
    client = OpenAI()
    if reference_image and reference_image.exists():
        from PIL import Image as PILImage
        img = PILImage.open(reference_image)
        img = img.convert("RGB")
        w, h = img.size
        s = min(w, h)
        left, top = (w - s) // 2, (h - s) // 2
        img = img.crop((left, top, left + s, top + s))
        img = img.resize((1024, 1024))
        tmp_png = ROOT / "data" / "tmp" / "_ref_tmp.png"
        img.save(tmp_png, format="PNG")
        result = client.images.edit(
            model=_OPENAI_IMAGE_MODEL,
            image=open(tmp_png, "rb"),
            prompt=prompt,
            size="1024x1536",
            quality="high",
            n=1,
        )
    else:
        result = client.images.generate(
            model=_OPENAI_IMAGE_MODEL,
            prompt=prompt,
            size="1024x1536",
            quality="high",
            n=1,
        )
    return base64.b64decode(result.data[0].b64_json)


def _generate_scene_gemini(prompt: str, reference_image: Path) -> bytes:
    """Fallback path: Gemini 2.5 Flash Image with reference photo."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set — cannot fallback to Gemini")
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "scripts"))
    from regen_vtuber_style import regen_one
    return regen_one(reference_image, prompt, api_key)


def generate_batch(prompts_file: Path, in_dir: Path | None, out_dir: Path,
                   api_key: str, dry_run: bool = False, n: int = 1,
                   progress_cb: callable = None,
                   reference_override: Path | None = None,
                   lock_scene: bool = True,
                   concept: dict | None = None,
                   cuts_by_tag: dict | None = None) -> int:
    """Batch generate from a prompts manifest (same format as regen_vtuber_style.py).

    PD 2026-06-12: `reference_override` — a CONCEPT-GROUNDED character reference
    (Ryani+Leo already placed in THIS episode's scene, generated fresh from the
    writer's concept). When set, EVERY cut uses it as the reference instead of the
    static official (indoor/studio) ref + the per-cut style-anchor CHAIN. The chain
    let the scene drift (beach → indoor mid-episode, ep 204306); a fixed
    scene-matched reference holds the scene across all cuts."""
    prompts = json.loads(prompts_file.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)

    overall_style = prompts.get("_base_style", "")
    preserve = prompts.get("_preserve_subjects", "")
    color_palette = prompts.get("_color_palette", "")
    texture = prompts.get("_texture", "")
    mood = prompts.get("_mood_atmosphere", "")

    style_parts = [s for s in [overall_style, f"Color palette: {color_palette}" if color_palette else "",
                               f"Texture: {texture}" if texture else "",
                               f"Mood: {mood}" if mood else ""] if s]
    full_style = " ".join(style_parts)

    # Load background references from DB
    bg_refs = {}
    try:
        import sqlite3
        _dbpath = ROOT / "data" / "agent.db"
        _con = sqlite3.connect(str(_dbpath), timeout=30)
        _con.row_factory = sqlite3.Row
        for row in _con.execute("SELECT space_name, file_path, description FROM background_refs").fetchall():
            bg_refs[row["space_name"].lower()] = {"path": row["file_path"], "desc": row["description"]}
        _con.close()
        if bg_refs:
            log.info("Loaded %d background references", len(bg_refs))
    except Exception:
        pass

    tags = [k for k in prompts if not k.startswith("_")]
    failures = 0

    # Style anchor: first generated cut becomes the reference for all subsequent cuts
    # This ensures visual consistency across the episode
    style_anchor: Path | None = None

    for tag in tags:
        per_cut = prompts[tag]

        # Add style consistency instruction after first cut
        style_note = ""
        if style_anchor:
            style_note = (
                "CRITICAL STYLE CONSISTENCY: Match the EXACT SAME visual style, "
                "color grading, lighting, and rendering quality as the reference image. "
                "Same level of photorealism, same color temperature, same fur texture detail. "
                "Do NOT switch between illustration and photorealistic styles."
            )

        # Check for matching background reference
        bg_note = ""
        for bg_key, bg_info in bg_refs.items():
            if bg_key in per_cut.lower() or bg_key in full_style.lower():
                bg_note = f"\nBACKGROUND REFERENCE: Use the actual room/space from this description: {bg_info['desc']}"
                break

        # PD 2026-06-12: when a concept-grounded reference is supplied, FORCE every
        # cut to keep that reference's exact setting + heads-up posture — the per-cut
        # prompt alone let cuts drift indoors and invent floor-eating (ep 6c2a048a:
        # beach concept, but cut2 rendered an indoor floor with Ryani nose-down).
        ref_lock_note = ""
        if reference_override and Path(reference_override).exists():
            if lock_scene:
                ref_lock_note = (
                    "⚠️ SCENE LOCK — the REFERENCE IMAGE defines the EXACT setting for this "
                    "whole episode. KEEP the same environment as the reference: same "
                    "location (e.g. if the reference is a BEACH, stay on that beach — do "
                    "NOT move indoors, do NOT invent a room/floor/furniture), same "
                    "background elements, same lighting. ONLY change the pets' pose/action "
                    "to match the scene direction below. POSE LOCK — keep Ryani and Leo in "
                    "the same upright, HEADS-UP posture as the reference unless the scene "
                    "direction EXPLICITLY says otherwise; do NOT add a nose-to-the-ground "
                    "sniffing/eating pose that isn't in the scene direction."
                )
            else:
                # PD 2026-06-13: MULTI-LOCATION journey (e.g. 무더위: 분수광장→거실 선풍기→
                # 욕실 세면대). Locking the scene forced every cut to the FIRST location
                # (분수), collapsing the journey. Lock only the CHARACTERS; let each cut's
                # own scene direction set the location/background + pose.
                ref_lock_note = (
                    "⚠️ CHARACTER LOCK — use the REFERENCE IMAGE ONLY to fix the two pets' "
                    "identity: the SAME Ryani (markings, breed, colors, no tail) and the SAME "
                    "Leo (orange tabby markings, eyes) exactly as in the reference. This "
                    "episode deliberately MOVES BETWEEN DIFFERENT LOCATIONS — render THIS cut "
                    "in the location/background described in its own scene direction below "
                    "(NOT the reference's location). Change background, lighting AND the pets' "
                    "pose/action to fully match this cut's scene direction. Keep ONLY the "
                    "pets' appearance constant across cuts; everything else follows the per-cut "
                    "direction."
                )
        prompt = build_character_prompt(
            scene_prompt=per_cut,
            subjects="both",
            overall_style=full_style,
            extra_rules=f"{preserve}\n{style_note}\n{bg_note}\n{ref_lock_note}".strip(),
        )

        # PD 2026-06-12: a concept-grounded reference (Ryani+Leo already in THIS
        # episode's scene) holds the scene for EVERY cut — no style-anchor chain,
        # which let the scene drift (beach→indoor). Falls back to the old chain when
        # no override is supplied.
        if reference_override and Path(reference_override).exists():
            ref_image = reference_override
        else:
            # Reference image: use style_anchor (previous cut) if available, else character ref
            ref_image = style_anchor  # previous cut = style anchor
            if ref_image is None:
                # First cut: use official character reference
                if in_dir:
                    for ext in (".jpg", ".jpeg", ".png"):
                        candidate = in_dir / f"{tag}{ext}"
                        if candidate.exists():
                            ref_image = candidate
                            break
                if ref_image is None:
                    ref_image = _get_reference_image("both", "S4")

        out_path = out_dir / f"{tag}.png"
        if progress_cb:
            progress_cb(f":art: [{tags.index(tag)+1}/{len(tags)}] 캐릭터 생성 중: {tag}")
        print(f"==> {tag}")
        print(f"    style: {full_style[:80]}...")
        print(f"    ref  : {ref_image or '(character only)'}")
        print(f"    out  : {out_path}")

        if dry_run:
            print(f"    [dry-run] prompt length: {len(prompt)} chars")
            print()
            continue

        # Skip if already generated (avoid re-doing on retry)
        if out_path.exists() and out_path.stat().st_size > 10000:
            print(f"    (exists — skip)")
            if progress_cb:
                progress_cb(f":fast_forward: {tag} 이미 생성됨 — 스킵")
            # Use existing as style anchor if none set
            if style_anchor is None:
                style_anchor = out_path
            continue

        def _gen_one() -> bytes | None:
            """One still with safety-rejection retries. None if all attempts fail."""
            for attempt in range(3):
                try:
                    rp = prompt if attempt == 0 else (
                        prompt + "\nKeep the scene simple and wholesome. Family-friendly content only.")
                    return generate_scene(rp, reference_image=ref_image,
                                          subjects="both", segment="S4")
                except Exception as e:
                    es = str(e)
                    log.warning("gen attempt %d failed for %s: %s", attempt + 1, tag, es[:200])
                    time.sleep(5 if ("safety" in es.lower() or "rejected" in es.lower()) else 2)
            return None

        def _crop916(p: Path) -> None:
            # GPT outputs 2:3 (1024x1536) → crop to exact 9:16 (1080x1920).
            try:
                from PIL import Image as _Img
                img = _Img.open(p); w, h = img.size
                if abs(w / h - 9 / 16) > 0.01:
                    new_w = int(h * 9 / 16); left = (w - new_w) // 2
                    img = img.crop((left, 0, left + new_w, h)).resize((1080, 1920), _Img.LANCZOS)
                    img.save(p, format="PNG")
            except Exception:
                pass

        # PD 2026-06-17: best-of-N stills per cut + VLM/Giri selection (the PD-expected
        # "컷당 5장 만들어 select"). Was 1 still/cut → marking/prop/background drift
        # shipped unselected. REGEN_BEST_OF=1 restores the single-still path (cost).
        best_of = max(1, int(os.getenv("REGEN_BEST_OF", "5")))
        cands = []
        for k in range(best_of):
            b = _gen_one()
            if not b:
                continue
            cp = out_dir / f"{tag}_cand{k+1}.png"
            cp.write_bytes(b); _crop916(cp)
            cands.append(cp)

        if cands:
            if len(cands) == 1:
                winner = cands[0]
            else:
                try:
                    from agents import still_select
                    pick = still_select.pick_best_still(
                        cands, cut=(cuts_by_tag or {}).get(tag), concept=concept, lane="ai_vtuber")
                    winner = Path(pick["winner_path"])
                    print(f"    best-of-{len(cands)} → #{pick['winner']}: {(pick.get('reason') or '')[:60]}")
                    if progress_cb:
                        progress_cb(f":mag: {tag} best-of-{len(cands)} → #{pick['winner']} 선택")
                except Exception as e:
                    log.warning("still_select failed for %s: %s — using first candidate", tag, e)
                    winner = cands[0]
            import shutil as _shutil
            _shutil.copy(winner, out_path)
            for cp in cands:  # keep only the winner
                if cp != winner and cp.exists():
                    try: cp.unlink()
                    except Exception: pass
            size_kb = out_path.stat().st_size / 1024
            print(f"    ok ({size_kb:.0f} KB) [9:16]")
            if progress_cb:
                progress_cb(f":white_check_mark: {tag} 완료 ({size_kb:.0f} KB)")
            if style_anchor is None:
                style_anchor = out_path
                print(f"    → style anchor set: {out_path.name}")
        else:
            print("    FAILED")
            failures += 1
            if progress_cb:
                progress_cb(f":x: {tag} 실패")

        print()
        time.sleep(1)  # Rate limiting

    return failures


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(name)s %(levelname)s %(message)s")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    p = argparse.ArgumentParser(description="Character-based scene generation")
    p.add_argument("--prompt", default=None, help="single scene prompt")
    p.add_argument("--reference", default=None, help="pose reference photo (optional)")
    p.add_argument("--subjects", default="both", choices=["ryani", "leo", "both"])
    p.add_argument("--style", default="", help="overall episode style")
    p.add_argument("--output", default=None, help="output PNG path")
    p.add_argument("--manifest", default=None, help="prompts manifest JSON (batch mode)")
    p.add_argument("--in-dir", default=None, help="reference images dir (batch)")
    p.add_argument("--out-dir", default=None, help="output dir (batch)")
    p.add_argument("--n", type=int, default=1, help="attempts per image")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set", file=sys.stderr)
        return 2

    # Batch mode
    if args.manifest:
        out_dir = Path(args.out_dir) if args.out_dir else Path("data/tmp/character_regen")
        in_dir = Path(args.in_dir) if args.in_dir else None
        failures = generate_batch(Path(args.manifest), in_dir, out_dir,
                                  api_key, args.dry_run, args.n)
        return 1 if failures else 0

    # Single mode
    if not args.prompt:
        print("ERROR: --prompt or --manifest required", file=sys.stderr)
        return 2

    prompt = build_character_prompt(
        scene_prompt=args.prompt,
        subjects=args.subjects,
        overall_style=args.style,
    )

    if args.dry_run:
        print("=== PROMPT ===")
        print(prompt)
        print(f"\n=== {len(prompt)} chars ===")
        return 0

    ref = Path(args.reference) if args.reference else None
    out = Path(args.output) if args.output else Path("data/tmp/character_test.png")
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Generating: {args.subjects}, ref={'custom' if ref else 'auto'}")
    img_bytes = generate_scene(prompt, reference_image=ref,
                               subjects=args.subjects, segment="S4")
    out.write_bytes(img_bytes)
    print(f"Saved: {out} ({len(img_bytes)/1024:.0f} KB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
