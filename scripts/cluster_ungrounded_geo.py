"""Image-cluster the GPS-less clips so PD grounds a whole place by answering a few
representatives — not every clip (PD 2026-11-26).

~5.8k iCloud assets genuinely lack GPS (shared/stripped/location-off — verified by
downloading the iCloud originals; the map can't ground them). PD's plan: cluster by
VISUAL similarity (same day ≠ same place, different days CAN be the same place — so it
must be IMAGE clustering, not date), ask PD (the only one who was there) about a few
representatives per cluster, and PROPAGATE the answer across the cluster. Target: answer
<10% of the total.

Extract one representative frame per ungrounded clip, embed it (Places365 scene features
by default — SCENE-centric, so the pet no longer dominates; --embedder clip for the old
object-centric comparison), cluster with agglomerative cosine linkage, and write a
per-cluster contact sheet so the cluster quality is EYEBALLED before any question is asked.
A cluster manifest (id → members, medoid, scene hint) is written for the PD-naming step:
PD names the biggest clusters, and scripts/backfill_geo_scene.py --name propagates the real
place across every member (geo_place, geo_source='pd').

NOTE on the <10% target: honest scene-centric clustering of this footage yields a HIGH
ask-count because the GPS-less clips are genuinely many distinct one-off places; forcing
<10% needs loose thresholds that WRONGLY merge places (re-introducing the very "inland →
beach" hallucination). The deterministic ocean-refute that actually fixes the safety goal
without any question lives in scripts/backfill_geo_scene.py; PD-naming here is the optional
richer layer that grounds the biggest venues by real name, biggest-first.

    python -m scripts.cluster_ungrounded_geo --kind video --threshold 0.20
    python -m scripts.cluster_ungrounded_geo --kind video --limit 120   # quick PoC
    python -m scripts.cluster_ungrounded_geo --embedder clip             # old object-centric
"""
import argparse
import os
from pathlib import Path

import numpy as np

from agents.producer import _db

from scripts.geo_thumb import FRAME_DIR, save_thumb

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "tmp" / "geo_clusters"


def _rows(kind: str, limit: int | None):
    where = "geo_water IS NULL AND source='icloud' AND file_path IS NOT NULL"
    if kind != "all":
        where += f" AND kind='{kind}'"
    q = (f"SELECT asset_id, kind, file_path, duration_sec, captured_iso "
         f"FROM assets WHERE {where} ORDER BY captured_iso DESC")
    if limit:
        q += f" LIMIT {int(limit)}"
    return _db().execute(q).fetchall()


def _frame(aid: str, kind: str, fp: str, dur: float | None, fetch: bool = True) -> Path | None:
    """One representative 224px frame per asset, cached (this IS the ingest thumbnail —
    icloud.sync writes the same FRAME_DIR/<aid>.jpg at ingest, so future runs never fetch).
    Video → mid-point frame. fetch=False skips the GCS pull (recurring jobs stay bounded to
    assets whose thumbnail already exists locally)."""
    out = FRAME_DIR / f"{aid}.jpg"
    if out.exists():
        return out
    src = Path(fp) if os.path.isabs(fp) else ROOT / fp
    if not src.exists() and fetch:
        # Most GPS-less clips are iCloud-optimized (not on this Mac). Pull the file from the
        # GCS mirror to the canonical local path (persists → reused by re-sync + next run).
        try:
            from icloud import gcs
            got = gcs.download_to(fp)
            if got:
                src = Path(got)
        except Exception:
            pass
    return save_thumb(aid, kind, src, dur)


def _embed_clip(frames: list[Path]) -> tuple[np.ndarray, list[str] | None]:
    """Full-frame CLIP. OBJECT-centric → the black Frenchie dominates the vector, so
    'same pet, different place' wrongly merges (the 246-blob). Kept for comparison only."""
    import open_clip
    import torch
    from PIL import Image
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k")
    model.eval().to(device)
    vecs = []
    with torch.no_grad():
        for i in range(0, len(frames), 64):
            batch = frames[i:i + 64]
            imgs = torch.stack([preprocess(Image.open(f).convert("RGB")) for f in batch]).to(device)
            feats = model.encode_image(imgs)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            vecs.append(feats.cpu().numpy())
            print(f"  embedded {min(i+64, len(frames))}/{len(frames)}", flush=True)
    return np.vstack(vecs).astype(np.float32), None


def _embed_places365(frames: list[Path]) -> tuple[np.ndarray, list[str]]:
    """Scene-centric 512-d embedding + top scene category per frame (a hint for PD/me)."""
    from scripts import _places365
    X, topk = _places365.predict(frames, topk=1)
    return X, [row[0] for row in topk]


def _embed(frames: list[Path], embedder: str) -> tuple[np.ndarray, list[str] | None]:
    return _embed_places365(frames) if embedder == "places365" else _embed_clip(frames)


def _cluster(X: np.ndarray, threshold: float) -> np.ndarray:
    from sklearn.cluster import AgglomerativeClustering
    cl = AgglomerativeClustering(n_clusters=None, distance_threshold=threshold,
                                 metric="cosine", linkage="average")
    return cl.fit_predict(X)


def _medoid(idxs: list[int], X: np.ndarray) -> int:
    sub = X[idxs]
    sim = sub @ sub.T
    return idxs[int(sim.mean(axis=1).argmax())]


def _contact_sheet(members: list[Path], out: Path, cols: int = 6) -> None:
    from PIL import Image
    if not members:
        return
    thumbs = [Image.open(f).convert("RGB").resize((160, 160)) for f in members[:30]]
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 160, rows * 160), "black")
    for i, t in enumerate(thumbs):
        sheet.paste(t, ((i % cols) * 160, (i // cols) * 160))
    sheet.save(out, "JPEG")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["video", "photo", "all"], default="video")
    ap.add_argument("--embedder", choices=["places365", "clip"], default="places365",
                    help="places365 = SCENE-centric (default, ignores the pet); clip = object-centric (the 246-blob)")
    ap.add_argument("--threshold", type=float, default=0.12, help="cosine distance; lower = tighter")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    rows = _rows(a.kind, a.limit)
    print(f"{len(rows)} ungrounded {a.kind} assets", flush=True)
    frames, keep = [], []
    for aid, kind, fp, dur, _cap in rows:
        f = _frame(aid, kind, fp, dur)
        if f:
            frames.append(f)
            keep.append(aid)
    print(f"extracted {len(frames)} frames", flush=True)
    if len(frames) < 2:
        return

    # Cache embeddings per (embedder, frame-set) so switching embedders never reuses stale vectors.
    cache = Path(f"/tmp/geo_emb_{a.embedder}_{a.kind}_{len(frames)}.npz")
    if cache.exists():
        d = np.load(cache, allow_pickle=True)
        X, scenes = d["X"], (list(d["scenes"]) if d["scenes"].size else None)
        print(f"loaded cached embeddings {cache.name}", flush=True)
    else:
        X, scenes = _embed(frames, a.embedder)
        np.savez(cache, X=X, scenes=np.array(scenes if scenes else [], dtype=object))
    labels = _cluster(X, a.threshold)
    n_clusters = len(set(labels))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    by_cluster: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        by_cluster.setdefault(int(lab), []).append(i)

    singletons = sum(1 for v in by_cluster.values() if len(v) == 1)
    sizes = sorted((len(v) for v in by_cluster.values()), reverse=True)
    print(f"\n=== {n_clusters} clusters from {len(frames)} clips "
          f"({singletons} singletons) — top sizes {sizes[:12]}", flush=True)
    print(f"=== ASK COUNT = {n_clusters} representatives "
          f"= {100*n_clusters/len(frames):.0f}% of {len(frames)} (target <10%)", flush=True)

    # Contact sheet per multi-member cluster (what PD eyeballs) + medoid asset_id +
    # dominant Places365 scene (a hint, not ground truth — PD confirms the real place).
    # Also dump a manifest the PD-naming step reads to propagate a name across the cluster.
    import json
    from collections import Counter
    manifest = {}
    for lab, idxs in sorted(by_cluster.items(), key=lambda kv: -len(kv[1])):
        if len(idxs) < 2:
            continue
        med = _medoid(idxs, X)
        sheet = OUT_DIR / f"cluster_{lab:03d}_n{len(idxs)}.jpg"
        _contact_sheet([frames[i] for i in idxs], sheet)
        hint = ""
        scene_top = None
        if scenes:
            top = Counter(scenes[i] for i in idxs).most_common(2)
            scene_top = top[0][0]
            hint = "  scene~ " + ", ".join(f"{s}×{c}" for s, c in top)
        print(f"  cluster {lab:>3} n={len(idxs):>3} medoid={keep[med]}{hint}", flush=True)
        manifest[str(lab)] = {"size": len(idxs), "medoid": keep[med],
                              "scene_hint": scene_top, "sheet": sheet.name,
                              "members": [keep[i] for i in idxs]}
    mpath = OUT_DIR / f"manifest_{a.embedder}_{a.kind}.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"\ncontact sheets + manifest ({len(manifest)} clusters) → {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
