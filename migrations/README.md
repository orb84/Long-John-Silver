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
- `107_scheduled_task_timing.sql` — one-off reminders, recurring assistant prompts, condition checks, and due-time bookkeeping.

## Migration authoring notes

Round 119 hardened the migration runner after startup failed on a semicolon inside a SQL `--` comment in migration 107. The runner now strips SQL comments and splits only on statement-terminating semicolons outside quoted strings before calling `sqlite.execute`.

Keep migrations additive and simple. Comments are allowed, including comments with semicolons, but prose must never be relied on as part of executable SQL. Prefer one DDL statement per semicolon-terminated block so duplicate-column recovery can continue to work during partially upgraded installs.

## 108 — Metadata cache and provider rate limits

Adds `category_metadata_cache` for provider result caching and `provider_rate_limits` for persisted upstream backoff/rate-limit state. Fresh installs also create these tables in the base schema.

## 110 — Web research evidence provenance

Adds category-neutral public web-research provenance tables: `web_research_query_log`, `web_source_evidence`, and `category_fact_provenance`. These tables store search/query logs, fetched source evidence, and later category-owned fact decisions. Search snippets remain discovery leads, not durable facts.

## 111 — Web information watches

Adds durable proactive web-information tracking tables: `web_information_watch` and `web_information_watch_event`. Watches store user/category tracking objectives, cadence, query plans, notification policy, and last evidence signature. Events store run outcomes and provenance references. The watch layer is public-information tracking only; it does not mutate category items or queue downloads.
