-- Add seed-in-place library sharing telemetry to download rows.
ALTER TABLE downloads ADD COLUMN save_path TEXT NOT NULL DEFAULT '';
ALTER TABLE downloads ADD COLUMN sharing_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE downloads ADD COLUMN uploaded_bytes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE downloads ADD COLUMN seed_ratio REAL NOT NULL DEFAULT 0.0;
CREATE INDEX IF NOT EXISTS idx_downloads_sharing ON downloads(sharing_enabled, status, category_id, item_id);
