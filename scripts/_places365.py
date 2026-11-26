"""Places365 scene recognition — the SCENE-centric embedder/classifier for GPS-less geo
grounding (PD 2026-11-26).

Full-frame CLIP embeds the OBJECT (the black Frenchie dominates the vector), so 'same pet,
different place' wrongly merges — the 246-blob. A ResNet18 trained on Places365 instead
names the PLACE a photo is of and is robust to foreground subjects, which is exactly what
we need to (a) cluster GPS-less clips by venue and (b) derive a conservative water class
that can REFUTE an "ocean/beach" hallucination without any PD question.

Weights + categories live under data/models/ (gitignored, Mac-local — the scene backfill
only runs where the Photos frames are). Download once:
    curl -L -o data/models/resnet18_places365.pth.tar \
        http://places2.csail.mit.edu/models_places365/resnet18_places365.pth.tar
    curl -L -o data/models/categories_places365.txt \
        https://raw.githubusercontent.com/csailvision/places365/master/categories_places365.txt
Both the cluster builder and the scene-water backfill import from here so the taxonomy and
the model load stay in one place. Heavy (torch) imports are lazy — importing this module is
cheap; nothing loads until predict() is called.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
_WEIGHTS = ROOT / "data" / "models" / "resnet18_places365.pth.tar"
_CATS = ROOT / "data" / "models" / "categories_places365.txt"


def available() -> bool:
    return _WEIGHTS.exists() and _CATS.exists()


def categories() -> list[str]:
    """Scene names without the '/a/..' prefix: '/a/airfield 0' → 'airfield',
    '/b/bow_window/indoor 44' → 'bow_window/indoor'."""
    out = []
    for line in _CATS.read_text().splitlines():
        name = line.strip().split(" ")[0]
        out.append(name[3:] if name.startswith("/") else name)
    return out


def _load():
    import torch
    import torchvision.transforms as T
    from torchvision.models import resnet18
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = resnet18(num_classes=365)
    ckpt = torch.load(_WEIGHTS, map_location="cpu", weights_only=False)
    state = {k.replace("module.", ""): v for k, v in ckpt["state_dict"].items()}
    model.load_state_dict(state)
    model.eval().to(device)
    preprocess = T.Compose([
        T.Resize((224, 224)),
        T.ToTensor(),
        T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    return model, preprocess, device


def predict(frames: list[Path], topk: int = 3, batch: int = 64, progress: bool = True):
    """Run Places365 over image paths.

    Returns (features, topk_scenes):
      features     — (N, 512) L2-normalized penultimate avgpool vectors (the place embedding
                     used for clustering).
      topk_scenes  — list of N lists, each the topk scene-category names (highest first),
                     used for the conservative water classification.
    """
    import torch
    from PIL import Image
    model, preprocess, device = _load()
    cats = categories()
    feats_out: list = []
    hook = model.avgpool.register_forward_hook(
        lambda _m, _i, out: feats_out.append(out.squeeze(-1).squeeze(-1).cpu()))
    vecs: list = []
    scenes: list[list[str]] = []
    with torch.no_grad():
        for i in range(0, len(frames), batch):
            chunk = frames[i:i + batch]
            imgs = torch.stack([preprocess(Image.open(f).convert("RGB")) for f in chunk]).to(device)
            feats_out.clear()
            logits = model(imgs)
            f = feats_out[0]
            f = f / f.norm(dim=-1, keepdim=True)
            vecs.append(f.numpy())
            for row in logits.topk(topk, dim=1).indices.cpu().tolist():
                scenes.append([cats[j] for j in row])
            if progress:
                print(f"  places365 {min(i + batch, len(frames))}/{len(frames)}", flush=True)
    hook.remove()
    return np.vstack(vecs).astype(np.float32), scenes
