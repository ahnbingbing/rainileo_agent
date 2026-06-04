-- 0002_bgm_tracks — BGM track library
--
-- Purpose
--   Track every royalty-free / CC0 audio file under assets/bgm/ so the
--   Cameraman agent can auto-pick a fitting BGM per card based on
--   tone_primary, energy, and recency (last_used_iso) for variety.
--
-- Idempotent: CREATE TABLE / INDEX IF NOT EXISTS only.

CREATE TABLE IF NOT EXISTS bgm_tracks (
    track_id            TEXT PRIMARY KEY,                -- pixabay numeric id (or hash)
    file_path           TEXT NOT NULL UNIQUE,
    filename            TEXT NOT NULL,
    artist              TEXT,
    title               TEXT,
    source              TEXT NOT NULL DEFAULT 'pixabay'
                        CHECK (source IN ('pixabay','yt_audio_library','freepd','internet_archive','jamendo','custom','other')),
    license             TEXT NOT NULL DEFAULT 'pixabay_content_license',

    duration_sec        REAL,
    bitrate             INTEGER,

    -- Auto-tag (filename-keyword based; flip manual_review=1 after human listen)
    tone_tag            TEXT NOT NULL DEFAULT 'unsorted'
                        CHECK (tone_tag IN ('warm','fun','calm','trends','unsorted')),
    energy_tag          TEXT NOT NULL DEFAULT 'mid'
                        CHECK (energy_tag IN ('low','mid','high')),
    instrument_csv      TEXT,                            -- e.g. 'ukulele,whistle'
    vibe_csv            TEXT,                            -- e.g. 'sweet,nostalgic'
    auto_tag_confidence REAL NOT NULL DEFAULT 0.0,       -- 0.0..1.0
    manual_review       INTEGER NOT NULL DEFAULT 0,      -- 0=auto, 1=human verified

    -- Variety / scheduling
    use_count           INTEGER NOT NULL DEFAULT 0,
    last_used_iso       TEXT,
    last_used_card_id   TEXT,

    added_at            TEXT NOT NULL DEFAULT (datetime('now')),
    notes               TEXT
);
CREATE INDEX IF NOT EXISTS idx_bgm_tone     ON bgm_tracks(tone_tag);
CREATE INDEX IF NOT EXISTS idx_bgm_energy   ON bgm_tracks(energy_tag);
CREATE INDEX IF NOT EXISTS idx_bgm_lastused ON bgm_tracks(last_used_iso);
CREATE INDEX IF NOT EXISTS idx_bgm_review   ON bgm_tracks(manual_review);
