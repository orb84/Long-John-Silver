-- Round 126: persistent category metadata cache and provider rate-limit state.

CREATE TABLE IF NOT EXISTS category_metadata_cache (
    category_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    stable_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok',
    payload_json TEXT NOT NULL DEFAULT '{}',
    provider_signature TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_accessed_at TEXT NOT NULL DEFAULT (datetime('now')),
    hit_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (category_id, provider, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_category_metadata_cache_expiry
    ON category_metadata_cache(category_id, provider, expires_at);

CREATE INDEX IF NOT EXISTS idx_category_metadata_cache_stable_id
    ON category_metadata_cache(category_id, stable_id);

CREATE TABLE IF NOT EXISTS provider_rate_limits (
    provider TEXT PRIMARY KEY,
    next_allowed_at TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '',
    remaining TEXT NOT NULL DEFAULT '',
    reset_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
