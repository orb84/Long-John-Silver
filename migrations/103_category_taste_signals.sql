CREATE TABLE IF NOT EXISTS category_taste_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT '',
    category_id TEXT NOT NULL,
    item_id TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    signal_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT 'conversation',
    confidence REAL NOT NULL DEFAULT 1.0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, category_id, item_id, signal_type, source)
);

CREATE INDEX IF NOT EXISTS idx_taste_signals_category ON category_taste_signals(user_id, category_id, signal_type);
CREATE INDEX IF NOT EXISTS idx_taste_signals_item ON category_taste_signals(category_id, item_id);
