"""Caption-salvage (PD 2026-06-11).

PD's insight: "이런 실패케이스도 자막만 바꾸면 되잖아 — 왜 검수 통과못했다고 그냥 끝내?"

When Giri fails an episode for a CAPTION problem (caption-vs-clip mismatch, a caption
that lies about what's on screen, bland/duplicated captions) — and NOT for a structural
defect (marking drift, melted face, human face, broken render) — we should NOT throw the
expensive Seedance render away and re-propose a whole new concept. We should rewrite the
captions to match what ACTUALLY rendered (VLM ground-truth, $0 — no Seedance), re-burn,
re-assemble, and re-review.

This reuses the artifacts the render already left on disk:
  - work_dir/animated/<tag>.mp4   (the rendered cuts — never thrown away)
  - work_dir/captions.json        (the caption manifest)
  - work_dir/render_meta.json     (BGM track + cut order + xfade — persisted by
                                    cameraman._persist_render_meta so the re-assemble
                                    is byte-faithful except for the captions)

Public API:
  is_caption_fixable(report) -> bool       # cheap triage from the Giri report
  salvage(card_id, report, ...) -> Path|None   # re-caption + re-assemble; None if unfixable

The caller (producer Giri loop / launch_selfheal) runs Giri again on the returned path.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import datetime as dt
from pathlib import Path

log = logging.getLogger("agents.caption_salvage")
ROOT = Path(__file__).resolve().parent.parent

# Words in the Giri report's headline problem that mean the defect is STRUCTURAL —
# not something a caption rewrite can fix. If any appears in 가장_큰_문제 / 핵심_판단,
# salvage refuses (return None) and the caller falls back to a real re-render.
_STRUCTURAL = [
    "마킹", "이마줄", "블레이즈", "blaze", "marking", "얼굴", "face", "녹아", "녹은",
    "melt", "왜곡", "distort", "orb", "breed", "품종", "꼬리", "tail", "드리프트",
    "drift", "인간 얼굴", "사람 얼굴", "정지 화면", "프레임 누락", "렌더", "깨진",
    "duplicat", "중복 가구", "merged", "합쳐",
]
# Words that mean the defect IS caption-shaped (fixable by rewriting text).
_CAPTION_SHAPED = [
    "캡션", "자막", "caption", "나레이션", "narration", "톤", "tone", "거짓",
    "불일치", "mismatch", "맞지 않", "설명", "중복", "반복", "지루", "bland",
    "외국", "foreign", "어미", "추측형",
]


def is_caption_fixable(report: dict | None) -> bool:
    """True iff the Giri verdict looks dominated by CAPTION issues (rewritable) and
    NOT by a structural render defect. Conservative: a single structural signal
    vetoes salvage (we never paper over a melted face with nicer words)."""
    if not report:
        return False
    head = " ".join(str(report.get(k, "")) for k in
                    ("가장_큰_문제", "핵심_판단", "최소_수정안", "툴_수정_요청"))
    low = head.lower()
    # Hard veto: an unwanted human face must never ship — never salvage it.
    if report.get("_face_violation"):
        return False
    for cut in (report.get("per_cut") or []):
        if cut.get("has_unwanted_human"):
            return False
    if any(s.lower() in low for s in _STRUCTURAL):
        return False
    mismatches = report.get("caption_vs_clip_mismatches") or []
    caption_signal = bool(mismatches) or any(s.lower() in low for s in _CAPTION_SHAPED)
    return caption_signal


def _find_work_dir(card_id: str) -> Path | None:
    """Most-recent persisted cameraman work_dir for this card that has a
    render_meta.json + an animated/ dir to re-caption."""
    tmp = ROOT / "data" / "tmp"
    if not tmp.exists():
        return None
    cand = [d for d in tmp.glob(f"cameraman_{card_id[:8]}_*")
            if d.is_dir() and (d / "render_meta.json").exists()
            and (d / "animated").is_dir()]
    if not cand:
        return None
    return max(cand, key=lambda d: d.stat().st_mtime)


def _vlm_describe(mp4: Path) -> str:
    """One short Korean ground-truth description of what a rendered cut shows.
    Empty string on any failure (caller then leaves that caption alone)."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or not mp4.exists():
        return ""
    try:
        from google import genai as _g
        from google.genai import types as _gt
        client = _g.Client(api_key=api_key, http_options=_gt.HttpOptions(
            timeout=int(os.getenv("VLM_TIMEOUT_MS", "90000"))))
        import tempfile
        parts = []
        with tempfile.TemporaryDirectory() as td:
            for t in (0.5, 2.5, 4.0):
                fp = Path(td) / f"f{t}.jpg"
                subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error",
                                "-ss", str(t), "-i", str(mp4), "-frames:v", "1",
                                str(fp)], check=False, timeout=15)
                if fp.exists() and fp.stat().st_size > 1000:
                    parts.append(_gt.Part.from_bytes(data=fp.read_bytes(),
                                                     mime_type="image/jpeg"))
            if not parts:
                return ""
            parts.append(
                "1-2 short Korean sentences: what ACTUALLY happens in this 5s pet "
                "clip? Be specific about which pet (고양이 레오 / 강아지 랴니), its "
                "position and movement, and any explicit sound (짖다/야옹). Ground-"
                "truth observer only, no speculation.")
            resp = client.models.generate_content(
                model=os.getenv("VLM_MODEL", "gemini-2.5-flash"),
                contents=parts,
                config=_gt.GenerateContentConfig(
                    thinking_config=_gt.ThinkingConfig(thinking_budget=0)))
            return (resp.text or "").strip()
    except Exception as e:
        log.warning("salvage VLM describe failed for %s: %s", mp4.name, e)
        return ""


def _rewrite_caption(actual: str, old_ko: str, lane: str) -> tuple[str, str] | None:
    """Given the VLM ground-truth + the old (wrong) caption, produce a truthful
    (ko, en) caption in the channel's vlog/narrator tone. None on failure."""
    try:
        from agents.llm_cascade import call_text_cascade
        tone = ("real_footage = 친근한 브이로그 톤 (관찰자)"
                if lane == "real_footage"
                else "ai_vtuber = TV동물농장/세나개 나레이션 톤")
        system = (
            "You rewrite ONE burned-in caption for the 'Ryani & Leo' pet Shorts so it "
            "matches what the clip ACTUALLY shows. Rules: Korean line + English line; "
            "no parentheses, no emoji, no script notes; " + tone + "; 레오=8개월 고양이, "
            "랴니=11살 강아지(꼬리 없음) — NEVER swap ages/species. If the clip shows "
            "nothing caption-worthy, return empty strings. Return ONLY JSON: "
            "{\"ko\":str,\"en\":str}.")
        user = (f"실제 화면(VLM 관찰): {actual}\n"
                f"기존(틀린) 캡션: {old_ko or '(없음)'}\n"
                "위 실제 화면에 맞는 새 캡션을 써라.")
        txt = call_text_cascade(system, user, max_tokens=300).strip()
        txt = re.sub(r"^```(?:json)?\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
        d = json.loads(txt)
        return (str(d.get("ko", "")).strip(), str(d.get("en", "")).strip())
    except Exception as e:
        log.warning("salvage caption rewrite failed: %s", e)
        return None


def _patch_captions(cap_path: Path, anim_dir: Path, report: dict, lane: str,
                    progress_cb=None) -> int:
    """Rewrite the captions Giri flagged (or blank the ones it calls outright lies).
    Edits cap_path in place. Returns number of cuts changed."""
    try:
        cap = json.loads(cap_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("salvage: captions.json unreadable: %s", e)
        return 0
    ordered_tags = [k for k in cap.keys() if not k.startswith("_")]
    mismatches = report.get("caption_vs_clip_mismatches") or []
    # Map Giri's 1-based cut_number → tag.
    targets: list[str] = []
    for m in mismatches:
        n = m.get("cut_number")
        if isinstance(n, int) and 1 <= n <= len(ordered_tags):
            targets.append(ordered_tags[n - 1])
    # No explicit per-cut mismatch but a caption-tone fail → rewrite ALL captioned cuts.
    if not targets:
        targets = [t for t in ordered_tags if cap.get(t, {}).get("scenes")
                   or cap.get(t, {}).get("ko")]
    targets = list(dict.fromkeys(targets))
    changed = 0
    for tag in targets:
        mp4 = anim_dir / f"{tag}.mp4"
        actual = _vlm_describe(mp4)
        entry = cap.get(tag) or {}
        old_ko = ""
        scenes = entry.get("scenes")
        if scenes:
            old_ko = scenes[0].get("ko", "")
        else:
            old_ko = entry.get("ko", "")
        if not actual:
            continue
        new = _rewrite_caption(actual, old_ko, lane)
        if not new:
            continue
        ko, en = new
        if not ko:  # VLM says nothing caption-worthy → blank this cut's caption
            if scenes:
                for sc in scenes:
                    sc["ko"], sc["en"] = "", ""
            else:
                entry["ko"], entry["en"] = "", ""
            cap[tag] = entry
            changed += 1
            if progress_cb:
                progress_cb(f":mute: salvage {tag}: 캡션 제거(화면에 자막거리 없음)")
            continue
        if scenes:
            scenes[0]["ko"], scenes[0]["en"] = ko, en
            # collapse extra scenes to avoid re-introducing a mismatch
            entry["scenes"] = [scenes[0]]
        else:
            entry["ko"], entry["en"] = ko, en
        cap[tag] = entry
        changed += 1
        if progress_cb:
            progress_cb(f":pencil2: salvage {tag}: {ko[:24]}")
    if changed:
        cap_path.write_text(json.dumps(cap, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    return changed


def salvage(card_id: str, report: dict, *, progress_cb=None,
            dry_run: bool = False) -> Path | None:
    """Re-caption an already-rendered (but Giri-failed-on-captions) episode and
    re-assemble it WITHOUT any Seedance re-render. Returns the new episode path, or
    None if the failure isn't caption-fixable / artifacts are gone. The caller
    re-runs Giri on the returned path."""
    if not is_caption_fixable(report):
        return None
    wd = _find_work_dir(card_id)
    if not wd:
        if progress_cb:
            progress_cb(":information_source: salvage 불가 — 렌더 산출물(work_dir) 없음")
        return None
    try:
        meta = json.loads((wd / "render_meta.json").read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("salvage: render_meta unreadable: %s", e)
        return None
    lane = meta.get("style") or "ai_vtuber"
    cap_path = Path(meta.get("captions") or (wd / "captions.json"))
    anim_dir = Path(meta.get("anim_dir") or (wd / "animated"))
    if not cap_path.exists() or not anim_dir.is_dir():
        return None
    if progress_cb:
        progress_cb(":lifebuoy: 캡션-salvage 시작 — Seedance 재렌더 없이 자막만 재작성")
    n = _patch_captions(cap_path, anim_dir, report, lane, progress_cb)
    if n == 0:
        if progress_cb:
            progress_cb(":information_source: salvage: 바꿀 캡션 없음 — 중단")
        return None
    if dry_run:
        return cap_path  # signal "would salvage" without rendering

    # Re-burn + re-assemble, reusing cameraman's exact helpers so 여운/font/xfade match.
    from agents import cameraman as cm
    manifests = {
        "captions": str(cap_path),
        "bgm": meta.get("bgm") or str(cm.DEFAULT_BGM),
        "font_override": meta.get("font_override") or "",
    }
    captioned_dir = (wd / "animated_captioned") if lane != "real_footage" \
        else (ROOT / "data" / "output" / "animated_captioned")
    try:
        cm._run(cm._burn_captions_cmd(manifests, anim_dir, captioned_dir),
                ":speech_balloon: [salvage] 자막 재번 burn", progress_cb, False)
    except Exception as e:
        log.warning("salvage burn failed: %s", e)
        return None
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = "rf" if lane == "real_footage" else "av"
    out = ROOT / "data" / "output" / "episodes" / f"episode_{prefix}_{ts}_salvaged.mp4"
    asm = ["python3", "scripts/assemble_episode.py",
           "--captions", str(cap_path),
           "--intro-bumper", str(cm.INTRO_BUMPER),
           "--outro-bumper", str(cm.OUTRO_BUMPER),
           "--music", manifests["bgm"],
           "--out", str(out)]
    xfade = meta.get("xfade_tags") or []
    if xfade and os.getenv("CHAIN_TRANSITION", "hardcut").lower() == "crossfade":
        asm += ["--xfade-tags", ",".join(xfade),
                "--xfade-dur", os.getenv("CHAIN_XFADE_DUR", "0.2")]
    try:
        cm._run(asm, ":clapper: [salvage] 재조립", progress_cb, False)
    except Exception as e:
        log.warning("salvage assemble failed: %s", e)
        return None
    if not out.exists():
        return None
    if progress_cb:
        progress_cb(f":sparkles: 캡션-salvage 완료 ({n}컷 자막 수정) — 재검수 진행")
    return out
