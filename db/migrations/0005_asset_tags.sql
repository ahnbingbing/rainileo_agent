-- 0005: Rich VLM-based asset tags for intelligent asset selection.
-- One-time bulk analysis + incremental for new assets.

ALTER TABLE assets ADD COLUMN scene_description TEXT;     -- "랴니가 쿠션 위에서 웅크려 자고 있음, 햇살이 비침"
ALTER TABLE assets ADD COLUMN activity TEXT;              -- sleeping, eating, playing, grooming, sitting, walking, cuddling
ALTER TABLE assets ADD COLUMN has_human INTEGER DEFAULT 0; -- 0=no human, 1=human visible (avoid for shorts)
ALTER TABLE assets ADD COLUMN composition TEXT;           -- closeup, medium, wide, overhead, profile
ALTER TABLE assets ADD COLUMN lighting TEXT;              -- natural_bright, natural_dim, indoor_warm, indoor_cool, backlit
ALTER TABLE assets ADD COLUMN mood TEXT;                  -- peaceful, playful, curious, sleepy, affectionate, alert
ALTER TABLE assets ADD COLUMN background TEXT;            -- living_room, window, kitchen, outdoor, bed, couch, floor
ALTER TABLE assets ADD COLUMN quality_score REAL;         -- 0.0-1.0 overall usability for shorts
ALTER TABLE assets ADD COLUMN focus_subject TEXT;         -- who is the main focus: ryani, leo, both, neither
ALTER TABLE assets ADD COLUMN decoration_level TEXT;      -- none, light, heavy (already has stickers/overlays?)
ALTER TABLE assets ADD COLUMN best_for TEXT;              -- comma-separated: cartoon_sticker, ai_vtuber, real_footage
ALTER TABLE assets ADD COLUMN vlm_analyzed_at TEXT;       -- when VLM analysis was done
