-- Persist category-owned lifecycle/suggestion state so startup and periodic jobs
-- can skip expensive provider/LLM work until an item is due or meaningfully invalidated.
CREATE TABLE IF NOT EXISTS category_item_processing_state (
    category_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    metadata_fingerprint TEXT NOT NULL DEFAULT '',
    library_fingerprint TEXT NOT NULL DEFAULT '',
    taste_fingerprint TEXT NOT NULL DEFAULT '',
    suggestion_fingerprint TEXT NOT NULL DEFAULT '',
    last_processed_at TEXT,
    next_check_at TEXT,
    next_check_reason TEXT NOT NULL DEFAULT '',
    valid_until TEXT,
    policy_version INTEGER NOT NULL DEFAULT 1,
    invalidated_by TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (category_id, item_id),
    FOREIGN KEY(category_id, item_id)
        REFERENCES category_items(category_id, item_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS category_item_processing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    metadata_fingerprint TEXT NOT NULL DEFAULT '',
    library_fingerprint TEXT NOT NULL DEFAULT '',
    taste_fingerprint TEXT NOT NULL DEFAULT '',
    suggestion_fingerprint TEXT NOT NULL DEFAULT '',
    policy_version INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS category_item_suggestion_state (
    category_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    suggestion_key TEXT NOT NULL,
    suggestion_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    title TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    suggestion_fingerprint TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    valid_until TEXT,
    invalidated_by TEXT NOT NULL DEFAULT '[]',
    policy_version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (category_id, item_id, suggestion_key),
    FOREIGN KEY(category_id, item_id)
        REFERENCES category_items(category_id, item_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_processing_due ON category_item_processing_state(next_check_at, category_id, item_id);
CREATE INDEX IF NOT EXISTS idx_processing_events_item ON category_item_processing_events(category_id, item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_suggestion_state_item ON category_item_suggestion_state(category_id, item_id, status, valid_until);
