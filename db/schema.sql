-- Ryani & Leo Agent — Phase 0 SQLite schema
-- All timestamps are ISO-8601 UTC unless suffixed _kst.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ──────────────────────────────────────────────────────────────────────
-- 1. milestones (anniversary calendar)
-- ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS milestones (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    tag                         TEXT NOT NULL,
    month                       INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    day                         INTEGER NOT NULL CHECK (day BETWEEN 1 AND 31),
    recurrence                  TEXT NOT NULL CHECK (recurrence IN ('annual_solar','annual_lunar','one_time','birthday')),
    memory_lane_default_variant TEXT NOT NULL CHECK (memory_lane_default_variant IN ('solo_archive','side_by_side','imagined_together')),
    imagined_youth_allowed      INTEGER NOT NULL DEFAULT 0,
    subjects_csv                TEXT NOT NULL,
    notes                       TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_milestones_md ON milestones(month, day);

-- ──────────────────────────────────────────────────────────────────────
-- 2. subjects (Ryani, Leo)
-- ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subjects (
    id              TEXT PRIMARY KEY,             -- 'ryani' | 'leo'
    species         TEXT NOT NULL,
    born_iso        TEXT NOT NULL,
    born_estimate   INTEGER NOT NULL DEFAULT 0,
    adopted_iso     TEXT,
    notes           TEXT
);

-- ──────────────────────────────────────────────────────────────────────
-- 3. assets (media: photos, videos, illustrations)
-- ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
    asset_id        TEXT PRIMARY KEY,             -- e.g. med_2026_05_09_181523_slack_*
    source          TEXT NOT NULL CHECK (source IN ('slack','icloud','archive','illustration','external')),
    kind            TEXT NOT NULL CHECK (kind IN ('photo','video','illustration')),
    file_path       TEXT NOT NULL,
    captured_iso    TEXT,                         -- when the moment was captured
    ingested_iso    TEXT NOT NULL DEFAULT (datetime('now')),
    duration_sec    REAL,
    width           INTEGER,
    height          INTEGER,
    phash           TEXT,                         -- LEGACY: 64-hex SHA-256 content hash (exact-dup only; NULL for video)
    vis_phash       TEXT,                         -- perceptual hash: photo=256-bit; video=multi-frame 'h1,h2,..' signature (agents/visual_hash.py)
    subjects_csv    TEXT,                         -- 'ryani', 'leo', 'leo,ryani'
    age_tag         TEXT,                         -- 'youth', 'adult', 'mixed', NULL
    location_tag    TEXT,                         -- 'living_room', 'park_path_north', etc.
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_assets_captured ON assets(captured_iso);
CREATE INDEX IF NOT EXISTS idx_assets_subjects ON assets(subjects_csv);
CREATE INDEX IF NOT EXISTS idx_assets_age ON assets(age_tag);

-- ──────────────────────────────────────────────────────────────────────
-- 4. cards (Concept Card v2 records)
-- ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cards (
    card_id              TEXT PRIMARY KEY,
    date                 TEXT NOT NULL,           -- target publish date (KST, YYYY-MM-DD)
    created_at           TEXT NOT NULL,
    author               TEXT NOT NULL,
    card_type            TEXT NOT NULL CHECK (card_type IN ('daily','memory_lane')),
    theme                TEXT,
    tone_primary         TEXT NOT NULL,
    tone_intensity       REAL,
    seasonal_tag         TEXT,
    trend_id             TEXT,
    memory_lane_variant  TEXT,                    -- NULL when card_type='daily'
    memory_lane_milestone TEXT,
    illustration_style   TEXT,
    background_id        TEXT,
    background_phash     TEXT,
    duration_target_sec  INTEGER,
    writer_confidence    REAL,
    ask_pd               INTEGER NOT NULL DEFAULT 0,
    ask_reason           TEXT,
    state                TEXT NOT NULL DEFAULT 'draft'
                         CHECK (state IN ('draft','pd_review','approved','rejected','rendered','published','archived')),
    payload_json         TEXT NOT NULL,           -- full Concept Card v2 JSON
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cards_date ON cards(date);
CREATE INDEX IF NOT EXISTS idx_cards_state ON cards(state);

-- ──────────────────────────────────────────────────────────────────────
-- 5. card_assets (M:N — which assets are referenced by a card)
-- ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS card_assets (
    card_id       TEXT NOT NULL,
    asset_id      TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('primary','supporting','fallback')),
    trim_start    REAL,
    trim_end      REAL,
    PRIMARY KEY (card_id, asset_id, role),
    FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id) REFERENCES assets(asset_id) ON DELETE RESTRICT
);

-- ──────────────────────────────────────────────────────────────────────
-- 6. runs (agent execution audit log)
-- ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent           TEXT NOT NULL CHECK (agent IN ('writer','pd','cameraman','memory','scheduler')),
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','error','skipped')),
    card_id         TEXT,
    input_snapshot  TEXT,                         -- JSON
    output_snapshot TEXT,                         -- JSON
    error_message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);

-- ──────────────────────────────────────────────────────────────────────
-- 7. tone_history (weekly Warm/Fun/Trends balance tracking)
-- ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tone_history (
    date            TEXT PRIMARY KEY,             -- YYYY-MM-DD
    tone_primary    TEXT NOT NULL,
    intensity       REAL NOT NULL,
    card_id         TEXT,
    FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE SET NULL
);

-- ──────────────────────────────────────────────────────────────────────
-- 8. background_history (daily-change + 7d 70% pHash variety rule)
-- ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS background_history (
    date            TEXT PRIMARY KEY,
    background_id   TEXT NOT NULL,
    phash           TEXT NOT NULL,
    card_id         TEXT,
    FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE SET NULL
);

-- ──────────────────────────────────────────────────────────────────────
-- 9. trends (active trend pool + expiry)
-- ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trends (
    trend_id        TEXT PRIMARY KEY,
    source          TEXT NOT NULL,                -- youtube_shorts, instagram_reels, ...
    category        TEXT NOT NULL CHECK (category IN ('format','challenge','meme','audio')),
    title           TEXT,
    fit_score       REAL,
    expiry_date     TEXT NOT NULL,
    discovered_at   TEXT NOT NULL DEFAULT (datetime('now')),
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_trends_expiry ON trends(expiry_date);
