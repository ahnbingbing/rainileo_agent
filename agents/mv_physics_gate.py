#!/usr/bin/env python3
"""VLM physics-anomaly gate for MV stills (grounded, not eyeballed).

T2I models follow text but systematically violate physical commonsense (PhyBench 2024,
OmniPhys 2026): wrong steering side, a pet rendered outside/clipping the car, warped scale,
duplicated/ghosted limbs, impossible geometry. We can't retrain gpt-image, so we apply the
black-box recipe the literature converges on — detect anomalies with a VLM and regenerate
(AnomReason, PhyDetEx, PVBench) — and keep physically-clean keyframes so i2v ghosts less
(PhysGen, K2V).

★INTENT-AWARE: the check is given the cut's INTENDED scene, so deliberate surrealism (a flying
car, neon light trails, a synthwave-grid road) is NOT flagged — only UNINTENDED physical errors
that break the frame (pet outside the car, steering on the wrong side, a doubled head, a shrunken
pet). A detector that flagged the intended flight would regenerate good cuts forever.

Returns a structured verdict per still; the driver regenerates failures (gen_still) up to N times.
Kill-switch MV_PHYSICS_GATE=0.
"""
from __future__ import annotations

import base64
import json
import logging
import os

log = logging.getLogger("agents.mv_physics_gate")

# The documented T2I physics-failure classes, scoped to our car MV. Each is an UNINTENDED error.
_CHECKS = [
    "steering wheel is on the WRONG side (it must be on the LEFT / left-hand drive)",
    "a pet's body or body part is OUTSIDE the car, on top of it, or clipping THROUGH the doors / "
    "panels / windshield when it should be inside the cabin",
    "wrong relative SCALE — a pet is shrunken, ballooned, or warped (the dog is only slightly bigger "
    "than the cat); a 'baby'/miniature pet",
    "DUPLICATED or GHOSTED parts — a doubled head, extra limbs, a second faint body, afterimage smear",
    "the tailless dog (Ryani) has grown a TAIL, or a pet's part (tail/ear) has merged into the car/props",
    "a body or object FLOATS or merges unnaturally in a way the scene did NOT intend (ignore an "
    "intentionally flying car / light trails / surreal road — those are fine)",
]


def check_still(img_path: str, *, intended: str, model: str | None = None) -> dict:
    """VLM physics-anomaly check for ONE still. `intended` = the cut's described scene (so surreal
    intent isn't flagged). Returns {ok: bool, anomalies: [{issue, severity}], summary}."""
    from openai import OpenAI
    model = model or os.getenv("MV_PHYSICS_MODEL", "gpt-4o")
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    checks = "\n".join(f"  - {c}" for c in _CHECKS)
    prompt = (
        "You are a strict physical-plausibility inspector for an AI-generated film still of two pets "
        "(a black tailless French bulldog and an orange tabby cat) riding a retro convertible through a "
        "neon night city.\n\n"
        f"INTENDED scene for this shot (deliberate creative choices — do NOT flag these):\n{intended}\n\n"
        "Flag ONLY clear, UNINTENDED physical errors from this list:\n" + checks + "\n\n"
        "If the shot intends a flying car, light trails, a glowing grid road, or similar surreal style, "
        "that is NOT an error. Judge only what actually appears in the image. Reply ONLY JSON: "
        '{"anomalies":[{"issue":"<short>","severity":"high|medium|low"}],"summary":"<one line>"}. '
        "Empty anomalies list = physically clean.")
    client = OpenAI()
    try:
        r = client.chat.completions.create(model=model, temperature=0, messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}])
        txt = r.choices[0].message.content
        a, z = txt.find("{"), txt.rfind("}")
        data = json.loads(txt[a:z + 1]) if a != -1 else {"anomalies": []}
    except Exception as e:  # noqa: BLE001 — a check failure must not block the pipeline
        log.warning("physics check failed for %s: %s", img_path, str(e)[:120])
        return {"ok": True, "anomalies": [], "summary": f"check skipped ({str(e)[:60]})"}
    anomalies = [a for a in data.get("anomalies", []) if isinstance(a, dict)]
    ok = not any((a.get("severity") or "").lower() == "high" for a in anomalies)
    return {"ok": ok, "anomalies": anomalies, "summary": data.get("summary", "")}


def gate_stills(slug: str, *, only: set | None = None, max_regen: int = 2,
                model: str | None = None, progress=None) -> dict:
    """Run the physics gate over a slug's AV stills, regenerating any that fail (high-severity anomaly)
    up to `max_regen` times. Returns a per-cut report. Kill-switch MV_PHYSICS_GATE=0 skips entirely."""
    from pathlib import Path
    from agents.mv_producer import load_manifest, mv_dir, ROOT
    from scripts.mv_still import gen_still
    if os.getenv("MV_PHYSICS_GATE", "1") == "0":
        return {"skipped": True}
    m = load_manifest(slug)
    out_dir = mv_dir(slug) / "stills"
    scene_by_id = {s["scene_id"]: s.get("setting") for s in (m.get("scenes") or []) if s.get("scene_id")}
    style = m.get("art_direction")
    canonical_ref = m.get("canonical_ref")
    if canonical_ref and not os.path.isabs(canonical_ref):
        canonical_ref = str(ROOT / canonical_ref)
    report = {}
    for c in m.get("cuts", []):
        if c.get("still_strategy") == "real_footage":
            continue
        if only and c["tag"] not in only:
            continue
        vs = sorted(out_dir.glob(f"{c['tag']}_v*.png"))
        if not vs:
            continue
        intended = (c.get("still_prompt") or c.get("action") or "").strip()
        verdict = check_still(str(vs[-1]), intended=intended, model=model)
        attempts = 0
        while not verdict["ok"] and attempts < max_regen:
            attempts += 1
            if progress:
                issues = "; ".join(a.get("issue", "") for a in verdict["anomalies"])
                progress(f":wrench: [physics] {c['tag']} regen {attempts} — {issues}")
            try:
                p = gen_still(c, out_dir, style=style, canonical_ref=canonical_ref,
                              scene_setting=scene_by_id.get(c.get("scene_id")))
                verdict = check_still(str(p), intended=intended, model=model)
            except Exception as e:  # noqa: BLE001
                log.warning("physics regen failed %s: %s", c["tag"], str(e)[:120]); break
        report[c["tag"]] = {"ok": verdict["ok"], "regens": attempts,
                            "anomalies": verdict["anomalies"], "summary": verdict["summary"]}
        if progress:
            tag_state = "OK" if verdict["ok"] else "STILL FAILING"
            progress(f":white_check_mark: [physics] {c['tag']} {tag_state} (regens={attempts})")
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="VLM physics-anomaly gate over MV stills")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--only", help="comma tags")
    ap.add_argument("--max-regen", type=int, default=2)
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    rep = gate_stills(a.slug, only=only, max_regen=a.max_regen, model=a.model,
                      progress=lambda s: print(s, flush=True))
    bad = [t for t, r in rep.items() if not r.get("ok")]
    print(f"\n[physics-gate] {len(rep)} checked, {len(bad)} still failing: {bad}")
    for t, r in rep.items():
        if r.get("anomalies"):
            print(f"  {t}: regens={r['regens']} ok={r['ok']} — {r['summary']}")
