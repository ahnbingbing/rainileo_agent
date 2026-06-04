-- 0008: Episode stories from Slack #episode channel
CREATE TABLE IF NOT EXISTS episode_stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    author TEXT,
    slack_ts TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
