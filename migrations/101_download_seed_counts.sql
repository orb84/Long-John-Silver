-- Persist live seed counts and source seeder snapshots for download diagnostics.
ALTER TABLE downloads ADD COLUMN num_seeds INTEGER DEFAULT 0;
ALTER TABLE downloads ADD COLUMN source_seeders INTEGER;
