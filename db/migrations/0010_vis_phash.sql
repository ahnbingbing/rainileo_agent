-- 0010: Add vis_phash — a REAL perceptual hash/signature for visual similarity.
-- The pre-existing `phash` column holds a 64-hex SHA-256 CONTENT hash (exact-dup
-- only; visually-similar pairs score ~random) and is NULL for every video, so it is
-- useless for "how similar do two clips look". vis_phash stores a true imagehash
-- perceptual hash: photos = one 256-bit hash (64 hex); videos = a ','-joined
-- multi-frame signature (best-frame matching). Populated by agents/visual_hash.py.
-- (agents.visual_hash.ensure_column also adds this idempotently at runtime.)
ALTER TABLE assets ADD COLUMN vis_phash TEXT;
