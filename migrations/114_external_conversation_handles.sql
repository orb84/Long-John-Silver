CREATE TABLE IF NOT EXISTS external_conversation_handles (
    handle_id TEXT PRIMARY KEY,
    internal_session_id TEXT NOT NULL UNIQUE,
    principal_id TEXT NOT NULL,
    client_id TEXT NOT NULL DEFAULT '',
    user_id TEXT,
    source TEXT NOT NULL DEFAULT 'external',
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_external_conversation_principal
ON external_conversation_handles(principal_id, client_id, last_active_at DESC);
