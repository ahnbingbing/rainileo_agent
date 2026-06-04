-- 0006: Retry log for Giri feedback-driven auto-fix loop.
-- Tracks each attempt, what Giri said, what fix was applied, and whether it worked.

CREATE TABLE IF NOT EXISTS retry_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         TEXT NOT NULL,
    attempt         INTEGER NOT NULL,
    giri_score      REAL,
    giri_verdict    TEXT,
    issue_type      TEXT,
    fix_applied     TEXT,
    fixed           INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_retry_card ON retry_log(card_id);
CREATE INDEX IF NOT EXISTS idx_retry_issue ON retry_log(issue_type);
