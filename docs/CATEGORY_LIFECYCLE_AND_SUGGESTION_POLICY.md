# Category Lifecycle, Suggestion, and Taste Policy

LJS must behave as a category-first system. The core app should not know that TV has episodes, games have versions, books have editions, or comics have issues. The core owns persistence, scheduling, fingerprints, and ledgers. Each category owns the domain policy that explains what matters and when work is useful.

## Design principle

Every tracked item has item-scoped lifecycle state. Expensive work is run only when the item is new, due, manually refreshed, or invalidated by a meaningful change.

The core lifecycle engine is intentionally generic:

- compute stable fingerprints for metadata, library units, taste snapshots, and suggestions;
- persist last processed time, next useful check, validity window, policy version, and invalidation reasons;
- append processing/invalidation events for auditability;
- call category-owned workflows only after the ledger says the item is due;
- keep saved suggestions valid until their inputs or category policy change.

Categories own the policy:

- identity fields;
- lifecycle fields;
- suggestion types;
- invalidation triggers;
- check intervals and next-check decisions;
- LLM policy descriptions used for bounded future decisions;
- taste facets and evidence weights.

## Persistent ledgers

Migration `106_item_lifecycle_policy_ledger.sql` adds three tables.

### `category_item_processing_state`

One row per `(category_id, item_id)` stores the latest lifecycle state:

- `metadata_fingerprint`
- `library_fingerprint`
- `taste_fingerprint`
- `suggestion_fingerprint`
- `last_processed_at`
- `next_check_at`
- `next_check_reason`
- `valid_until`
- `policy_version`
- `invalidated_by`

This is the scheduler's cheap answer to: “do we actually need to do anything for this item?”

### `category_item_processing_events`

Append-only processing and invalidation events. This is useful for debugging startup churn, provider calls, and suggestion refreshes.

### `category_item_suggestion_state`

Stable per-suggestion validity records. The existing `suggested_actions` table still backs the current UI, but this table records item-scoped validity, invalidation, policy version, and suggestion fingerprints.

## Runtime flow

### Startup

Startup performs cheap ledger reconciliation for tracked items. It creates missing state rows and computes fingerprints from local database state. It must not rebuild taste profiles or recompile suggestions for every item at boot.

### Library scan

A library scan may change category units or file state. When that happens the media repository marks the item invalidated with `library_scan_changed`. The next lifecycle pass can then re-run only the affected item.

### Scheduled update

The scheduler asks `CategoryLifecycleEngine.should_process_item(...)` before calling category workflows. If fingerprints are unchanged and `next_check_at` is in the future, the workflow is skipped. The category policy decides the next useful check.

### Suggestions

`SuggestionCompiler` now routes item suggestion compilation through `CategoryLifecycleEngine.compile_suggestions_for_item(...)`. Suggestions are generated only when due or invalidated, then persisted with validity metadata. If nothing changed, the existing suggestions remain active.

## Category policy contract

Each category can override these methods on `CategoryMedia`:

```python
def lifecycle_policy(self) -> dict[str, Any]: ...
def lifecycle_decision(self, item: Any, context: dict[str, Any]) -> dict[str, Any]: ...
def suggestion_policy(self) -> dict[str, Any]: ...
```

A lifecycle policy should look like this:

```json
{
  "policy_version": 2,
  "identity_fields": ["provider", "external_id", "title"],
  "lifecycle_fields": ["status", "metadata", "library_units", "taste_snapshot"],
  "suggestion_types": ["missing_item", "quality_upgrade", "metadata_repair"],
  "invalidation_triggers": [
    "metadata_changed",
    "library_changed",
    "taste_changed",
    "download_completed",
    "download_failed",
    "manual_refresh",
    "policy_version_changed"
  ],
  "default_check_interval_days": 90,
  "llm_policy_description": "Category-specific guidance for bounded policy decisions."
}
```

`policy_version` is important. Bump it when category policy changes in a way that should invalidate old suggestions or processing state.

## Examples

### TV

TV categories care about airing state, next air date, season completeness, missing episodes, language, quality, release cadence, and specials. A show with a known future air date should keep suggestions stable until shortly after that date unless the local library, provider metadata, user preferences, or download state changes.

### Movies

Movies are usually stable after release. They may still need metadata repair, quality/language upgrade checks, or related-media suggestions, but they should not behave like an actively airing show.

### Games

Games should eventually define lifecycle around platform, current version, latest known version, DLC, editions, compatibility, and release status. The core should not add game-specific branches; a game category should express this policy itself.

### Books

Books often need very little recurring work unless they belong to an unfinished saga, have edition/language/audiobook monitoring enabled, or the user changes preferences.

## Taste system integration

Taste is part of lifecycle invalidation, not an isolated side system.

The current taste profiler already records category-scoped evidence and snapshots. The lifecycle engine fingerprints the persisted taste snapshot for each category. If the taste profile changes, item suggestions can become invalid because candidate ranking, related suggestions, and quality/language preferences may change.

Prompt construction should prefer persisted taste snapshots over rebuilding category profiles for every chat turn. Rebuilding remains available as a fallback, but regular assistant context should use the stored snapshot.

Taste rules remain category-owned:

- store raw evidence first;
- keep likes, dislikes, curiosity, downloads, accepts, rejects, and mentions scoped to the category/item/facet;
- do not turn metadata existence into preference evidence;
- do not infer broad dislike from one rejected item unless the user says so;
- allow non-library conversational mentions to enrich taste after category-owned metadata enrichment.

## Category creation requirements

Generated categories must include a starter lifecycle policy, suggestion policy, and conservative lifecycle decision. The scaffold should warn when lifecycle fields or suggestion types are missing, because that means the generated category is still too generic for heavy automation.

The rule for future work is simple:

> If a behavior depends on domain meaning, add it to the category policy or category workflow. Do not add a new category-specific branch to scheduler, assistant, UI, or generic repositories.
