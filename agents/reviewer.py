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

from agents import canon  # central character canon — judge the SAME pets we generate

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

**DO NOT FABRICATE MISSING CUTS (PD 2026-06-09 — critical reviewer-accuracy fix)**: The frames are a SPARSE SAMPLE (~2 per cut) of an already-assembled episode. Every storyboard cut IS present in the final video — the assembler concatenates them all; cut presence is NOT in question and is NOT your job to adjudicate. NEVER claim a cut is "누락/missing/통째로 빠짐" just because you don't see a frame from it — that is a sampling gap, not a missing cut, and such false claims have wrongly blocked good episodes. Likewise, do NOT claim "captions are truncated/cut short" from frame sampling — each frame shows whatever caption was on screen at that instant; a different caption in the next sampled frame is normal scene progression, not truncation. Judge the QUALITY of what you can see (composition, story, character fidelity, caption-vs-clip truthfulness per visible frame), not what you infer to be absent.

**CRITICAL CHECK 0 — Caption-vs-Clip truthfulness (PD 2026-06-03 strict)**:
Before judging anything else, scan each frame against the burned-in caption visible in that frame:
- The caption describes specific objects/actions (e.g., "사료가 톡 튕겼어요", "장난감을 쫓아가요", "발라당 누웠어요").
- The frame must SHOW that specific thing happening. NOT a similar thing — the EXACT thing claimed.
- If caption says "사료가 톡 튕겼어요" but the frame shows no food bowl and no food motion → FAIL.
- If caption says "장난감을 쫓아가요" but the frame shows cat sitting with back to camera, no toy in frame → FAIL.
- If caption ignores Ryani's clear visible presence (e.g., play-bow with Ryani in frame, but caption only describes Leo) → FAIL.
- **CAPTION DENSITY — real_footage ONLY (PD 2026-06-06, scoped 2026-06-09): for real_footage, captions should be dense/continuous (the caption carries the story), so a frame with no caption is a minor defect — note "자막 공백" in `개선점`. ⚠️ For ai_vtuber, do NOT penalize this — AV is a visual one-take where brief gaps between scene captions are normal; a momentary no-caption frame is NOT a defect and must not lower the 캡션 score or the verdict.**
- **NO HUMAN FACE — HARD RULE (PD 2026-06-06): a human FACE must NEVER be visible in any frame. If you see a human face, verdict MUST be "수정 필요" (or worse) and note "인간 얼굴 노출 — crop 필요" in `개선점`. Human hands/legs without a face are acceptable.**
- **NO STATIC/FROZEN FEEL (PD 2026-06-06): if a cut looks like a still photo with only a zoom (subject not actually moving), note "정지 화면 느낌 — 캐릭터 모션 필요" in `개선점`.**
- **KICK (PD 2026-06-06): the episode should have ONE standout moment (play-bow, camera-direct gaze, belly-up, a striking expression, a twist) within a COHERENT arc. If the whole thing is flat observation with NO peak at all, note "킥 부족" in `개선점` (do NOT force a fail by itself). BUT coherence matters more than kick intensity — a smooth natural story with a modest kick is GOOD; a jumbled story contorted around a forced kick is BAD. If the narrative feels forced/jumbled (e.g. an artificial "초대/답장" conceit, food beat in a weird order), note "흐름 어색 — 자연스러운 단일 arc 필요".**
- **CLAIMED PAYOFF/KICK MUST BE VISIBLE (PD 2026-06-22)**: the cut whose caption promises the kick/payoff action — 발라당/배 보임/belly-up, 점프, 물기, 첨벙, 윙크 등 — MUST actually show that action in its frames. The kick line is the MOST-faked caption; give it NO benefit of the doubt. A claimed "배 펼치기/발라당" the clip never shows is a CHECK 0 LIE (an episode shipped at 9/10 with a belly-flop the video lacked) → log it as a mismatch and cap the score ≤5.
- **갑작스러운 종료 — 여운 (PD 2026-06-22)**: the episode must NOT cut to black the instant the final caption appears. The last cut needs a beat of 여운 — the closing line readable and the moment landing — before it ends. If the ending feels abruptly chopped (last caption flashes and it's over), flag "갑작스러운 종료 — 마지막 여운 부족"; the verdict then cannot be "업로드" (cap ≤7).
- **캡션 가독 시간 (PD 2026-06-22, real_footage)**: each caption must stay up long enough to actually READ both KO + EN at a phone glance. Dense narration is good (see CAPTION DENSITY) ONLY if each line is still readable — density must never mean captions flipping faster than a viewer can finish reading. If lines are crammed so tight you couldn't read them, flag "캡션 표시 시간 부족 — 못 읽음" and cap ≤6.
- For each Caption-vs-Clip failure, list in `caption_vs_clip_mismatches` (one entry per cut).
- If `caption_vs_clip_mismatches` has ≥2 entries, the verdict MUST be "수정 필요" or worse, and overall score MUST NOT exceed 5/10.
- If ≥1 mismatch, score must not exceed 7/10.

This check OVERRIDES any other positive scoring. A pretty episode with lying captions is worse than an ugly episode with honest captions.

**ADDITIONAL HARD CAPS — you are PD's CRITICAL MIRROR, not a hype-man (PD 2026-06-23)**:
Default SKEPTICAL — assume defects until you verify them cut by cut. Polished,
on-brand visuals are NOT a pass; the caps below OVERRIDE polish. A beautiful episode
that trips one of these is NOT "업로드". Do NOT write glowing praise to justify a high
score — deduct first, and a violation forces the score DOWN regardless of how good it
looks. These are CAPS (a ceiling), not soft notes:
- **시점 미표기 (real_footage)**: if the episode mixes time periods (baby-Leo / years-ago
  clips alongside present ones) and the captions do NOT state the timeframe
  ("○년 전" / "아기 땐" / "지금은"), that reads as confusing/disconnected — flag "시점
  미표기" and the score MUST NOT exceed 6 (verdict ≤ 수정 필요). Seamlessly connecting
  past+present with NO time anchor is a defect, never "smooth editing".
- **배경/공간 드리프트 (ai_vtuber, single-space concepts)**: a single-space concept's room
  must stay the SAME across cuts. If the background unintentionally changes between cuts
  — especially the closer suddenly in a different room — flag "배경 드리프트", cap ≤6.
  (EXCEPTION: a concept's INTENTIONAL space transition — 현실→상상→현실, a deliberate
  fantasy realm like 무릉도원 — is NOT drift; do not penalize a scripted scene change.)
- **정적 이야기 (both — HARD CAP, not a soft penalty)**: if the episode is flat observation
  — pets mostly still, no action/progression, cuts interchangeable — it is NOT shippable;
  score ≤6, verdict 수정 필요. An action/explore concept (e.g. spy) with no actual
  exploring/searching is a static FAIL. (Intentional surreal physics is fine and separate
  — a surreal episode can still be statically boring; judge MOTION/progression here.)
- **주체 저노출/트림 누락 (both)**: if a pet the concept centers on (or that PD wants more
  of — e.g. Leo) barely appears, OR the clip's payoff (the subject entering/acting) is
  trimmed off so the good part never shows → flag "주체 저노출/트림", cap ≤6.

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
- **INTENTIONAL SURREAL HOOK — DO NOT PENALIZE (PD 2026-06-11, important)**: ai_vtuber is ENCOURAGED to defy real-world physics when the impossibility IS the hook — that is the channel's signature fun, not a defect. GOOD intentional surrealism (score the hook HIGHER, never lower): 랴니가 거실에서 수영(a dog swimming across the living-room floor), pets floating, indoor rain/snow, a room filling with water. Do NOT write "비현실적/물리 법칙 위반/말이 안 됨" as a problem when the concept or caption FRAMES it as a playful fantasy — that is the SINGLE MOST COMMON reviewer mistake, and a hook like 거실 수영 must be REWARDED as the opening/emotional hook, not flagged. The test: **"Could a human animator have drawn this ON PURPOSE as a fun gag?"** If YES → it's an intentional hook, ALLOW it and reward it.
  - **세면대 범람 → 서핑 = GOOD example, but the MECHANIC must be coherent (PD 2026-06-11)**: the correct, ALLOWED version is — a sink MOUNTED AT COUNTER HEIGHT overflows, the water cascades DOWN and floods the living-room floor, and Ryani/Leo surf on that flood. That is a great hook → reward it. The FORBIDDEN version is a glitch: the sink BASIN itself sitting ON THE FLOOR (grounded at floor level). Same scene, two outcomes: high sink + overflow + flood + surf = HOOK (allow); sink basin on the floor = DEFECT (penalize). Judge which one rendered.
  - This is DIFFERENT from a BROKEN RENDER (still a real defect, still penalize): geometry/anatomy that is GLITCHED rather than fantastical — a melted/orb/dissolving face, an extra or merged limb, a character half-fused into furniture, drift to a different breed, OR a fixture grounded incoherently (the floor-sink above). These look like the model malfunctioned, not like a deliberate fun image. Penalize those normally.
  - Rule of thumb: physics-defying-but-cleanly-drawn = HOOK (allow); incoherent/glitched/ugly = DEFECT (penalize).
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
  - {canon.REVIEW_RYANI}
  - {canon.REVIEW_LEO}
  - **Marking enforcement (HARD CAP — AI-RENDERED CUTS ONLY)**: this applies to AI-generated frames (ai_vtuber, or real_footage photo_i2v cuts) where Seedance can drift. If the automated marking check (이마줄/눈썹/회색주둥이/흰가슴) reports 3+ ❌ across AI-rendered cuts, overall score MUST NOT exceed 7/10; 4/4 ❌ → max 5/10, verdict="수정 필요". **EXCEPTION (PD 2026-06-08): for real_footage real-clip cuts, the dog IS the real Ryani — her markings are correct by definition; do NOT penalize markings on real clips (the pixel heuristic false-negatives on real angles/lighting). Judge real clips on story/clarity, not the marking pixel check.**
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
- **Caption sizing & fit (캡션 크기 — PD priority)**: the rendered KO+EN caption must FIT the 9:16 frame — sized to sit within the safe margins, fully on-screen, never running off the left/right edges, never so large it covers the pets or dominates the frame, and large enough to read on a phone. An OVERSIZED caption that overflows the frame edge, wraps/clips awkwardly, or blocks the subject is a real defect → lower `caption_quality`, set `caption_overflow: true`, and note "캡션 크기/넘침". Judge ONLY what is visibly oversized/overflowing WITHIN a single frame — do NOT infer "truncation/cut short" from a *different* caption appearing in the next sampled frame (that is normal scene progression — see the sparse-sampling note above). This sizing check is about the rendered text fitting; it is separate from CHECK 0 (caption-vs-clip truthfulness).
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


def _probe_dur(p: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(p)],
            capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def _extract_frames(video: Path, n_cuts: int = 4, per_cut: int = 2,
                    max_frames: int = 16) -> list[Path]:
    """Sample ~`per_cut` frames PER actual cut across the content region.

    PD 2026-06-09 fix: the old version HARD-CODED 4 content cuts
    (`cut_dur=(duration-4.0)/4`), so a 5-cut episode sampled at misaligned
    positions and MISSED a whole cut's time window — the reviewer LLM then saw no
    frame from that cut and hallucinated "cut N 누락". Now we (a) take the real cut
    count, (b) probe the actual intro/outro bumper lengths to find the content
    region, and (c) sample enough frames that EVERY cut is covered."""
    tmpdir = Path(tempfile.mkdtemp(prefix="review_"))
    duration = _probe_dur(video)
    if duration <= 0:
        duration = 30.0
    intro = _probe_dur(ROOT / "assets" / "branding" / "intro_bumper.mp4")
    outro = _probe_dur(ROOT / "assets" / "branding" / "outro_bumper.mp4")
    c0 = min(intro + 0.3, duration * 0.25) if intro > 0 else 0.5
    c1 = max(duration - outro - 0.3, c0 + 1.0) if outro > 0 else duration - 0.6
    n_cuts = max(1, int(n_cuts or 4))
    n_mid = min(max_frames - 2, max(4, per_cut * n_cuts))
    times = [0.5]  # hook (intro bumper region)
    for k in range(n_mid):
        frac = (k + 0.5) / n_mid
        times.append(round(c0 + frac * (c1 - c0), 2))
    times.append(round(duration - 0.8, 2))  # last (여운/outro)

    frames = []
    for i, t in enumerate(times):
        out = tmpdir / f"frame_{i:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(video),
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


def _check_face_integrity(client, model_name, frames, _types) -> dict:
    """PD 2026-06-10: dedicated FOCUSED gate for AI face corruption — the kind
    Seedance photo_i2v / i2v produces (a melted/smeared face, mismatched eyes, a
    floating white blob/orb stuck on the forehead) that BOTH the marking check and
    the holistic review miss (markings can read 'correct' on a melted face, so the
    holistic reviewer passed a clearly-distorted Ryani at 9/10). Separate call =
    undivided VLM attention (the proven "don't bundle" lesson). Worded to flag ONLY
    clear AI corruption, NOT a real face that is merely sleepy / blurry / side-on /
    low-light. Fail-open (no defect) on error. Returns {face_defect, severity,
    worst_frame, detail}."""
    from PIL import Image
    # PD 2026-06-10: feed FACE-CROPPED frames. With full frames a small artifact
    # (a floating forehead orb) got lost when 16 images shared one call's attention
    # — the same orb was reliably caught once each frame was cropped to the face
    # region (top ~62% of a vertical 9:16 pet frame, where the head sits). Verified
    # on 003111: full-batch missed it, face-crop batch caught it.
    prompt = (
        "These are FACE-CROPPED frames from an animal video. Some cuts animate a "
        "still photo with AI, which can corrupt the face. Examine EACH animal's face. "
        "Flag ONLY clear AI corruption: a melted / smeared / distorted muzzle or eyes, "
        "grossly asymmetric or mismatched eyes, a face that warps unnaturally, or a "
        "floating white blob / orb / dot artifact stuck on the face or forehead. Do "
        "NOT flag a real, natural face for being sleepy, eyes-closed, motion-blurred, "
        "side-profile, or low-light — those are perfectly fine. Return ONLY JSON: "
        '{"face_defect": true|false, "severity": "none"|"minor"|"major", '
        '"worst_frame": <1-based int, or 0>, "detail": "<defect + which animal/where, '
        'or empty>"}.'
    )
    try:
        parts = []
        for fp in frames:
            img = Image.open(fp)
            if img.mode != "RGB":
                img = img.convert("RGB")
            # crop to the head region (top 62%) so a small artifact isn't diluted
            img = img.crop((0, 0, img.width, int(img.height * 0.62)))
            if max(img.size) > 1024:
                r = 1024 / max(img.size)
                img = img.resize((int(img.width * r), int(img.height * r)))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=88)
            parts.append(_types.Part.from_bytes(data=buf.getvalue(),
                                                mime_type="image/jpeg"))
        parts.append(prompt)
        resp = client.models.generate_content(
            model=model_name, contents=parts,
            config=_types.GenerateContentConfig(response_mime_type="application/json"))
        t = (resp.text or "").strip()
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        data = json.loads(t)
        # The model sometimes returns a per-frame LIST — collapse to the worst hit.
        if isinstance(data, list):
            hits = [d for d in data if isinstance(d, dict) and d.get("face_defect")]
            if hits:
                worst = next((d for d in hits if (d.get("severity") or "").lower() == "major"), hits[0])
                return {"face_defect": True, "severity": worst.get("severity", "minor"),
                        "worst_frame": worst.get("worst_frame", 0),
                        "detail": worst.get("detail", "")}
            return {"face_defect": False, "severity": "none", "worst_frame": 0, "detail": ""}
        return data if isinstance(data, dict) else {
            "face_defect": False, "severity": "none", "worst_frame": 0, "detail": ""}
    except Exception as e:
        log.warning("face integrity check failed: %s", e)
        return {"face_defect": False, "severity": "none", "worst_frame": 0, "detail": ""}


# Tokens that mark a caption as narrating an archive clip's time-distance
# ("그때 / N년 전 / 아기 시절 / 자랐어요" …). Covers KO + EN. A single hit
# ANYWHERE in the episode's captions proves the era-mix is narrated as memory-
# lane and the temporal gate stands down.
_TEMPORAL_TOKENS = (
    # KO — unambiguous time-distance phrases. Korean substring-matches, so bare
    # nouns are dangerous: "아가" hits "닮아가는", "아기" hits "아기자기", "자라"/"컸"
    # hit unrelated verbs. We keep only tokens that don't collide, and space-
    # guard the baby words ("아기 레오" matches; "아기자기" does not). Bias toward
    # FIRING — a missing auto-pass token only over-flags, which PD prefers to a
    # rubber-stamp.
    "년 전", "년전", "개월 전", "개월전", "달 전", "주 전", "일 전",
    "그때", "그 때", "그땐", "그시절", "그 시절", "시절", "예전", "옛날", "과거",
    "어릴", "어렸", "아기 ", "아가 ", "새끼 ", "꼬꼬마", "갓난", "갓 태어",
    "작년", "재작년", "처음 만", "만나기 전", "만나기전", "처음 왔",
    "자랐", "커버린", "자라서", "자라났", "세월", "옛", "추억",
    # EN — only unambiguous time-distance phrases. (Bare "baby"/"little"/"grew"
    # false-match present captions: "a little", "baby steps", "grew quiet".)
    "ago", "back then", "back when", "used to", "younger", "as a baby",
    "as a puppy", "as a kitten", "grew up", "years back", "as a pup", "as a kit",
)


def _temporal_grounding_gate(concept: dict | None, report: dict) -> None:
    """Deterministic era-mix gate (PD 2026-06-23).

    Giri (the LLM reviewer) kept rubber-stamping era-mix episodes — clips
    spanning years (baby-Leo 2017 + present, puppy-Ryani + now) cut together as
    if one moment — at 9/10 "업로드", because a sparse frame sample CANNOT reveal a
    clip's capture date and the LLM defaults to praise. Adding rule TEXT to the
    prompt did not fix it (regression: still 9/10). So the signal is computed in
    CODE and fed to the verdict as a boolean — the giri-update core principle:
    deterministic enforcement beats trusting the LLM on something it can't see.

    Logic: read each cut's source-asset captured_iso from the DB, measure the
    date span across the episode, and scan every caption for a time-distance
    token. Span > 1 year WITH NO temporal token anywhere = an un-narrated
    era-mix → cap score ≤5 and force 수정 필요 (blocks auto-publish). Era-mix that
    IS narrated ("그땐 아기였는데 지금은~") passes untouched — narration is the fix,
    not avoidance. Best-effort: any missing data → no-op (never false-positive)."""
    if not concept:
        return
    cuts = concept.get("cuts") or []
    if len(cuts) < 2:
        return
    import datetime as _dt
    aids = [c.get("asset_id") or c.get("secondary_asset_id") for c in cuts]
    aids = [a for a in aids if a]
    if len(aids) < 2:
        return
    try:
        con = sqlite3.connect(str(ROOT / "data" / "agent.db"))
        try:
            qs = ",".join("?" * len(aids))
            rows = con.execute(
                f"SELECT asset_id, captured_iso, subjects_csv FROM assets "
                f"WHERE asset_id IN ({qs})",
                aids,
            ).fetchall()
        finally:
            con.close()
    except Exception as e:
        log.warning("temporal gate DB read failed: %s", e)
        return
    dates = []          # all dated cuts
    leo_dates = []      # dates of cuts that contain Leo
    for _aid, iso, subj in rows:
        if not iso:
            continue
        try:
            d = _dt.date.fromisoformat(str(iso)[:10])
        except Exception:
            continue
        dates.append(d)
        if "leo" in (subj or "").lower():
            leo_dates.append(d)
    if len(dates) < 2:
        return
    span_days = (max(dates) - min(dates)).days

    # Two ways an episode reads as an un-narrated era-mix:
    #
    # (1) GENERAL — any two source clips are > 1 year apart. Catches archive
    #     mixes like puppy-Ryani (2016) cut against present, where the same
    #     animal is visibly a different age.
    #
    # (2) LEO KITTEN FAST-GROWTH — Leo (born 2025-09-25) changes dramatically
    #     month-to-month in his first year, so a flat 1-year rule misses him.
    #     PD flagged ep 030752 as "아기 레오 era-mix": a 4.4-month-old kitten Leo
    #     clip (Feb) cut into a "오늘도…" present montage with 8-month Leo — only
    #     ~4 months by date, but a baby vs a grown cat on screen. So when the
    #     YOUNGEST Leo clip shows a clear kitten (< 6 months) AND the Leo clips
    #     span > ~2.5 months, the visible age jump is real. Recent episodes use
    #     today's ~10-month Leo (youngest > 6mo) so this never false-fires on them.
    _LEO_BORN = _dt.date(2025, 9, 25)
    general_fire = span_days > 365
    leo_fire = False
    if len(leo_dates) >= 2:
        youngest_leo_age = (min(leo_dates) - _LEO_BORN).days
        leo_span = (max(leo_dates) - min(leo_dates)).days
        if youngest_leo_age < 183 and leo_span > 75:
            leo_fire = True
    if not (general_fire or leo_fire):
        return
    span_years = round(span_days / 365.25, 1)

    # scan ALL caption text (ko + en, scene arrays + flat fields) for a token
    blob = []
    for c in cuts:
        caps = c.get("captions") or []
        if isinstance(caps, list):
            for sc in caps:
                if isinstance(sc, dict):
                    blob.append(str(sc.get("ko", "")))
                    blob.append(str(sc.get("en", "")))
                else:
                    blob.append(str(sc))
        for k in ("ko", "en", "caption", "time_ago_phrase"):
            if c.get(k):
                blob.append(str(c[k]))
    text = " ".join(blob).lower()
    grounded = any(tok.lower() in text for tok in _TEMPORAL_TOKENS)
    if leo_fire:
        _reason = (f"아기 레오({youngest_leo_age/30.4:.1f}개월) 클립과 "
                   f"{round(leo_span/30.4, 1)}개월 뒤 다 큰 레오 클립이 한 회차에 섞였습니다")
    else:
        _reason = f"클립 촬영일이 {span_years}년 차이로 벌어졌습니다"

    if grounded:
        report.setdefault("_temporal_gate", []).append(
            f"era-mix ({_reason}) — 캡션 시점 narration 확인됨 (pass)")
        log.info("temporal gate: era-mix but narrated → pass (%s)", _reason)
        return

    # un-narrated era-mix → deterministic defect
    note = (f"시점 미표기(결정론적 게이트): {_reason}. 그런데 캡션에 시점 토큰"
            f"(그때/N년 전/아기 시절/자랐어요 등)이 전혀 없어 다른 시기의 footage를 "
            f"한 순간처럼 이어 붙였습니다. 같은 시기 클립으로 통일하거나, 캡션에 "
            f"시점을 명시해 memory-lane으로 narration할 것.")
    prev = report.get("가장_큰_문제", "") or ""
    report["가장_큰_문제"] = note if (not prev or "없" in prev[:6]) else f"{note} / {prev}"
    try:
        report["점수"] = min(int(report.get("점수", 10)), 5)
    except Exception:
        report["점수"] = 5
    cur = report.get("판정", "")
    if cur in ("업로드", "즉시 업로드", "소폭 수정 후 업로드", ""):
        report["판정"] = "수정 필요"
    report["최종_결정"] = report.get("판정", "수정 필요")
    report["_temporal_gate_override"] = note
    log.info("temporal gate FIRED: %s → 판정=%s 점수=%s", note,
             report.get("판정"), report.get("점수"))


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

    client = _genai.Client(api_key=os.environ["GOOGLE_API_KEY"],
                           http_options=_types.HttpOptions(
                               timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
    model_name = os.getenv("VLM_MODEL", "gemini-2.5-flash")

    # Extract frames — sample per ACTUAL cut count so no cut is missed.
    n_cuts = 0
    if storyboard:
        n_cuts = len(storyboard)
    elif concept and isinstance(concept.get("cuts"), list):
        n_cuts = len(concept["cuts"])
    log.info("Extracting review frames from %s (n_cuts=%s)", video.name, n_cuts or "?")
    frames = _extract_frames(video, n_cuts=n_cuts or 4)

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
        marking_pass = sum([blaze_ok, eyebrow_ok, muzzle_ok])

        # PD 2026-06-08: the marking pixel-check + hard cap is for AI-RENDERED cuts
        # (ai_vtuber, or real_footage photo_i2v where Seedance can drift Ryani's
        # blaze). On a PURE real-clip rf episode the dog IS the real Ryani — her
        # markings are correct and the heuristic just false-negatives on real
        # angles → skip the cap. BUT if the rf episode has a photo_i2v (AI) cut,
        # keep the cap so drifted markings ARE gated (PD: "퀄리티 좋을 때만 i2v 줌인").
        _rs = (concept or {}).get("render_style", "")
        _has_photo_i2v = any(
            (c.get("source_hint") or "").strip().lower() == "photo_i2v"
            for c in (concept or {}).get("cuts", []))
        is_rf = _rs == "real_footage" and not _has_photo_i2v
        dims = report.get("dimensions", {})
        if is_rf:
            report.setdefault("_marking_overrides", []).append(
                f"real_footage(순수 실제클립) — 마킹 하드캡 미적용. 픽셀신호 pass={marking_pass}/3")
        else:
            # PD 2026-06-09: the pixel marking heuristic (이마줄 밝기차 etc.) is
            # UNRELIABLE — it false-negatives on perfectly good AI renders too (it
            # measures brightness in a narrow region, can't see a thin blaze on a
            # greying senior dog / odd angle / lighting). It was forcing "수정 필요" +
            # needless re-work on EXCELLENT episodes (202307: holistic 9/10 + 즉시
            # 업로드 + a genuinely THIN correct blaze, yet capped to char=3 + reworked).
            # Markings are now enforced at RENDER time by the per-cut reference-image
            # blaze gate (_cut_character_ok); this reviewer's pixel check is therefore
            # ADVISORY ONLY — it records a note (so PD sees the signal) but does NOT
            # cap the score or force a verdict change. PD's per-episode veto + the
            # render gate are the real marking enforcement.
            llm_says_clear = bool(report.get("ryani_markings_clear", True))
            report.setdefault("_marking_overrides", []).append(
                f"마킹 픽셀신호 pass={marking_pass}/3 (blaze={'✓' if blaze_ok else '✗'} "
                f"눈썹={'✓' if eyebrow_ok else '✗'} 주둥이={'✓' if muzzle_ok else '✗'}) "
                f"— ADVISORY only (불신뢰 휴리스틱; 렌더 게이트+PD veto가 실제 게이트). "
                f"LLM markings_clear={llm_says_clear}. 점수/판정 영향 없음.")
    except Exception as e:
        log.warning("Character similarity check failed: %s", e)

    # Face-integrity gate (PD 2026-06-10): focused call catches AI face corruption
    # (melted face / mismatched eyes / floating orb) that the marking check AND the
    # holistic review both miss — markings can read 'correct' on a melted face. A
    # major defect FAILS the episode; a minor one caps the score + downgrades verdict.
    try:
        fi = _check_face_integrity(client, model_name, frames, _types)
        report["face_integrity"] = fi
        sev = (fi.get("severity") or "none").lower()
        detail = fi.get("detail", "") or ""
        # A clear, describable artifact (orb/blob/melted/distorted/asymmetric) must
        # NOT auto-publish even if the VLM softly labels it "minor" — PD's hard rule
        # on face accuracy. Vague minors stay a downgrade-but-pass.
        is_artifact = bool(re.search(
            r"orb|blob|dot|melt|smear|distort|warp|deform|asymmetr|mismatch",
            detail, re.IGNORECASE))
        if fi.get("face_defect") and (sev in ("minor", "major") or is_artifact):
            fail = (sev == "major") or is_artifact
            note = (f"AI 얼굴 무결성 결함({'major' if fail else sev}): {detail}"
                    f" [frame {fi.get('worst_frame')}]")
            prev = report.get("가장_큰_문제", "") or ""
            report["가장_큰_문제"] = note if (not prev or "없" in prev[:6]) else f"{note} / {prev}"
            cap = 5 if fail else 7
            try:
                report["점수"] = min(int(report.get("점수", 10)), cap)
            except Exception:
                report["점수"] = cap
            cur = report.get("판정", "")
            if fail:
                report["판정"] = "수정 필요"   # NOT in GIRI_PASS → won't auto-publish
            elif cur in ("업로드", "즉시 업로드"):
                report["판정"] = "소폭 수정 후 업로드"
            report["최종_결정"] = report["판정"]
            report["_face_integrity_override"] = note
            log.info("face-integrity gate: %s → 판정=%s 점수=%s", note,
                     report["판정"], report["점수"])
    except Exception as e:
        log.warning("Face integrity gate failed: %s", e)

    # Deterministic era-mix gate (PD 2026-06-23): catches un-narrated time-jumps
    # the LLM can't see in a sparse frame sample. Runs LAST so its 수정 필요 verdict
    # is authoritative over a softer LLM/face verdict.
    try:
        _temporal_grounding_gate(concept, report)
    except Exception as e:
        log.warning("Temporal grounding gate failed: %s", e)

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
