"""
agents/reviewer.py — Review Agent (Giri v1).

Based on notes/shorts_review_agent_giri.md. Reviews rendered episodes
and makes a clear decision: upload / revise / regenerate / discard.

Checks:
  1. Opening hook (first 1-2s)
  2. Character clarity (Ryani's white markings, Leo's stripes)
  3. Motion quality (real motion vs zoom/fade camouflage)
  4. Emotional hook
  5. Visual style coherence
  6. Pacing
  7. Caption quality + BGM
  8. Cultural/occasion fit
  9. Photo selection quality (per photo_selection_guide)

Usage:
    python -m agents.reviewer <video.mp4> --concept <concept.json>
    python -m agents.reviewer <video.mp4> --storyboard "cut1: ..., cut2: ..."
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("agents.reviewer")

REVIEW_GUIDE = (ROOT / "notes" / "shorts_review_agent_giri.md").read_text(encoding="utf-8") \
    if (ROOT / "notes" / "shorts_review_agent_giri.md").exists() else ""

PHOTO_GUIDE = ""
for p in ["photo_selection_guide_v1.0.md", "photo_selection_guide.md"]:
    fp = ROOT / "notes" / p
    if fp.exists():
        try:
            PHOTO_GUIDE = fp.read_text(encoding="utf-8")
            break
        except Exception:
            pass

REVIEW_PROMPT = f"""\
{REVIEW_GUIDE}

---
## Photo/Clip Selection Rules (from photo_selection_guide):

{PHOTO_GUIDE[:3000]}

---
## Your task:

You are reviewing a rendered YouTube Short. I'm showing you FRAMES EXTRACTED from the video (not still images — these are screenshots from a video that has actual motion).
Also provided: the original storyboard concept and audio analysis.

IMPORTANT: These frames are from a VIDEO, so do NOT penalize for "lack of motion" or "still images". The motion exists in the video between frames. Judge composition, subject clarity, style coherence, and storyboard matching — NOT whether the frame itself moves.

**CRITICAL CHECK 0 — Caption-vs-Clip truthfulness (PD 2026-06-03 strict)**:
Before judging anything else, scan each frame against the burned-in caption visible in that frame:
- The caption describes specific objects/actions (e.g., "사료가 톡 튕겼어요", "장난감을 쫓아가요", "발라당 누웠어요").
- The frame must SHOW that specific thing happening. NOT a similar thing — the EXACT thing claimed.
- If caption says "사료가 톡 튕겼어요" but the frame shows no food bowl and no food motion → FAIL.
- If caption says "장난감을 쫓아가요" but the frame shows cat sitting with back to camera, no toy in frame → FAIL.
- If caption ignores Ryani's clear visible presence (e.g., play-bow with Ryani in frame, but caption only describes Leo) → FAIL.
- **CAPTIONS MUST ALWAYS BE ON SCREEN (PD 2026-06-06): a frame with NO burned-in caption is a DEFECT — real_footage captions must be dense/continuous so a caption is showing at every moment. If you see frames with no caption, note it in `개선점` as "자막 공백 (촘촘하게 채워야 함)".**
- **NO HUMAN FACE — HARD RULE (PD 2026-06-06): a human FACE must NEVER be visible in any frame. If you see a human face, verdict MUST be "수정 필요" (or worse) and note "인간 얼굴 노출 — crop 필요" in `개선점`. Human hands/legs without a face are acceptable.**
- **NO STATIC/FROZEN FEEL (PD 2026-06-06): if a cut looks like a still photo with only a zoom (subject not actually moving), note "정지 화면 느낌 — 캐릭터 모션 필요" in `개선점`.**
- **KICK (PD 2026-06-06): the episode should have ONE standout moment (play-bow, camera-direct gaze, belly-up, a striking expression, a twist) within a COHERENT arc. If the whole thing is flat observation with NO peak at all, note "킥 부족" in `개선점` (do NOT force a fail by itself). BUT coherence matters more than kick intensity — a smooth natural story with a modest kick is GOOD; a jumbled story contorted around a forced kick is BAD. If the narrative feels forced/jumbled (e.g. an artificial "초대/답장" conceit, food beat in a weird order), note "흐름 어색 — 자연스러운 단일 arc 필요".**
- For each Caption-vs-Clip failure, list in `caption_vs_clip_mismatches` (one entry per cut).
- If `caption_vs_clip_mismatches` has ≥2 entries, the verdict MUST be "수정 필요" or worse, and overall score MUST NOT exceed 5/10.
- If ≥1 mismatch, score must not exceed 7/10.

This check OVERRIDES any other positive scoring. A pretty episode with lying captions is worse than an ugly episode with honest captions.

IMPORTANT STYLE RULES:
- "ai_vtuber" style has multiple generation modes (Seedance 2.0 since 2026-05-30):
  - **chain mode (short tier default, PD 2026-06-01)**: Cut 1 = Seedance ref mode (character refs + scene_ref + R2V). Cuts 2+ = Seedance i2v with previous cut's last ffmpeg-extracted frame as input. Natural speed, no slowdown. Ends with a story-driven wink ending cut.
  - **ref mode**: Seedance reads character + scene refs + text prompt, outputs photorealistic video. iPhone snapshot aesthetic.
  - **text_to_video (legacy)**: Veo 3.0 t2v. Mostly replaced by Seedance.
  - **Special concept (특별 컨셉)**: illustration style OK — only for holidays/seasons when PD explicitly approves.
- "real_footage" style = actual video/photo clips from DB. AI-generated images ARE ALLOWED if created for THIS episode.
  - real_footage도 ai_vtuber와 **동일한 스토리 품질 기준** 적용! 단순 클립 나열 ≠ 에피소드.
  - 인과관계 있는 스토리 전개 필수 (원인→행동→결과→리액션)
  - 컷 수/길이/캡션 개수는 Writer가 결정 — 고정 포맷 아님
  - 같은 날짜+장소 클립이 자연스럽게 이어져야 함
- For BOTH styles: Do NOT reject images just because they look AI-generated or photorealistic. The channel's style IS photorealistic AI generation. Text-to-video output may look slightly different from real photos — this is expected and OK.
- Only reject if: wrong characters, wrong theme, content from a different episode, or completely off-topic.
- **BGM**: Must match the concept's mood. Cozy concept = gentle/lofi BGM. Fun concept = playful/upbeat BGM. Do NOT use epic/cinematic/orchestral BGM for cute pet content.
- **Cut repetition**: If multiple cuts show the same pose/scene/background, that is a MAJOR issue. Every cut must be visually distinct. Penalize heavily for repeated scenes.
- **Storytelling check**: Unusual scenes are OK if there's a story behind them. Penalize only if NO narrative context.
- **Caption quality — TV동물농장/세나개 나레이션 톤 필수**:
  - Captions should read like TV동물농장 narration: "오늘도 어김없이 레오는...", "과연 참을 수 있을까요?", "아니나 다를까..."
  - Or 세나개 style: explaining WHY the pet does something: "이건 사냥 본능이에요", "놀자는 신호입니다"
  - PENALIZE HEAVILY: bland descriptive captions like "소파에 앉아있다", "레오의 반응", "놀자 신호를 보냈습니다"
  - All captions in sequence must form ONE coherent story — no random disconnected captions
  - Korean REQUIRED, English REQUIRED below Korean. No parentheses. No emojis. No script notes.
  - "랴니엄마" = Leo's affectionate name for Ryani (NOT a separate human owner). Used in Leo-POV captions to refer to Ryani. Never mapped to a human body part. The actual human owner, when shown via hands/feet, is "사람" or unnamed.
  - Captions at BOTTOM of screen.
- **Direction quality** (film/drama level required):
  - POV: camera at pet eye-level. Humans CAN show body (torso, arms, legs, hands, feet) — but face MUST be hidden (framed from neck down, shot from behind, or low angle cropping face out). Mirror/glass reflections of face also count as face exposure.
  - Scene continuity: cuts must flow naturally. Walking in hallway → arriving at bed = good. Walking → suddenly on sofa = bad.
  - Space variety: multiple rooms/areas within same episode. Single room = boring.
  - Cutaways/crosscuts: "meanwhile Ryani is..." = adds depth.
  - Protagonist separation: not always together. Solo cuts are fine.
  - Action specificity: "delivers toy" = carrying + arriving + dropping. Not just sitting nearby.
  - Penalize HEAVILY: all cuts same angle/distance, pets always together, no spatial movement, vague actions.
- **Character appearance accuracy** (PD 2026-06-02: TIGHTENED):
  - **Ryani (French Bulldog, 11yr)**: a THIN Boston Terrier-style WHITE BLAZE (narrow line, NOT a wide splash) from nose to forehead, white dot above each eye, silver-grey aged muzzle, white chin, large white chest patch, large bat ears, ABSOLUTELY NO TAIL (her rear is bare — flag any tail rendering as a major failure), stocky compact body, only black/white/grey — no brown.
  - **Leo (orange tabby, ~8mo)**: pale yellow-green chartreuse eyes (NOT gold-amber), faint scar across nose bridge, white chin tuft. Tail often in question mark shape. Lean agile body, paler cream-orange cheeks/belly than the back.
  - **Marking enforcement (HARD CAP)**: if the automated marking check (이마줄/눈썹/회색주둥이/흰가슴) reports 3+ ❌ across any cut, your overall score MUST NOT exceed 7/10 — character fidelity is foundational. State this explicitly in your reasoning. If 4/4 ❌, max 5/10 and verdict = "수정 필요".
  - **Cross-cut consistency**: pets should look IDENTICAL across cuts within the same episode. Different breed renderings between cuts 1 and 4 = major drift, cap at 6.
- **Animal behavior accuracy**: Body language must match the scene's emotion and be species-accurate:
  - Leo (cat): tail shape (?=curious, up=happy, puffed=scared), ear direction, slow blink, butt wiggle before jump, kneading, grooming
  - Ryani (dog): tongue out=happy, head tilt=curious, paw raise=attention, belly up=trust, sniffing
  - Motion prompts must use specific animal behaviors, not vague "gentle motion"
- **Safety**: Pets outside or in vehicles MUST wear harness. Ryani: in carrier on passenger seat. Leo: back seat with long leash. Penalize if harness not visible in outdoor/vehicle scenes.
- **Mixed media OK**: ai_vtuber episodes CAN include real footage clips (e.g., real car wash video mixed with AI character scenes). This is intentional, not a bug.
- **Size consistency**: If the episode references an earlier time period, Leo should be noticeably smaller (S1-S3 growth segments).

Follow the scoring rubric from above (1-10 scale) and evaluate ALL dimensions:
A. Opening hook, B. Character clarity, C. Motion quality, D. Emotional hook,
E. Visual style, F. Pacing, G. Upload value, H. Cultural fit

Additionally check:
- **Photo selection quality**: Do the selected photos match the narrative beats?
  Are Ryani's white markings visible? Is there background variety across cuts?
- **Caption quality**: Readable? Correct position? Not overflowing? Not blocking subjects?
- **BGM**: Present? Appropriate mood?

Return JSON:
{{
  "판정": "업로드" | "소폭 수정 후 업로드" | "수정 필요" | "컨셉 재작업" | "폐기",
  "점수": 1-10,
  "핵심_판단": "2-4문장",
  "좋은_점": ["..."],
  "가장_큰_문제": "한 가지",
  "최소_수정안": "가장 작은 수정",
  "툴_수정_요청": "Claude Code / Veo용 수정 문장",
  "최종_결정": "정확히 무엇을 할지",
  "dimensions": {{
    "opening_hook": 1-10,
    "character_clarity": 1-10,
    "motion_quality": 1-10,
    "emotional_hook": 1-10,
    "visual_style": 1-10,
    "pacing": 1-10,
    "caption_quality": 1-10,
    "photo_selection": 1-10,
    "bgm_fit": 1-10,
    "prop_fidelity": 1-10
  }},
  "prop_fidelity_detail": {{
    "expected_objects_present": ["object name_ko that DID appear correctly"],
    "expected_objects_missing": ["object name_ko that SHOULD have been present but wasn't"],
    "wrong_versions": ["object that appeared but in wrong style/era vs canonical description"]
  }},
  "caption_vs_clip_mismatches": [
    {{
      "cut_number": 1,
      "caption_text": "사료가 톡 튕겼어요",
      "what_clip_actually_shows": "Leo seated on orange chair looking down, no food visible",
      "severity": "critical" | "moderate"
    }}
  ],
  "per_cut": [
    {{
      "cut": 1,
      "storyboard_match": 0.0-1.0,
      "subject_visible": true/false,
      "ryani_markings_clear": true/false,
      "has_unwanted_human": false,
      "caption_readable": true/false,
      "caption_overflow": false,
      "issue": "문제 있으면 설명"
    }}
  ]
}}
"""


def _extract_frames(video: Path, n: int = 6) -> list[Path]:
    """Extract frames: first frame + 1 per cut middle + last frame."""
    tmpdir = Path(tempfile.mkdtemp(prefix="review_"))

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(video)],
        capture_output=True, text=True,
    )
    duration = float(json.loads(probe.stdout)["format"]["duration"])

    times = [0.5]  # first frame (hook)
    cut_start = 1.5  # after intro bumper
    cut_dur = (duration - 4.0) / 4  # 4 content cuts
    for i in range(4):
        times.append(cut_start + i * cut_dur + cut_dur / 2)
    times.append(duration - 1.0)  # last frame

    frames = []
    for i, t in enumerate(times[:n]):
        out = tmpdir / f"frame_{i}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "2", str(out)],
            capture_output=True, timeout=10,
        )
        if out.exists():
            frames.append(out)
    return frames


def _check_audio(video: Path) -> dict:
    """Check BGM presence and volume."""
    result = {"has_bgm": False, "mean_db": None, "issues": []}

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type", "-of", "json", str(video)],
        capture_output=True, text=True, timeout=10,
    )
    if not json.loads(probe.stdout).get("streams"):
        result["issues"].append("오디오 스트림 없음")
        return result

    try:
        vol = subprocess.run(
            ["ffmpeg", "-i", str(video), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
        mean_match = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", vol.stderr)
        if mean_match:
            result["mean_db"] = float(mean_match.group(1))
            result["has_bgm"] = result["mean_db"] > -50
    except Exception:
        pass

    if not result["has_bgm"]:
        result["issues"].append("BGM 없음 또는 무음")
    return result


# ──────────────────────────────────────────────────────────────────────
# Character similarity — compare generated frames vs real reference photos
# ──────────────────────────────────────────────────────────────────────
# Reference photos for Ryani (best face shots from DB)
_RYANI_REFS = [
    ROOT / "data" / "assets" / "photos" / "2026" / "med_2026_02_07_100934_icloud_7e837ca4.jpeg",
    ROOT / "data" / "assets" / "photos" / "2024" / "med_2024_03_02_120833_icloud_824f4ff1.jpeg",
    ROOT / "data" / "assets" / "photos" / "2023" / "med_2023_08_06_190305_icloud_984b096b.jpeg",
]
_LEO_REFS = [
    ROOT / "data" / "assets" / "photos" / "2026" / "med_2026_01_06_095940_icloud_44e254c1.jpeg",
]


def _crop_face_region(img, ratio=0.6):
    """Crop center-top region where face typically is in a portrait."""
    w, h = img.size
    left = int(w * 0.15)
    right = int(w * 0.85)
    top = int(h * 0.05)
    bottom = int(h * ratio)
    return img.crop((left, top, right, bottom))


def _compute_similarity(img1, img2, size=(256, 256)):
    """Compute combined similarity score between two images.

    Uses MSE + normalized cross-correlation + color histogram.
    Returns a combined score where higher = more similar.
    """
    import numpy as np
    i1 = img1.resize(size).convert("RGB")
    i2 = img2.resize(size).convert("RGB")
    a1 = np.array(i1, dtype=np.float64)
    a2 = np.array(i2, dtype=np.float64)

    # MSE (lower = more similar)
    mse = float(np.mean((a1 - a2) ** 2))

    # Normalized cross-correlation (higher = more similar)
    a1_norm = (a1 - a1.mean()) / (a1.std() + 1e-8)
    a2_norm = (a2 - a2.mean()) / (a2.std() + 1e-8)
    ncc = float(np.mean(a1_norm * a2_norm))

    # Color histogram similarity
    hist_sim = 0.0
    for c in range(3):
        h1, _ = np.histogram(a1[:, :, c], bins=32, range=(0, 255), density=True)
        h2, _ = np.histogram(a2[:, :, c], bins=32, range=(0, 255), density=True)
        hist_sim += float(np.sum(np.sqrt(h1 * h2)))
    hist_sim /= 3

    # Combined score (higher = more similar)
    combined = (1 - mse / 10000) * 0.3 + ncc * 0.3 + hist_sim * 0.4
    return {"mse": mse, "ncc": ncc, "hist_sim": hist_sim, "combined": combined}


def _check_character_similarity(frames: list[Path],
                                concept: dict | None = None) -> dict:
    """Check if generated frames have correct Ryani markings.

    Instead of pixel-level comparison, checks for SPECIFIC marking features:
    1. Forehead blaze (lighter stripe between eyes)
    2. Grey muzzle (age greying)
    3. White chest patch

    Uses color analysis on specific face regions.
    """
    import numpy as np
    from PIL import Image

    result = {"ryani_score": 0.0, "checks": {}, "details": []}
    frame_scores = []

    for fp in frames:
        try:
            img = Image.open(fp).convert("RGB")
            w, h = img.size
            arr = np.array(img, dtype=np.float64)

            # Define face regions (assuming portrait 9:16, subject centered)
            # Forehead: top 20-35% of image, center 30% width
            forehead = arr[int(h*0.20):int(h*0.35), int(w*0.35):int(w*0.65)]
            # Muzzle: 35-50% of image height, center 40%
            muzzle = arr[int(h*0.35):int(h*0.50), int(w*0.30):int(w*0.70)]
            # Chest: 55-70%, center 30%
            chest = arr[int(h*0.55):int(h*0.70), int(w*0.35):int(w*0.65)]

            checks = {}

            # Check 1: Forehead blaze — is there a lighter stripe in the center?
            # Compare center column vs side columns of forehead
            fh, fw = forehead.shape[:2]
            center_strip = forehead[:, int(fw*0.35):int(fw*0.65)]  # center 30%
            side_strips = np.concatenate([forehead[:, :int(fw*0.25)],
                                          forehead[:, int(fw*0.75):]], axis=1)
            center_brightness = np.mean(center_strip)
            side_brightness = np.mean(side_strips)
            blaze_diff = center_brightness - side_brightness
            # Real Ryani: center is 10-30 units brighter than sides
            checks["forehead_blaze"] = {
                "diff": round(float(blaze_diff), 1),
                "pass": blaze_diff > 5,  # center must be noticeably brighter
            }

            # Check 2: Grey muzzle — muzzle should be lighter than forehead
            muzzle_brightness = np.mean(muzzle)
            forehead_brightness = np.mean(forehead)
            grey_diff = muzzle_brightness - forehead_brightness
            checks["grey_muzzle"] = {
                "muzzle_brightness": round(float(muzzle_brightness), 1),
                "forehead_brightness": round(float(forehead_brightness), 1),
                "diff": round(float(grey_diff), 1),
                "pass": grey_diff > 10,  # muzzle must be lighter than forehead
            }

            # Check 3: Eyebrow markings — lighter patches directly above each eye
            # Left eyebrow region: above left eye
            left_brow = arr[int(h*0.22):int(h*0.28), int(w*0.25):int(w*0.42)]
            # Right eyebrow region: above right eye
            right_brow = arr[int(h*0.22):int(h*0.28), int(w*0.58):int(w*0.75)]
            # Surrounding forehead (should be darker)
            forehead_side = arr[int(h*0.18):int(h*0.25), int(w*0.10):int(w*0.25)]

            left_brow_bright = float(np.mean(left_brow))
            right_brow_bright = float(np.mean(right_brow))
            forehead_side_bright = float(np.mean(forehead_side))
            avg_brow = (left_brow_bright + right_brow_bright) / 2
            brow_diff = avg_brow - forehead_side_bright

            checks["eyebrow_marks"] = {
                "left_brightness": round(left_brow_bright, 1),
                "right_brightness": round(right_brow_bright, 1),
                "forehead_side": round(forehead_side_bright, 1),
                "diff": round(brow_diff, 1),
                "pass": brow_diff > 2,  # eyebrow area must be brighter than side forehead (subtle marking)
            }

            # Check 4: White chest — chest area should be significantly bright
            chest_brightness = np.mean(chest)
            checks["white_chest"] = {
                "brightness": round(float(chest_brightness), 1),
                "pass": chest_brightness > 100,  # should be light
            }

            # Combined score: 0-1 (4 checks now)
            n_pass = sum(1 for c in checks.values() if c["pass"])
            frame_score = n_pass / 4.0
            frame_scores.append(frame_score)

            result["details"].append({
                "frame": fp.name,
                "score": round(frame_score, 2),
                "checks": checks,
            })
        except Exception as e:
            log.warning("Similarity check failed for %s: %s", fp.name, e)

    if frame_scores:
        result["ryani_score"] = round(sum(frame_scores) / len(frame_scores), 3)
        n = len(result["details"])
        result["checks"] = {
            "blaze_pass_rate": sum(1 for d in result["details"]
                                   if d["checks"].get("forehead_blaze", {}).get("pass")) / n,
            "eyebrow_pass_rate": sum(1 for d in result["details"]
                                     if d["checks"].get("eyebrow_marks", {}).get("pass")) / n,
            "muzzle_pass_rate": sum(1 for d in result["details"]
                                    if d["checks"].get("grey_muzzle", {}).get("pass")) / n,
            "chest_pass_rate": sum(1 for d in result["details"]
                                   if d["checks"].get("white_chest", {}).get("pass")) / n,
        }

    return result


def review(video: Path, storyboard: list[dict] | None = None,
           concept: dict | None = None) -> dict:
    """Full review: extract frames + audio check + VLM review.

    PD 2026-06-03: migrated from `google.generativeai` (deprecated, DNS
    issues) to `google.genai`. The end-to-end concept-vs-video check that
    runs after each render is now actually reachable instead of silently
    timing out on every call."""
    from google import genai as _genai
    from google.genai import types as _types
    from PIL import Image

    client = _genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    model_name = os.getenv("VLM_MODEL", "gemini-2.5-flash")

    # Extract frames
    log.info("Extracting review frames from %s", video.name)
    frames = _extract_frames(video)

    # Audio check
    audio = _check_audio(video)

    # Build context
    context = "## Storyboard:\n"
    if storyboard:
        for i, cut in enumerate(storyboard):
            desc = cut.get("description", cut.get("ko", ""))
            beat = cut.get("beat", f"cut{i+1}")
            context += f"  Cut {i+1} ({beat}): {desc}\n"
    if concept:
        context += f"\n## Concept:\n{json.dumps(concept, ensure_ascii=False, indent=2)[:1000]}\n"

    # Phase E — prop fidelity: list expected canonical objects for this set
    if concept and concept.get("set_anchor"):
        try:
            con = sqlite3.connect(ROOT / "data" / "agent.db")
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT name_ko, description, category, era FROM set_objects "
                "WHERE set_anchor=? AND frequency IN ('always','often')",
                (concept["set_anchor"],),
            ).fetchall()
            if rows:
                context += "\n## Canonical objects expected at this set (prop_fidelity check):\n"
                for r in rows:
                    era = f" (era: {r['era']})" if r["era"] else ""
                    context += f"  - {r['name_ko']} [{r['category']}]{era}: {r['description'][:120]}\n"
                context += (
                    "Score `prop_fidelity` 1-10 based on whether these specific objects "
                    "appear in the frames AND match the description. Fill "
                    "`prop_fidelity_detail` with present/missing/wrong_versions lists. "
                    "AI-invented generic versions of named objects = low score.\n"
                )
        except Exception as e:
            log.warning("prop_fidelity context build failed: %s", e)

    context += f"\n## Audio:\nBGM: {'있음' if audio['has_bgm'] else '없음'}"
    if audio["mean_db"] is not None:
        context += f" ({audio['mean_db']:.0f}dB)"
    if audio["issues"]:
        context += f"\n문제: {', '.join(audio['issues'])}"

    # Build VLM request
    parts = []
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    for fp in frames:
        img = Image.open(fp)
        if img.mode != "RGB":
            img = img.convert("RGB")
        max_dim = 1024
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        parts.append(_types.Part.from_bytes(
            data=buf.getvalue(), mime_type="image/jpeg"
        ))

    parts.append(REVIEW_PROMPT + "\n\n" + context)

    response = client.models.generate_content(
        model=model_name,
        contents=parts,
        config=_types.GenerateContentConfig(response_mime_type="application/json"),
    )
    text = (response.text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    report = json.loads(text)

    # Merge audio into report
    report["audio"] = audio

    # Character similarity check — compare generated frames vs real Ryani/Leo photos
    try:
        char_sim = _check_character_similarity(frames, concept)
        report["character_similarity"] = char_sim

        # HARD OVERRIDE: marking checks trump VLM subjective scores
        checks = char_sim.get("checks", {})
        blaze_ok = checks.get("blaze_pass_rate", 0) > 0.5
        eyebrow_ok = checks.get("eyebrow_pass_rate", 0) > 0.5
        muzzle_ok = checks.get("muzzle_pass_rate", 0) > 0.5

        dims = report.get("dimensions", {})
        vlm_char = dims.get("character_clarity", 7)

        # 이마줄 빠지면 캐릭터 최대 5점 (VLM이 10점 줘도 무시)
        if not blaze_ok:
            dims["character_clarity"] = min(vlm_char, 5)
            report.setdefault("_marking_overrides", []).append(
                "이마줄 없음 → character_clarity 최대 5점")
        # 이마줄+눈썹 둘 다 빠지면 최대 3점
        if not blaze_ok and not eyebrow_ok:
            dims["character_clarity"] = min(dims.get("character_clarity", 7), 3)
            report.setdefault("_marking_overrides", []).append(
                "이마줄+눈썹 없음 → character_clarity 최대 3점")

        report["dimensions"] = dims

        # 마킹 픽셀 체크는 SIGNAL로만 사용 (2026-05-30 변경).
        # 이전: marking_pass < 2 면 무조건 verdict override → VLM 9/10도 강제 "수정 필요"로.
        # 현재: marking_pass < 1 (즉 3개 다 실패) 일 때만 override. Seedance ref가
        # 마킹을 흐릿하게 reproduce 하지만 캐릭터 정체성은 유지하는 경우가 많아서,
        # VLM 점수를 신뢰하고 픽셀 체크는 한계 케이스만 잡도록 조정.
        marking_pass = sum([blaze_ok, eyebrow_ok, muzzle_ok])
        if marking_pass < 1 and report.get("판정") == "업로드":
            report["판정"] = "수정 필요"
            report["가장_큰_문제"] = f"랴니 마킹 전부 누락 (이마줄={'✓' if blaze_ok else '✗'} 눈썹={'✓' if eyebrow_ok else '✗'} 주둥이={'✓' if muzzle_ok else '✗'})"
            report["최소_수정안"] = "Seedance prompt에 랴니 마킹 설명 강화 또는 standard 모델로 격상"
    except Exception as e:
        log.warning("Character similarity check failed: %s", e)

    # Cleanup
    for f in frames:
        f.unlink(missing_ok=True)
        try:
            f.parent.rmdir()
        except OSError:
            pass

    return report


def print_report(report: dict) -> None:
    """Pretty-print the review report."""
    score = report.get("점수", 0)
    verdict = report.get("판정", "?")

    verdict_emoji = {
        "업로드": "✅", "소폭 수정 후 업로드": "🔧",
        "수정 필요": "⚠️", "컨셉 재작업": "🔄", "폐기": "❌"
    }
    emoji = verdict_emoji.get(verdict, "❓")

    print(f"\n{'='*50}")
    print(f"{emoji} 판정: {verdict} ({score}/10)")
    print(f"{'='*50}\n")

    print(f"핵심: {report.get('핵심_판단', '')}\n")

    # Dimensions
    dims = report.get("dimensions", {})
    if dims:
        print("차원별 점수:")
        dim_names = {
            "opening_hook": "오프닝 훅",
            "character_clarity": "캐릭터 인식",
            "motion_quality": "모션 품질",
            "emotional_hook": "감정 전달",
            "visual_style": "비주얼 스타일",
            "pacing": "페이싱",
            "caption_quality": "캡션 품질",
            "photo_selection": "사진 선정",
            "bgm_fit": "BGM 적합성",
        }
        for key, label in dim_names.items():
            val = dims.get(key, "?")
            bar = "█" * int(val) + "░" * (10 - int(val)) if isinstance(val, (int, float)) else ""
            print(f"  {label:12} {bar} {val}/10")
        print()

    # Audio
    audio = report.get("audio", {})
    bgm_icon = "🎵" if audio.get("has_bgm") else "🔇"
    print(f"  BGM: {bgm_icon} {'있음' if audio.get('has_bgm') else '없음'}")

    # Per-cut
    for cut in report.get("per_cut", []):
        n = cut.get("cut", "?")
        match = cut.get("storyboard_match", 0)
        icon = "✓" if match >= 0.7 else "△" if match >= 0.4 else "✗"
        human = " 👤" if cut.get("has_unwanted_human") else ""
        overflow = " 📏" if cut.get("caption_overflow") else ""
        ryani = " (랴니 마킹 ✓)" if cut.get("ryani_markings_clear") else ""
        print(f"  Cut {n}: {icon} {match:.1f}{human}{overflow}{ryani}")
        if cut.get("issue"):
            print(f"    ⚠ {cut['issue']}")

    print(f"\n좋은 점:")
    for p in report.get("좋은_점", []):
        print(f"  + {p}")

    print(f"\n가장 큰 문제: {report.get('가장_큰_문제', '없음')}")
    print(f"최소 수정안: {report.get('최소_수정안', '없음')}")
    print(f"\n최종 결정: {report.get('최종_결정', '?')}")
    print()


def format_slack_report(report: dict) -> str:
    """Format review for Slack message."""
    score = report.get("점수", 0)
    verdict = report.get("판정", "?")
    emoji_map = {"업로드": ":white_check_mark:", "소폭 수정 후 업로드": ":wrench:",
                 "수정 필요": ":warning:", "컨셉 재작업": ":arrows_counterclockwise:", "폐기": ":x:"}
    emoji = emoji_map.get(verdict, ":question:")

    lines = [
        f"{emoji} *검수 결과: {verdict}* ({score}/10)",
        f"_{report.get('핵심_판단', '')}_",
        "",
    ]

    # Dimensions bar
    dims = report.get("dimensions", {})
    dim_short = {"opening_hook": "훅", "character_clarity": "캐릭터", "motion_quality": "모션",
                 "emotional_hook": "감정", "visual_style": "스타일", "caption_quality": "캡션",
                 "photo_selection": "사진선정", "bgm_fit": "BGM"}
    for key, label in dim_short.items():
        val = dims.get(key, 0)
        try:
            v = min(int(val), 10)
        except (ValueError, TypeError):
            v = 0
        lines.append(f"  {label}: {'█' * v}{'░' * (10 - v)} {val}")

    # Character marking checks
    char_sim = report.get("character_similarity", {})
    checks = char_sim.get("checks", {})
    if checks:
        blaze_r = checks.get("blaze_pass_rate", 0)
        brow_r = checks.get("eyebrow_pass_rate", 0)
        muzzle_r = checks.get("muzzle_pass_rate", 0)
        chest_r = checks.get("chest_pass_rate", 0)
        b_icon = ":white_check_mark:" if blaze_r > 0.5 else ":x:"
        e_icon = ":white_check_mark:" if brow_r > 0.5 else ":x:"
        m_icon = ":white_check_mark:" if muzzle_r > 0.5 else ":x:"
        c_icon = ":white_check_mark:" if chest_r > 0.5 else ":x:"
        lines.append(f"  랴니마킹: 이마줄{b_icon} 눈썹{e_icon} 회색주둥이{m_icon} 흰가슴{c_icon}")
        # Show raw blaze diff for debugging
        details = char_sim.get("details", [])
        if details:
            avg_blaze = sum(d.get("checks", {}).get("forehead_blaze", {}).get("diff", 0)
                           for d in details) / len(details)
            lines.append(f"    이마줄 밝기차: {avg_blaze:+.1f} (실제랴니: +14.9, 양수=줄 있음)")

    # Audio
    audio = report.get("audio", {})
    lines.append(f"  BGM: {'🎵' if audio.get('has_bgm') else '🔇 없음'}")

    if report.get("가장_큰_문제"):
        lines.append(f"\n*가장 큰 문제*: {report['가장_큰_문제']}")
    if report.get("최소_수정안"):
        lines.append(f"*최소 수정안*: {report['최소_수정안']}")
    lines.append(f"\n*최종 결정*: {report.get('최종_결정', '?')}")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(name)s %(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="Review Agent (Giri v1)")
    p.add_argument("video", help="path to rendered .mp4")
    p.add_argument("--concept", default=None, help="concept JSON file")
    p.add_argument("--storyboard", default=None, help="inline: 'cut1: desc, cut2: desc'")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    args = p.parse_args()

    video = Path(args.video)
    if not video.exists():
        print(f"Video not found: {video}", file=sys.stderr)
        return 2

    storyboard = None
    concept = None
    if args.concept:
        concept = json.loads(Path(args.concept).read_text(encoding="utf-8"))
        storyboard = concept.get("cuts", [])
    elif args.storyboard:
        storyboard = [{"description": s.strip()} for s in args.storyboard.split(",")]

    report = review(video, storyboard=storyboard, concept=concept)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    return 0 if report.get("판정") in ("업로드", "소폭 수정 후 업로드") else 1


if __name__ == "__main__":
    sys.exit(main())
