"""agents/asset_embed.py — embedding-similarity search over the real asset library.

PD 2026-06-15: the winning AV method (first-swim) anchors each generated still on a
REAL frame that looks like the intended cut, then gen_still_multiref + Giri-pick from
it. To automate "find the most similar real cut from our footage", we need a vector
search — the VLM scene_descriptions were rich but stored as plain text, not a vector
DB ("VLM이 벡터DB가 아니라 걱정"). This module embeds them.

Approach (no heavy local deps — torch/CLIP/vertexai are NOT installed):
- Semantic vector = Gemini `text-embedding-004` over each asset's VLM description
  (scene_description + activity + subjects). This makes the VLM corpus a real vector
  DB: cosine-nearest search for "a cut like THIS".
- Optional visual re-rank via agents/visual_hash (pHash) when a reference image exists.
- Pixel-level CLIP/multimodal embedding is a future upgrade (needs install + GCP).

Index persisted to data/asset_embeddings.npz (vectors + asset_ids + meta).

    from agents import asset_embed
    asset_embed.build_index()                       # one-time / incremental
    hits = asset_embed.find_similar(
        "랴니가 파란 수영장에서 개헤엄으로 헤엄친다", k=5,
        kind="video", subject="ryani")
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DB = ROOT / "data" / "agent.db"
INDEX = ROOT / "data" / "asset_embeddings.npz"
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")


def _client():
    from google import genai
    return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


def embed_texts(texts: list[str]) -> "list[list[float]]":
    """Embed a batch of texts with Gemini text-embedding-004."""
    client = _client()
    out = []
    # The SDK embeds one content per call reliably; batch in a loop (cheap).
    for t in texts:
        r = client.models.embed_content(model=EMBED_MODEL, contents=(t or " ")[:8000])
        vec = r.embeddings[0].values if getattr(r, "embeddings", None) else r.embedding.values
        out.append(list(vec))
    return out


def _asset_doc(row: dict) -> str:
    parts = [row.get("scene_description") or "", row.get("activity") or "",
             row.get("subjects_csv") or ""]
    return " | ".join(p for p in parts if p)


def build_index(rebuild: bool = False) -> int:
    """Embed every asset's VLM description into the vector index (incremental)."""
    import numpy as np
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(assets)")}
    sel = ["asset_id", "kind", "scene_description"]
    for c in ("activity", "subjects_csv", "captured_iso", "duration_sec", "file_path"):
        if c in cols:
            sel.append(c)
    rows = [dict(r) for r in con.execute(f"SELECT {','.join(sel)} FROM assets")]
    rows = [r for r in rows if (r.get("scene_description") or "").strip()]

    have: dict[str, list] = {}
    if INDEX.exists() and not rebuild:
        d = np.load(INDEX, allow_pickle=True)
        for aid, vec in zip(d["ids"], d["vecs"]):
            have[str(aid)] = vec
    todo = [r for r in rows if r["asset_id"] not in have]
    if todo:
        docs = [_asset_doc(r) for r in todo]
        # batch in chunks for resilience
        vecs = []
        B = 32
        for i in range(0, len(docs), B):
            vecs.extend(embed_texts(docs[i:i + B]))
        for r, v in zip(todo, vecs):
            have[r["asset_id"]] = v
    ids = list(have.keys())
    arr = np.array([have[i] for i in ids], dtype="float32")
    meta = {r["asset_id"]: {k: r.get(k) for k in ("kind", "subjects_csv", "activity",
            "captured_iso", "file_path", "scene_description")} for r in rows}
    np.savez(INDEX, ids=np.array(ids), vecs=arr,
             meta=np.array([json.dumps(meta.get(i, {}), ensure_ascii=False) for i in ids]))
    return len(todo)


def find_similar(query: str, k: int = 5, *, kind: str | None = None,
                 subject: str | None = None, min_dur: float | None = None) -> list[dict]:
    """Cosine-nearest real assets to a text query, with optional filters.

    Returns [{asset_id, score, file_path, scene_description, kind, ...}] best-first.
    """
    import numpy as np
    if not INDEX.exists():
        build_index()
    d = np.load(INDEX, allow_pickle=True)
    ids = [str(x) for x in d["ids"]]
    vecs = d["vecs"].astype("float32")
    metas = [json.loads(m) for m in d["meta"]]
    qv = np.array(embed_texts([query])[0], dtype="float32")

    def norm(a):
        n = np.linalg.norm(a, axis=-1, keepdims=True)
        return a / np.clip(n, 1e-8, None)
    sims = (norm(vecs) @ norm(qv.reshape(1, -1)).reshape(-1))
    order = np.argsort(-sims)
    out = []
    for idx in order:
        m = metas[idx]
        if kind and (m.get("kind") != kind):
            continue
        if subject and subject.lower() not in (m.get("subjects_csv") or "").lower():
            continue
        if min_dur and (m.get("duration_sec") or 0) < min_dur:
            continue
        out.append({"asset_id": ids[idx], "score": float(sims[idx]),
                    "file_path": m.get("file_path"),
                    "scene_description": (m.get("scene_description") or "")[:160],
                    "kind": m.get("kind"), "subjects_csv": m.get("subjects_csv")})
        if len(out) >= k:
            break
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--query", default="")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--kind", default=None)
    ap.add_argument("--subject", default=None)
    a = ap.parse_args()
    if a.build or a.rebuild:
        n = build_index(rebuild=a.rebuild)
        print(f"indexed {n} new assets → {INDEX}")
    if a.query:
        for h in find_similar(a.query, k=a.k, kind=a.kind, subject=a.subject):
            print(f"{h['score']:.3f} [{h['kind']}] {h['asset_id'][:40]} | {h['scene_description']}")
