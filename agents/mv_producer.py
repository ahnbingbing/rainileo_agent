#!/usr/bin/env python3
"""MV pipeline orchestrator — the `producer` analogue for original music videos.

Drives the stage-agents (0 discovery → 8 QC), owns the manifest + asset-reuse
ledger + the toggleable PD-gate framework. Mirrors the production stack pattern
(producer → writer_director → cameraman → reviewer) but keys all state on
`data/tmp/mv/<slug>/manifest.json` instead of the episode DB, because an MV is
slug-keyed, N-cut and song-driven (not a 5-cut DB card).

DESIGN INVARIANTS (from notes/mv_agent_feedback_log.md + memories):
  • Reuse, don't rebuild. Each stage calls an EXISTING leaf where one exists
    (gen_song, video_actions.align_audio, make_mv, the cameraman ref-lock core).
    Building a parallel path is the #1 documented failure (cli_quality_drift_root,
    mv_character_consistency_lock §8.13).
  • Never overwrite generated art (§7). Stills/clips are versioned files on disk;
    the manifest references them by path + hash.
  • Deterministic asset-reuse ledger (§8.3): a cut re-renders IFF its still hash
    changed or it is flagged dirty — good cuts are never silently regenerated.
  • Gates are a function, not a branch (same code path for PD-now and autonomous-
    later). MV_GATES env toggles each gate pd|auto|off.

Build status: Phase A wires stage 2 (align) + stage 7 (assemble) to existing
leaves and proves the spine with a hand-authored cutlist. Stages 0/1/3/4/5/6/8
are phase-marked stubs that pass through pre-supplied manifest data for now.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent

# Load the repo .env (SUNO / GOOGLE / BYTEPLUS / FAL keys live here) — the same explicit
# load the leaf scripts do. Without this, running `python -m agents.mv_producer` only sees
# shell-exported keys (OpenAI), and the Suno/Gemini/Seedance stages fail with MISSING keys.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

ProgressCb = Callable[[str], None] | None

SCHEMA_VERSION = 1
STAGE_ORDER = ["discovery", "song", "align", "reference",
               "director", "rf_match", "board", "render", "assemble", "qc"]

# Gates sit AFTER these stages. Keep in sync with _run_gates_after().
GATE_AFTER = {
    "discovery": "concept_pick",  # agent proposes concepts from 함미하비 story → PD picks
    "song": "song_pick",       # after song-variant gen, before alignment
    "board": "still_qc",       # after contact-sheet, BEFORE any paid render (top lever)
    "assemble": "title",       # after body assembled; reuse_body makes retitle cheap
    "qc": "final_qc",          # after full assembly → PD final / revision loop
}


# ─────────────────────────────── paths / manifest ───────────────────────────────

def mv_dir(slug: str) -> Path:
    return ROOT / "data" / "tmp" / "mv" / slug


def manifest_path(slug: str) -> Path:
    return mv_dir(slug) / "manifest.json"


def _past_mv_themes(exclude_slug: str) -> list[str]:
    """Themes/titles of MVs already made (sibling mv*/manifest.json with a chosen concept).
    Feeds discovery's exclude-block so new concepts don't keep converging on the same
    '두 식구 집 안 일상 케미' — without this, each MV is blind to its predecessors."""
    out: list[str] = []
    root = ROOT / "data" / "tmp" / "mv"
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name == exclude_slug:
            continue
        try:
            m = json.loads((d / "manifest.json").read_text())
        except Exception:
            continue
        chosen = (m.get("concept") or {}).get("chosen") or {}
        theme, title = chosen.get("theme"), chosen.get("title")
        if theme:
            out.append(f"{theme}" + (f" ({title})" if title else ""))
    return out


def new_manifest(slug: str, *, theme: str | None = None, genre_lane: str | None = None) -> dict:
    return {
        "slug": slug,
        "schema_version": SCHEMA_VERSION,
        "stage": None,                 # last COMPLETED stage — the resume key
        "genre_lane": genre_lane,      # "ballad_rf" | "dance_av"
        "concept": {"theme": theme} if theme else {},
        "song": {},                    # {variants, chosen, mp3, vocal_start, duration, spliced}
        "lyric_lines": [],             # [{text, subject, onomatopoeia}]
        "karaoke_lines": [],           # [{text, start, end}] song-relative (stage 2)
        "spliced_align": None,         # splice segment math if song was cut-and-joined (§8.5)
        "homage": {},                  # {doc, killpoint_cut}
        "wardrobe_lock": {},           # {ryani, leo} canonical per-concept (§8.12)
        "cuts": [],                    # see _new_cut()
        "title": {},                   # {png, text, style, credit}
        "song_bumper_overlap": None,   # bumper_overlap_fade seconds for make_mv (§5)
        "gates": {},                   # {gate_name: "passed"|"pending"|None}
        "output": None,
    }


def _new_cut(tag: str, **kw) -> dict:
    cut = {
        "tag": tag,
        "subject": None, "shot_size": None, "camera_move": None,
        "still_strategy": None,        # av_ref_lock | kontext_bgreplace | t2i_wide | real_footage
        "motion_prompt": None, "references": [], "duration": 5.0,
        "is_killpoint": False,
        "still": {"version": 0, "path": None, "hash": None, "locked": None},
        "render": {"mp4": None, "still_hash_at_render": None, "defects": []},
        "change_flags": {"still_dirty": False, "render_dirty": False},
    }
    cut.update(kw)
    return cut


def load_manifest(slug: str) -> dict | None:
    p = manifest_path(slug)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_manifest(m: dict) -> Path:
    p = manifest_path(m["slug"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ─────────────────────────────── asset-reuse ledger (§8.3) ───────────────────────────────

def file_hash(path: str | Path) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def cut_needs_render(cut: dict) -> bool:
    """Deterministic §8.3 rule: re-render IFF the still changed or the cut is flagged
    render-dirty. A good cut is NEVER silently regenerated (that produced the §8.13-④
    'cigarette-in-nose' nonsense when good dance cuts were re-rolled)."""
    if cut["change_flags"].get("render_dirty"):
        return True
    rendered = cut["render"].get("mp4")
    if not rendered or not Path(ROOT / rendered if not os.path.isabs(rendered) else rendered).exists():
        return True
    return cut["still"].get("hash") != cut["render"].get("still_hash_at_render")


def mark_cut_dirty(cut: dict, *, still: bool = False, render: bool = False) -> None:
    if still:
        cut["change_flags"]["still_dirty"] = True
    if render:
        cut["change_flags"]["render_dirty"] = True


# ─────────────────────────────── PD decision capture (collect now → rubric later) ───────────────────────────────
# PD 2026-11-19: "한동안 내가 판단하는 것들을 별도 저장한 다음에 나중에 그 기준으로 루브릭 만들자."
# Every PD gate decision (concept/song/still/title/final) is logged RAW to a git-tracked,
# MV-scoped store — NOT distilled yet (premature generalization from one point is exactly
# what PD is avoiding: "댄스는 좋아하는데 A가 뽕삘이라 거름" ≠ "항상 힙합"). A rubric is
# derived LATER from the accumulated corpus (reuse agents/pd_taste.taste_digest pattern,
# lane='mv'). Kept separate from the episode Giri corpus (notes/pd_corpus.jsonl).
_MV_DECISIONS = ROOT / "notes" / "mv_pd_decisions.jsonl"


def log_pd_decision(slug: str, kind: str, *, chosen: str, rejected: str = "",
                    reason: str = "", options: list | None = None) -> None:
    """Append one raw PD taste decision to the MV decision corpus (durable, git-tracked).
    `reason` = PD's own words (the load-bearing signal). Do NOT generalize here."""
    rec = {"slug": slug, "kind": kind, "decision": "pick",
           "chosen": chosen, "rejected": rejected, "reason": reason,
           "options": options or []}
    try:
        _MV_DECISIONS.parent.mkdir(parents=True, exist_ok=True)
        import datetime as _dt
        # a readable timestamp from the manifest-free clock is fine here (append log only)
        rec["ts"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_MV_DECISIONS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    # Also mirror into the structured pd_selections table (lane='mv') so taste_digest(lane='mv')
    # can build the rubric later — best-effort, local DB.
    try:
        import sqlite3
        from agents import pd_taste
        con = sqlite3.connect(str(ROOT / "data" / "agent.db"))
        pd_taste.log_selection(con, lane="mv", kind=kind, decision="pick",
                               subject=slug, chosen=chosen, rejected=rejected, reason=reason)
        con.close()
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────── gate framework (toggleable, single path) ───────────────────────────────

class GatePause(Exception):
    """Raised when a PD gate is pending — run_mv returns control to the caller
    (Slack/CLI). A later resume (PD reply ingested) re-enters and passes the gate."""
    def __init__(self, gate: str, slug: str):
        super().__init__(f"gate '{gate}' pending for {slug}")
        self.gate = gate
        self.slug = slug


def _gate_mode(name: str) -> str:
    """Per-gate mode from MV_GATES env. Examples:
         MV_GATES="song_pick=pd,still_qc=pd,title=pd,final_qc=pd"  (default: all pd)
         MV_GATES="all_auto"   (autonomous VM-cron path)
         MV_GATES="all_off"    (hand-authored spine test / fully trusted)
    """
    raw = os.getenv("MV_GATES", "").strip().lower()
    if raw in ("all_auto", "auto"):
        return "auto"
    if raw in ("all_off", "off"):
        return "off"
    for kv in raw.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            if k.strip() == name and v.strip() in ("pd", "auto", "off"):
                return v.strip()
    return "pd"


def _mv_review(m: dict) -> dict:
    """Run the MV Giri (agents.mv_reviewer) over the manifest — the deterministic defect gate.
    Returns the report; callers surface its issues (PD mode) or gate on them (auto mode)."""
    from agents import mv_reviewer
    return mv_reviewer.review(m, past_themes=_past_mv_themes(m["slug"]))


# Gates that are PD TASTE picks (choose among valid options) — the reviewer has no defect
# verdict for these, so auto-mode trusts them. The QC gates below get the defect review.
_REVIEW_GATES = {"still_qc", "final_qc"}


def _auto_judge(name: str, payload: dict, m: dict) -> bool:
    """Autonomous gate judge. Taste-pick gates pass; the QC gates (still_qc/final_qc) run the
    MV reviewer and only pass if it finds no defect — so an autonomous run self-rejects the same
    classes PD used to catch by hand (repeated clip, indoor-as-snow, caption desync, foreign pet)."""
    if name not in _REVIEW_GATES:
        return True
    from agents import mv_reviewer
    report = _mv_review(m)
    m.setdefault("qc", {})["review"] = report
    if not mv_reviewer.passed(report):
        log.warning("mv_reviewer gated %s: %s", name, mv_reviewer.summary(report))
        return False
    return True


def run_gate(name: str, payload: dict, m: dict, progress_cb: ProgressCb) -> None:
    """Pass, auto-judge, or pause. Downstream stages never know which mode ran."""
    mode = _gate_mode(name)
    if m["gates"].get(name) == "passed":
        return
    if mode == "off":
        m["gates"][name] = "passed"
        return
    if mode == "auto":
        m["gates"][name] = "passed" if _auto_judge(name, payload, m) else "pending"
        if m["gates"][name] != "passed":
            save_manifest(m)
            raise GatePause(name, m["slug"])
        return
    # mode == "pd": post the payload for review and pause until resolved. For the QC gates, run the
    # reviewer FIRST and surface its findings — so PD confirms a pre-screened draft instead of being
    # the one who has to catch the repeat-class defects.
    m["gates"][name] = "pending"
    review_line = ""
    if name in _REVIEW_GATES:
        from agents import mv_reviewer
        report = _mv_review(m)
        m.setdefault("qc", {})["review"] = report
        if not mv_reviewer.passed(report):
            review_line = "\n:rotating_light: " + mv_reviewer.summary(report)
    save_manifest(m)
    if progress_cb:
        progress_cb(f":hourglass_flowing_sand: GATE [{name}] — PD 확인 대기: "
                    f"{json.dumps(payload, ensure_ascii=False)[:300]}{review_line}")
    raise GatePause(name, m["slug"])


def pass_gate(slug: str, name: str) -> dict:
    """Mark a PD gate resolved (called when PD approves). Then resume run_mv."""
    m = load_manifest(slug)
    if m is None:
        raise FileNotFoundError(f"no manifest for {slug}")
    m["gates"][name] = "passed"
    save_manifest(m)
    return m


def pick_concept(slug: str, index: int, *, reason: str = "") -> dict:
    """PD selects one discovery candidate → it becomes the chosen concept; passes
    the concept_pick gate. The chosen candidate's genre_lane drives the lane."""
    m = load_manifest(slug)
    if m is None:
        raise FileNotFoundError(f"no manifest for {slug}")
    cands = m["concept"].get("candidates") or []
    if not (0 <= index < len(cands)):
        raise IndexError(f"concept index {index} out of range (0..{len(cands)-1})")
    chosen = cands[index]
    m["concept"]["chosen"] = chosen
    m["concept"]["theme"] = chosen.get("theme") or m["concept"].get("theme")
    m["concept"]["title"] = chosen.get("title")
    # ★RF/AV routing axis (PD: decided at stage 0, propagates to Director→board).
    m["origin"] = chosen.get("origin")            # real_story | concept_shot
    m["rf_weight"] = chosen.get("rf_weight")      # rf_heavy | av_heavy | balanced
    m["music_genre"] = chosen.get("music_genre")  # for the songwriter (vocals/arrangement only)
    # compat genre_lane (older consumers): derive from rf_weight when present
    m["genre_lane"] = chosen.get("genre_lane") or (
        "ballad_rf" if m["rf_weight"] == "rf_heavy" else
        "dance_av" if m["rf_weight"] == "av_heavy" else m.get("genre_lane"))
    m["gates"]["concept_pick"] = "passed"
    save_manifest(m)
    log_pd_decision(slug, "concept", chosen=chosen.get("title", ""),
                    rejected="; ".join(c.get("title", "") for j, c in enumerate(cands) if j != index),
                    reason=reason, options=[c.get("title", "") for c in cands])
    return m


def pick_song(slug: str, variant_id: str, *, reason: str = "") -> dict:
    """PD selects one Suno take → it becomes the MV's song; passes song_pick gate."""
    m = load_manifest(slug)
    if m is None:
        raise FileNotFoundError(f"no manifest for {slug}")
    variants = m["song"].get("variants") or []
    chosen = next((v for v in variants if v["id"] == variant_id), None)
    if chosen is None:
        raise KeyError(f"variant '{variant_id}' not in {[v['id'] for v in variants]}")
    m["song"]["mp3"] = chosen["mp3"]
    m["song"]["duration"] = chosen.get("duration")
    m["song"]["chosen_variant"] = variant_id
    m["gates"]["song_pick"] = "passed"
    save_manifest(m)
    log_pd_decision(slug, "song", chosen=f"{variant_id} [{chosen.get('style_label','')}]",
                    rejected="; ".join(f"{v['id']} [{v.get('style_label','')}]"
                                       for v in variants if v["id"] != variant_id),
                    reason=reason, options=[v["id"] for v in variants])
    return m


# ─────────────────────────────── stages ───────────────────────────────
# Each stage is idempotent: it checks the manifest for its output before doing
# paid work, so a resumed run never re-pays for completed stages.

class StageNotBuilt(NotImplementedError):
    """A stage whose agent is not built yet (phase-marked). The hand-authored spine
    test supplies this stage's output in the manifest so the stage is a no-op."""


def stage_discovery(m: dict, progress_cb: ProgressCb, dry_run: bool) -> dict:
    """Stage 0 — the agent PROPOSES concepts from 함미하비 story + VLM material; PD picks
    at the concept_pick gate. `concept.chosen` present → already selected (skip). A bare
    `theme` biases the proposal but is NOT a chosen concept (PD still picks)."""
    c = m["concept"]
    if c.get("chosen"):
        return m
    if c.get("candidates"):
        return m  # proposed already; waiting at the concept_pick gate for PD's pick
    if dry_run:
        return m
    from agents.mv_discovery import propose_mv_concepts
    past = m.get("past_mv_themes") or _past_mv_themes(m["slug"])
    cands = propose_mv_concepts(4, theme=c.get("theme"),
                                context={"exclude_themes": past})
    if not cands:
        raise StageNotBuilt("stage 0 (discovery): LLM returned no concept candidates")
    c["candidates"] = cands
    if progress_cb:
        progress_cb(f":bulb: [discovery] 함미하비 소재에서 컨셉 {len(cands)}개 발굴 — PD 선정 대기")
    return m


def stage_song(m: dict, progress_cb: ProgressCb, dry_run: bool) -> dict:
    """Stage 1 — lyrics (mv_songwriter) → Suno generation of BOTH style variants ×
    all-clips → variants for PD to pick at the song_pick gate. The chosen concept
    drives lyrics; cute/dance mood drives the Suno style. Idempotent: skips if a
    song is already chosen; skips generation if variants already exist."""
    if m["song"].get("mp3") and Path(_abs(m["song"]["mp3"])).exists():
        return m  # a variant already chosen / hand-supplied
    concept = (m.get("concept") or {}).get("chosen")
    if not concept:
        raise StageNotBuilt("stage 1 (song): no chosen concept — run discovery + concept_pick first")

    # 1a: lyrics + Suno style variants (LLM, cheap). Store so re-runs don't rewrite.
    if not m["song"].get("lyrics_data"):
        if dry_run:
            if progress_cb:
                progress_cb(":pencil: [song] (dry-run) would write lyrics + styles")
            return m
        from agents.mv_songwriter import write_lyrics
        if progress_cb:
            progress_cb(":pencil: [song] 작사 + Suno 스타일 2안 생성")
        ld = write_lyrics(concept)
        m["song"]["lyrics_data"] = ld
        m["lyric_lines"] = ld.get("lyric_lines", [])
        save_manifest(m)

    ld = m["song"]["lyrics_data"]

    # 1b: generate each style variant via Suno (paid). all_clips → 2 takes per style.
    if not m["song"].get("variants"):
        if dry_run:
            if progress_cb:
                progress_cb(":musical_note: [song] (dry-run) would generate Suno variants")
            return m
        import subprocess
        from scripts.gen_song import generate_song
        song_dir = mv_dir(m["slug"]) / "song"
        song_dir.mkdir(parents=True, exist_ok=True)
        variants = []
        styles = ld.get("suno_styles", [])[:2]
        vg = "f" if "female" in (ld.get("vocal_mood") or "") else "m"
        for si, st in enumerate(styles):
            label = st.get("label", f"style{si+1}")
            out = song_dir / f"style{si+1}.mp3"
            if progress_cb:
                progress_cb(f":musical_note: [song] Suno 생성 {si+1}/{len(styles)}: {label}")
            try:
                paths = generate_song(out_mp3=out, lyrics=ld.get("lyrics"),
                                      style=st.get("style"), title=ld.get("title"),
                                      vocal_gender=vg, all_clips=True, mood="mv")
            except Exception as e:  # noqa: BLE001
                if progress_cb:
                    progress_cb(f":warning: [song] 스타일 {si+1} 생성 실패: {e}")
                continue
            for pi, p in enumerate(paths):
                try:
                    dur = float(subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
                        capture_output=True, text=True, check=True).stdout.strip())
                except Exception:
                    dur = None
                variants.append({"id": f"s{si+1}t{pi+1}", "style_label": label,
                                 "mp3": str(Path(p).relative_to(ROOT)), "duration": dur})
        if not variants:
            raise StageNotBuilt("stage 1 (song): no variants generated (Suno failed)")
        m["song"]["variants"] = variants
        save_manifest(m)
        if progress_cb:
            progress_cb(f":white_check_mark: [song] {len(variants)}개 테이크 생성 — PD pick 대기")
    return m


def stage_align(m: dict, progress_cb: ProgressCb, dry_run: bool) -> dict:
    if m.get("karaoke_lines"):
        return m
    song = m["song"].get("mp3")
    lines = [l["text"] for l in m.get("lyric_lines", []) if l.get("text")]
    if not song or not lines:
        raise StageNotBuilt("stage 2 (align) needs song.mp3 + lyric_lines[].text")
    if dry_run:
        if progress_cb:
            progress_cb(":microphone: [align] (dry-run) would align karaoke lines")
        return m
    from scripts.mv_align import align_lines
    if progress_cb:
        progress_cb(f":microphone: [align] karaoke 정렬 {len(lines)}줄 "
                    f"({'splice-math' if m.get('spliced_align') else 'Gemini'})")
    m["karaoke_lines"] = align_lines(_abs(song), lines, spliced=m.get("spliced_align"))
    return m


def stage_reference(m: dict, progress_cb: ProgressCb, dry_run: bool) -> dict:
    """Stage 3 — reference-directing analysis. Finds a reference MV matching the chosen
    concept, downloads it, and analyzes it SHOT-BY-SHOT at frame level (agents/mv_ref_director)
    → a homage directing standard the Director must meet (stage_director passes homage.doc).
    PD rule: ground the look in a real analyzed reference, not generic "pretty" cuts.

    Idempotent (skips if homage.doc present). Kill-switch MV_REFERENCE=0. Best-effort: if the
    download/analysis fails (no yt-dlp, network), the Director still proceeds from concept +
    lyrics — reference is an enhancer, never a hard blocker. A concept may pin the reference
    via `reference_url` / `reference_query`; `MV_REF_PALETTE` recolors the homage palette to
    the concept's own intent (channel rule: no pink-dominant)."""
    if m["homage"].get("doc"):
        return m
    if dry_run or os.getenv("MV_REFERENCE", "1") == "0":
        if progress_cb:
            progress_cb(":information_source: [reference] 분석 생략 — Director가 컨셉+가사로 진행")
        return m
    concept = (m.get("concept") or {}).get("chosen") or {}
    try:
        from agents.mv_ref_director import fetch_and_analyze
        homage = fetch_and_analyze(
            slug=m["slug"], concept=concept,
            ref_url=concept.get("reference_url") or os.getenv("MV_REF_URL"),
            query=concept.get("reference_query"),
            palette_override=concept.get("palette") or os.getenv("MV_REF_PALETTE"))
        m["homage"] = homage
        if progress_cb:
            progress_cb(f":mag: [reference] {homage.get('reference')} "
                        f"{homage.get('shots_n')}샷 프레임분석 → 연출기준 수립")
    except Exception as e:  # noqa: BLE001 — enhancer, never block the pipeline
        log.warning("reference analysis skipped (%s) — Director proceeds without homage", e)
        if progress_cb:
            progress_cb(":information_source: [reference] 분석 실패/생략 — Director가 컨셉+가사로 진행")
    return m


def stage_director(m: dict, progress_cb: ProgressCb, dry_run: bool) -> dict:
    """Stage 4 — per-cut cue-sheet from concept + lyrics (mv_director)."""
    if m.get("cuts"):
        return m  # cue-sheet already present
    concept = (m.get("concept") or {}).get("chosen")
    ld = m["song"].get("lyrics_data")
    if not concept or not ld:
        raise StageNotBuilt("stage 4 (Director) needs chosen concept + lyrics_data")
    if dry_run:
        return m
    from agents.mv_director import direct_cuts
    if progress_cb:
        progress_cb(":clapper: [director] 컷 큐시트 생성 (RF/AV 라우팅)")
    cuts = direct_cuts(concept, ld, m.get("rf_weight", "rf_heavy"),
                       music_genre=m.get("music_genre", "dance"),
                       homage=(m.get("homage") or {}).get("doc"))
    m["cuts"] = [_new_cut(
        c.get("tag", f"c{i+1:02d}"),
        subject=c.get("subject"), shot_size=c.get("shot_size"),
        camera_move=c.get("camera_move"), motion_prompt=c.get("motion_prompt"),
        still_strategy=("real_footage" if c.get("cut_type") == "rf" else "av_ref_lock"),
        cut_type=c.get("cut_type", "av"), clip_query=c.get("clip_query"),
        is_killpoint=bool(c.get("is_killpoint")),
        lyric=c.get("lyric"), action=c.get("action"), setting=c.get("setting"),
        still_prompt=c.get("still_prompt"),
    ) for i, c in enumerate(cuts)]
    n_rf = sum(1 for c in m["cuts"] if c.get("cut_type") == "rf")
    if progress_cb:
        progress_cb(f":white_check_mark: [director] {len(m['cuts'])}컷 (RF {n_rf} / AV {len(m['cuts'])-n_rf})")
    return m


def stage_rf_match(m: dict, progress_cb: ProgressCb, dry_run: bool) -> dict:
    """Stage 4.5 — resolve RF cuts' clip_query → real library clip + beat-synced trim
    window (mv_rf_matcher). Sets every cut's duration from its karaoke beat first, so the
    body tiles the song. AV cuts are untouched (handled by board/render). Idempotent:
    re-matches only RF cuts still missing rf_clip."""
    rf_cuts = [c for c in m.get("cuts", []) if c.get("cut_type") == "rf"]
    if not rf_cuts:
        from agents.mv_rf_matcher import set_cut_durations
        set_cut_durations(m)  # AV-only MV still needs beat timing
        return m
    unmatched = [c for c in rf_cuts if not c.get("rf_clip")]
    if not unmatched and all(c.get("duration") for c in m["cuts"]):
        return m  # all RF cuts already matched + timed
    if dry_run:
        return m
    from agents.mv_rf_matcher import match_manifest
    ground = os.getenv("MV_RF_GROUND", "1") != "0"
    return match_manifest(m, progress_cb=progress_cb, ground=ground)


def stage_board(m: dict, progress_cb: ProgressCb, dry_run: bool) -> dict:
    """Stage 5 — AV cuts get a gpt-image-2 ref-lock still; RF cuts ride their matched clip.
    Build the hybrid contact sheet (RF frame + AV still, cut order) for the still_qc gate."""
    from scripts.mv_still import build_board_sheet
    out_dir = mv_dir(m["slug"]) / "stills"
    # record latest still path onto each AV cut (version-managed files live on disk)
    for c in m["cuts"]:
        if c.get("still_strategy") == "real_footage":
            continue
        vs = sorted(out_dir.glob(f"{c['tag']}_v*.png"))
        if vs:
            c["still"]["path"] = str(vs[-1].relative_to(ROOT))
    missing = [c["tag"] for c in m["cuts"]
               if not c["still"].get("path") and c.get("still_strategy") != "real_footage"]
    if missing:
        raise StageNotBuilt(f"stage 5 (board) — AV stills missing: {missing} "
                            f"(generate: python -m scripts.mv_still --slug {m['slug']} --all)")
    sheet, summary = build_board_sheet(m, out_dir, mv_dir(m["slug"]) / "contact_sheet.png")
    m["board_sheet"] = str(sheet.relative_to(ROOT)) if sheet else None
    m["board_summary"] = summary
    if progress_cb:
        n_av = sum(1 for s in summary if s["type"] == "AV")
        progress_cb(f":framed_picture: [board] hybrid 컨택트시트 {len(summary)}컷 "
                    f"(AV {n_av} / RF {len(summary)-n_av}) → {m['board_sheet']}")
    return m


def stage_render(m: dict, progress_cb: ProgressCb, dry_run: bool) -> dict:
    # RF cuts are "rendered" by pointing at their matched real clip — make_mv (assemble)
    # tone-maps/crops/CFR/trims it inline via ss+dur, so there's no separate render pass.
    for c in m["cuts"]:
        if c.get("still_strategy") == "real_footage" and c.get("rf_clip"):
            rel = c["rf_clip"]
            try:
                rel = str(Path(rel).relative_to(ROOT))
            except ValueError:
                pass
            c["render"]["mp4"] = rel
            c["render"]["still_hash_at_render"] = c["still"].get("hash")  # both None → no re-render
            c["change_flags"]["render_dirty"] = False
    todo = [c["tag"] for c in m["cuts"] if cut_needs_render(c)]
    if not todo:
        return m  # every cut has an up-to-date rendered clip
    unmatched_rf = [c["tag"] for c in m["cuts"]
                    if c.get("still_strategy") == "real_footage" and not c.get("rf_clip")]
    if unmatched_rf:
        raise StageNotBuilt(f"stage 6 (render): RF cuts with no matched clip: {unmatched_rf}")
    if dry_run:
        return m
    # AV cuts → Seedance FAST i2v from their approved stills (scripts/mv_render, brillion pattern).
    from scripts.mv_render import render_manifest
    fast = os.getenv("MV_SEEDANCE_FAST", "1") != "0"
    render_manifest(m, progress_cb=progress_cb, fast=fast)
    still_todo = [c["tag"] for c in m["cuts"] if cut_needs_render(c)]
    if still_todo:
        raise StageNotBuilt(f"stage 6 (render): Seedance did not produce clips for {still_todo}")
    return m


def stage_assemble(m: dict, progress_cb: ProgressCb, dry_run: bool) -> dict:
    """Stage 7 — assemble via make_mv (VERBATIM reuse). Build clips from the cut
    render clips, karaoke lines, and the title; enforce body≥song via make_mv."""
    clips = []
    for c in m["cuts"]:
        mp4 = c["render"].get("mp4")
        if not mp4:
            raise StageNotBuilt(f"stage 7 (assemble): cut {c['tag']} has no rendered clip")
        dur = float(c.get("duration", 5.0))
        clip = {"src": _abs(mp4), "dur": dur}
        # fill (time-scale to the slot) ONLY when the slot is longer than the ~4.9s Seedance clip;
        # shorter slots just trim the clip's first `dur` seconds at natural speed (no speed-warp).
        if c.get("still_strategy") != "real_footage" and dur > 4.8:
            clip["fill"] = True
        if c.get("ss") is not None:
            clip["ss"] = float(c["ss"])
        if c.get("grade"):
            clip["grade"] = c["grade"]
        clips.append(clip)

    out = mv_dir(m["slug"]) / f"{m['slug']}.mp4"
    title = m.get("title") or {}
    if dry_run:
        if progress_cb:
            progress_cb(f":clapper: [assemble] (dry-run) {len(clips)} clips → {out.name}")
        m["output"] = str(out.relative_to(ROOT))
        return m

    from scripts.make_mv import make_mv
    if progress_cb:
        progress_cb(f":clapper: [assemble] make_mv {len(clips)} clips, {len(m['karaoke_lines'])} caption lines")
    kw = {}
    if title.get("text"):
        kw["title"] = title["text"]
    if title.get("png"):
        kw["title_image"] = _abs(title["png"])
    if title.get("style"):
        kw["title_style"] = title["style"]
    if title.get("credit"):
        kw["credit"] = title["credit"]
    if m.get("song_bumper_overlap") is not None:
        kw["bumper_overlap_fade"] = float(m["song_bumper_overlap"])
    if m.get("caption_style"):
        kw["caption_style"] = m["caption_style"]
    make_mv(_abs(m["song"]["mp3"]), clips, m["karaoke_lines"], out, **kw)
    m["output"] = str(out.relative_to(ROOT))
    return m


def stage_qc(m: dict, progress_cb: ProgressCb, dry_run: bool) -> dict:
    if not m.get("output"):
        raise StageNotBuilt("stage 8 (QC) needs an assembled output")
    if dry_run:
        if progress_cb:
            progress_cb(":white_check_mark: [qc] (dry-run) skipped structural checks")
        return m
    # Phase A: deterministic structural checks only (final length == song, output exists).
    # Full frame-level contact-sheet QC + both-visible + marking fidelity = Phase D
    # (agents/mv_reviewer.py) following reviewer.review's deterministic score-cap pattern.
    out = _abs(m["output"])
    if not Path(out).exists():
        raise RuntimeError(f"QC: output missing: {out}")
    try:
        import subprocess
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", out],
            capture_output=True, text=True, check=True).stdout.strip())
        song_dur = m["song"].get("duration")
        if song_dur:
            song_dur = float(song_dur)
            # The song must play in FULL (§5). make_mv either overlaps the bumper onto the
            # finale (final ≈ song) or appends the channel outro bumper (final = song + ~2–4s).
            # So the real defect is final < song (song truncated / finale swallowed) or a
            # runaway length; an appended bumper (song ≤ final ≤ song + ~5s) is expected.
            if dur < song_dur - 1.0:
                m.setdefault("qc", {})["length_warning"] = (
                    f"song TRUNCATED: mv {dur:.1f}s < song {song_dur:.1f}s")
            elif dur > song_dur + 5.0:
                m.setdefault("qc", {})["length_warning"] = (
                    f"mv unexpectedly long: {dur:.1f}s vs song {song_dur:.1f}s (+bumper≈2–4s)")
        m.setdefault("qc", {})["duration"] = round(dur, 2)
    except Exception as e:  # noqa: BLE001
        if progress_cb:
            progress_cb(f":warning: [qc] ffprobe failed: {e}")
    if progress_cb:
        progress_cb(f":white_check_mark: [qc] structural checks done → {Path(out).name}")
    return m


STAGE_FN = {
    "discovery": stage_discovery, "song": stage_song, "align": stage_align,
    "reference": stage_reference, "director": stage_director,
    "rf_match": stage_rf_match, "board": stage_board,
    "render": stage_render, "assemble": stage_assemble, "qc": stage_qc,
}


# ─────────────────────────────── driver ───────────────────────────────

def _abs(rel: str | Path) -> str:
    p = Path(rel)
    return str(p if p.is_absolute() else ROOT / p)


def run_mv(slug: str, *, theme: str | None = None, genre_lane: str | None = None,
           resume: bool = True, dry_run: bool = False,
           stop_after: str | None = None,
           progress_cb: ProgressCb = None) -> dict:
    """Drive stages in order from the resume point. Returns the manifest.

    Resume: re-enters at the stage after manifest['stage']. Each stage is idempotent.
    A pending PD gate raises GatePause — the caller resolves it (pass_gate) then
    calls run_mv again to continue. stop_after limits the run to one stage (handy
    for building/validating stage-by-stage, PD's 차근히 cadence)."""
    m = load_manifest(slug) if resume else None
    if m is None:
        m = new_manifest(slug, theme=theme, genre_lane=genre_lane)
        save_manifest(m)
    elif theme and not m["concept"].get("theme"):
        m["concept"]["theme"] = theme

    done = m.get("stage")
    start_idx = (STAGE_ORDER.index(done) + 1) if done in STAGE_ORDER else 0

    for name in STAGE_ORDER[start_idx:]:
        if progress_cb:
            progress_cb(f":arrow_forward: stage: {name}")
        m = STAGE_FN[name](m, progress_cb, dry_run)
        m["stage"] = name
        save_manifest(m)
        # Gate (if any) sits AFTER the stage completes.
        gate = GATE_AFTER.get(name)
        if gate:
            run_gate(gate, {"stage": name}, m, progress_cb)  # may raise GatePause
            save_manifest(m)
        if stop_after and name == stop_after:
            break
    return m


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="MV pipeline orchestrator")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--theme", default=None)
    ap.add_argument("--genre-lane", default=None, choices=["ballad_rf", "dance_av"])
    ap.add_argument("--spec", default=None,
                    help="seed/merge a hand-authored manifest spec JSON (song, cuts, lyric_lines, title)")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stop-after", default=None, choices=STAGE_ORDER)
    ap.add_argument("--pass-gate", default=None, help="mark a pending gate passed, then resume")
    ap.add_argument("--pick-concept", type=int, default=None,
                    help="PD selects discovery candidate N → chosen concept, passes concept_pick")
    ap.add_argument("--pick-song", default=None,
                    help="PD selects Suno variant id (e.g. s1t2) → chosen song, passes song_pick")
    ap.add_argument("--reason", default="", help="PD's reason for a pick (captured to mv_pd_decisions)")
    args = ap.parse_args()

    if args.pick_concept is not None:
        c = pick_concept(args.slug, args.pick_concept, reason=args.reason)
        print(f"[concept] picked: {c['concept']['title']} ({c.get('genre_lane')})")
    if args.pick_song:
        c = pick_song(args.slug, args.pick_song, reason=args.reason)
        print(f"[song] picked: {c['song']['chosen_variant']} ({c['song'].get('duration')}s)")
    if args.pass_gate:
        pass_gate(args.slug, args.pass_gate)

    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        m = load_manifest(args.slug) or new_manifest(args.slug, theme=args.theme,
                                                      genre_lane=args.genre_lane)
        for k, v in spec.items():
            m[k] = v
        # normalize cuts through _new_cut so ledger fields always exist
        if "cuts" in spec:
            m["cuts"] = [_new_cut(c["tag"], **{k: v for k, v in c.items() if k != "tag"})
                         for c in spec["cuts"]]
        save_manifest(m)

    def _pc(msg: str) -> None:
        print(msg, flush=True)

    try:
        m = run_mv(args.slug, theme=args.theme, genre_lane=args.genre_lane,
                   resume=not args.no_resume, dry_run=args.dry_run,
                   stop_after=args.stop_after, progress_cb=_pc)
    except GatePause as gp:
        print(f"[PAUSED] gate '{gp.gate}' pending — resolve then rerun with "
              f"--pass-gate {gp.gate}")
        return 0
    except StageNotBuilt as e:
        print(f"[STAGE NOT BUILT] {e}")
        return 3
    print(f"[DONE] stage={m.get('stage')} output={m.get('output')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
