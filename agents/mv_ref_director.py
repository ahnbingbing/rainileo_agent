"""Reference-directing analysis — the real thing behind stage_reference (was a stub).

PD rule (mv_frame_level_qc_continuity): you must analyze a reference MV at frame-level
(~24fps), not from text descriptions of its "aesthetic". This module downloads/loads an
actual reference video, uploads it to the Gemini Files API, and asks for a SHOT-BY-SHOT
directing breakdown (per-shot timing, shot size, camera move, subject framing, colour,
lighting, transition, signature technique) plus a synthesized directing standard. The
output becomes the Director's homage floor (mv_producer.stage_director passes homage.doc).

It deliberately reuses scripts.video_actions' Files-API machinery (resumable upload, key,
SSL ctx) instead of re-implementing it — only the prompt + schema differ (shots, not pet
action moments).

CLI:
    .venv/bin/python -m agents.mv_ref_director <video.mp4> [--concept "neon night drive"] \
        [--out ref.json] [--model gemini-2.5-pro]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import urllib.request

from scripts.video_actions import _upload, _key, _CTX, BASE, MODEL

ROOT = Path(__file__).resolve().parent.parent

# Shot-level directing schema — constrained decoding keeps the array valid on long outputs
# (same rationale as video_actions._ACTIONS_SCHEMA). Attributes are the vocabulary the MV
# Director already speaks (shot_size / camera_move), so the breakdown drops straight into a
# homage the Director can honour.
_SHOTS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "shots": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start": {"type": "NUMBER"},
                    "end": {"type": "NUMBER"},
                    "shot_size": {"type": "STRING",
                                  "enum": ["WS", "MS", "CU", "ECU", "insert", "aerial"]},
                    "camera_move": {"type": "STRING"},
                    "subject_framing": {"type": "STRING"},
                    "color_palette": {"type": "STRING"},
                    "lighting": {"type": "STRING"},
                    "transition_in": {"type": "STRING"},
                    "technique": {"type": "STRING"},
                },
                "required": ["start", "end", "shot_size", "camera_move",
                             "color_palette", "technique"],
            },
        },
        "directing_standard": {
            "type": "OBJECT",
            "properties": {
                "palette": {"type": "STRING"},
                "signature_shots": {"type": "ARRAY", "items": {"type": "STRING"}},
                "camera": {"type": "STRING"},
                "cut_cadence": {"type": "STRING"},
                "lighting": {"type": "STRING"},
                "texture": {"type": "STRING"},
                "mood": {"type": "STRING"},
            },
            "required": ["palette", "signature_shots", "camera", "cut_cadence", "mood"],
        },
    },
    "required": ["shots", "directing_standard"],
}


def _prompt(concept_hint: str) -> str:
    return (
        "You are a music-video director doing a SHOT-BY-SHOT breakdown of this reference MV, "
        "watching it at frame level (it is 24fps — resolve cuts to the shot, not a coarse "
        f"sample). We are making a homage with this concept: \"{concept_hint}\".\n\n"
        "For EACH distinct shot (a continuous take between cuts) return: start/end seconds, "
        "shot_size (WS/MS/CU/ECU/insert/aerial), camera_move (e.g. 'lateral tracking', "
        "'slow push-in', 'static hold', 'whip pan', 'handheld follow'), subject_framing "
        "(where the subject sits in frame), color_palette (the dominant neon/grade hues this "
        "shot), lighting (source + quality), transition_in (hard cut / dissolve / flash / "
        "match cut), and technique (the signature directing move — headlight trails, "
        "windscreen reflection, mirror shot, silhouette, lens flare, wet-pavement reflection, "
        "etc.).\n\n"
        "Then synthesize a directing_standard: the palette, the recurring signature_shots, the "
        "camera language, the cut_cadence (how cuts relate to the beat — tight on drops, held "
        "in verses, etc.), lighting, texture (grain/scanlines/bloom), and mood. This is the "
        "STANDARD a homage must meet. Be concrete and specific to what you SEE, not generic."
    )


def analyze_reference(local_path: str, *, concept_hint: str = "neon night drive",
                      model: str | None = None) -> dict:
    """Upload the reference video → Gemini shot-by-shot breakdown + directing standard."""
    uri = _upload(local_path)
    gen_cfg = {"temperature": 0.2, "responseMimeType": "application/json",
               "thinkingConfig": {"thinkingBudget": 0},
               "maxOutputTokens": 65535}  # a fast-cut MV has 100+ shots → large JSON; default truncates
    use_schema = os.getenv("STRUCT_OUT_GEMINI", "1") != "0"
    if use_schema:
        gen_cfg["responseSchema"] = _SHOTS_SCHEMA
    mime = "video/mp4"
    body = json.dumps({
        "contents": [{"parts": [
            {"file_data": {"mime_type": mime, "file_uri": uri}},
            {"text": _prompt(concept_hint)}]}],
        "generationConfig": gen_cfg,
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/v1beta/models/{model or MODEL}:generateContent?key={_key()}",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    # Larger maxOutputTokens → a fast-cut MV (100+ shots) can take minutes to generate; 300s read
    # timed out. 600s + the lenient parser (salvages a truncated array) covers long shot lists.
    with urllib.request.urlopen(req, timeout=600, context=_CTX) as r:
        out = json.loads(r.read())
    txt = out["candidates"][0]["content"]["parts"][0]["text"]
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return _lenient_parse(txt)


def _lenient_parse(txt: str) -> dict:
    """Recover a shot analysis from a TRUNCATED JSON response (a fast-cut MV yields 100+ shots →
    the model can hit the output-token cap mid-array). Salvage every COMPLETE `{...}` object inside
    the shots array by brace-matching, plus directing_standard if it closed."""
    try:
        return json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    except Exception:  # noqa: BLE001
        pass
    shots, i, n = [], 0, len(txt)
    key = txt.find('"shots"')
    if key != -1:
        i = txt.find("[", key)
    while 0 <= i < n:
        start = txt.find("{", i)
        if start == -1:
            break
        depth, j, instr, esc = 0, start, False, False
        while j < n:
            c = txt[j]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            elif c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0 or j >= n:
            break  # truncated object — stop
        try:
            shots.append(json.loads(txt[start:j + 1]))
        except json.JSONDecodeError:
            break
        i = j + 1
        if txt[i:].lstrip()[:1] == "]":
            break
    ds = {}
    dkey = txt.find('"directing_standard"')
    if dkey != -1:
        dstart = txt.find("{", dkey)
        try:
            ds = json.loads(txt[dstart:txt.rfind("}") + 1])
        except Exception:  # noqa: BLE001
            ds = {}
    return {"shots": shots, "directing_standard": ds}


def analyze_reference_frames(local_path: str, *, fps: int = 24, k: float = 3.0,
                             min_shot_s: float = 0.25, thumb: str = "160:90") -> dict:
    """★Reliable reference analysis the way PD specified: UNIFORM frame extraction at `fps` (24),
    then DETERMINISTIC cut detection by consecutive-frame difference — NOT a model guessing the
    segmentation (which was unreliable: one ref came back 9 shots, another 627 uniform-MS). A cut =
    a frame-diff spike (> mean + k·std); micro-flashes shorter than `min_shot_s` are merged into the
    neighbouring shot. Returns shots[{start,end,dur,mid_frame}] = the editing grammar (cut count,
    durations, cadence) from uniform frames. Shot-size labeling is a separate VLM pass on mid_frame.

    No network, no model — ffmpeg + numpy only."""
    import glob
    import subprocess
    import tempfile
    import numpy as np
    from PIL import Image
    work = Path(tempfile.mkdtemp(prefix="refframes_"))
    subprocess.run(["ffmpeg", "-y", "-i", str(local_path), "-vf", f"fps={fps},scale={thumb}",
                    str(work / "f%06d.png")], check=True, capture_output=True)
    fs = sorted(glob.glob(str(work / "*.png")))
    if len(fs) < 3:
        return {"shots": [], "fps": fps, "n_frames": len(fs)}
    arr = np.stack([np.asarray(Image.open(f).convert("L"), dtype=np.float32) for f in fs])
    diffs = np.abs(np.diff(arr, axis=0)).mean(axis=(1, 2))
    thr = diffs.mean() + k * diffs.std()
    cut_frames = [i for i, v in enumerate(diffs, start=1) if v > thr]
    bounds = [0] + cut_frames + [len(fs)]
    # merge shots shorter than min_shot_s (strobe/flash false cuts) into the previous shot
    merged = [bounds[0]]
    for b in bounds[1:]:
        if (b - merged[-1]) / fps < min_shot_s and len(merged) > 1:
            merged[-1] = b
        else:
            merged.append(b)
    shots = []
    for i in range(len(merged) - 1):
        s, e = merged[i], merged[i + 1]
        shots.append({"start": round(s / fps, 2), "end": round(e / fps, 2),
                      "dur": round((e - s) / fps, 2), "mid_frame": fs[(s + e) // 2]})
    durs = [sh["dur"] for sh in shots]
    return {"shots": shots, "fps": fps, "n_frames": len(fs),
            "n_cuts": len(shots) - 1,
            "median_shot_s": round(float(np.median(durs)), 2) if durs else 0.0,
            "mean_shot_s": round(float(np.mean(durs)), 2) if durs else 0.0,
            "frame_dir": str(work)}


def label_shot_sizes(shots: list[dict], *, model: str = "gpt-4o-mini", batch: int = 16) -> list[dict]:
    """VLM-label each deterministic shot's mid_frame with a shot_size (WS/MS/CU/ECU/insert/aerial).
    The CUTS are already fixed (analyze_reference_frames); this only classifies framing per shot —
    the reliable split PD wanted (deterministic segmentation + per-frame labeling, not model guessing
    the whole segmentation). Batches mid-frames into one call each (tiny thumbnails)."""
    import base64
    from openai import OpenAI
    client = OpenAI()
    out = [dict(s) for s in shots]
    for b0 in range(0, len(out), batch):
        grp = out[b0:b0 + batch]
        content = [{"type": "text", "text":
            "Classify the SHOT SIZE of each numbered film frame. Reply ONLY a JSON array like "
            '[{"i":0,"shot_size":"WS"}...]. shot_size ∈ WS (wide, subject small in a big scene), '
            "MS (medium, subject chest-up), CU (close-up, a face fills frame), ECU (extreme close-up, "
            "one detail), insert (an object detail, subject absent/incidental), aerial (top-down)."}]
        for i, s in enumerate(grp):
            with open(s["mid_frame"], "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            content.append({"type": "text", "text": f"frame {i}:"})
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        try:
            r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": content}],
                                               temperature=0)
            txt = r.choices[0].message.content
            a, z = txt.find("["), txt.rfind("]")
            labels = json.loads(txt[a:z + 1]) if a != -1 else []
            for lab in labels:
                idx = lab.get("i")
                if isinstance(idx, int) and 0 <= idx < len(grp):
                    grp[idx]["shot_size"] = lab.get("shot_size")
        except Exception:  # noqa: BLE001 — leave shot_size unset on failure
            pass
    return out


# ─────────────────────────── edit-grammar template ───────────────────────────
# The reliable analysis (analyze_reference_frames + label_shot_sizes) is distilled into an
# "edit_template": the editing GRAMMAR our Director maps our cuts onto. Three load-bearing numbers:
#   • shot_size_mix     — the framing distribution (so we don't come out all-medium).
#   • shot_change_rate  — fraction of adjacent shots that CHANGE size = the hard-cut variety rhythm.
#   • median_shot_s     — the cadence floor (tight), with hero/wide/dance cuts held longer.
# Camera-move is deliberately NOT asserted as a measured constraint here: the reliable frame
# analyzer does not label camera moves, so claiming a tracking-vs-push-in ratio would be "analyzed-
# pretend" (the one rule PD cares about most). Camera variety is a Director PRINCIPLE instead (§block).

def build_edit_template(analysis: dict, *, main_ref: str,
                        supplementary: list[dict] | None = None) -> dict:
    """Distill a frame-level shot analysis into the Director's edit-grammar template. The MAIN ref
    dominates the mix/cadence; `supplementary` labels only nudge accents (never flatten the concept
    — a flat average of many refs blurs the look). Deterministic: same analysis → same template."""
    from collections import Counter
    shots = analysis.get("shots") or []
    sizes = [s.get("shot_size") for s in shots if s.get("shot_size")]
    durs = [s.get("dur") for s in shots if s.get("dur")]
    n = len(sizes)
    mix = {k: round(v / n, 2) for k, v in Counter(sizes).most_common()} if n else {}
    changes = sum(1 for a, b in zip(sizes, sizes[1:]) if a != b)
    change_rate = round(changes / max(1, len(sizes) - 1), 2) if len(sizes) > 1 else 0.0
    med = analysis.get("median_shot_s")
    if med is None and durs:
        med = round(sorted(durs)[len(durs) // 2], 2)
    return {
        "main_ref": main_ref,
        "method": "analyze_reference_frames + label_shot_sizes",
        "n_shots": len(shots),
        "median_shot_s": med,
        "mean_shot_s": analysis.get("mean_shot_s"),
        "shot_size_mix": mix,
        "shot_change_rate": change_rate,
        "supplementary": [s.get("main_ref") or s.get("label") for s in (supplementary or [])],
        "shots": [{"start": s.get("start"), "end": s.get("end"), "dur": s.get("dur"),
                   "shot_size": s.get("shot_size")} for s in shots],
    }


def load_edit_template(slug: str) -> dict | None:
    """Load a prebuilt edit_template.json for this MV (data/tmp/mv/<slug>/edit_template.json), the
    reliable main-ref grammar. Returns None if absent so the Director proceeds without it (enhancer,
    never a blocker — same contract as homage)."""
    p = ROOT / "data/tmp/mv" / slug / "edit_template.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def edit_template_grammar_block(tpl: dict | None, *, n_cuts: int | None = None) -> str:
    """Render the edit_template as the Director's PRODUCTION CONSTRAINT (Korean) — §3 analysis→
    constraint. The numbers become per-cut rules (mix target, change rhythm, cadence), not a note
    the model may ignore. When `n_cuts` is given, the mix becomes CONCRETE per-size cut counts (far
    better honored than percentages). Returns '' when no template so callers concatenate freely."""
    if not tpl or not tpl.get("shot_size_mix"):
        return ""
    mix = tpl["shot_size_mix"]
    ordered = sorted(mix.items(), key=lambda kv: -kv[1])
    mix_txt = ", ".join(f"{k} {int(round(v * 100))}%" for k, v in ordered)
    # Concrete target counts (round to nearest, keep ≥1 for any present size) — the model honors
    # "roughly 7 WS, 7 MS" far more than "26%". The failure mode is BOTH directions: not just the
    # medium-skew (still-gen's default) but also a close-up/insert-skew (pet-face instinct) that
    # drops the wide/medium shots the scene needs for its space and grandeur.
    count_txt = ""
    if n_cuts:
        counts = {k: max(1, round(v * n_cuts)) for k, v in ordered}
        count_txt = (f" {n_cuts}컷 기준 목표 개수 ≈ "
                     + ", ".join(f"{k} {counts[k]}" for k, _ in ordered) + "개")
    lines = [
        f"★편집 문법 템플릿(메인 레퍼 {tpl.get('main_ref', '?')} — {tpl.get('n_shots', '?')}샷 "
        "프레임단위 실분석 → 이 분포를 그대로 생산 제약으로 써라. 노트가 아니라 컷 지정이다):",
        f"  - **샷사이즈 믹스 목표**: {mix_txt}.{count_txt} 이 비율을 **양방향으로 지켜라** — 미디엄으로도, "
        "클로즈업/인서트로도 쏠리지 마라. 특히 **WS(와이드)와 MS(미디엄)를 이 비율만큼 꼭 넣어라**: 나이트드라이브의 "
        "장대함은 네온 도시 속 작은 차를 보여주는 WS와 운전석 투샷 MS에서 나온다 — 펫 얼굴 CU/디테일 insert만 "
        "쌓으면 공간감이 사라지고 답답해진다(CU/ECU/insert는 리듬 악센트지 뼈대가 아니다). 믹스를 이기는 건 네 지정이다.",
    ]
    chg = tpl.get("shot_change_rate")
    if chg is not None:
        lines.append(
            f"  - **변주 리듬**: 메인 레퍼는 인접 컷의 약 {int(round(chg * 100))}%가 샷사이즈를 바꾼다 — "
            "같은 샷사이즈를 **2컷 넘게 연달지 마라**. 이 끊어치기가 곧 편집의 하드컷 리듬이다.")
    med = tpl.get("median_shot_s")
    if med:
        lines.append(
            f"  - **케이던스**: 기본은 타이트(중앙값 샷 ≈ {med}s 느낌의 빠른 컷감), **단** 시그니처·와이드·"
            "히어로·댄스 킬 컷은 **길게 홀드**하라(좋은 와이드를 0.8초로 끊지 마라 — 보여줄 컷은 보여줘라).")
    return "\n".join(lines)


def _derive_query(concept: dict) -> str:
    """Turn a chosen concept into a YouTube search for a matching reference MV."""
    genre = (concept.get("music_genre") or "").strip()
    theme = (concept.get("theme") or concept.get("title") or "").strip()
    hint = concept.get("reference_hint") or ""
    base = " ".join(x for x in [genre, hint, "music video"] if x).strip()
    return base or f"{theme} music video"


def fetch_reference(*, query: str | None = None, ref_url: str | None = None,
                    dest_dir: str | Path, label: str = "reference") -> str:
    """Download a reference MV (yt-dlp) to dest_dir, ≤480p for fast analysis. Cache-aware:
    returns an existing file if already downloaded. `ref_url` wins over `query`."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    cached = sorted(dest.glob(f"{label}.mp4"))
    if cached:
        return str(cached[0])
    target = ref_url or (f"ytsearch1:{query}" if query else None)
    if not target:
        raise ValueError("fetch_reference needs query or ref_url")
    out_tmpl = str(dest / f"{label}.%(ext)s")
    cmd = ["yt-dlp", "--no-playlist", "-f",
           "bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480]/b",
           "-o", out_tmpl, target]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    got = sorted(dest.glob(f"{label}.mp4")) or sorted(dest.glob(f"{label}.*"))
    if not got:
        raise RuntimeError(f"yt-dlp produced no file for {target}")
    return str(got[0])


def build_homage_doc(analysis: dict, *, ref_label: str, concept: dict | None = None,
                     palette_override: str | None = None) -> dict:
    """Turn a shot-by-shot analysis into the Director's homage block. We homage the
    reference's TECHNIQUE (shots/camera/cadence/texture) but may recolor the palette to the
    concept's own intent (channel rule: no pink-dominant) via palette_override."""
    ds = analysis.get("directing_standard") or {}
    shots = analysis.get("shots") or []
    n = len(shots)
    durs = [s["end"] - s["start"] for s in shots if s.get("end", 0) > s.get("start", 0)]
    med = round(sorted(durs)[len(durs) // 2], 1) if durs else None
    from collections import Counter
    moves = Counter(s.get("camera_move", "?") for s in shots).most_common(4)
    sizes = Counter(s.get("shot_size", "?") for s in shots)
    palette = palette_override or ds.get("palette", "")
    # We homage the reference's LOOK + shot grammar, never its story/subject. Drop signature
    # entries that are about the reference's own narrative (a bloodied face, a performer, a
    # club act) — those don't transfer to our pets and would poison the Director's mood.
    _narr = ("face", "smile", "blood", "performer", "dancer", "club", "woman", "man",
             "actor", "character", "madness", "grotesque")
    sig = [s for s in (ds.get("signature_shots") or [])
           if not any(w in s.lower() for w in _narr)]
    lines = [f"[레퍼런스 디렉팅 분석 — {ref_label} ({n}샷 프레임단위 분석)]",
             "  ※ 레퍼런스에서 가져올 것 = 룩·샷 문법·컷 케이던스·텍스처뿐. "
             "무드·서사·피사체는 우리 컨셉을 따르라(레퍼런스의 스토리 주입 금지)."]
    lines.append(f"  - 팔레트(우리 컨셉 기준): {palette}")
    if sig:
        lines.append("  - 시그니처 샷(관측, 전이 가능): " + "; ".join(sig))
    if ds.get("camera"):
        lines.append(f"  - 카메라: {ds['camera']}")
    if moves:
        lines.append("  - 카메라무브 분포(관측): " + ", ".join(f"{mv}×{c}" for mv, c in moves))
    if ds.get("cut_cadence"):
        lines.append(f"  - 컷 케이던스: {ds['cut_cadence']}"
                     + (f" (관측 중앙값 샷≈{med}s, 샷사이즈 {dict(sizes)})" if med else ""))
    if ds.get("texture"):
        lines.append(f"  - 텍스처: {ds['texture']}")
    return {
        "doc": "\n".join(lines),
        "directing_standard": ds,
        "shots_n": n,
        "median_shot_s": med,
        "reference": ref_label,
        "analyzed_by": "mv_ref_director (Gemini frame-level shot analysis)",
    }


def fetch_and_analyze(*, slug: str, concept: dict, ref_url: str | None = None,
                      query: str | None = None, model: str | None = None,
                      palette_override: str | None = None) -> dict:
    """End-to-end reference stage: download a matching reference MV → shot-by-shot analysis
    → homage block for the Director. Cache-aware so re-runs don't re-download/re-bill."""
    q = query or _derive_query(concept)
    label = (ref_url and "reference") or "reference"
    dest = ROOT / "data/tmp/mv" / slug / "ref"
    cache = dest / "shots.json"
    if cache.exists():
        analysis = json.loads(cache.read_text())
        ref_label = analysis.get("_ref_label", q)
    else:
        path = fetch_reference(query=q, ref_url=ref_url, dest_dir=dest, label=label)
        analysis = analyze_reference(path, concept_hint=_derive_query(concept), model=model)
        analysis["_ref_label"] = q
        cache.write_text(json.dumps(analysis, ensure_ascii=False, indent=2))
        ref_label = q
    return build_homage_doc(analysis, ref_label=ref_label, concept=concept,
                            palette_override=palette_override)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Reference-directing shot-by-shot analysis")
    ap.add_argument("video")
    ap.add_argument("--concept", default="neon night drive citypop")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    res = analyze_reference(a.video, concept_hint=a.concept, model=a.model)
    shots = res.get("shots", [])
    print(f"[ref] {len(shots)} shots analyzed")
    for s in shots[:40]:
        print(f"  {s.get('start'):>6.1f}-{s.get('end'):<6.1f} {s.get('shot_size','?'):<7} "
              f"{s.get('camera_move','?'):<20} | {s.get('color_palette','?')[:40]:<40} | "
              f"{s.get('technique','?')[:46]}")
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"[ref] written → {a.out}")
