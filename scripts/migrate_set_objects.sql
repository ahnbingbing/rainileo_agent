-- Phase B migration: set_objects table for auto-extracted + PD-curated
-- per-set object knowledge.
--
-- Distinct from object_refs (PD-only Slack-curated). set_objects is
-- populated by agents/set_knowledge_builder.py from VLM analysis aggregation,
-- and Writer/Director read from BOTH tables when planning concepts.

CREATE TABLE IF NOT EXISTS set_objects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    set_anchor      TEXT NOT NULL,                  -- e.g. 'home_pet_feeding_area'
    name_ko         TEXT NOT NULL,                  -- 한국어 이름 ('파란 사료 받침대')
    name_en         TEXT,                           -- optional English alias
    description     TEXT NOT NULL,                  -- visual description for prompt embedding
    category        TEXT NOT NULL                   -- furniture/food/toy/vessel/accessory/decor
        CHECK (category IN ('furniture','food','toy','vessel','accessory','decor','other')),
    frequency       TEXT NOT NULL DEFAULT 'often'   -- always/often/sometimes
        CHECK (frequency IN ('always','often','sometimes')),
    era             TEXT,                           -- date range when applicable, e.g. '2025-11+'
    source          TEXT NOT NULL DEFAULT 'auto'    -- auto/pd_added/pd_edited
        CHECK (source IN ('auto','pd_added','pd_edited')),
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_set_objects_anchor ON set_objects(set_anchor);
CREATE INDEX IF NOT EXISTS idx_set_objects_category ON set_objects(category);
