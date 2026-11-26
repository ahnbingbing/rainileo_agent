"""Image-cluster the GPS-less clips so PD grounds a whole place by answering a few
representatives — not every clip (PD 2026-11-26).

~5.8k iCloud assets genuinely lack GPS (shared/stripped/location-off — verified by
downloading the iCloud originals; the map can't ground them). PD's plan: cluster by
VISUAL similarity (same day ≠ same place, different days CAN be the same place — so it
must be IMAGE clustering, not date), ask PD (the only one who was there) about a few
representatives per cluster, and PROPAGATE the answer across the cluster. Target: answer
<10% of the total.

This is the PoC + builder: extract one representative frame per ungrounded clip, embed
with CLIP (open_clip ViT-B-32), cluster with agglomerative cosine linkage, and write a
per-cluster contact sheet so the cluster quality is EYEBALLED before any question is asked.
Prints the ask-count (= #clusters) vs total so we can see the <10% target is met.

    python -m scripts.cluster_ungrounded_geo --kind video --threshold 0.18
    python -m scripts.cluster_ungrounded_geo --kind video --limit 120   # quick PoC
"""
import argparse
import os
import subprocess
from pathlib import Path

import numpy as np

from agents.producer import _db

ROOT = Path(__file__).resolve().parents[1]
FRAME_DIR = ROOT / "data" / "tmp" / "geo_frames"
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


def _frame(aid: str, kind: str, fp: str, dur: float | None) -> Path | None:
    """One representative 224px frame per asset, cached. Video → mid-point frame."""
    out = FRAME_DIR / f"{aid}.jpg"
    if out.exists():
        return out
    src = Path(fp) if os.path.isabs(fp) else ROOT / fp
    if not src.exists():
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "video":
            ss = max(0.0, (float(dur or 2.0)) / 2.0)
            subprocess.run(["ffmpeg", "-nostdin", "-ss", f"{ss:.2f}", "-i", str(src),
                            "-frames:v", "1", "-vf", "scale=224:224:force_original_aspect_ratio=increase,crop=224:224",
                            "-y", str(out)], capture_output=True, timeout=60)
        else:
            from PIL import Image
            im = Image.open(src).convert("RGB")
            im.thumbnail((224, 224))
            im.save(out, "JPEG")
    except Exception:
        return None
    return out if out.exists() else None


def _embed(frames: list[Path]) -> np.ndarray:
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
    return np.vstack(vecs).astype(np.float32)


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
    ap.add_argument("--threshold", type=float, default=0.18, help="cosine distance; lower = tighter")
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

    X = _embed(frames)
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

    # Contact sheet per multi-member cluster (what PD eyeballs) + medoid asset_id.
    for lab, idxs in sorted(by_cluster.items(), key=lambda kv: -len(kv[1])):
        if len(idxs) < 2:
            continue
        med = _medoid(idxs, X)
        _contact_sheet([frames[i] for i in idxs], OUT_DIR / f"cluster_{lab:03d}_n{len(idxs)}.jpg")
        print(f"  cluster {lab:>3} n={len(idxs):>3} medoid={keep[med]}", flush=True)
    print(f"\ncontact sheets → {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
