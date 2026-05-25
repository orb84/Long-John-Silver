-- Persist stable provider/download identity from torrent selection through library import.
ALTER TABLE downloads ADD COLUMN import_context_json TEXT NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_downloads_import_context ON downloads(category_id, item_id, season, episode);
