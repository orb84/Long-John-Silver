DROP INDEX IF EXISTS idx_action_receipts_idempotency;

ALTER TABLE action_command_receipts ADD COLUMN request_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE action_command_receipts ADD COLUMN receipt_persisted INTEGER NOT NULL DEFAULT 1;
ALTER TABLE action_command_receipts ADD COLUMN persistence_error TEXT;

CREATE INDEX IF NOT EXISTS idx_action_receipts_scoped_idempotency
ON action_command_receipts(action_name, source, user_id, session_id, idempotency_key, completed_at DESC);

CREATE TABLE IF NOT EXISTS action_command_claims (
    action_name TEXT NOT NULL,
    source TEXT NOT NULL,
    user_scope TEXT NOT NULL DEFAULT '',
    session_scope TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    owner_command_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('running', 'completed', 'failed', 'uncertain')),
    lease_expires_at TEXT NOT NULL,
    receipt_command_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (action_name, source, user_scope, session_scope, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_action_command_claim_owner
ON action_command_claims(owner_command_id);

CREATE INDEX IF NOT EXISTS idx_action_command_claim_receipt
ON action_command_claims(receipt_command_id);

ALTER TABLE operational_events ADD COLUMN user_id TEXT;
ALTER TABLE operational_events ADD COLUMN session_id TEXT;
ALTER TABLE operational_events ADD COLUMN idempotency_key TEXT;
ALTER TABLE operational_events ADD COLUMN request_fingerprint TEXT NOT NULL DEFAULT '';
