CREATE TABLE IF NOT EXISTS web_research_query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    category_id TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    intent TEXT NOT NULL DEFAULT 'general_research',
    status TEXT NOT NULL DEFAULT 'running',
    error_code TEXT NOT NULL DEFAULT '',
    result_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_web_research_query_item
    ON web_research_query_log(category_id, item_id, started_at);
CREATE INDEX IF NOT EXISTS idx_web_research_query_status
    ON web_research_query_log(status, started_at);

CREATE TABLE IF NOT EXISTS web_source_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_log_id INTEGER,
    category_id TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT 'unknown',
    source_name TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    extracted_text_hash TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    snippet TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'candidate',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(category_id, item_id, canonical_url),
    FOREIGN KEY(query_log_id) REFERENCES web_research_query_log(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_web_source_evidence_item
    ON web_source_evidence(category_id, item_id, source_kind, updated_at);
CREATE INDEX IF NOT EXISTS idx_web_source_evidence_url
    ON web_source_evidence(canonical_url);

CREATE TABLE IF NOT EXISTS category_fact_provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '',
    fact_type TEXT NOT NULL DEFAULT '',
    value_json TEXT NOT NULL DEFAULT '{}',
    source_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.0,
    decided_by TEXT NOT NULL DEFAULT 'deterministic',
    decided_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_category_fact_provenance_item
    ON category_fact_provenance(category_id, item_id, fact_type, decided_at);
