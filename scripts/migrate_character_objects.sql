-- Phase F: character_objects table
-- Stores VLM-aggregated recurring appearance items per human character.
-- Parallel to set_objects (Phase B), populated by populate_character_objects.py
-- which reads enriched character_library.json `recurring_outfits[]`.

CREATE TABLE IF NOT EXISTS character_objects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id  TEXT NOT NULL,        -- "grandma" / "grandpa" / "imo" / "cousin_sister" / "owner"
    name_ko       TEXT NOT NULL,        -- e.g. "꽃무늬 카디건"
    name_en       TEXT,
    description   TEXT NOT NULL,        -- visual description suitable for Veo/Seedance prompt
    category      TEXT NOT NULL,        -- "outfit"|"hair"|"accessory"|"body_feature"|"footwear"
    frequency     TEXT NOT NULL,        -- "always"|"often"|"sometimes"
    era           TEXT,                 -- date range if changed over time
    source        TEXT NOT NULL,        -- "phase_f_auto"|"pd_added"|"pd_confirmed"
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(character_id, name_ko)
);

CREATE INDEX IF NOT EXISTS idx_char_objects_char ON character_objects(character_id);
CREATE INDEX IF NOT EXISTS idx_char_objects_freq ON character_objects(frequency);
