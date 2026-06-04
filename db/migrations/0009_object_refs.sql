-- Object/item references shared via #references Slack channel.
-- PD uploads a photo of a real object (toy, basket, chives, etc.)
-- with a text description. Producer uses these to write accurate veo_prompts.

CREATE TABLE IF NOT EXISTS object_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,           -- local path to saved image
    name TEXT NOT NULL,                -- short name: "부추 소쿠리", "낚싯대 장난감"
    description TEXT NOT NULL,         -- detailed description from PD
    category TEXT DEFAULT 'object',    -- object / food / toy / furniture / clothing
    subjects TEXT DEFAULT NULL,        -- related pets: "leo", "ryani", "both"
    slack_ts TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
