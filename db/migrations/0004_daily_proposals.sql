-- 0004: Daily proposals table for the Producer agent.
-- Tracks concept proposals, PD feedback, and production status.

CREATE TABLE IF NOT EXISTS daily_proposals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_date     TEXT NOT NULL,
    proposed_at     TEXT NOT NULL DEFAULT (datetime('now')),
    proposal_json   TEXT NOT NULL,
    thread_ts       TEXT,
    pd_feedback     TEXT,
    finalized_json  TEXT,
    status          TEXT NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed','confirmed','produced','published')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_proposals_date ON daily_proposals(target_date);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON daily_proposals(status);
