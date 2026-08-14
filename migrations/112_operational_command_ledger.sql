CREATE TABLE IF NOT EXISTS action_command_receipts (
    command_id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    idempotency_key TEXT,
    action_name TEXT NOT NULL,
    source TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    user_id TEXT,
    session_id TEXT,
    status TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 0,
    arguments_json TEXT NOT NULL DEFAULT '{}',
    data_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_action_receipts_idempotency
ON action_command_receipts(action_name, idempotency_key)
WHERE idempotency_key IS NOT NULL AND idempotency_key <> '' AND ok = 1;

CREATE INDEX IF NOT EXISTS idx_action_receipts_correlation
ON action_command_receipts(correlation_id, completed_at DESC);

CREATE TABLE IF NOT EXISTS operational_events (
    event_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    subsystem TEXT NOT NULL DEFAULT '',
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    previous_state_json TEXT NOT NULL DEFAULT '{}',
    new_state_json TEXT NOT NULL DEFAULT '{}',
    details_json TEXT NOT NULL DEFAULT '{}',
    error_class TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_operational_events_command
ON operational_events(command_id, created_at);

CREATE INDEX IF NOT EXISTS idx_operational_events_correlation
ON operational_events(correlation_id, created_at);
