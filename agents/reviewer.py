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
import time
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("agents.reviewer")

# Review ffmpeg/ffprobe timeout (PD 2026-07-05). The GCP VM (e2-medium, 2 shared
# vCPU) runs Seedance renders + ffmpeg concurrently; a single review frame-extract
# ("-ss … -frames:v 1") that was fine on the Mac hit the old 10s ceiling and threw,
# aborting the Giri review → the slot failed for a NON-content reason. Configurable so
# a bigger machine can tighten it back. Not a substitute for right-sizing the VM.
FFMPEG_REVIEW_TIMEOUT = int(os.getenv("REVIEW_FFMPEG_TIMEOUT", "30"))

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
- **CLAIMED MOTION / PROGRESSION / SCENE-CHANGE MUST HAPPEN (PD 2026-07-02)**: the same "claimed action must be visible" principle is NOT limited to a discrete kick — it applies to ANY caption that asserts the subject MOVES or the scene PROGRESSES: 일어난다/일어섭니다(rises), 걸어간다/떠난다/나선다(walks off/leaves/heads out), 다가온다/도착(approaches/arrives), 어디로 간다·출동(goes somewhere), 장면·장소가 바뀐다(scene/location change), or any narrated EVENT (something happens) — AND equally to IN-PLACE motion the body never performs: 뒹굴다/구르다(rolls over), 기지개/길어진다(stretches out), 두리번/주변을 스캔(looks around/scans the room), 다리를 젓다/발 구르기(paddles/kicks). The pet does NOT have to LEAVE for the caption to lie. If the sampled frames show the subject STAYING PUT the whole cut — lounging, sitting, sleeping in the same spot, only shifting slightly, or completely motionless (a play-dead) — while the caption narrates rising, leaving, arriving, OR rolling/stretching/scanning it never does, that is a FABRICATED ARC, not normal "scene progression" — a CHECK 0 LIE. (Worked example: a cat lying dead-still with only its tail flicking, captioned "레오 뒹굴뒹굴 시작!" and "갑자기 주변 쓱-스캔!" — the body never rolled and never scanned; only the tail moved.) (Worked example that shipped at pass: an orange cat lounging in one sunspot for the entire clip, captioned "느릿느릿 기지개 켠 레오, 일어섭니다! → 햇살 구역은 이만, 비밀 탐험 시작 → 의자 틈으로 쏙, 탐정 레오 출동!" — the cat never rose, never left, never went anywhere.) Give narrated locomotion/transitions NO benefit of the doubt: if you do not actually SEE the movement or the scene-change across the frames, treat it as not happening → log each such cut in `caption_vs_clip_mismatches` and cap the score ≤5. This is separate from the kick check above but shares its ≤5 consequence.
- **주장된 상호작용·감정도 프레임과 일치해야 (CHECK 0 caption-vs-clip 확장)**: the motion rule above extends to two more fabrications the frames plainly contradict. (a) **Pet-to-pet interaction**: a caption claiming the two pets ENGAGE each other — 눈맞춤/눈빛으로 대화/서로 바라봄/티격태격/코인사 — must show them ORIENTED toward each other and engaged. If the frames show them side by side but facing AWAY or off at different angles (co-existing, not interacting), "눈빛으로 대화 중" is a fabrication. (b) **Emotional state contradicted by the action**: a pet that is clearly GROOMING, eating, sniffing, or sprawled out is self-absorbed/content — captioning it 시무룩/삐짐/외로워/슬픔 contradicts the frame (grooming ≠ sulking). Both are frame-visible lies → log in `caption_vs_clip_mismatches` and cap ≤5. (Give a plausibly-ambiguous mood the benefit of the doubt — flag only when the action clearly contradicts the claimed feeling or interaction.)
- **엔딩 호흡 — 갑작스럽지도, 늘어지지도 (PD 2026-06-22 + 2026-06-26 리텐션)**: the close must LAND but stay TIGHT — there are two failure poles. (a) Too abrupt: cuts to black the instant the final caption appears, the wink/closing line never gets a beat to register → flag "갑작스러운 종료 — 마지막 여운 부족". (b) Too draggy — and per the retention data this is now the BIGGER leak: viewers are won early but bleed in the back half (~15s onward, below the average Short), so a long slow-mo closer (a 7-8s linger, tempo 0.7-0.8), dead air after the wink lands, or post-payoff cuts that re-tread the same beat all extend exactly the tail people are leaving during → flag "후반 늘어짐 — 페이오프 이후 새그/긴 슬로우 클로저". The target is a tight satisfying button: the payoff visible by ~midpoint, the back half lean, the wink landing fast (closer ~3-5s, tempo 1.0, a gentle push-in) and ending. Whichever pole applies, the verdict cannot be "업로드" (cap ≤7). (Length itself is NOT the test — a dense long episode is fine; a back half that goes loose/repeats after the payoff is the leak.)
- **캡션 가독 시간 (PD 2026-06-22, real_footage)**: each caption must stay up long enough to actually READ both KO + EN at a phone glance. Dense narration is good (see CAPTION DENSITY) ONLY if each line is still readable — density must never mean captions flipping faster than a viewer can finish reading. If lines are crammed so tight you couldn't read them, flag "캡션 표시 시간 부족 — 못 읽음" and cap ≤6.
- **캡션이 안 바뀜 — 하나가 너무 오래 (real_footage, PD 2026-07-08)**: RF는 캡션이 이야기를 끌어가므로, 캡션은 클립의 동작 순간마다 바뀌어야 한다. 샘플한 프레임 대부분이 **같은 캡션 한 줄**을 그대로 달고 있으면(긴 클립인데 처음부터 끝까지 캡션이 거의 안 바뀜) 그 자체로 결함 — flag "캡션이 안 바뀜 — 하나가 너무 오래" and cap ≤6. (이건 96번의 '너무 빨리 바뀜'과 반대 극. 읽을 시간은 주되, 한 줄로 15초를 버티면 안 된다.)
- **표면·장소·목적지 그라운딩 (PD 2026-07-08)**: 캡션이 특정 표면이나 장소/목적지를 명시하면 프레임의 실제와 반드시 일치해야 한다. 펫이 어디에 있고 어디로 가는지는 프레임에 분명히 보인다. 두 축 모두 CHECK 0 거짓이다 → caption_vs_clip_mismatches에 기록하고 cap ≤5. (a) **표면은 서로 대체 불가**: 소파 ≠ 침대 ≠ 방석 ≠ 맨바닥(마루/타일/잔디). "침대"라는데 실제로 소파거나, "침대"라는데 맨 바닥이면 거짓 — 같은 '푹신한 면'끼리라도 소파를 침대라 하면 틀린 것이다. (b) **명명된 목적지/장소도 정체가 맞아야 한다**: 창가 ≠ 현관 ≠ 부엌 ≠ 문 앞. 레오가 현관 쪽으로 가는데 캡션이 "창가"라고 하면, 이동은 맞아도 목적지 정체가 거짓이다.
- **A caption-vs-clip mismatch requires FRAME EVIDENCE — do NOT fabricate one (PD 2026-07-23, critical reviewer-accuracy fix, parallel to the sparse-sampling note in CHECK 0).** A `caption_vs_clip_mismatches` entry is ONLY valid when the frames you can SEE UNAMBIGUOUSLY CONTRADICT the caption — a claimed kick/motion/scene-change (94-95) or surface/place (99) that plainly isn't happening. Three false-positive patterns are FORBIDDEN because they wrongly emptied good slots:
  1. **Pet-ABSENCE inferred from sparse frames.** Both pets are often in frame together, and per-scene pet-visibility + caption grounding is ALREADY enforced upstream by the deterministic real_footage grounding gate (2 frames per scene). You review a sparse whole-episode sample — you CANNOT prove a named pet is absent. Do NOT log a mismatch because you don't spot a named pet in your sampled frame (it may be elsewhere in the frame/scene), nor because the caption names a pet that isn't the frame's *main* action while it is still present (a cat sitting on the table captioned as "on the table" is CORRECT even if a person is petting the dog in the same shot). Worked example: a cafe cut captioned "레오, 식탁 위 냥-오브제" was wrongly flagged "레오 없음" and the slot emptied — the orange cat was plainly on the table.
  2. **Deviation from the STORYBOARD.** Judge caption-vs-CLIP truthfulness, NEVER caption-vs-storyboard. The burned caption is deliberately grounded to what the clip ACTUALLY shows (the upstream grounding gate rewrites captions to match reality, overriding the storyboard intent). A caption that differs from the storyboard beat but matches the clip is CORRECT — do NOT flag it or deduct for it.
  3. **An INVENTED backstory the caption "should" have told.** Judge the caption against the FRAMES and THIS episode's own concept ONLY — NEVER against a richer narrative you imagine or pattern-match from OTHER recent episodes. A caption that truthfully describes what the frames show is CORRECT even if you think a deeper story (a memory, a reunion, an off-screen cause, another pet tagging along) "should" be there. "Missing narrative depth / doesn't mention <event>" is NOT a caption-vs-clip mismatch and must NOT lower the 캡션 score or the verdict — a mismatch requires the caption to CONTRADICT the frames, not to omit a story that is nowhere in them. Do NOT assert "PD 정보/사실에 따르면 …" to manufacture a required narrative unless that fact is in the appearance/character canon you were given AND is visible or clearly implied in THIS clip. Worked example that was wrongly capped 캡션 4/10 → 수정 필요: a clip of the cat's first grass walk was penalized because the reviewer decided "PD info says he recognized a past place and cried, and the dog came along" — that backstory belonged to a DIFFERENT episode and appears nowhere in this clip's frames or concept; the captions correctly narrated the actual walk.
  Default: if you are INFERRING absence/mismatch/missing-story rather than SEEING a contradiction, it is NOT a mismatch. Give a present-pet, frame-truthful caption the benefit of the doubt; reserve the mismatch flag (and its score cap) for the clear frame-visible lies above.
- For each Caption-vs-Clip failure, list in `caption_vs_clip_mismatches` (one entry per cut), and STATE the concrete frame-visible contradiction (what the caption claims vs what the frame shows) — an entry with no cited frame evidence is not a valid mismatch.
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
- **era-mix 인과성 없음 (real_footage)**: a time anchor is necessary but NOT sufficient.
  Even WITH "○년 전"/"지금은" captions, if two different-era clips are just stitched
  together with no causal/story link — e.g. a recent 먹방 cut followed by an unrelated
  8-years-ago 친구 만남 — the story breaks ("왜 이게 이어지지?"). A coherent episode is ONE
  event/flow, or an era-mix bound by an explicit cause→effect / then-now memory-lane arc.
  If the cuts read as unrelated clips from different times bolted together, flag
  "인과성 없는 era-mix — 무관 클립 나열" and cap ≤6 (verdict ≤ 수정 필요), even if the
  timeframe is captioned.
- **배경/공간 드리프트 (ai_vtuber, single-space concepts)**: a single-space concept's room
  must stay the SAME across cuts. If the background unintentionally changes between cuts
  — especially the closer suddenly in a different room — flag "배경 드리프트", cap ≤6.
  This includes **furniture morph**: a single piece (sofa, table) visibly stretching,
  elongating, or warping between cuts — typically from a shot_size jump (one cut going
  wider re-generates room area Seedance never saw, e.g. a sofa stretching past the couch
  end). Same defect, same cap ≤6 → flag "가구/배경 morph".
- **소품(상호작용 오브젝트) 드리프트 (ai_vtuber)**: a physical prop the pets HANDLE across cuts
  — a toy, a ball, a snack — must keep the SAME identifiable shape/type in every cut it appears.
  i2v regenerates an unanchored prop per cut and it morphs (the actual defect: a teal dumbbell-
  shaped tug toy became a round rubber ball mid-episode; a stick snack becoming a pellet is the
  same failure). Compare the prop across ALL cuts that show it — not one frame — and if its
  shape/type changes, flag "소품 드리프트" and cap ≤6. Why it matters: the story says the pets
  play with / eat the SAME object, so a prop that changes shape mid-episode breaks that
  continuity. (Interactive-prop cousin of 가구 morph above; RF real footage never morphs, so
  this is AV-only.)
  (EXCEPTION: a concept's INTENTIONAL space transition — 현실→상상→현실, a deliberate
  fantasy realm like 무릉도원 — is NOT drift; do not penalize a scripted scene change.)
- **펫 순간이동 / 위치 불일치 (ai_vtuber)**: distinct from background drift (that's the
  ROOM). Here a PET jumps to an unexplained new position cut-to-cut — at the door in one
  cut, asleep on a scratcher in the background of the next, back at the door in a third —
  with no shown move. A viewer reads it as the pet teleporting. If a pet's location changes
  cut-to-cut without a cut that shows the move, flag "펫 순간이동/위치 불일치", cap ≤6. This
  INCLUDES being ON a surface then gone from it: a pet on the sofa/bench/piano in one cut must
  still be there (or be shown getting down) in the next — e.g. Leo on the sofa behind, then
  Leo simply not on the sofa when Ryani is in front (PD 2026-06-30). Check each pet's perch
  across consecutive cuts, not just the floor position.
- **Off-cast 펫 (ai_vtuber)**: a cut centered on ONE pet (a solo close-up) must not show the
  OTHER pet — especially drifting/resting in the background. The other pet appearing unbidden
  in a single-subject cut is an off-cast defect (관찰왕: a Ryani-only close-up with Leo asleep
  on a scratcher behind her). Flag "off-cast 펫(단독 컷에 다른 펫)", cap ≤6. (A cut that
  legitimately features BOTH pets is fine — judge against what the cut is about.)
- **빌드업 후 페이오프 미발생 (both — structural)**: the episode spends its cuts building
  toward a climax (someone arrives, the treat is served, the chase connects) but the payoff
  never visibly HAPPENS on screen — it cuts from the tension straight to the wink/closer, so
  it "builds up and then nothing happens" (관찰왕: footsteps-tension → wink, the 하비 greeting
  reunion was never a cut). The payoff is the point; if the buildup has no on-screen payoff
  cut, flag "페이오프 미발생 — 빌드업만" and cap ≤6 (verdict ≤ 수정 필요). This is structural,
  separate from the caption-promised-kick check (CHECK 0): there a single caption's action is
  faked; here the whole arc's payoff beat is missing.
- **사건 무마 / 전제 부정 (both — closer defuses the payoff)**: the reverse of the above — the
  payoff DID happen on screen, but the captions SOOTHE it away or NEGATE the premise. When the
  footage shows a reaction or event (a treat taken/gone, a toy stolen, a startle, one pet reacting
  while the other plays innocent), the closer must LAND that reaction, not re-label it as calm
  ("둘만의 평온한 리듬", "껌딱지 모드") and never negate the very thing that drove the scene ("먹거리만이
  답은 아니죠" when the snack IS the plot). A closer that defuses the reaction or contradicts the
  driving event kills the gag → flag "사건 무마/전제 부정 — 반응을 재워버림", cap ≤6 (verdict ≤ 수정 필요).
- **배경 흔들림 (ai_vtuber)**: distinct from drift. When the camera moves (panning / push-in /
  handheld sway) under big subject motion, Seedance makes the BACKGROUND wobble, warp or
  shake WITHIN a cut — which reads worse than a dead-still frame. The channel standard is a
  locked, static camera + static background (energy from pet motion). If the background
  visibly shakes/warps from camera movement, flag "배경 흔들림(카메라 무빙)", cap ≤7.
  EXCEPTION: the closing wink's gentle push-in is fine (one subject, little background).
- **정적 이야기 (both — HARD CAP, not a soft penalty)**: if the episode is flat observation
  — pets mostly still, no action/progression, cuts interchangeable — it is NOT shippable;
  score ≤6, verdict 수정 필요. An action/explore concept (e.g. spy) with no actual
  exploring/searching is a static FAIL. (Intentional surreal physics is fine and separate
  — a surreal episode can still be statically boring; judge MOTION/progression here.)
- **주체 저노출/트림 누락 (both)**: if a pet the concept centers on (or that PD wants more
  of — e.g. Leo) barely appears, OR the clip's payoff (the subject entering/acting) is
  trimmed off so the good part never shows → flag "주체 저노출/트림", cap ≤6.
- **훅 묻힘 — 사건이 뒤로, 정적이 앞 (real_footage)**: RF 편집의 첫 판단은 "가장 볼거리 있는 비트를
  앞세웠나"다. 한 컷은 뭔가 벌어지는 사건(먹방·물놀이·놀이·간식 쟁탈·재롱)이고 다른 컷은 정적(자는·눕는·
  멍하니 앉음)인데, **오프닝(cut1)이 정적 컷이고 더 사건성 있는 비트가 뒤에 짧게 묻혀 있으면** 볼거리가
  다 지난 뒤에야 훅이 나와 이미 이탈한 뒤다 → flag "훅 묻힘 — 사건을 앞·길게, 정적을 뒤로", cap ≤6.
  (Worked example: 레오 청어 먹방을 맨 뒤 10초에 묻고 발라당 누운 정적 컷을 앞 30초로 깐 회차 — 먹방이
  이 소재의 훅인데 숨었다.) 어느 컷이 능동적 사건이고 어느 컷이 정적인지는 프레임에 보인다. ★구분: 전 컷이
  다 잔잔한 관찰이면 이 캡은 안 쓴다(그건 '킥 부족'/'RF 내용 빈약' 축) — 사건 컷이 분명히 존재하는데 순서가
  뒤로 밀린 경우에만.
- **주체 원거리·정체 불명 — 매력 부족 (real_footage)**: RF의 주인공은 레오·랴니이므로 그게 **누구인지
  프레임에서 또렷이 읽혀야** 한다. 피사체가 너무 작고 멀거나(원거리 wide/overhead) 배경 잡동사니에 묻혀
  **레오·랴니인지조차 분간이 안 되고 "관계없는 아무 길고양이/강아지"처럼 보이면** → flag "주체 원거리·정체
  불명 — close 클립 필요", cap ≤6. (Worked example: 랴니가 멀찍이 작게 걸어가는 원거리 산책 클립 → PD
  "아예 관계없는 길고양이 같다".) ★구분: 주체가 또렷이 우리 애로 읽히는데 단지 넓은 구도인 건 문제 아님 —
  정체가 안 읽힐 때만 문다('주체 저노출'과도 다름: 저노출은 거의 안 나옴, 이건 나오지만 너무 작아 정체불명).
- **테마/비주얼 훅이 화면에 없음 — 캡션-only 테마 (ai_vtuber)**: if the concept's title/captions
  promise a distinctive THEME or visual hook (카지노·잭팟, 무대, 우주, 폭죽 등) but the frames
  show only ordinary pets with NO sign of that theme (no themed prop/set/effect/overlay), that
  is a caption-vs-content / style mismatch → flag "테마 미표시(캡션-only)", cap ≤6. A theme
  delivered via a post-render overlay effect (e.g. a JACKPOT marquee with bursting confetti
  composited in) DOES count as shown; only fail when the theme is nowhere on screen.
- **컨셉/포맷이 캡션으로 안 읽힘 (ai_vtuber)**: the episode has a named theme / format /
  challenge (read `theme` / `narrative_oneliner` — 댄스 챌린지·월드컵 응원·카지노 등) but the
  captions never ESTABLISH what it is — the opening caption only narrates motion ("오늘의
  도전자, 랴니와 레오!") instead of naming the format, so a cold viewer can't tell it's that
  concept and the in-concept gag (칼각 vs 엇박 등) has no frame to land in. Flag "컨셉
  미전달(캡션이 포맷 안 세움)", cap ≤6. This is the COMPLEMENT of the theme-visual check above:
  there the SCREEN lacks the theme; here the CAPTIONS fail to name it — a concept needs both
  (named in the opening caption AND shown on screen). An episode shipped 8/10 with a
  `댄스 챌린지` concept whose captions never said "댄스 챌린지" — exactly this miss.
- **쇼케이스 스포트라이트 오프레이밍 (양레인, 특히 ai_vtuber)**: 컨셉이 한 주체의 쇼/실력/자랑/도전(예:
  "레오의 참치 해체쇼", "랴니 칼각 댄스", "○○의 먹방왕 등극")이면 빌드업·절정 캡션의 스포트라이트가 그
  **퍼포머의 솜씨·활약**에 있어야 한다. 절정(payoff) 캡션이 엉뚱하게 **다른 주체의 '운명/위기'로 프레이밍**되면
  (퍼포머 쇼케이스인데 조연의 불길한 운명을 물음) 스포트라이트가 딴 데로 새고 컨셉에 없던 불길한 톤이 얹힌다 →
  flag "스포트라이트 오프레이밍 — 퍼포머 아닌 조연 위기로", cap ≤6. (Worked example: "오마카세 셰프 레오의
  참치 해체쇼"의 해체 직전 컷을 "과연 랴니의 운명은?"으로 써 스포트라이트가 셰프 레오 실력에서 손님 랴니 위기로
  샜다 — "과연 레오의 실력은?"이 옳다.) ★구분: 조연이 payoff를 받는 결말(랴니가 완성된 한 점을 대접받음)은
  퍼포머의 성과를 완성하는 정상 전개라 캡 사유 아님 — 절정 자체를 조연의 위기/운명으로 바꾼 캡션만 문다.
- **제목·theme이 말이 안 되는 조어 (양레인)**: the `title`/`theme` (and any concept name a
  caption states) must read as real, instantly-comprehensible Korean. If it hangs on a coined
  or invented word / forced mashup a cold viewer can't parse (지어낸 조어·억지 합성어), the hook
  fails no matter how well the content matches — the viewer can't tell what the video is in the
  2-second glance. This is a SEPARATE axis from the caption-vs-content checks: 내용과 맞아도
  말이 안 통하면 실패다. Flag "제목/컨셉 조어 — 말이 안 통함", cap ≤6. (Worked example: an
  episode titled "레오의 관조봇 따라하기" — '관조봇'은 없는 말이라 레오가 무엇을 따라하는지
  안 잡힌다; "명상하는 누나 따라하기"처럼 실재어면 통한다.)
- **테마 과장 — 잔잔한 영상을 배틀/축제로 부풀림 (양레인, SOFT 노트)**: if the `title`/`theme`
  frames the footage with far more energy than it actually has — a calm cool-off / nap / 나란히
  쉼 hyped as a 워터밤·전쟁·대결·챌린지 while the frames show a quiet mundane moment — the promised
  tension isn't on screen and it reads as clickbait. This is honest-framing, not a coinage. Note
  it in `개선점` (e.g. "테마 과장 — 화면은 잔잔한데 제목이 배틀/축제로 부풀림; 실제 결에 맞춰라")
  and, if the over-hype is blatant, lean the verdict toward 소폭 수정. Keep it SOFT — do NOT
  hard-cap on a merely lively-but-fair title; reserve the note for a clear energy mismatch
  (worked example: a quiet at-home 더위 식히기 titled "방구석 워터밤").
- **AV가 자기 레인을 정당화 못함 — 무훅·무사건 (ai_vtuber, PD 2026-06-30 / 재범위화 2026-07-04)**:
  이건 **CONTENT(스토리) 결함**이지 LOOK(실사 여부) 결함이 절대 아니다. ★혼동 금지: ai_vtuber가
  photoreal·실사처럼·real_footage처럼 보이는 것, 일상 소재인 것은 **감점 사유가 아니다**(오히려 정상 —
  아래 STYLE RULES 참조, PD가 lo-fi 퀄리티로 결정). 이 규칙이 잡는 건 오직 **끝까지 볼 이유가 없는
  저자극 '무사건 나열'** — 원인→행동→결과→리액션도, 대비도, 반전·payoff도 하나 없이 그냥 평범한 펫
  행동(창밖 보기, 어슬렁, 나란히 쉼, 서로 쳐다봄, 장난감 깨작)만 훑고 끝나는 경우. 그럴 때만 flag
  "AV 무훅 — 스토리·payoff 없음", **cap ≤5, verdict ≤ 수정 필요**. 판단 기준 = theme/oneliner의 컨셉이
  화면에서 실제로 **아크(설정→전개→payoff)**로 펼쳐지며 '왜 끝까지 봐야 하나'에 답을 주는가. 답을
  주면 **소재가 일상이어도, 실사처럼 보여도 통과**한다(예: 고양이가 도도하게 세수 vs 강아지가 놀자고
  조름 → 결국 부비며 화해 = 명확한 대비+payoff = 통과). 상상/판타지·불가능 갸그는 훅을 만드는 **한 방법일
  뿐 필수가 아니다**. (PD 2026-06-30: "hook이 없잖아" = 이야기가 없다는 뜻이지 실사처럼 보인다는 뜻이
  아니었다.) 이 cap은 무-스토리를 못 잡고 러버스탬프하던 구멍만 막는다 — 실사 LOOK을 잡는 게 아니다.
  **이 판단을 `story_arc_present`에 반드시 명시하라**: 설정→전개→payoff가 실제로 펼쳐지면 true, 무사건
  나열이면 false. 홀리스틱 점수에 묻지 말고 이 필드로 또렷이 답하라 — false면 시스템이 자동으로 cap≤5로
  내린다(러버스탬프 방지). "거실이 커진다면", "~하는 이유" 같은 추상 무드·가정형 컨셉이 화면에서 구체 사건으로
  전개되지 못하면 false다.
- **RF도 자기 레인을 정당화해야 — 단발 소재는 부적합 (real_footage, PD 2026-08-02)**: 이건 AV
  무훅 캡의 real_footage 짝이다. RF의 일은 '실제 순간'을 관찰자 내레이션으로 끝까지 보게 만드는 것 —
  그런데 클립이 **하나의 얇은 갸그(한 동작의 반복)뿐이고 원인→행동→반응/전개·대비·변화가 전혀 없이**
  같은 장면만 20초를 끄는 경우(예: 더워서 혀를 내밀었다 넣었다만 반복 — 그 이상 아무 일도 없음), 캡션을
  아무리 붙여도 볼 이유가 안 생긴다. 그럴 때 flag "RF 내용 빈약 — 단발 소재(스토리 없음)", cap ≤6,
  verdict ≤ 수정 필요. 판단 기준 = 이 클립이 시작→전개→마무리의 작은 아크나 뚜렷한 대비/반전을 담고
  있는가. 담고 있으면(짧아도) 통과 — 얇은 단발이면 컨셉을 다시 짜거나 AV로 돌리는 게 맞다. (일상의
  잔잔함 자체는 결함이 아니다 — '아무 사건도 없는 한 동작 반복'만 잡는다.)
- **랴니 목뒤 흰마킹 금지 (ai_vtuber, PD 2026-08-02)**: 랴니의 목뒤(nape)·척추·등은 canon상 **순검정**
  이다 — 흰색은 오직 이마 블레이즈(얇은 선)·턱·목앞→가슴·발끝뿐. i2v가 목 뒤에 흰 점/패치를 만들면
  그건 삐용이(턱시도)의 흰색이 번진 것이라 틀렸다. 목뒤/등/척추에 흰 점·줄이 보이면 flag "랴니 목뒤
  흰마킹" 하고 캐릭터 충실도를 낮춰라(목앞·턱·가슴 흰색은 정상이니 절대 감점 금지). (결정론 nape 게이트가
  실제 enforcer지만, 프레임에 또렷이 보이면 홀리스틱에서도 반드시 짚어라.)
- **최근 회차와 컨셉 중복 — 우려먹기 금지 (both lanes, PD 2026-08-02)**: 아래 컨텍스트의 "최근 공개/예약
  회차" 목록과 이 회차의 theme/컨셉을 비교하라. 소재·구도·훅이 최근(특히 하루 이틀 사이) 회차와 사실상
  같은데 새로운 각도가 없으면(예: 이틀 연속 '거실에서 서로 눈치/평행 무빙 밈') flag "최근 회차와 컨셉
  중복 — 우려먹기", cap ≤6. ★예외: **의도된 시리즈/후속편**(제목이 '~1탄/2탄', 명시적 시즌·연작)이며
  실제로 새 장면·새 전개를 담으면 우려먹기가 아니다 — 통과. 즉 라벨이 아니라 '새 내용이 있는가'로 가른다.
- **상상 컷이 상상으로 안 읽힘 (ai_vtuber)**: a beat meant as a daydream/상상 (the caption says
  so, or the action is impossible-on-purpose) must READ as imagination — a dreamy look
  (misty haze OR a vivid, luminous, magical dreamscape — a wonder-fantasy SHOULD look lush
  and saturated, not faded; don't demand desaturated-misty) and/or a clear "○○의 상상 속!"
  label. If a fantasy beat is rendered identical to
  the reality cuts with no marker (or a "sleeping/dreaming" caption sits over an obviously
  wide-awake pet) so a viewer can't tell it's imagined, note "상상/현실 구분 불명확" and lower
  caption/style; do not pass it as "업로드". This marks intent — it is NOT a penalty on the
  surreal content itself.

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
- **사실적으로 보이는 건 절대 감점 사유가 아니다 — 오히려 정상 (양 레인, PD 2026-07-04, 절대)**: 채널의
  ai_vtuber 룩은 **의도적으로 photoreal lo-fi**다 — 실제 폰으로 찍은 실사처럼 보이게 만들어 real_footage
  레인과 자연스럽게 섞이도록 한 것(PD가 lo-fi 퀄리티로 결정). 따라서 **ai_vtuber 결과물이 "너무 실사 같다 /
  real_footage처럼 보인다 / AI 티가 안 난다 / 요청한 툴 체인/스타일과 불일치"는 결함이 아니며, visual_style
  점수나 판정을 이 이유로 절대 낮추지 마라.** 이는 리뷰어의 흔한 오판이다(실제로 AV18 아침루틴을 "고품질
  실사라 ai_vtuber 스타일 불일치"로 style 2/10 주고 reject → 렌더는 정상인데 슬롯이 빌 뻔한 사고). "ai_vtuber"
  라벨은 **제작 방식**(프레임을 생성)이지 결과가 인공적으로 보여야 한다는 요구가 아니다. AI 생성이든
  photoreal이든 **룩만으로는 절대 거부·감점하지 마라** — 캐릭터/테마/스토리로만 판단하라.
- Only reject if: wrong characters, wrong theme, content from a different episode, or completely off-topic.
- **BGM**: Must match the concept's mood. Cozy concept = gentle/lofi BGM. Fun concept = playful/upbeat BGM. Do NOT use epic/cinematic/orchestral BGM for cute pet content.
- **Cut repetition**: If multiple cuts show the same pose/scene/background, that is a MAJOR issue. Every cut must be visually distinct. Penalize heavily for repeated scenes.
- **Storytelling check**: Unusual scenes are OK if there's a story behind them. Penalize only if NO narrative context.
- **INTENTIONAL SURREAL HOOK — DO NOT PENALIZE (PD 2026-06-11, important)**: ai_vtuber is ENCOURAGED to defy real-world physics when the impossibility IS the hook — that is the channel's signature fun, not a defect. GOOD intentional surrealism (score the hook HIGHER, never lower): 랴니가 거실에서 수영(a dog swimming across the living-room floor), pets floating, indoor rain/snow, a room filling with water. Do NOT write "비현실적/물리 법칙 위반/말이 안 됨" as a problem when the concept or caption FRAMES it as a playful fantasy — that is the SINGLE MOST COMMON reviewer mistake, and a hook like 거실 수영 must be REWARDED as the opening/emotional hook, not flagged. The test: **"Could a human animator have drawn this ON PURPOSE as a fun gag?"** If YES → it's an intentional hook, ALLOW it and reward it.
  - **세면대 범람 → 서핑 = GOOD example, but the MECHANIC must be coherent (PD 2026-06-11)**: the correct, ALLOWED version is — a sink MOUNTED AT COUNTER HEIGHT overflows, the water cascades DOWN and floods the living-room floor, and Ryani/Leo surf on that flood. That is a great hook → reward it. The FORBIDDEN version is a glitch: the sink BASIN itself sitting ON THE FLOOR (grounded at floor level). Same scene, two outcomes: high sink + overflow + flood + surf = HOOK (allow); sink basin on the floor = DEFECT (penalize). Judge which one rendered.
  - This is DIFFERENT from a BROKEN RENDER (still a real defect, still penalize): geometry/anatomy that is GLITCHED rather than fantastical — a melted/orb/dissolving face, an extra or merged limb, a character half-fused into furniture, drift to a different breed, OR a fixture grounded incoherently (the floor-sink above). These look like the model malfunctioned, not like a deliberate fun image. Penalize those normally.
  - Rule of thumb: physics-defying-but-cleanly-drawn = HOOK (allow); incoherent/glitched/ugly = DEFECT (penalize).
- **Caption tone — 기본은 발랄, 도사체는 결함 (cap ≤6)**:
  - 채널 캡션의 기본 에너지는 **발랄·생기** — 친구가 옆에서 신나게 중계하는 톤. 화면이 활발하면 캐주얼+느낌표("또 시작이네!", "이건 못 참지"), 진짜 조용한 컷(잠·멍때림)만 나직하게. 동물농장식 관찰 위트는 OK지만 그것도 *발랄하게*, 점잖게가 아니다.
  - **도사·시인·잠언·설교·이력서체 = 결함.** 다음이 보이면 `개선점`에 flag + **점수 ≤6, 판정 "수정 필요"**:
    - 잠언/관조 시구: "여유란 이런 것", "인생이란…", "두 마음은 삐끗 어긋난 자리", "~하는 법", "이건 사냥 본능이에요" 식 설교·설명체
    - 이력서/연륜 라벨 남발: "N년 경력", "N년차 ~", "베테랑 프로토콜", "~ 모드 ON"을 한 영상에서 반복 (랴니 연륜 언급은 1회까지만 OK)
    - 점잖은 여운으로 전체가 무겁게 깔림 — 모든 줄이 "~인가 봐요/~한 모양입니다/~듯합니다"면 도사체다 (추측형은 차분한 컷의 *양념*일 뿐, 전 컷 디폴트면 감점)
  - 여전히 PENALIZE: 밋밋한 묘사 캡션("소파에 앉아있다", "레오의 반응", "놀자 신호를 보냈습니다") — 묘사는 화면이 한다.
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
- **Size consistency**: Leo has grown to roughly Ryani's size — present-day cuts should read them as COMPARABLE (a kitten-tiny Leo beside adult Ryani is a defect, "baby-Leo drift"). Only when the episode references an EARLIER time period should Leo read noticeably smaller (past growth segments).

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
  "story_arc_present": true | false,
  "story_arc_reason": "한 문장 — 설정→전개→payoff가 화면에서 실제로 펼쳐지면 true, 원인/대비/반전/payoff 하나 없이 평범한 펫 행동만 훑는 '무사건 나열'이면 false",
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
            capture_output=True, text=True, timeout=FFMPEG_REVIEW_TIMEOUT)
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
            capture_output=True, timeout=FFMPEG_REVIEW_TIMEOUT,
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
        capture_output=True, text=True, timeout=FFMPEG_REVIEW_TIMEOUT,
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


def _check_ryani_nape(client, model_name, frames, _types) -> dict:
    """PD 2026-08-02: Ryani's NAPE (back of neck) / spine / back are canon SOLID BLACK —
    her white is ONLY the thin forehead blaze, chin, FRONT-of-throat→chest patch and toes.
    Seedance/i2v intermittently hallucinates a white spot/patch on the BACK of her neck
    (삐용이's tuxedo marking bleeding over). The render-time gate (_cut_character_ok) samples
    only a few frames and let a white-nape AV ship; the holistic reviewer had NO nape lens at
    all, so it rubber-stamped it at 9/10. This is the reviewer-side deterministic backstop: a
    FOCUSED call (undivided attention — the proven don't-bundle lesson) comparing the rendered
    Ryani's nape against her clean ryani_solo.png reference. Fail-open (no defect) on error /
    no ref. AV-scoped by the caller (real footage of the real dog already has a black nape).
    Returns {nape_white, worst_frame, detail}."""
    from PIL import Image
    ref = ROOT / "assets" / "character_ref" / "ryani_solo.png"
    try:
        parts = []
        for fp in frames:
            img = Image.open(fp)
            if img.mode != "RGB":
                img = img.convert("RGB")
            if max(img.size) > 1024:
                r = 1024 / max(img.size)
                img = img.resize((int(img.width * r), int(img.height * r)))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=88)
            parts.append(_types.Part.from_bytes(data=buf.getvalue(),
                                                mime_type="image/jpeg"))
        ref_note = ""
        if ref.exists():
            parts.append(_types.Part.from_bytes(data=ref.read_bytes(),
                                                mime_type="image/png"))
            ref_note = (" The LAST image is the REFERENCE of the black French Bulldog "
                        "(Ryani): her nape, spine and back are SOLID BLACK; her white is "
                        "ONLY the thin forehead blaze, chin, front-of-throat→chest patch "
                        "and toes.")
        prompt = (
            "These are frames from an animal video featuring a black French Bulldog "
            "(Ryani) and an orange tabby cat." + ref_note + " Examine the DOG. Her BACK, "
            "the NAPE (back of the neck / behind the head) and spine must be PURE BLACK. "
            "Set nape_white=true ONLY if you clearly see a WHITE spot, dot, patch, stripe "
            "or line on the BACK of her neck / nape / spine / back in a frame where that "
            "area is clearly visible. Her FRONT-of-throat / chin / chest white is CORRECT "
            "— do NOT flag that. If her back/nape is black, or is not clearly visible, set "
            'false. Return ONLY JSON: {"nape_white": true|false, "worst_frame": <1-based '
            'int, or 0>, "detail": "<where the white appears, or empty>"}.')
        parts.append(prompt)
        resp = client.models.generate_content(
            model=model_name, contents=parts,
            config=_types.GenerateContentConfig(response_mime_type="application/json"))
        t = (resp.text or "").strip()
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        data = json.loads(t)
        if isinstance(data, list):
            hit = next((d for d in data
                        if isinstance(d, dict) and d.get("nape_white")), None)
            data = hit or {"nape_white": False, "worst_frame": 0, "detail": ""}
        return data if isinstance(data, dict) else {
            "nape_white": False, "worst_frame": 0, "detail": ""}
    except Exception as e:
        log.warning("nape-white check failed: %s", e)
        return {"nape_white": False, "worst_frame": 0, "detail": ""}


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


def _caption_mismatch_gate(concept: "dict | None", report: dict) -> None:
    """Make `caption_vs_clip_mismatches` DETERMINISTIC (PD 2026-08-17). The rule "≥2 mismatches →
    cap≤5, and a mismatch needs frame evidence" lived ONLY in the prompt, so the LLM both INVENTED
    mismatches and SELF-applied the cap — a fully non-deterministic path that false-rejected good RF
    and let real lies score inconsistently. Three deterministic steps:

    1) SCRUB the pet-identity/pet-absence class for real_footage. "cut3's caption is about Leo but the
       frame shows Ryani" / "레오 없음" is the documented sparse-frame hallucination
       ([[giri_false_reject_frame_evidence]]): 2 frames/cut can't reliably tell which pet is on screen,
       and the deterministic RF grounding gate is the authoritative caption-frame check. Removed, never caps.
    2) ENFORCE ≥2-real-mismatch → cap≤5 in CODE on the SURVIVING (surface/place/motion/kick) entries,
       so a genuine lie is caught regardless of the LLM's self-scoring.
    3) RELIEF: if RF and the low verdict rested SOLELY on now-scrubbed identity/absence hallucinations
       (수정필요, 점수≤5, zero surviving mismatches, story present, no OTHER deterministic gate fired),
       lift to 소폭 수정(점수≥6) so a hallucination can't empty a good slot. GIRI_MISMATCH_GATE=0 reverts."""
    import os as _os
    if _os.getenv("GIRI_MISMATCH_GATE", "1") != "1":
        return
    ms = report.get("caption_vs_clip_mismatches")
    if not isinstance(ms, list):
        return
    is_rf = (concept or {}).get("render_style", "") == "real_footage"

    def _pets(t):
        t = (t or "").lower()
        p = set()
        if "레오" in t or "leo" in t:
            p.add("leo")
        if "랴니" in t or "ryani" in t or "라니" in t:
            p.add("ryani")
        return p

    def _is_identity_absence(e):
        shows = str(e.get("what_clip_actually_shows") or "")
        low = shows.lower()
        if re.search(r"없|안\s*보|보이지\s*않|부재|등장하지\s*않|not\s+(visible|present|there|shown)"
                     r"|no\s+(leo|ryani|cat|dog)|absent|wrong\s+(pet|subject)", low):
            return True
        cp, sp = _pets(e.get("caption_text")), _pets(shows)
        # caption implies one pet, clip-desc asserts the OTHER (the "about Leo but shows Ryani" pattern)
        if cp and sp and cp != sp and (sp - cp) and \
           re.search(r"아니|대신|not |instead|실제로|actually|위에|shown\s+over|표시", low):
            return True
        return False

    # PD 2026-08-25 (B23): the INVENTED-backstory class. Giri conflates a DIFFERENT recent
    # episode's story with this clip and logs every cut as a mismatch for not telling that
    # narrative ("PD 정보에 따르면 레오가 과거 장소에 돌아와 울었다 … 캡션에 반영 안 됨"). This is an
    # OMISSION-of-imagined-story, not a frame contradiction — a real surface/motion/kick mismatch
    # cites the concrete frame, never a missing backstory. Scrub entries whose justification is
    # narrative-omission (prompt rule alone didn't hold — the LLM re-invented it, so gate it).
    _BACKSTORY = re.compile(
        r"배경\s*스토리|더\s*깊은\s*이야기|서사(가|를|적|\s|가\s*빠|\s*누락)|스토리(가|를)?\s*(빠|누락|반영|담기지)"
        r"|반영(되지|하지|이\s*안|\s*안)|누락|과거(에|의|\s*장소|\s*기억|\s*상황)|돌아(온|와|간)|재회|재방문"
        r"|감정(적|선)|감동|기억(을|이|하는|나는)|pd\s*(정보|가\s*제공|제공)|narrative|back-?story|deeper\s+story",
        re.IGNORECASE)

    def _is_backstory_omission(e):
        blob = " ".join(str(e.get(k) or "") for k in
                        ("what_clip_actually_shows", "why", "reason", "mismatch", "note", "설명"))
        return bool(_BACKSTORY.search(blob))

    scrubbed, kept = [], []
    for e in ms:
        if not isinstance(e, dict):
            continue
        (scrubbed if (is_rf and (_is_identity_absence(e) or _is_backstory_omission(e)))
         else kept).append(e)
    if scrubbed:
        report["caption_vs_clip_mismatches"] = kept
        report["_mismatch_scrubbed"] = [
            f"cut{e.get('cut_number', '?')}: {str(e.get('what_clip_actually_shows'))[:60]}" for e in scrubbed]
        log.info("caption-mismatch gate: scrubbed %d RF pet-identity/absence hallucination(s)", len(scrubbed))

    if len(kept) >= 2:  # deterministic cap on REAL (frame-grounded) mismatches
        report["점수"] = min(int(report.get("점수", 10) or 10), 5)
        if report.get("판정") in ("업로드", "즉시 업로드", "소폭 수정 후 업로드"):
            report["판정"] = "수정 필요"
        report["최종_결정"] = report.get("판정")
        _note = f"캡션-클립 불일치 {len(kept)}건(프레임근거) — 사실과 어긋남"
        _prev = report.get("가장_큰_문제", "") or ""
        report["가장_큰_문제"] = _note if (not _prev or "없" in _prev[:6]) else f"{_note} / {_prev}"
        report["_mismatch_cap_override"] = _note
        log.info("caption-mismatch gate: %d real mismatches → cap≤5", len(kept))
        return

    # false-reject relief — only when scrubbed identity hallucination was the SOLE basis
    if (is_rf and scrubbed and not kept
            and report.get("story_arc_present") is not False
            and str(report.get("판정", "")).strip() == "수정 필요"
            and int(report.get("점수", 10) or 10) <= 5
            and not any(k.endswith("_override") for k in report.keys())):
        prob = str(report.get("가장_큰_문제") or "")
        _prob_scrubbable = bool(re.search(
            r"위에\s*표시|표시되는|아니라|대신|shown\s+over|wrong\s+(pet|subject)"
            r"|(레오|랴니|leo|ryani)\s*[가는이]?\s*(없|안\s*보|보이지\s*않|not\s+(visible|present|there))",
            prob.lower())) or bool(_BACKSTORY.search(prob))
        if (not prob.strip()) or _prob_scrubbable:
            report["점수"] = max(int(report.get("점수", 0) or 0), 6)
            report["판정"] = "소폭 수정 후 업로드"
            report["최종_결정"] = report["판정"]
            report["_mismatch_false_reject_relief"] = prob[:80]
            log.info("caption-mismatch gate: RF false-reject relief (scrubbed identity "
                     "hallucination was sole basis) → 판정=%s 점수=%s", report["판정"], report["점수"])


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
        # ONLY scan what actually gets burned on screen. NOT time_ago_phrase —
        # that is _stamp_years_ago's INTERNAL knowledge of the gap, not a visible
        # caption; including it made the gate think every dated archive clip was
        # "narrated" and pass (false negative on ep 034500's 2016-17 footage).
        for k in ("ko", "en", "caption"):
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


# Deterministic 도사체(잠언·설교·이력서) caption patterns — high-confidence only.
# These phrasings almost always signal the preachy/sage register PD rejects, so we
# bias toward PRECISION (a short list of near-certain hits) over recall: a good
# playful caption must never trip it. Fuzzy/poetic cases ("두 마음은 삐끗 어긋난
# 자리") are left to the LLM 발랄 cap in CHECK 0 — this gate is the guarantee for
# the unambiguous ones.
_PREACHY_PATTERNS = (
    # 잠언/관조 "X란 ~" 정의체
    "여유란", "인생이란", "삶이란", "사랑이란", "행복이란", "산다는 건", "산다는 것",
    # 설교/교본체
    "하는 법", "사는 법", "의 미학",
    # 이력서/연륜 라벨
    "년 경력", "베테랑 프로토콜",
    # EN
    "the art of", "years of experience", "veteran protocol", "the meaning of",
    "a lesson in",
)


def _preachy_caption_gate(concept: dict | None, report: dict) -> None:
    """Deterministic 도사체(preachy/sage) caption gate (PD 2026-06-27).

    PD repeatedly rejects captions that drift into a 도사·시인·잠언·이력서 register
    ("여유란 이런 것", "7년 경력") — the channel voice is 발랄, not a wise elder. The
    generator prompts now guard it, but Giri rubber-stamped such captions as
    "동물농장 톤 충족". A handful of these phrasings are unambiguous, so we detect
    them in CODE and cap the verdict; the LLM rubric covers the fuzzier poetic
    cases. High-confidence patterns only → no false fail on a good playful line."""
    if not concept:
        return
    cuts = concept.get("cuts") or []
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
        for k in ("ko", "en", "caption"):
            if c.get(k):
                blob.append(str(c[k]))
    text = " ".join(blob).lower()
    hits = sorted({p for p in _PREACHY_PATTERNS if p.lower() in text})
    if not hits:
        return
    note = (f"도사체 캡션(결정론적 게이트): {', '.join(hits)} — 잠언·설교·이력서체는 "
            f"채널 보이스가 아니다. 발랄·캐주얼하게 다시 써라 "
            f"(예: '여유란 이런 것'→'여기 완전 편하지', '7년 경력'→'역시 우리 랴니').")
    prev = report.get("가장_큰_문제", "") or ""
    report["가장_큰_문제"] = note if (not prev or "없" in prev[:6]) else f"{note} / {prev}"
    try:
        report["점수"] = min(int(report.get("점수", 10)), 6)
    except Exception:
        report["점수"] = 6
    if report.get("판정", "") in ("업로드", "즉시 업로드", "소폭 수정 후 업로드", ""):
        report["판정"] = "수정 필요"
    report["최종_결정"] = report.get("판정", "수정 필요")
    report["_preachy_gate_override"] = note
    log.info("preachy gate FIRED: %s → 판정=%s 점수=%s", hits,
             report.get("판정"), report.get("점수"))


# Deterministic 호칭/나이 canon violations — high-confidence only (precision over recall).
# Canon: 랴니=11살 여자(누나/엄마, '랴니엄마') · 레오=8개월 남자 막내(랴니를 엄마로 여김). 등장인물이
# 이 둘뿐이라 연상 남자('오빠/형')는 존재하지 않는다 → 캡션의 '오빠/형아/형님'은 항상 관계 역전.
_HONORIFIC_VIOLATIONS = (
    (r"오빠|형아|형님", "'오빠/형' 호칭 — 연상 남자 캐릭터가 없음 (레오=막내 남, 랴니=여자 누나/엄마)"),
    (r"레오(?:는|가|은|이|도|만)?\s*(?:베테랑|시니어|노령|senior|맏이)|(?:베테랑|시니어|노령|senior|맏이)\s*레오",
     "레오를 연장자/시니어로 — 레오는 8개월 막내"),
    (r"랴니(?:는|가|은|이|도|만)?\s*(?:막내|신참)|(?:막내|신참)\s*랴니",
     "랴니를 막내/신참으로 — 랴니는 원조 첫째, 레오가 막내/신참"),
)

# "아기/꼬맹이 랴니" is NOT a fixed violation like 막내/신참: Ryani genuinely WAS a baby
# (2016–2017, before Leo existed 2025-09), so a memory-lane caption may correctly say "아기
# 랴니". It's a canon reversal ONLY in the PRESENT — flag it just when NO time-distance marker
# frames it as past. (This is the era-fact vs reversed-hierarchy distinction PD called out.)
_RYANI_BABY_PAT = r"랴니(?:는|가|은|이|도|만)?\s*(?:아기|꼬맹이)|(?:아기|꼬맹이)\s*랴니"
# TIME-DISTANCE markers only — deliberately EXCLUDES the baby-descriptor words ("아기 "/"아가 "/
# "새끼 ") that _TEMPORAL_TOKENS carries, because "아기 랴니" must not self-exempt. A hit here means
# the caption frames the footage as PAST, so a baby-Ryani mention is era-fact, not a reversal.
_PAST_ERA_MARK = (
    "년 전", "년전", "개월 전", "개월전", "달 전", "그때", "그 때", "그땐", "그시절", "그 시절",
    "시절", "예전", "옛날", "과거", "어릴", "어렸", "갓난", "갓 태어", "작년", "재작년",
    "만나기 전", "만나기전", "처음 왔", "처음 만", "자랐", "커버린", "자라서", "세월", "옛", "추억",
    "ago", "back then", "back when", "used to", "younger", "as a baby", "as a puppy",
    "as a kitten", "grew up", "years back", "as a pup", "as a kit",
)


def _canon_honorific_gate(concept: "dict | None", report: dict) -> None:
    """Deterministic canon honorific/age gate (PD 2026-07-04, AV18 '랴니가 막내 레오를 오빠라 부름').

    caption_agent.md already states the rule (랴니 POV의 레오='막내/레오', 레오 POV의 랴니='랴니엄마';
    랴니=senior, never 막내/아기) — but the LLM ignored it and wrote "오빠, 나랑 놀자!" (Ryani, the
    11yr elder, calling the 8mo youngest 오빠), and Giri never checked honorifics → it shipped 9/10.
    So enforce in CODE. High-confidence patterns only so a good caption never trips it."""
    if not concept:
        return
    cuts = concept.get("cuts") or []
    blob = []
    for c in cuts:
        caps = c.get("captions") or []
        if isinstance(caps, list):
            for sc in caps:
                blob.append(str(sc.get("ko", "")) if isinstance(sc, dict) else str(sc))
        for k in ("ko", "en", "caption"):
            if c.get(k):
                blob.append(str(c[k]))
    text = " ".join(blob)
    hits = [msg for pat, msg in _HONORIFIC_VIOLATIONS if re.search(pat, text)]
    # "아기/꼬맹이 랴니" only violates canon in the PRESENT — allow it as memory-lane era-fact
    # when a time-distance marker (그때/N년 전/아기 시절/ago…) frames the footage as past.
    if re.search(_RYANI_BABY_PAT, text) and not any(t in text for t in _PAST_ERA_MARK):
        hits.append("현재 시점에 랴니를 아기/꼬맹이로 — 랴니는 11살(과거 아기 시절은 'N년 전' 등 시점을 "
                    "명시하면 허용)")
    if not hits:
        return
    note = ("호칭/나이 canon 역전(결정론적 게이트): " + " / ".join(hits) +
            ". 등장인물은 랴니(11살 여자 누나·엄마, '랴니엄마')와 레오(8개월 남자 막내)뿐 — '오빠/형'은 "
            "존재하지 않는다. 레오→랴니='랴니엄마/누나', 랴니→레오='막내/레오'로 고쳐라.")
    prev = report.get("가장_큰_문제", "") or ""
    report["가장_큰_문제"] = note if (not prev or "없" in prev[:6]) else f"{note} / {prev}"
    try:
        report["점수"] = min(int(report.get("점수", 10)), 5)
    except Exception:
        report["점수"] = 5
    if report.get("판정", "") in ("업로드", "즉시 업로드", "소폭 수정 후 업로드", ""):
        report["판정"] = "수정 필요"
    report["최종_결정"] = report.get("판정", "수정 필요")
    report["_honorific_gate_override"] = note
    log.info("honorific gate FIRED: %s → 판정=%s 점수=%s", hits,
             report.get("판정"), report.get("점수"))


_COINED_GATE_SYS = (
    "You judge ONE Korean YouTube-Shorts title/theme for COMPREHENSIBILITY. A viewer must "
    "grasp what the video is in a 2-second glance. FLAG only genuinely OPAQUE coined words / "
    "invented mashups whose meaning does not come across (지어낸 조어·뜻이 안 통하는 억지 "
    "합성어). Example to flag: '관조봇' (관조+봇 — not a real word, a viewer can't tell what it "
    "means). ALLOW and do NOT flag: real standard Korean words; proper names / pet names "
    "(삐용이, 남산이); this channel's fixed family terms (랴니엄마 = what Leo calls Ryani, "
    "함미/하비 = grandma/grandpa); loanwords (챌린지, 브이로그); and understandable playful compounds whose "
    "meaning is obvious (간식 심사위원, 낮잠 요정, 꼬리 댄스). When in doubt that a viewer WOULD "
    "understand it, do NOT flag — precision over recall. Return ONLY JSON: "
    "{\"coined\":[\"<opaque word>\", ...], \"comprehensible\": true|false}.")


def _coined_concept_gate(concept: dict | None, report: dict) -> None:
    """Deterministic-ish coined-concept gate (both lanes): the title/theme must be real,
    instantly-understood Korean — a coined nonsense word ('관조봇') makes the hook unreadable
    in the 2s Shorts glance. The holistic reviewer rubber-stamps such titles (관조봇 shipped
    9/10), so a focused single-purpose classifier on JUST the title/theme text fires reliably
    where the whole-episode rubric doesn't. Fail-safe: any API/parse error → no-op (never a
    false fail from an infra hiccup); precision-tuned to allow proper names + clear compounds."""
    if not concept:
        return
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return
    fields = [str(concept.get(k, "")) for k in ("title", "theme", "narrative_oneliner")]
    text = " · ".join(f for f in fields if f).strip()
    if not text:
        return
    try:
        from google import genai as _g
        from google.genai import types as _gt
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        resp = client.models.generate_content(
            model=os.getenv("VLM_MODEL", "gemini-2.5-flash"),
            contents=[f"제목/테마: {text}"],
            config=_gt.GenerateContentConfig(
                system_instruction=_COINED_GATE_SYS, response_mime_type="application/json",
                thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
        d = json.loads((resp.text or "{}").strip())
    except Exception as e:
        log.warning("coined-concept gate skipped (fail-safe): %s", e)
        return
    coined = [w for w in (d.get("coined") or []) if isinstance(w, str) and w.strip()]
    if not coined and d.get("comprehensible", True) is not False:
        return
    note = ("제목/컨셉 조어(결정론적 게이트): " + ", ".join(coined or ["말이 안 통하는 제목"]) +
            " — 뜻이 안 통하는 지어낸 말이라 2초 안에 무슨 영상인지 안 잡힌다. 실재하고 바로 "
            "이해되는 말로 고쳐라 (예: '관조봇 따라하기'→'명상하는 누나 따라하기').")
    prev = report.get("가장_큰_문제", "") or ""
    report["가장_큰_문제"] = note if (not prev or "없" in prev[:6]) else f"{note} / {prev}"
    try:
        report["점수"] = min(int(report.get("점수", 10)), 6)
    except Exception:
        report["점수"] = 6
    if report.get("판정", "") in ("업로드", "즉시 업로드", "소폭 수정 후 업로드", ""):
        report["판정"] = "수정 필요"
    report["최종_결정"] = report.get("판정", "수정 필요")
    report["_coined_gate_override"] = note
    log.info("coined-concept gate FIRED: %s → 판정=%s 점수=%s", coined,
             report.get("판정"), report.get("점수"))


# False "first swim/water" framing about Ryani (PD 2026-06-28). Ryani is a lifelong
# water-maniac / strong swimmer (canon), so any caption that frames a swim/water beat
# as her FIRST is a factual lie — even on genuine 2016 baby footage (that era she was
# briefly clumsy, not afraid, and never "first"). The correct angle is the progression
# "처음엔 서툴렀는데 이젠 완벽 적응 / 물에서 안 나오려 함". Patterns are multiword + water-
# specific so the progression phrasing ("처음엔 서툴렀는데") never trips this gate.
_FALSE_FIRST_WATER_PATTERNS = (
    "첫 수영", "첫 수영장", "첫 물놀이", "첫 풍덩", "첫 입수", "첫 물", "첫 다이빙",
    "물에 처음", "처음 물에", "처음 만나는 수영", "처음 만나는 물", "생애 첫 수영",
    "인생 첫 풍덩", "인생 첫 수영",
    "first swim", "first splash", "first dip", "first pool",
    "first time in the water", "first time swimming", "first plunge",
)


def _false_first_water_gate(concept: dict | None, report: dict) -> None:
    """Deterministic gate: RF captions must not call a Ryani swim/water beat her FIRST.

    Canon (agents/canon.py) establishes Ryani as a veteran '펠프스급' swimmer, so a
    '첫 수영/인생 첫 풍덩' hook is a lie the Writer keeps inventing for old (2016) pool
    footage. The generator prompts + canon now forbid it, but Giri rubber-stamped it as
    a cute memory-lane hook, so we cap it in code. Scoped to real_footage (the lane PD
    flagged); high-precision multiword water phrases only, so the canon-correct
    progression wording ('처음엔 서툴렀는데 이젠 완벽') is never falsely failed."""
    if not concept:
        return
    if (concept.get("render_style") or "") != "real_footage":
        return
    cuts = concept.get("cuts") or []
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
        for k in ("ko", "en", "caption"):
            if c.get(k):
                blob.append(str(c[k]))
    for k in ("title", "narrative_oneliner"):
        if concept.get(k):
            blob.append(str(concept[k]))
    text = " ".join(blob).lower()
    hits = sorted({p for p in _FALSE_FIRST_WATER_PATTERNS if p.lower() in text})
    if not hits:
        return
    note = (f"랴니 '첫 수영/첫 물' 거짓 프레이밍(결정론적 게이트): {', '.join(hits)} — 랴니는 "
            f"평생 물 매니아·펠프스급 수영선수라 '첫 수영'은 사실이 아니다(옛 2016 클립도 마찬가지). "
            f"'처음엔 서툴렀는데 이젠 완벽 적응 / 물에서 안 나오려 함' 진행형으로 다시 써라.")
    prev = report.get("가장_큰_문제", "") or ""
    report["가장_큰_문제"] = note if (not prev or "없" in prev[:6]) else f"{note} / {prev}"
    try:
        report["점수"] = min(int(report.get("점수", 10)), 5)
    except Exception:
        report["점수"] = 5
    if report.get("판정", "") in ("업로드", "즉시 업로드", "소폭 수정 후 업로드", ""):
        report["판정"] = "수정 필요"
    report["최종_결정"] = report.get("판정", "수정 필요")
    report["_false_first_water_override"] = note
    log.info("false-first-water gate FIRED: %s → 판정=%s 점수=%s", hits,
             report.get("판정"), report.get("점수"))


def _pd_groundtruth_block(concept: dict | None) -> str:
    """PD per-clip ground-truth → Giri (PD 2026-06-23).

    PD repeatedly tells us what a clip ACTUALLY is — "the friend in this one is
    삐용이", "this is the day we changed Ryani's car seat", "Leo is throwing a
    tantrum to follow mom out". That context is the authoritative truth the
    captions must honor, but Giri only ever saw the VLM's guess + the storyboard,
    so it couldn't catch a caption that contradicts what PD said. This pulls each
    cut's asset `pd_notes` (the PD-authoritative override, the [PD]-prefixed
    ground truth) and hands it to the reviewer so caption-truthfulness is judged
    against PD's stated content, not just the VLM. Empty when no cut has a PD note."""
    if not concept:
        return ""
    cuts = concept.get("cuts") or []
    pairs = []  # (label, asset_id) in cut order
    for i, c in enumerate(cuts, 1):
        aid = c.get("asset_id") or c.get("secondary_asset_id")
        if aid:
            pairs.append((c.get("beat") or c.get("tag") or f"cut{i}", aid))
    if not pairs:
        return ""
    aids = [a for _, a in pairs]
    notes = {}
    try:
        con = sqlite3.connect(str(ROOT / "data" / "agent.db"))
        try:
            qs = ",".join("?" * len(aids))
            for aid, pdn in con.execute(
                    f"SELECT asset_id, pd_notes FROM assets WHERE asset_id IN ({qs})", aids):
                if pdn and str(pdn).strip():
                    notes[aid] = str(pdn).strip()
        finally:
            con.close()
    except Exception as e:
        log.warning("pd ground-truth read failed: %s", e)
        return ""
    if not notes:
        return ""
    lines = ["\n## PD ground-truth per clip (AUTHORITATIVE — PD told us what this clip "
             "actually is; it OVERRIDES the VLM tags and your own visual guess):"]
    for label, aid in pairs:
        if aid in notes:
            lines.append(f"  - {label}: {notes[aid]}")
    lines.append(
        "→ The captions MUST be consistent with these PD-stated facts (the named "
        "friend/person, the real event, what the pet is actually doing). A caption that "
        "contradicts, renames, or ignores PD's stated content for a clip is a CHECK 0 "
        "truthfulness failure — record it in 가장_큰_문제 and cap 점수 ≤5.")
    return "\n".join(lines)


# Off-brand GRANDIOSE / pompous caption register (PD 2026-07-05): the channel voice is
# 발랄·잔잔한 관찰, so epic/majestic diction ("웅장", "장엄", "서사시", "전설의") over everyday pet
# footage reads as pompous and out of place ("뭐가 웅장해? 그냥 자는 내용이잖아"). It's a REGISTER
# problem like the 도사체(preachy) gate — not about motion — so we catch the diction itself.
# Playful energy/locomotion ("우다다 출동", "탐험 시작") is fine and deliberately NOT here. RF-scoped
# (an AV fantasy concept may legitimately be grand). High-confidence words only. Cap ≤6, 수정 필요.
_GRANDIOSE_WORDS = (
    "웅장", "장엄", "웅대", "장대", "위대한", "대서사", "서사시", "대서사시", "전설의",
    "영웅적", "장중한", "숭고", "에픽", "epic", "장엄한", "웅장한",
)


def _caption_grandiose_gate(concept: "dict | None", report: dict) -> None:
    """RF-scoped deterministic register gate: grandiose/pompous caption diction ("웅장" 류) that
    clashes with the channel's casual 발랄 voice on everyday footage. Cap ≤6, verdict 수정 필요."""
    if not concept or (concept.get("render_style") or "") != "real_footage":
        return
    blob = []
    for c in concept.get("cuts") or []:
        caps = c.get("captions") or []
        if isinstance(caps, list):
            for sc in caps:
                blob.append(str(sc.get("ko", "")) if isinstance(sc, dict) else str(sc))
        for k in ("ko", "en", "caption"):
            if c.get(k):
                blob.append(str(c[k]))
    text = " ".join(blob)
    hits = sorted({w for w in _GRANDIOSE_WORDS if w in text})
    if not hits:
        return
    note = (f"과장·거들먹 어체(결정론): {', '.join(hits)} — 웅장·서사시 같은 register는 채널 보이스"
            f"(발랄·잔잔한 일상 관찰)에 안 맞고 화면(평범한 일상)과도 겉돈다. 담백·발랄하게 다시 써라 "
            f"(예: '웅장한 낮잠'→'세상 편한 낮잠').")
    prev = report.get("가장_큰_문제", "") or ""
    report["가장_큰_문제"] = note if (not prev or "없" in prev[:6]) else f"{note} / {prev}"
    try:
        report["점수"] = min(int(report.get("점수", 10)), 6)
    except Exception:
        report["점수"] = 6
    if report.get("판정", "") in ("업로드", "즉시 업로드", "소폭 수정 후 업로드", ""):
        report["판정"] = "수정 필요"
    report["최종_결정"] = report.get("판정", "수정 필요")
    report["_grandiose_gate_override"] = note
    log.info("grandiose caption gate FIRED: %s → 판정=%s 점수=%s", hits,
             report.get("판정"), report.get("점수"))


def _clip_reuse_gate(concept: "dict | None", report: dict) -> None:
    """Deterministic freshness gate (PD 2026-07-07): this episode reuses a clip that is
    ALREADY LIVE in a recently-published episode (last 7d). The 7/7 root — a re-render
    grabbed a clip from that day's public video because the clip-cooldown was inert. The
    generator now seeds that cooldown, but Giri is the guarantee: same footage live in two
    concurrent episodes = viewers see the identical clip twice. Cap ≤5, verdict 수정 필요.
    The episode under review isn't published yet, so no self-match.

    REAL_FOOTAGE ONLY (PD 2026-07-09): the "same footage on screen twice" premise holds
    only when asset_id IS the on-screen clip. For ai_vtuber, asset_id is a POSE/GENERATION
    reference — the same good reference photo is legitimately reused across episodes and each
    render is a fresh Seedance still, so no viewer-facing duplication. Firing here was a
    category error that killed whole AV batches (giri_fail after Seedance spend). Mixed-media
    AV real-clip cuts are covered by the generator's clip-cooldown seed, not this gate."""
    if not concept:
        return
    if (concept.get("render_style") or "") != "real_footage":
        return
    mine = {c.get("asset_id") or c.get("secondary_asset_id") for c in (concept.get("cuts") or [])}
    mine = {a for a in mine if a}
    if not mine:
        return
    try:
        import datetime as _dt
        con = sqlite3.connect(str(ROOT / "data" / "agent.db"))
        try:
            since = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()
            live: set = set()
            for (pj,) in con.execute(
                "SELECT payload_json FROM cards WHERE date >= ? AND youtube_video_id IS NOT NULL "
                "AND state!='archived'", (since,)).fetchall():
                try:
                    for c in (json.loads(pj or "{}").get("cuts") or []):
                        aid = c.get("asset_id") or c.get("secondary_asset_id")
                        if aid:
                            live.add(aid)
                except Exception:
                    continue
        finally:
            con.close()
    except Exception as e:
        log.warning("clip-reuse gate skipped: %s", e)
        return
    dup = sorted(mine & live)
    if not dup:
        return
    note = (f"클립 재사용(결정론): 이 편이 최근 7일 내 공개된 다른 에피소드가 이미 쓴 클립을 다시 썼다 "
            f"({', '.join(d[:28] for d in dup[:3])}) — 같은 footage가 동시에 여러 편에 살아 있으면 "
            f"시청자가 같은 영상을 두 번 본다. 아직 안 쓴 다른 클립으로 교체하라.")
    prev = report.get("가장_큰_문제", "") or ""
    report["가장_큰_문제"] = note if (not prev or "없" in prev[:6]) else f"{note} / {prev}"
    try:
        report["점수"] = min(int(report.get("점수", 10)), 5)
    except Exception:
        report["점수"] = 5
    if report.get("판정", "") in ("업로드", "즉시 업로드", "소폭 수정 후 업로드", ""):
        report["판정"] = "수정 필요"
    report["최종_결정"] = report.get("판정", "수정 필요")
    report["_clip_reuse_gate_override"] = note
    log.info("clip-reuse gate FIRED: %s → 판정=%s 점수=%s", dup[:3],
             report.get("판정"), report.get("점수"))


def _caption_scene_sets(concept: "dict | None") -> list:
    """(label, scenes) for each RF cut — ground truth is the captions.json actually BURNED
    (workdir), not the card payload, which can be stale/overwritten (e.g. a concurrent
    recaption). Falls back to concept.cuts[].captions. Handles both {scenes:[...]} and a
    flat scene list."""
    out = []
    caps = None
    try:
        from agents.caption_salvage import _find_work_dir
        cid = (concept or {}).get("card_id") or ""
        wd = _find_work_dir(cid) if cid else None
        if wd and (wd / "captions.json").exists():
            caps = json.loads((wd / "captions.json").read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("caption-hold gate: workdir captions read failed: %s", e)
    if isinstance(caps, dict):
        for k, v in caps.items():
            if k.startswith("_"):
                continue
            sc = v.get("scenes") if isinstance(v, dict) else (v if isinstance(v, list) else None)
            if sc:
                out.append((k, sc))
    if not out:
        for i, c in enumerate((concept or {}).get("cuts") or []):
            cap = c.get("captions")
            sc = None
            if isinstance(cap, list):
                sc = cap
            elif isinstance(cap, dict):
                sc = cap.get("scenes") or (cap.get("ko") if isinstance(cap.get("ko"), list) else None)
            if sc:
                out.append((c.get("tag") or c.get("beat") or f"cut{i+1}", sc))
    return out


def _caption_hold_gate(concept: "dict | None", report: dict) -> None:
    """Deterministic '캡션이 안 바뀜' (PD 2026-07-10). RF captions carry the story, so a single
    line held for a long stretch reads as static. The 7/8 rule for this was FRAME-JUDGED (VLM
    prompt) and got rubber-stamped two days later — Giri praised a 20.9s nap under ONE caption.
    So compute it from the burned captions.json instead: any single scene held ≥
    RF_CAPTION_MAX_HOLD_S seconds → cap ≤6, 수정 필요. RF only. (The generator should beat-split
    the clip's action arc; a static clip that can't be split shouldn't be a full episode.)"""
    if not concept or (concept.get("render_style") or "") != "real_footage":
        return
    hold_max = float(os.getenv("RF_CAPTION_MAX_HOLD_S", "8.0"))
    worst, worst_label = 0.0, ""
    for label, sc in _caption_scene_sets(concept):
        for s in sc:
            try:
                d = float(s.get("end", 0)) - float(s.get("start", 0))
            except Exception:
                continue
            if d > worst:
                worst, worst_label = d, label
    if worst < hold_max:
        return
    note = (f"캡션이 안 바뀜(결정론): {worst_label} 컷에서 캡션 한 줄이 {worst:.0f}초 동안 그대로 유지된다 "
            f"(≥{hold_max:.0f}s). RF는 캡션이 이야기를 끌어가므로 클립의 동작 순간마다 바뀌어야 한다 — 한 줄로 "
            f"긴 클립을 버티면 정적으로 읽힌다. 동작 arc를 순간별 beat로 쪼개 캡션을 여러 번 바꿔라.")
    prev = report.get("가장_큰_문제", "") or ""
    report["가장_큰_문제"] = note if (not prev or "없" in prev[:6]) else f"{note} / {prev}"
    try:
        report["점수"] = min(int(report.get("점수", 10)), 6)
    except Exception:
        report["점수"] = 6
    if report.get("판정", "") in ("업로드", "즉시 업로드", "소폭 수정 후 업로드", ""):
        report["판정"] = "수정 필요"
    report["최종_결정"] = report.get("판정", "수정 필요")
    report["_caption_hold_gate_override"] = note
    log.info("caption-hold gate FIRED: %s held %.1fs → 점수=%s", worst_label, worst,
             report.get("점수"))


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

    # PD per-clip ground-truth (PD 2026-06-23): captions must honor what PD said
    # each clip actually is (삐용이 friend / car-seat day / Leo's tantrum …).
    context += _pd_groundtruth_block(concept)

    # Recent-episode context for the cross-day concept-dedup check (PD 2026-08-02): give the
    # reviewer the themes of episodes published/scheduled in the last few days so it can flag
    # a near-rerun. An intentional 1탄/2탄 series is exempted in the rubric (not by this list).
    try:
        _rc = sqlite3.connect(ROOT / "data" / "agent.db")
        _this_id = str((concept or {}).get("card_id") or "")
        _recent = _rc.execute(
            "SELECT date, theme FROM cards WHERE youtube_video_id IS NOT NULL "
            "AND state != 'archived' AND date >= date('now','-3 day') "
            "AND card_id != ? ORDER BY date DESC LIMIT 12", (_this_id,)).fetchall()
        _rc.close()
        if _recent:
            context += "\n## 최근 공개/예약 회차 (컨셉 중복 체크용):\n"
            for _d, _th in _recent:
                context += f"  - [{_d}] {(_th or '')[:70]}\n"
    except Exception as e:
        log.warning("recent-episode context build failed: %s", e)

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

    # PD 2026-07-10: the Giri gate call had NO model fallback and NO retry — a single
    # transient Gemini 404 / 5xx (or a momentarily-unresolvable model name) failed the
    # whole review and stranded an ALREADY-RENDERED (paid ~$50) episode as giri_fail
    # with an empty slot (7/9 AV 08:00+18:00). The primary model itself is fine; this
    # only guards the intermittent case. Mirror the tag_assets fallback: retry transient
    # errors per-model, then fall through to the next model (gemini-flash-latest) on a
    # 404/non-transient. VLM_FALLBACK_MODELS / VLM_MAX_RETRIES tune it.
    _fallbacks = [m.strip() for m in os.getenv(
        "VLM_FALLBACK_MODELS", "gemini-flash-latest").split(",") if m.strip()]
    _models = [model_name] + [m for m in _fallbacks if m != model_name]
    _max_retries = int(os.getenv("VLM_MAX_RETRIES", "4"))
    _cfg = _types.GenerateContentConfig(response_mime_type="application/json")
    response = None
    _last_err = None
    for _mn in _models:
        for _attempt in range(_max_retries):
            try:
                response = client.models.generate_content(
                    model=_mn, contents=parts, config=_cfg)
                break  # got a response
            except Exception as e:  # noqa: BLE001
                _last_err = e
                response = None
                _transient = any(s in str(e) for s in (
                    "429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500",
                    "INTERNAL", "deadline", "timeout", "Timeout"))
                if _transient and _attempt < _max_retries - 1:
                    time.sleep(min(2 ** _attempt + 0.5, 20))
                    continue
                break  # non-transient (e.g. 404) or exhausted → try next model
        if response is not None:
            if _mn != model_name:
                log.warning("Giri VLM fell back to %s (primary %s failed: %s)",
                            _mn, model_name, _last_err)
            break
    if response is None:
        raise _last_err or RuntimeError("Giri VLM failed on all models")
    text = (response.text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    report = json.loads(text)

    # Merge audio into report
    report["audio"] = audio

    # Character similarity check — compare generated frames vs real Ryani/Leo photos
    # PD 2026-07-05: a real_footage episode with NO Seedance/AI cut is real clips of the
    # REAL Ryani — her markings are correct BY DEFINITION, and the pixel heuristic only
    # false-flags on real angles/lighting. Don't just skip the CAP (below) — skip the whole
    # check so it never runs, never prints "이마줄 ❌", and never sways the LLM review. Only
    # when RF actually uses Seedance (photo_i2v / interp / ref) can markings drift → check.
    _rs0 = (concept or {}).get("render_style", "")
    _has_ai_cut = any(
        (c.get("source_hint") or "").strip().lower() == "photo_i2v"
        or (c.get("seedance_mode") or "").strip().lower() in ("i2v", "interp", "ref")
        for c in (concept or {}).get("cuts", []))
    _pure_rf = _rs0 == "real_footage" and not _has_ai_cut
    try:
        char_sim = {"checks": {}} if _pure_rf else _check_character_similarity(frames, concept)
        report["character_similarity"] = char_sim
        if _pure_rf:
            report.setdefault("_marking_overrides", []).append(
                "real_footage(Seedance 미사용) — 마킹 픽셀검사 생략(실제 랴니)")

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
        # Two tiers of defect (PD 2026-07-12, after the face gate over-fired on a natural
        # belly-up sleeping pose — a soft muzzle read as "smeared" → hard-failed a clean,
        # real-home episode and wasted a $50 render):
        #  - STRUCTURAL collapse (melted/orb/blob/deformed/duplicated/mismatched-eyes) is
        #    unambiguous — it kills the episode ALONE, even if the holistic read is clean.
        #  - SOFT softness (smear/blend/distort/asymmetric/blur) is what the focused VLM
        #    over-calls on natural sleeping/belly-up poses. It kills ONLY when the MAIN
        #    holistic review did NOT independently call the episode upload-clean — i.e. two
        #    signals must agree before a borderline smear discards a paid render.
        _HARD_RE = (r"orb|blob|melt|deform|warp|fused|floating|duplicat|mismatch|"
                    r"extra[ -]?(eye|limb|leg|head|paw)")
        is_hard = bool(re.search(_HARD_RE, detail, re.IGNORECASE))
        is_soft = bool(re.search(
            r"smear|blend|distort|asymmetr|blurr|soft", detail, re.IGNORECASE))
        holistic_clean = str(report.get("판정", "")).strip() in ("업로드", "즉시 업로드")
        # A HARD defect fails the episode ALONE, overriding a clean holistic 9/10 — so it
        # must be REAL, not a flaky single-call VLM hallucination. This VLM face check is
        # non-deterministic: it intermittently reports a "floating orb/blob" (a light bokeh,
        # a lens highlight, a fantasy sparkle misread as an artifact) that FAILS a Giri-9 AV
        # and empties the slot, yet the SAME mp4 passes cleanly on re-review (the recurring
        # empty-AV-slot root). So CORROBORATE a hard defect with a second independent face
        # check before letting it alone-fail: a genuine structural collapse (melted face)
        # is persistent and both calls catch it; a flaky hallucination isn't confirmed and
        # is demoted (→ soft path, which still needs holistic disagreement to fail). This
        # preserves the "melted face can pass holistic" guard while killing the false fails.
        # AV_FACE_CORROBORATE=0 reverts to single-call alone-fail.
        if (is_hard and fi.get("face_defect")
                and os.getenv("AV_FACE_CORROBORATE", "1") == "1"):
            try:
                fi2 = _check_face_integrity(client, model_name, frames, _types)
                d2 = (fi2.get("detail") or "")
                hard2 = bool(fi2.get("face_defect") and re.search(_HARD_RE, d2, re.IGNORECASE))
                if not hard2:
                    log.info("face-integrity: hard defect NOT corroborated on 2nd check "
                             "(fi=%r fi2=%r) — demoting flaky hit", detail, d2)
                    is_hard = False
                    if not fi2.get("face_defect"):
                        fi["face_defect"] = False   # both-clean-ish → dismiss flaky orb
                        report["face_integrity"] = {**fi, "_corroboration": "cleared"}
            except Exception as _e:
                log.warning("face-integrity corroboration failed (keeping 1st): %s", _e)
        if fi.get("face_defect") and (sev in ("minor", "major") or is_hard or is_soft):
            fail = is_hard or (sev == "major" and not (is_soft and holistic_clean))
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

    # Ryani nape-white gate (PD 2026-08-02): her nape/spine/back are canon SOLID BLACK; a
    # white spot there is 삐용이's tuxedo bleeding over. The render gate samples few frames and
    # let a white-nape AV ship, and the holistic reviewer had no nape lens — it rubber-stamped
    # it. Focused ref-compare backstop, AV-scoped (real footage already has a black nape) →
    # cap ≤5, verdict 수정 필요 (canon violation, on the same tier as a blaze/marking drift).
    try:
        if (concept or {}).get("render_style", "") == "ai_vtuber":
            nz = _check_ryani_nape(client, model_name, frames, _types)
            report["nape_check"] = nz
            if nz.get("nape_white"):
                report["점수"] = min(int(report.get("점수", 10) or 10), 5)
                if report.get("판정") in ("업로드", "즉시 업로드", "소폭 수정 후 업로드"):
                    report["판정"] = "수정 필요"
                report["최종_결정"] = report["판정"]
                _wf = nz.get("worst_frame")
                _note = ("랴니 목뒤 흰마킹(삐용이 마킹 번짐) — 목뒤/척추/등은 순검정이어야 함"
                         + (f" [frame {_wf}]" if _wf else ""))
                _prev = report.get("가장_큰_문제", "") or ""
                report["가장_큰_문제"] = _note if (not _prev or "없" in _prev[:6]) else f"{_note} / {_prev}"
                report["_nape_white_override"] = _note
                log.info("nape-white gate: → 판정=%s 점수=%s", report["판정"], report["점수"])
    except Exception as e:
        log.warning("nape-white gate failed: %s", e)

    # Deterministic no-story cap (PD 2026-07-12): the '무훅·무사건' rule existed but the
    # holistic reviewer kept rubber-stamping an eventless AV (a '거실이 커진다면' abstract mood
    # with disconnected observation captions shipped as content). Force the reviewer to answer
    # story_arc_present explicitly, then cap deterministically on its OWN answer — an ai_vtuber
    # episode the reviewer itself judged to have no setup→escalation→payoff arc cannot pass.
    try:
        _rs_av = (concept or {}).get("render_style", "") == "ai_vtuber"
        if _rs_av and report.get("story_arc_present") is False:
            report["점수"] = min(int(report.get("점수", 10) or 10), 5)
            if report.get("판정") in ("업로드", "즉시 업로드", "소폭 수정 후 업로드"):
                report["판정"] = "수정 필요"
            report["최종_결정"] = report["판정"]
            _why = str(report.get("story_arc_reason") or "").strip()
            _note = f"AV 무훅 — 스토리·payoff 없음(무사건 나열){': ' + _why[:80] if _why else ''}"
            _prev = report.get("가장_큰_문제", "") or ""
            report["가장_큰_문제"] = _note if (not _prev or "없" in _prev[:6]) else f"{_note} / {_prev}"
            log.info("no-story cap: AV story_arc_present=false → 판정=%s 점수=%s",
                     report["판정"], report["점수"])
    except Exception as e:
        log.warning("no-story cap failed: %s", e)

    # Deterministic era-mix gate (PD 2026-06-23): catches un-narrated time-jumps
    # the LLM can't see in a sparse frame sample. Runs LAST so its 수정 필요 verdict
    # is authoritative over a softer LLM/face verdict.
    try:
        _temporal_grounding_gate(concept, report)
    except Exception as e:
        log.warning("Temporal grounding gate failed: %s", e)

    # Deterministic 도사체 gate (PD 2026-06-27): catches unambiguous preachy/sage/
    # résumé captions ("여유란 이런 것", "7년 경력") the LLM rubber-stamps as on-brand.
    try:
        _preachy_caption_gate(concept, report)
    except Exception as e:
        log.warning("Preachy caption gate failed: %s", e)

    # Deterministic 호칭/나이 canon gate (PD 2026-07-04): catches age/relationship inversions
    # ("오빠, 나랑 놀자!" — 랴니가 막내 레오를 오빠라 부름) the LLM writes despite the caption prompt.
    try:
        _canon_honorific_gate(concept, report)
    except Exception as e:
        log.warning("Honorific canon gate failed: %s", e)

    # Coined-concept gate (both lanes): title/theme must be real, instantly-understood Korean
    # — a nonsense coinage ('관조봇') the holistic reviewer rubber-stamped as on-brand (9/10).
    try:
        _coined_concept_gate(concept, report)
    except Exception as e:
        log.warning("Coined-concept gate failed: %s", e)

    # False "first swim/water" framing about Ryani (PD 2026-06-28): she is a lifelong
    # swimmer, so a '첫 수영/인생 첫 풍덩' hook on old pool footage is a lie. RF-scoped.
    try:
        _false_first_water_gate(concept, report)
    except Exception as e:
        log.warning("False-first-water gate failed: %s", e)

    # Off-brand grandiose register (PD 2026-07-05): "웅장" 류 pompous diction over everyday
    # footage — Giri rubber-stamped it. Deterministic register gate (like 도사체), RF-scoped;
    # playful energy/locomotion ("우다다 출동") is fine and excluded.
    try:
        _caption_grandiose_gate(concept, report)
    except Exception as e:
        log.warning("Grandiose caption gate failed: %s", e)

    # Clip reused from a currently-live episode (PD 2026-07-07): the 7/7 re-render grabbed
    # a clip already public that day. Deterministic — Giri is the guarantee behind the
    # generator's clip-cooldown seed.
    try:
        _clip_reuse_gate(concept, report)
    except Exception as e:
        log.warning("Clip-reuse gate failed: %s", e)

    # RF caption held on one line for a long stretch = 캡션이 안 바뀜 (PD 2026-07-10). The
    # 7/8 frame-judged VLM rule rubber-stamped it; this reads the burned captions.json and
    # caps deterministically.
    try:
        _caption_hold_gate(concept, report)
    except Exception as e:
        log.warning("Caption-hold gate failed: %s", e)

    # Deterministic caption-mismatch handling — runs LAST so its false-reject relief can see whether
    # any OTHER deterministic gate already fired (scrub + real-mismatch cap are order-independent).
    try:
        _caption_mismatch_gate(concept, report)
    except Exception as e:
        log.warning("Caption-mismatch gate failed: %s", e)

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
        # default=str: the report can carry numpy bools/scalars from the gates, which
        # the stock JSON encoder rejects — the production path consumes the dict directly,
        # only this debug CLI serializes it, so coerce non-native types to str.
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(report)

    return 0 if report.get("판정") in ("업로드", "소폭 수정 후 업로드") else 1


if __name__ == "__main__":
    sys.exit(main())
