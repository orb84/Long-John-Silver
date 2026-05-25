ALTER TABLE category_taste_signals ADD COLUMN polarity TEXT NOT NULL DEFAULT 'neutral';
ALTER TABLE category_taste_signals ADD COLUMN strength REAL NOT NULL DEFAULT 0.0;
ALTER TABLE category_taste_signals ADD COLUMN interpreted_facets_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE category_taste_signals ADD COLUMN evidence_text TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS category_taste_facet_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT '',
    category_id TEXT NOT NULL,
    facet_key TEXT NOT NULL,
    facet_value TEXT NOT NULL,
    affinity REAL NOT NULL DEFAULT 0.0,
    positive_score REAL NOT NULL DEFAULT 0.0,
    negative_score REAL NOT NULL DEFAULT 0.0,
    confidence REAL NOT NULL DEFAULT 0.0,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    source_signal_ids_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, category_id, facet_key, facet_value)
);

CREATE TABLE IF NOT EXISTS category_taste_profile_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT '',
    category_id TEXT NOT NULL,
    profile_json TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '',
    evidence_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_taste_facets_category ON category_taste_facet_scores(user_id, category_id, facet_key);
CREATE INDEX IF NOT EXISTS idx_taste_snapshots_category ON category_taste_profile_snapshots(user_id, category_id);
