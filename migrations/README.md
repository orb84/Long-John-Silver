# Migrations

This scaffold targets a fresh category-first database. The initial schema is
created in `src/core/database.py` and intentionally avoids legacy show/movie
special-case tables. Future schema changes should be additive migration files
starting at version 101.

## Current additive migrations

- `101_download_seed_counts.sql` — download seed count persistence.
- `102_library_sharing_seed_in_place.sql` — seed-in-place library sharing metadata.
- `103_category_taste_signals.sql` — raw category taste evidence.
- `104_evidence_based_taste_profiles.sql` — persisted evidence-derived taste snapshots.
- `105_download_import_context.sql` — download/import context preservation.
- `106_item_lifecycle_policy_ledger.sql` — item-scoped lifecycle processing, events, and suggestion validity ledgers.
