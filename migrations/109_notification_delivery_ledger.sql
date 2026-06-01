-- Round 208: bridge-delivery ledger for durable notifications.
--
-- The notifications table deduplicates the web inbox.  External bridges need
-- their own delivery state because a notification can be unread in the web UI
-- while already delivered to Discord/Telegram/WhatsApp.  Without this table,
-- registering a bridge on every app launch replays the same unread completion
-- notifications forever.
CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id INTEGER NOT NULL,
    bridge_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(notification_id, bridge_id),
    FOREIGN KEY(notification_id) REFERENCES notifications(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_bridge
    ON notification_deliveries(bridge_id, status, updated_at);
