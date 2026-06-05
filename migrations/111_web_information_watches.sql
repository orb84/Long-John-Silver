CREATE TABLE IF NOT EXISTS web_information_watch (
    id TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL DEFAULT 'user_task',
    title TEXT NOT NULL DEFAULT '',
    objective TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT 'general_research',
    category_id TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '',
    item_name TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'auto',
    cadence_minutes INTEGER NOT NULL DEFAULT 10080,
    enabled INTEGER NOT NULL DEFAULT 1,
    notify_only_if_meaningful INTEGER NOT NULL DEFAULT 1,
    llm_evaluation_required INTEGER NOT NULL DEFAULT 1,
    allow_download_queueing INTEGER NOT NULL DEFAULT 0,
    query_plan_json TEXT NOT NULL DEFAULT '{}',
    user_feedback_json TEXT NOT NULL DEFAULT '{}',
    last_run_at TEXT,
    next_run_at TEXT,
    last_event_id INTEGER,
    last_evidence_signature TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT 'never_run',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_information_watch_due
    ON web_information_watch(enabled, next_run_at, cadence_minutes);
CREATE INDEX IF NOT EXISTS idx_web_information_watch_item
    ON web_information_watch(category_id, item_id, intent, enabled);

CREATE TABLE IF NOT EXISTS web_information_watch_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    summary TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT 'no_change',
    evidence_signature TEXT NOT NULL DEFAULT '',
    source_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    query_log_ids_json TEXT NOT NULL DEFAULT '[]',
    notification_recommended INTEGER NOT NULL DEFAULT 0,
    llm_review_required INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(watch_id) REFERENCES web_information_watch(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_web_information_watch_event_watch
    ON web_information_watch_event(watch_id, created_at);
CREATE INDEX IF NOT EXISTS idx_web_information_watch_event_type
    ON web_information_watch_event(event_type, notification_recommended, created_at);
