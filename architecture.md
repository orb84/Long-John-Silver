# LJS Architecture

This document is the living architecture contract for the project. Keep it updated whenever code changes the way core systems interact. Future agents should read this file before touching library, category, suggestion, taste, download, or UI state flows.

## Non-Negotiable Boundary Rule

**The core app must never contain category-specific library semantics.**

The library core does not know what a TV show, movie, game, book, episode, chapter, track, version, DLC, or edition means. The core stores and retrieves generic envelopes. The owning category defines:

- the canonical item schema;
- the unit types and fields;
- how scanner results become item/unit/progress envelopes;
- how provider metadata is interpreted;
- how local state, provider state, and user preferences produce computed fields;
- suggestion workflows and invalidation rules;
- LLM/taste context for that category.

When tempted to write logic such as “if category is tv” or “episode means missing content” in core code, stop. Add or improve a category hook instead.

## Category Item Mutation Coordinator

`CategoryItemCoordinator` (`src/core/category_item_coordinator.py`) is the
authoritative write path for durable category-item mutations. UI routes,
assistant tools, automation, import/discovery paths, and future bridge actions
must call this coordinator, either directly or through `ActionGateway` actions
such as `category_item_add`, `category_item_update`, and
`category_item_remove`.

The coordinator owns the mutation ordering invariant:

```text
normalize mutation intent
        ↓
ask owning category to create/update the item model
        ↓
ask owning category to enrich metadata when the mutation source allows provider I/O
        ↓
persist settings + category_items repository envelope
        ↓
invalidate lifecycle ledgers
        ↓
ask scheduler to resync category watch policy / RSS / release watches
```

The coordinator is generic. It must not know what a season, episode, sports
fixture, book edition, album, or DLC is. Categories expose hooks such as
`create_item()`, `enrich_item_on_add()`, and `build_watch_plan()`; the
coordinator simply calls those hooks and persists the returned generic
envelope.

Direct writes to `settings.tracked_items` are allowed only in:

- `CategoryItemCoordinator` itself;
- startup compatibility/identity repair (`StateCoordinator`);
- explicitly documented low-level migrations or repair scripts.

New UI, agent, scheduled-task, import, scanner-discovery, or bridge code must
not append/remove tracked items by hand. Bypassing the coordinator risks exactly
the class of bugs this project has repeatedly hit: item added in one interface
but no metadata enrichment, stale RSS feeds until restart, missing release
watches, lifecycle ledgers not invalidated, or repository/settings drift.

For cheap background discovery, callers may pass mutation context such as
`source="library_scan"` and `enrich_metadata=False`. This still centralizes
persistence and watch-policy synchronization while avoiding provider storms
during startup or filesystem scans.

## Category-Owned Background Automation Safety

Background automation is not a global permission to reinterpret category state.
Release-watch retry, queued-download promotion, and startup recovery must ask the
owning category how to interpret conventional per-item automation fields such as
`auto_download`.  For TV, `auto_download=True` is the only state that may
auto-start new-episode rows; `False` and legacy/null values are hard stops.
Generic code may enforce queue slots, dedupe rows, and persist retry ledgers, but
it must not decide what a missing episode, complete season, or active-airing
season means.

Watch-plan synchronization is authoritative.  When a category rebuilds a watch
plan from canonical library/provider state, any nonterminal release-watch rows
for that item that are omitted by the new plan must be retired/cancelled.  This
prevents stale watches from continuing to search or queue units that the current
canonical object says are already present or no longer eligible.

User cancellation is persistent for the matching category unit.  Download rows
carry the category-owned `unit_descriptor`; cancellation uses that descriptor to
cancel the matching release-watch unit instead of letting retry resurrect the
same background download.

The user-visible category item row and the runtime tracked-item settings must
not be allowed to disagree for background automation.  Before unattended
scheduled checks, watch-policy sync, or release-watch retry, the scheduler asks
the owning category to reconcile settings with the persisted category item row.
For TV, any non-literal-true value in either store is an authoritative off state
and is written back to both stores before any search or queue action can run.

Scheduled category workflows must use the category watch plan for unattended
units.  They must not invent new TV episode coordinates from local progress such
as `last_episode + 1`, because provider seasons can be complete and historical
gaps are not consent to search or queue.


### Category-Owned Agent Search Normalization

Generic search/download tools preserve user wording and structured tool
arguments, but they must not parse domain vocabulary such as seasons, episodes,
volumes, discs, chapters, tracks, packs, editions, or collections from natural
language titles.  When an LLM accidentally puts category unit words inside the
`name` field, the flow is:

```text
search_media_torrents tool argument
        ↓
category.normalize_agent_search_name_argument(...) when category_id is known
        ↓
generic literal-title recovery preserving user wording
        ↓
scheduler category routing/parser evidence
        ↓
category.normalize_agent_search_units_from_name(...)
```

`MediaTitleRepair` is deliberately category-neutral.  It may recover dropped
title stopwords from the current prompt, but it must not strip words such as
season, episode, volume, album, pack, edition, or language terms.  Categories
that understand those words own the normalization hook.

Scheduler core may validate already-structured numeric tool arguments and may
ask the registry which category owns a request. It must not run fallback
English/Italian phrase parsers before category selection.  Ambiguous untracked
searches fall back to neutral `media`, not to Movie or TV, unless a category
router/parser/provider gives evidence.

User-facing download notifications should use the category-owned
`DownloadItem.unit_label` when available. Legacy `season`/`episode` fields are
only a compatibility fallback for old rows; generic notification code must not
infer a category such as TV from those fields.

### Round 263 — Release-Watch Search Suppression and Truthful Queue Receipts

A due background watch must not perform even a notification-only search when the
owning category says the watched unit is already present in the canonical library
object.  Release-watch retry therefore checks the category-owned
`discovery_already_satisfied(...)` hook before both branches: automatic queueing
and candidate-notification search.  If the hook says the unit is local, the watch
is marked completed and no provider/indexer search is attempted.

Category satisfaction checks should prefer canonical library objects.  Raw unit
rows are compatibility fallbacks only.  This keeps suggestions, release watches,
agent context, and UI state aligned around the same read model instead of letting
one subsystem believe a show is complete while another continues searching stale
units.

Queueing tools must treat scheduler receipts as authoritative.  A search result
is not a download.  A duplicate failed/cancelled/completed row is not a newly
queued download.  `queue_download` receipts must distinguish `queued`,
`already_active`, `already_complete`, and `not_queued`; agent tools may report
success only for verified active queue receipts and must surface partial failures
instead of turning them into a successful batch.

### Round 282 — Canonical Completion, Selective Bundles, and Action Truth

A terminal download row is transfer history, not proof that the requested
logical unit still exists in the library.  Before a manual request is blocked by
a matching `complete` row, generic queue orchestration must ask the owning
category to compare the category-owned unit descriptor with the current
canonical library object.  Only a positive category verdict may report
`already_complete`.  A verified canonical absence may revive or replace the
terminal transfer row; an unknown verdict remains conservative and does not
discard duplicate protection for queued or active rows.

Bundle selection capability is also category-owned.  A category may publish
that a bundle proves coverage of the requested unit and supports metadata-time
file priority selection.  Generic search policy consumes that declaration but
must not invent pack parsing or categorically ban selective queueing.  The queue
receipt for such a request says only that the torrent was registered and file
selection is pending metadata; it must not claim that the target file has
already been extracted, selected, downloaded, or imported.

Configured language preference is request context, never release evidence.
Candidate language facts must come from the concrete release title, provider
fields, or later file inspection.  `MULTI` without the requested language named
is unverified, and an explicit non-English request must not be queued from that
signal alone.

State-changing tool receipts outrank LLM prose.  If `queue_download` returns no
verified queue/active identifier, the agent loop replaces any contradictory
success prose with the structured failure.  Partial batch receipts must report
the verified success count and the failed units separately.  Metadata, search
history, preferences, and old conversation summaries cannot establish that a
download is currently active; a current queue/status tool is required.

When a generic/tag search result has no provider-backed media identity and no
category-owned structured unit descriptor, the concrete release title is the
best available identity for duplicate checks.  The result-set query text, such
as a broad tag or search phrase, must not collapse unrelated candidates into one
logical media identity.

## Category-First Data Flow

```text
Filesystem scan / provider metadata / downloads / user edits
        ↓
Category-owned normalization hooks
        ↓
Generic SQLite envelope store
        ↓
Category-owned canonical library object builder
        ↓
UI, suggestions, lifecycle, taste, and agent tools
```

The raw tables are storage envelopes, not the public source of truth:

- `category_items` stores category item envelopes.
- `category_item_units` stores category unit envelopes.
- `category_item_metadata` stores provider metadata envelopes.
- lifecycle/suggestion tables store processing ledgers.

Consumers should request a canonical library object through `CanonicalLibraryObjectBuilder` or category context hooks. They should not reconstruct domain state from raw rows.

## Canonical Library Objects

The canonical read model is implemented by:

- `src/core/library_objects.py`
- `CategoryContractMixin.library_object_spec()`
- `CategoryContractMixin.library_item_from_scan()`
- `CategoryContractMixin.library_units_from_scan()`
- `CategoryContractMixin.library_progress_from_scan()`
- `CategoryContractMixin.build_library_object()`

The core builder fetches generic rows and delegates final shape to the category. This is the only acceptable route for systems that need to know “what is in the library.”

A canonical object must include:

- `schema_version`
- `category_id`
- `item_id`
- `display_name`
- `properties`
- `metadata`
- `state`
- `units`
- `computed`
- optional category-owned nested views such as `seasons`, `files`, `volumes`, `versions`, or `tracks`

For local files, categories should include as much factual evidence as available:

- `file_path`
- `size_bytes`
- `estimated_bitrate_kbps` or extracted bitrate when available
- `resolution`
- `codec`
- `language`
- `subtitle_files`
- `quality`
- `downloaded_at` when known

Physical local files and logical content units are not always the same thing.
A category may have multiple files for one logical item: alternate releases,
quality upgrades, split files, subtitle variants, platform builds, DLC packages,
or audiobook parts. The storage `unit_key` should identify the physical/local
unit being stored. The category object may then expose logical nested views such
as episodes, versions, volumes, or tracks with nested `files` arrays. Do not key
physical storage rows only by a logical coordinate such as `S01E01` unless the
category can prove there can never be more than one local payload for that
coordinate.

Bitrate may initially be estimated from file size and a category default runtime. That is acceptable only as a clearly named field such as `estimated_bitrate_kbps`; a future media-probe layer can replace or supplement it with extracted bitrate.

## Category Object Specifications

Each category owns a declarative `library_object_spec()` describing the item identity fields, unit types, required/optional unit fields, computed fields, and source-of-truth rule. The spec is documentation and runtime guidance. The UI and agent can display or reason over the spec without the core learning the category domain.

Examples of valid category-owned specs:

- TV category: storage units are physical episode payload files; the category groups them into logical seasons/episodes with nested file arrays.
- Movie category: storage units are physical movie payload files.
- Game category: units may be releases, versions, DLC, patches, platform builds.
- Book category: units may be volumes, editions, formats, chapters, audiobook parts.

Only the category package may define those meanings.

## Suggestions

`SuggestionCompiler` is a category-neutral coordinator. It does not import category workflows directly. It asks the owning category for `create_suggestion_workflow(context)`.

Suggestion workflows must read local library state from canonical library objects, not from ad-hoc aliases, cleaned titles, or legacy progress rows. If a suggestion appears wrong because the canonical object is wrong, fix the category object builder.

Suggestion rows must include explanations and evidence. A user-facing suggestion should answer:

- what was observed locally;
- what provider or policy evidence was used;
- what user preference or taste signal mattered;
- what action is being recommended;
- why the recommendation is safe or useful now.

## Lifecycle and Freshness

`CategoryLifecycleEngine` gates expensive work. It fingerprints metadata, canonical library objects, taste snapshots, suggestions, and policy version. It should not fingerprint raw category units directly because raw rows are not the read model.

Categories define lifecycle policy through:

- `lifecycle_policy()`
- `lifecycle_decision()`
- `suggestion_policy()`

The core records due times and invalidation events but does not decide what a domain-specific lifecycle means.

## Taste System

Taste evidence is category-scoped. The taste infrastructure stores observations and snapshots generically, while categories define meaningful metadata dimensions and enrichment sources.

Rules:

- Conversation mentions may create weak category-scoped taste observations.
- Explicit likes/dislikes create stronger signed observations.
- Candidate accept/reject behavior should be persisted as evidence when available.
- Taste snapshots should be reused in prompts and lifecycle fingerprints instead of rebuilding profiles constantly.
- Category taste fields must not be mixed into one global blob.

## Agent Metadata Fact Grounding

Episode and release-date facts are grounded through the generic metadata contract, not bridge-specific logic. For episode-level questions, `metadata_lookup` must provide requested episode evidence under compact answer hints, and deterministic plans may compare those dates with `compare_date_to_now` for tense-safe wording. The executor may repair common planner alias paths to the compact requested-episode hint, but it must not reinterpret provider result lists or invent category semantics.

Compressed conversation summaries are also part of the agent context. Because they are trusted later, summary generation and deterministic compression must not add unsupported media dates, titles, episode numbers, or download states. If a summarizer introduces a date not present in the source turns, omit that date detail rather than preserving a hallucination.

## Agent Access

Agent tools should expose category-owned library state through canonical objects or category enquiry hooks. Do not add tools such as “list TV episodes” in core. Add a generic tool that asks the owning category for its canonical object, or add a category-owned workflow/action.

The prompt should make clear when information comes from:

- local canonical object;
- provider metadata;
- lifecycle ledger;
- suggestion evidence;
- taste snapshot.

## Evidence-Backed Title Authority

Torrent and Soulseek release titles are messy, but deterministic singular/plural
or punctuation heuristics must not be the primary source of truth for media
identity. When a category has provider metadata, category search and validation
should use provider-backed title authority first: canonical title, original
title, alternative titles, localized/translated titles, and service-specific
aliases. Query ladders may include the user's literal item key as a fallback,
but provider-known titles should lead search and exact title-containment
validation.

If provider metadata is unavailable, the category may use conservative fuzzy
matching as a degraded recall path. That fallback must remain bounded and must
not collapse unrelated titles such as one title being a suffix or partial token
window of another. Single-token aliases are especially risky: a request for
``Title`` may match the isolated series/movie title, or the title followed only
by release disambiguators such as a year, quality/source tag, language tag, or
season/episode marker, but it must not match a longer semantic title such as
``Title Fighter`` or ``Atomic Title`` merely because the token appears in the
release name. Public web research can be used for title-ambiguity resolution
when metadata providers cannot identify a title, but fetched public evidence
should establish aliases/context rather than becoming a torrent queueing
shortcut.

Category search hooks should publish their title-identity verdicts on candidate
payloads when they already have item metadata or requested-title scope. Generic
search workspaces may consume that boolean verdict, stable descriptor keys, and
category warnings, but they must not re-parse TV/movie titles or infer that a
matching episode coordinate makes a different title safe.

Movie search follows the same authority-first rule as TV. Before an interactive
movie download/search reaches the generic LLM candidate reviewer, the Movie
category should enrich TMDB title authority when configured, build query ladders
from provider/user title variants without dropping explicit sequel numbers, and
run a category-owned exact-title payload filter. Keyword-neighbor rows such as
``The Best Exotic Marigold Hotel`` for a request named ``Hotel Exotica`` must
be rejected before the LLM sees them, regardless of seeder count. Low-seeded
exact-title rows may be low-confidence availability evidence, but they must not
be buried behind unrelated higher-seeded rows.

## UI Access

The UI should render canonical library objects and category manifests. Generic screens can display common fields (`display_name`, `computed`, `units`, artwork) and pass through category-specific nested sections. Category-specific UI components may exist, but they belong to the category or are selected by manifest/component declaration.

Detail payloads must include the category-built canonical object. They may mirror common sections from the canonical object for legacy components, but those mirrors must be copied from the canonical object according to the category specification, not rebuilt independently.

## Naming, Consolidation, and Library Exposure

Naming templates are category settings. The generic consolidator may walk files and perform safe moves, but it must ask the category to map a parsed local file into a target path through `consolidation_target_for_file()`. Download completion should ask the category for normal target planning and, if that fails, a category-owned `fallback_library_path()` before using a flat generic safety fallback.

This matters because the same canonical object contract must govern all library interfaces: scanner, consolidation, download completion, UI, suggestions, and agent tools. Never fix one of those systems in isolation without checking the others.

## Category Schedules

The scheduler does not know what an air date, release date, patch date, chapter publication date, or DLC drop means. It calls `category.next_scheduled_unit(item, context)` and persists the category-owned `state_updates` returned by that hook.

## Round 110 Context Hygiene: Routing Must See Recent Chat, Not Library Dumps

Short follow-ups such as "I meant released movie", "not that one", "in Italian", or equivalent multilingual corrections must be routed with compact recent conversation context. The intent router must not see only pending torrent handles, because many obvious corrections are SEARCH or CHAT refinements rather than download actions.

Prompt construction should keep category-owned packets targeted. When a user message only matches a broad category word like "movie" or "show" but no tracked item, categories expose a lean router overview and sample keys, not the whole category library. Full category/unit context is reserved for matched tracked items or explicit library-state tools.

TMDB person search results must remain person results. The metadata lookup tool should return compact person credits for director/actor questions instead of treating a person ID as a movie or TV ID.

## User Scheduled Assistant Tasks

User-created reminders and future checks are separate from category lifecycle schedules. A request such as "remind me in 7 days" creates a one-off reminder that sends the stored reminder text through notifications. A request such as "check whether this torrent exists in 3 weeks and report back" creates a one-off scheduled assistant task with `task_type=condition_check`; when it fires, the shared assistant runtime receives the stored prompt and may use the normal category/search/download tools according to tool policy.

These tasks must remain generic and contract-bound:

- store absolute `next_run_at` / `due_at` timestamps instead of relying only on elapsed intervals;
- use `task_type` (`reminder`, `scheduled_prompt`, `condition_check`) and `schedule_type` (`one_off`, `recurring`) to make behavior explicit;
- do not hard-code torrent/media semantics into the prompt scheduler; the scheduled prompt uses the same LLM/category tools as chat;
- one-off tasks disable themselves after a successful run; recurring tasks advance `next_run_at`;
- failures are persisted in `last_error` and retried later rather than spinning every scheduler tick.

## Startup Discipline

Startup must avoid provider storms. A startup pass may reconcile cheap local ledgers and repair missing artwork, but provider-heavy work should run only when lifecycle state says an item is due, invalidated, or manually refreshed.

## Anti-Patterns That Must Not Reappear

Do not:

- branch in core on category ids for library semantics;
- make suggestions inspect title aliases to guess local ownership;
- make UI screens reconstruct episodes/files from raw rows;
- let scanner, suggestions, agent, and UI each invent their own state model;
- store category facts only in settings when they belong in canonical library envelopes;
- use legacy progress rows as the truth for missing-unit decisions;
- add provider-specific logic to scheduler or generic repositories;
- silently recompute suggestions at every boot.

## Where to Put New Work

- New category schema/normalization: category class.
- New category suggestion behavior: category workflow under `src/core/categories/workflows/` or the category package.
- New provider interpretation: category metadata/enrichment hook.
- New generic storage operation: repository.
- New generic read model flow: `CanonicalLibraryObjectBuilder` only if it remains category-neutral.
- New UI category presentation: category manifest/spec or category-selected component.

## Current Transition Notes

The project still contains older fields such as `season` and `episode` in generic download models, torrent candidate flows, and some compatibility view models because the existing downloader and torrent flows predate the canonical object contract. Treat these as legacy structured-unit coordinates until they are migrated behind category-owned unit descriptors. Do not expand that pattern. New systems must use category specifications and canonical library objects.

Quality inference, content cleanup, and some assistant planning paths still carry media-category assumptions from the original app. They must be migrated behind category-owned policies/hooks before open-source hardening is considered complete.

`ScannedEpisode` remains as a backwards-compatible alias for `ScannedFileObservation`; new code should use the category-neutral name.

## Round 73 Review Notes — Boundary Hardening

The second canonical-library review tightened three seams that had become easy to misuse:

1. **Scan shape compatibility:** category scanners may return their native `ScannedItem` dataclass and the scanner facade may convert that to `ScannedLibraryItem`. Both shapes now expose local payload evidence through the neutral `files` seam. Category canonical object builders should read local file observations through that seam, not through old names that imply a specific domain.
2. **Path planning:** new path code should use `compute_target_path_from_fields()` or category-owned hooks such as `consolidation_target_for_file()` and `download_target_for_item()`. The older `compute_target_path(source, item, season, episode, ...)` wrapper remains only for compatibility with existing category code and tests. Generic services must not call it as their primary path API.
3. **Search preparation:** the search pipeline delegates category-specific item preparation to `category.prepare_search_item()`. Size limits, naming quirks, unit labels, and provider-specific preparation belong in categories; the pipeline only passes context.

When reviewing future patches, treat these as hard boundaries. A change that fixes only one caller by adding a fallback alias, default category id, or hardcoded unit coordinate in a generic service is a regression.

## Round 74 Review Notes — Search and Cleanup Hook Hardening

The third canonical-library review moved two more legacy seams behind category contracts:

1. **Search labels are opaque to the pipeline.** `SearchPipeline` no longer parses labels as TV episodes, builds `SxxEyy` fallback queries, computes TV-size/bitrate references, or constructs duplicate-detection unit keys. It calls category hooks instead: `build_search_query()`, `build_alternative_search_queries()`, `validate_search_result_for_request()`, `quality_reference_for_search()`, `discovery_already_satisfied()`, and `download_coordinates_from_search_result()`.
2. **Cleanup/listing records are category-owned.** `ContentCleanup` no longer maps Plex `episode`/`movie` types to built-in category IDs or converts scan payloads through `detailed_episodes`. It asks categories through `matches_external_media_type()`, `library_file_records_from_scan()`, and `file_record_matches_selector()`.
3. **Transitional download coordinates remain contained.** Download rows still carry legacy `season`/`episode` columns for existing workflows, but only the owning category may populate those coordinates from search results. New download-state work should replace those columns with a category-owned unit descriptor instead of adding more fixed coordinates.

Future review passes should continue with downloader state, assistant planning schemas, and notification/presentation helpers. Those areas still expose legacy names because the original app was media-first; do not copy those names into new generic services.

## Round 75 Review Notes — Download Unit Descriptor Hardening

The fourth canonical-library review moved the next high-risk downloader seam toward the category contract:

1. **Download unit identity is category-owned.** Search candidates and queue cache rows now carry a `unit_descriptor` produced by the owning category. The descriptor may describe an episode, season pack, book volume, game version, DLC, disc, track, edition, or any future unit shape. Generic services may only treat `stable_key`, `label`, `granularity`, `sort_key`, and `coordinates` as optional conventions; they must not infer domain semantics from those fields.
2. **Legacy coordinates are compatibility only.** `season` and `episode` remain in public tool schemas and download rows for existing workflows, but category hooks populate them from descriptors. New work should pass descriptors through search, queue, download import context, UI receipts, and agent tools.
3. **Batch queueing is descriptor-based.** The scheduling tool no longer builds multi-unit recommendations by formatting `SxxEyy` labels. It asks `category.batch_group_for_candidate()` and queues the resulting candidate IDs.
4. **Queue fallback matching is descriptor-based.** `queue_download` uses `category.candidates_represent_same_unit()` or descriptor stable keys before falling back to legacy coordinates for old cache rows.
5. **Duplicate detection prefers descriptor keys.** Download import context now stores `unit_descriptor`; repository duplicate checks compare descriptor-backed `stable_unit_key` before legacy coordinate overlap.
6. **Seed-in-place pathing is category-owned.** The generic downloader sharing mixin now calls `category.sharing_save_path_for_item()` instead of creating season folders itself. TV implements the season-folder behavior inside the TV category.

Future work should remove legacy structured-coordinate arguments from assistant-facing schemas once the UI and agent can pass arbitrary category unit descriptors directly.

## Round 76 Stabilization Notes — Descriptor-First Download Management

After the canonical-library refactor, the highest-risk remaining problem was not only raw library ownership; it was that queue management, health-test selection, LLM download-control filters, and torrent-selection prompts could still *present* or *order* units through legacy structured fields. That kept the app vulnerable to the same class of error: a generic service accidentally treating one category's unit names as universal.

The stabilization rule is now:

1. **Download rows may retain legacy structured-coordinate columns only as compatibility data.** They are not the primary read model for ordering, filtering, duplicate detection, or user-facing explanations.
2. **The category-owned `unit_descriptor` is the primary unit handoff.** It travels through search results, result caches, queue requests, `DownloadImportContext`, `DownloadItem`, file-level download state, UI snapshots, and agent tools.
3. **Generic code may only read descriptor conventions:** `stable_key`, `label`, `granularity`, `sort_key`, and `coordinates` as an opaque compatibility blob. It must not interpret `coordinates` as seasons, chapters, tracks, DLC, versions, or anything else.
4. **Queue ordering is descriptor-first.** `DownloadImportContext.descriptor_sort_key` and `DownloadItem.unit_sort_key` are the generic seams. If a category wants meaningful order, it must put that order into `unit_descriptor.sort_key`.
5. **Download control filters are descriptor-first.** New calls should use `unit_key`, `unit_label`, and `unit_granularity`. Legacy `season`/`episode` filters are accepted only for old prompts and old rows.
6. **Torrent-selection fallback prompts are category-neutral.** Specific media rejection rules, expected formats, and unit semantics should come from `category.build_torrent_selection_guidance()` or related category hooks.

This does not fully remove every old field. It changes the preferred path and centralizes fallback behavior so generic consumers do not each reinvent their own legacy logic.

## Stabilization vs. Refactor Policy

For the remainder of the canonical-library migration, prefer stabilization over broad rewrites:

- fix concrete bugs or high-risk seams first;
- move one interaction boundary at a time;
- add tests that prove the boundary is descriptor/canonical-object based;
- document any retained compatibility fields as transition-only;
- do not claim the old codebase is fully purified until guards cover the claim.

A future cleanup can remove legacy public schema fields after UI, agent tools, tests, and persisted data have a migration path to arbitrary category unit descriptors.

## Round 77 Review Notes — Category-Neutral Defaults

Round 77 tightened boundary seams that were technically working but still unsafe for future categories:

1. **No generic runtime assumptions in scanning:** scan summaries may report file counts and sizes, but bitrate estimates that require a content runtime must come from `category.scan_average_bitrate_kbps(scanned)`. The scanner must not assume episode/movie/track/game runtimes.
2. **No built-in default category in search aggregation:** provider aggregation now treats a missing category as an unscoped search (`None`/`all`) rather than defaulting to TV. Callers that know the category must pass it explicitly.
3. **Provider filters are configuration, not app semantics:** Torznab category filters are injected through `category_filters`; provider adapters must not hardcode built-in category IDs as domain rules.
4. **Generic feeds use category parsing hooks:** RSS monitoring classifies/parses feed items through the category registry and asks categories for optional unit labels through `rss_unit_label_from_parsed()`.
5. **Frontend actions carry category IDs:** generic UI controls must use the category attached to the item or manifest. They must not fall back to `tv` when an item/category is missing; use a manifest-selected category or neutral `media` fallback.

Remaining TV-shaped compatibility services, especially selective season-pack handling, should be migrated behind category-owned pack/collection hooks rather than patched with more global fallbacks.

## Round 78 Review Notes — Generic Bundle/Pack Handling

Round 78 removed the last active TV-shaped selective-download service from generic core. `src/core/season_pack.py` has been retired and replaced by `src/core/bundle_download.py`.

The new rule is:

1. **A bundle is category-neutral.** A bundle may be a TV season pack, a movie collection, a game bundle, a book anthology, a discography, a DLC bundle, or any future grouped payload. Generic code must not decide what that means.
2. **Categories detect and describe bundles.** Candidate hints come from `category.torrent_bundle_candidate_context(result, item, unit_label)`. The LLM receives these hints so it can evaluate total size, useful per-unit/file size, selectability, and fallback strategy.
3. **Categories select files from torrent metadata.** Once libtorrent metadata arrives, `BundleDownloadHandler` asks the owning category for `unit_descriptor_from_file()`, `torrent_file_matches_target()`, and `torrent_file_priority()`. It only writes priorities; it does not parse TV episodes, movie titles, game versions, book volumes, or any other category semantics.
4. **Full-unit requests can prefer bundles but must keep fallbacks.** TV season requests may rank season packs highly, but exact episode releases remain valid fallbacks. Specific TV episode requests may accept a containing same-season/range pack when exact releases are unavailable or poor.
5. **Flat categories can still use bundles.** Movies now expose collection-aware file matching by requested title/year. Other categories should implement equivalent hooks for versions, editions, authors, volumes, platforms, DLC, or whatever their object spec requires.
6. **LLM judgment is part of the architecture.** Deterministic code should remove only clearly unsafe/non-queueable candidates. Ambiguous bundle usefulness, release naming, language tags, payload grouping, and quality tradeoffs are intentionally delegated to the LLM with category-provided evidence.

Legacy public parameters such as `selective_season` and `selective_episodes` remain only as transitional bridges into category unit descriptors. New code should pass `selective_descriptors` or a `DownloadImportContext.unit_descriptor`. TV-specific pack parsing now lives in `src/core/categories/tv_bundle.py`; generic quality and candidate models use only bundle-neutral fields.

## Round 79 Review Notes — Scenario-Trace Testing

Round 79 added explicit scenario traces for the bundle/pack paths introduced in Round 78. These are not only static guards; they simulate realistic user outcomes through the production seams:

1. **Specific TV episode from a multi-season torrent:** A request such as `S02E05` may accept an `S01-S03 Complete` bundle. The requested unit descriptor must remain `S02E05`; the bundle title's first season must not overwrite the target. Selective priorities should download only the matching episode file.
2. **Whole TV season from a multi-season torrent:** A request such as `Season 2` may accept `Seasons 1-3 Complete`, but metadata selection should prioritize only season-2 files and ignore neighboring seasons/extras.
3. **Range and complete-series fallbacks:** Partial ranges such as `S01E03-E07` are accepted only when the requested episode is inside the range. Complete-series torrents remain eligible for LLM/category judgment and later metadata-based selective selection, rather than being hard rejected because the title lacks a single `SxxEyy` token.
4. **Movie from collection torrent:** A movie request may accept a collection torrent, but the movie category must select only the requested film payload and reject sequels, samples, and extras.
5. **Large bundles reach the LLM:** Large total torrent size is not a hard rejection. The category supplies useful per-unit estimates and the quality layer passes the candidate forward for semantic evaluation.

This pass also fixed a real trace failure: TV bundle target descriptors now prefer the explicit request label over parsed container coordinates. Without that, a request for `S02E05` inside `S01-S03 Complete` could have been converted into `S01E05` because the bundle title begins at season 1.

Executable scenario traces live in `scripts/round79_scenario_trace_tests.py`; pytest wrappers live in `tests/test_round79_scenario_traces.py` for environments with the full test dependency set installed.

## Round 80 Review Notes — Fresh-Install TV Scan/Detail Regression

Round 80 fixes the regression reported from a fresh install where TV show folders were discovered, but local episodes appeared empty in the frontend and missing-episode suggestions could be compiled before the scan had finished reconciling units.

Root causes:

1. **Detail payload rebuilt TV seasons from the wrong unit type.** The canonical TV object stores physical episode payloads as `unit_type: file` with `role: episode_payload`, then groups them into logical episode objects under `seasons`. The TV detail mixin still filtered for `unit_type == episode` and overwrote the canonical seasons with an empty list. The detail modal now uses `canonical_object.seasons` as source of truth and only falls back to raw unit reconstruction when no canonical seasons exist.
2. **Fresh scans missed common season-folder layouts.** Already-organized libraries often use `Season 1/01.mkv`, `Season 1/E02.mkv`, `S01/E03.mkv`, or localized folders such as `Stagione 1/Episodio 03.mkv`. TV scanning now infers episode coordinates from the filename plus season folder context inside the TV category boundary.
3. **Some TV helpers still queried retired logical episode rows.** They now read downloaded canonical units by status and interpret only TV-owned file/episode coordinates.
4. **Suggestion compilation can race first-scan reconciliation.** Scheduler-level suggestion compilation now waits for an active library scan to finish before reading canonical objects. This prevents populated libraries from generating bogus “everything is missing” suggestions while files are still being written to category-unit storage.

The key architectural rule remains unchanged: generic storage holds category-owned units; the TV category converts file-backed units into logical seasons/episodes; UI, suggestions, and agent context must consume the canonical object instead of re-parsing raw rows independently.

Executable regression coverage lives in `scripts/round80_fresh_scan_regression_tests.py` and simulates the fresh-install path without requiring the full pytest dependency stack.

## Round 81 Review Notes — Gentle Media-Probe Metadata Restoration

Round 81 fixes the regression where fresh scans recovered TV episode ownership but lost actual audio-language metadata. The old behavior had drifted back toward filename-only language hints, and tracked items could skip file probing entirely. That was wrong: local library quality and language state must come from the media streams when stream metadata is available.

The restored rule is:

1. **Actual streams win over names.** Filename/release tags may be used as fallback hints, but scanned local files should prefer ffprobe audio/subtitle stream metadata for language state.
2. **Probing is serialized and cached.** `src/core/categories/media_probe.py` owns ffprobe calls behind a process-wide semaphore with concurrency `1`. Scans use `probe_media_files_serial()` so a large library cannot launch one ffprobe per file concurrently. Results are cached by absolute path, size, and mtime in `data/cache/media_probe_cache.json`; unchanged files reuse cache entries on later scans.
3. **File observations carry stream facts.** `ScannedFileObservation` / `ScannedMediaFile` now carry `detected_language`, `audio_languages`, `audio_tracks`, `subtitle_languages`, `subtitle_tracks`, and `media_probe`.
4. **Canonical units preserve stream facts.** TV episode payloads and movie file units persist audio/subtitle track metadata into `category_item_units`. The display `language` field remains for compatibility, but `audio_languages`/`audio_tracks` are the real structured fields.
5. **Logical TV episodes aggregate files.** The TV canonical object groups physical files into episodes and now aggregates per-file audio/subtitle languages into episode-level fields. Multi-audio files such as Italian + English remain visible to the UI/agent instead of being collapsed to one guessed language.
6. **Agent/UI context uses file-backed episode units.** TV LLM context and detail payloads accept `unit_type: file` with `role: episode_payload`; they no longer rely on retired logical `unit_type: episode` rows.

This is deliberately a stabilization fix, not a broad scanner rewrite. The probe cache avoids repeated disk work after the first scan, and the single-flight semaphore protects disks on first scan or after file changes.

## Round 82 stabilization note — stream metadata and Discord

Local media language/resolution metadata must come from actual media streams where possible, not just filename hints. The category scanners use `src/core/categories/media_probe.py`, which serializes `ffprobe` calls and caches by file path, size, mtime, and parser version. If old canonical rows lack stream metadata, the scheduler's `media_metadata_repair` job detects that stale state and triggers a normal serialized scan instead of a concurrent disk sweep.

Discord bridge availability must be checked dynamically because optional bridge dependencies may be installed after process startup. The Discord bridge also exposes slash commands as a fallback for servers where the privileged Message Content Intent is not enabled.

## Round 83 — Media resolution provenance

Local media resolution is now treated as a probed video-stream fact. Category
canonical units prefer ffprobe width/height over filename quality tags and expose
`resolution_source`, `video_width`, and `video_height`. File size is used only to
estimate bitrate when ffprobe does not provide format bit rate; it is never used
to infer 720p/1080p/2160p. The media probe cache parser version was bumped so
older stream rows are repaired safely through the existing serialized ffprobe
path.


## Round 84 — PlanCoordinator download-plan crash guard

Round 84 fixes a websocket chat crash where `PlanCoordinator._normalize_download_plan()` referenced `_looks_like_direct_category_download_tool()` even though the method was not defined. Because every DOWNLOAD plan passes through this normalizer, the failure could break ordinary chat requests before any tool execution happened.

The repair adds a category-neutral guard for dotted category workflow names that imply direct queue/download/import execution, such as `<category>.download_*`, `<category>.queue_*`, or `<category>.import_*`. Those tools are treated as unsafe as first-step discovery for fresh user download requests. The coordinator rewrites such plans to the generic `search_media_torrents` discovery flow for the matched tracked item, preserving configured language and only already-structured numeric unit constraints. Natural-language concepts such as “latest season” belong to the LLM and category context packet, not deterministic regex fallback.

The specific logged scenario, "grab me the episodes I am missing from the latest season of For All Mankind", now normalizes to generic discovery for `For All Mankind` in the configured language instead of crashing or trying to execute unresolved category placeholders like `${find_missing.results.missing_episodes}`. The LLM/category context determines the latest/missing unit semantics.

A new executable regression trace, `scripts/round84_plan_coordinator_regression_tests.py`, checks the exact logged plan shape through both `_normalize_download_plan()` and the async `prepare_plan()` path used by websocket chat. It also statically audits `PlanCoordinator` for dangling private `self._...()` calls so this class cannot ship with the same missing-method class of bug again.

Round 84 also tightens one TV-owned fresh-scan parser edge case seen in the same logs: legacy filenames like `s1.08` now resolve to season/episode coordinates inside `TvShowCategory._infer_episode_coordinates_from_path()`, while quality-like tokens such as `S1 720p` remain rejected. This is a category-owned parser correction, not a generic scanner rule.

## Round 85 — LLM Tool Philosophy and End-to-End Download Flow

Round 85 corrects the tool-use direction after the Round 84 crash fix. The desired architecture is **not** dozens of category-specific LLM tools such as `tv.find_missing_episodes`, `tv.download_missing_batch`, `books.download_volume`, or `games.queue_patch`. Those workflows may exist as UI actions, scheduled jobs, or internal category implementation details, but they are not the ordinary LLM download surface.

The ordinary LLM-facing download chain is intentionally small:

```text
Category library context packet / enquire_about_media
        ↓
search_media_torrents
        ↓
queue_download
```

The division of responsibility is:

1. **Categories own context and semantics.** A category tells the model what local units exist, what provider metadata says has been released/aired, what language/quality preferences apply, and what bundles/packs mean. TV may expose seasons/episodes; books may expose volumes/editions; games may expose versions/platforms/DLC. Generic code must not hardcode those meanings.
2. **The LLM decides from evidence.** For prompts such as “grab the episodes I am missing from the latest season of For All Mankind”, the model should inspect the category context packet: local episode keys, provider/aired metadata, computed missing units, configured language, and existing episode audio languages. It then decides the concrete search/queue strategy.
3. **Generic tools execute the decision.** `search_media_torrents` accepts the exact item key plus structured unit fields when relevant. The category search hook expands that into exact releases and safe bundles/packs. `queue_download` queues explicit candidate IDs/result-set IDs returned by search. The model must not invent or call direct category micro-tools for ordinary download decisions.
4. **Language is a first-class decision input.** The category context packet must include configured item language and observed local audio/subtitle languages when available. If the user does not explicitly override language, searches should use the configured language and preserve continuity with existing episodes. Multi-audio is acceptable only when it contains the configured/existing language. A different-language-only candidate should be presented for confirmation rather than silently queued.
5. **No hidden deterministic auto-queue for batches.** Search may return `batch_recommendation.queue_download_arguments` as evidence, but the chat loop should not auto-queue it behind the model’s back. The LLM evaluates the recommendation against context, language, quality, seeders, bundles, and ambiguity, then calls `queue_download` or asks the user.
6. **Category-specific action declarations remain for UI/internal flows.** The manifest can still declare actions such as “download missing episodes” for buttons, suggestions, or scheduled workflows. That does not mean those action names should be exposed as ordinary LLM tools. The LLM surface stays generic.

The executable Round 85 trace lives in `scripts/round85_end_to_end_intent_flow_tests.py` and verifies the exact For All Mankind prompt path: active TV context is present, configured language and existing audio languages are visible, direct category micro-tools are not exposed to DOWNLOAD intent, unsafe/invented direct category plans normalize to generic search, and the streaming plan executor does not auto-queue batch recommendations without LLM selection.

## Round 86 — Candidate Selection, Confirmation, and Queue Regression Guard

Round 86 fixes a concrete regression seen in websocket chat after the Round 85 tool-philosophy pass: a batch recommendation marked a lower-seeded duplicate as the preferred S05E04 candidate for *For All Mankind*, and the follow-up confirmation path then failed because cached candidate resolution tried to read an uninjected category registry.

The corrected rules are now explicit:

1. **Seeders are a first-class availability metric.** For candidates that already satisfy category constraints such as unit coverage, configured/preferred language, acceptable resolution, queueable magnet, and safe pack semantics, an otherwise equivalent candidate with materially more seeders wins. Provider/indexer order must never be treated as ranking evidence.
2. **Batch recommendations are deterministic evidence, not hidden auto-queue.** `search_media_torrents` may expose `batch_recommendation.queue_download_arguments`, built from stable cached candidate IDs. The LLM can show those recommendations or call `queue_download` after user confirmation, but generic code does not silently queue them behind the model/user.
3. **Superseded by Round 87.** Round 86's phrase-based confirmation guard was removed. Follow-up intent is now LLM-routed from structured pending-action context, not from English words or the immediately previous assistant message.
4. **Cached queue resolution receives category hooks explicitly.** `CachedCandidateResolver` is constructed with the scheduler category registry so batch ordering/fallback hooks can run without relying on globals or missing private attributes.
5. **Static private-attribute audit is mandatory around AI planner/tool changes.** The Round 86 trace walks `src/ai/**/*.py` and fails if a class reads `self._x` without assigning it, declaring it as a class attribute, or defining it as a method. This catches the same class of regression as the missing `PlanCoordinator._looks_like_direct_category_download_tool()` method and the missing `CachedCandidateResolver._categories` attribute.

The executable trace is `scripts/round86_intent_selection_regression_tests.py`. It now checks the exact duplicate-candidate case from the logs, structured pending-result context for later LLM-routed follow-ups, DOWNLOAD tool-surface restrictions, direct category micro-tool normalization, cached candidate resolver wiring, and the AI private-attribute audit.

## Round 87 — LLM-Owned Intent Routing and Context Budgeting

Round 87 corrects the Round 86 follow-up mistake. The application must not classify user replies with hard-coded English phrases such as “ok”, “yes”, “proceed”, “first”, or “download this”. Users may speak any language, may answer indirectly, and may refer back to a candidate table several turns later. Intent routing is therefore LLM-owned: the router receives the current user message plus structured pending-action context, then returns SEARCH, DOWNLOAD, CONFIG, CHAT, or CLARIFY.

Recent torrent result sets are persisted as structured state and exposed to the router/planner as `PENDING ACTION CONTEXT`, including `result_set_id`, `candidate_id`, candidate seeders/language/resolution, and `batch_recommendation.queue_download_arguments`. This replaces previous “immediately previous assistant message contains candidate words” logic. The LLM decides whether the user is referring to that pending state; deterministic code validates IDs, queueability, and tool permissions only after the model chooses an action.

Context assembly is task/model-aware. `TaskLLMClient` resolves provider/model context-window metadata when available; user settings may cap `llm.max_context_tokens`, `llm.context_budget_percent`, `llm.reserved_output_tokens`, and `llm.max_recent_conversation_turns`. Round 89 supersedes Round 87's trim-first wording: `LLMTaskRuntime` applies an output-aware budget before every streaming and non-streaming completion, and `TokenBudgetManager` now compresses older history/tool payloads before any last-resort drop.

The old deterministic language-word override was also removed. The planner/category prompt tells the LLM to use the configured item language unless the user explicitly overrides it. Code may fill configured language only when the plan omitted a language; it must not scan the prompt for a fixed list of language names.

Executable trace: `scripts/round87_llm_context_intent_tests.py`.

## Round 88 — Endpoint Context Window and User Cap UI

Round 87 added context budgeting, but it was incomplete: the runtime only used provider/model context metadata when the model catalog had already been loaded, and the Compass UI did not expose the context cap. Round 88 closes that gap.

The context-window contract is now:

1. **Endpoint/model metadata is the default.** Before each LLM call, `LLMTaskRuntime` asks `TaskLLMClient` to warm the provider model catalog. If the provider model endpoint exposes context metadata, that endpoint value is the default maximum.
2. **The user cap is separate from the endpoint maximum.** `TaskLLMClient.resolve_task().context_limit` represents the provider/model maximum. `llm.max_context_tokens` is only a user cap applied by `LLMTaskRuntime` afterward.
3. **Compass exposes the cap.** The AI & LLM Gateway panel shows the detected endpoint maximum and lets the user enter a cap from the minimum selectable window to that maximum. Saving the endpoint maximum stores `null`, meaning “follow the endpoint maximum for the selected model.”
4. **A usable minimum is enforced.** Values below the minimum selectable window are raised to that minimum because tool schemas, category context, pending action state, and the current request need room to function.
5. **Provider gaps are visible.** If a provider does not return context metadata from its model-list endpoint, the UI and runtime use the conservative fallback and label it as fallback rather than pretending the endpoint supplied it.

Executable trace: `scripts/round88_context_window_settings_tests.py`.

## Round 89 — Compression-First Context Assembly

Round 89 corrects the Round 88 budgeting policy. Trimming old conversation as normal behavior is not acceptable. The default policy is now **compression-first**:

1. **The model context window includes output.** `LLMTaskRuntime.context_budget_for_task()` first resolves the provider/model endpoint maximum, applies the user cap, applies the safety percentage, and then subtracts `reserved_output_tokens`. The remaining tokens are the prompt budget.
2. **Recent conversation stays raw.** `llm.raw_recent_context_percent` defaults to `30`, reserving that share of the conversation-history budget for the newest turns without compression.
3. **Older conversation is compressed.** The rest of the conversation-history budget is used for compressed older turns and stored summary packets. Old turns should not simply disappear because a chat got long.
4. **Final provider-call enforcement is still safe.** `TokenBudgetManager.trim_messages()` remains as a compatibility method name, but it delegates to compression-first behavior: compact tool payloads, compress older unprotected history into a system packet, shrink compressed packets if needed, and drop only as a last-resort safety fallback when even compressed content cannot fit.
5. **The minimum selectable context window is enforced.** `llm.max_context_tokens` values below the minimum are raised to the minimum; the app no longer treats `0` as a normal user setting because real tool workflows need a working prompt budget.

Compass → AI & LLM Gateway now exposes the raw recent-history reserve so users can decide how much of the conversation history should remain uncompressed. The default is 30% raw recent history / 70% compressed older history within the conversation-history budget.

Executable trace: `scripts/round89_context_compression_tests.py`.


## Round 90 — Endpoint Context Probing, Matched-Item Context, and Placeholder Stability

Round 90 fixes three regressions seen in the 2026-05-24 logs.

1. **Endpoint context probing is provider-aware.** `TaskLLMClient` now probes provider/model context limits before runtime budgeting. LM Studio-style endpoints are checked through the native `/api/v1/models` route as well as OpenAI-compatible `/models`; loaded instance `config.context_length` wins over theoretical maxima when available. Generic providers are parsed for common metadata fields such as `context_length`, `max_context_length`, `context_window`, `max_context_tokens`, `n_ctx`, `num_ctx`, and `max_model_len`. The shared fallback is 16,384 tokens, not 8k.
2. **User caps have a usable floor.** The UI and runtime expose endpoint maximum and user cap separately, but users cannot set the cap below the minimum selectable context window, normally 10,000 tokens unless the endpoint itself is smaller. `null` still means “follow endpoint maximum.”
3. **Single-item requests no longer carry the whole library.** Category LLM context packets scope `tracked_items` to the matched item when the prompt clearly refers to one item, and include only a small sample of other tracked keys for disambiguation.
4. **Planner prompts are compact.** `ReasoningPlanner` now sends compact tool summaries and a compact plan-shape example rather than full tool JSON Schemas plus the full Pydantic schema.
5. **Metadata season placeholders no longer crash plans.** Placeholder resolution treats `${lookup_metadata.seasons.length}` and similar `*.seasons.length` aliases as “latest/known season count” when metadata payloads expose season counts or season lists.

Executable trace: `scripts/round90_context_planning_regression_tests.py`.

## Round 91 — LLM context loop and completed-download target rules

Round 91 addresses the continuation logs where a single missing-episode request kept growing prompt context across repeated search/select turns, and a completed TV download planned an unsafe target outside the configured TV root.

DOWNLOAD prompts must stay evidence-rich but loop-compact. The normal LLM-facing download chain remains small: category context or `enquire_about_media`, then `search_media_torrents`, then `queue_download`. Do not expose browser, broad research, or category-scaffold tools to ordinary DOWNLOAD intent unless the user is explicitly doing research/configuration. Tool results fed back to the model must preserve stable queue identifiers and decision evidence, but must not include raw tracker payloads, full candidate arrays, or full nested provider data on every loop.

`ToolResultCompactor` is now the shared compaction boundary for LLM-facing tool messages and token-budget fallback compaction. If a new tool can return large machine payloads, add a compaction rule before exposing it to iterative chat loops.

Completed-download target planning remains category-owned. Generic completion code asks the category for a target, validates it through `SafePathResolver`, and may retry once through the category fallback if the first target is blocked. Category fallbacks must preserve the category root, item folder, unit folder conventions, and original source filename. Generic code must not derive TV seasons, book volumes, game versions, or other category unit semantics itself.

Executable trace: `scripts/round91_context_loop_download_target_tests.py`.

## Round 92 Discord and download-turn stabilization

- Discord bridge interactions must use the same immediate/periodic persona progress pattern as web chat and must not cancel valid assistant runs at an arbitrary bridge timeout.
- DOWNLOAD prompts use the compact torrent quality guide; the full torrent guide is retained for offline explainability and tests, not routine prompt injection.
- Multi-unit/missing-unit download plans must preserve category ownership of fan-out by removing guessed single-unit coordinates from generic search steps.
- Base category organization must not pass unit coordinates twice into `compute_target_path()`.

## Round 93 — Unified chat interface architecture

All interactive user chat surfaces now share one transport-neutral assistant-turn runner: `src/ai/chat_session_runner.py`.

The canonical rule is: **web chat, REST chat, Discord, Telegram, WhatsApp, and future communication bridges must not implement their own assistant loop, context policy, whole-turn timeout, memory recording, progress schedule, or error-persona behavior.** They must create a `ChatTurnRequest`, consume `ChatSessionRunner.run_events()`, and translate only the resulting `status`, `token`, and `done` events into their platform's delivery primitives.

This keeps complex conversations equal across interfaces:

1. **One context/memory pipeline.** Every chat surface enters through `AIAssistant.run_stream()` via `ChatSessionRunner`, so intent routing, pending-action context, compression-first conversation history, category context packets, tool policies, tool-result compaction, taste ingestion, and assistant turn recording remain identical.
2. **Bridge code is an adapter only.** Discord can reply/follow up, Telegram can send Markdown replies, WhatsApp can call the Cloud API, and web can send websocket JSON frames, but those transports may not decide how to budget context or when to cancel an assistant run.
3. **Progress is platform-independent.** Immediate acknowledgement and periodic persona progress pings are emitted by the shared runner. Bridges decide how to display the ping, not whether the assistant turn uses a different execution path.
4. **No arbitrary whole-turn bridge timeout.** External-service calls may have their own timeouts, but a communication bridge must not cancel a valid agent turn because it lasts longer than a platform wrapper's old request timeout.
5. **Formatting remains transport guidance.** Platform-specific Markdown/formatting instructions are still supplied through bridge metadata/session prefixes, but formatting must not fork agent context, planning, tool selection, or memory semantics.

Executable trace: `scripts/round93_unified_chat_bridge_tests.py`.

## Round 94 Review Notes — Episode Fact Grounding and Date-Tense Safety

Factual media questions must not be answered from model memory or generic web vibes. The shared assistant pipeline now treats episode air dates, release dates, and schedule facts as metadata-first claims:

- `metadata_lookup` is the preferred first source for media facts across all chat surfaces.
- Follow-up metadata questions may be repaired from recent tracked-item context only after the LLM has selected metadata/research tools; this is a structured context binding step, not a separate bridge-specific chat path.
- Episode-specific answers require episode-level evidence. Season-level fields such as season count, season air date, or total episode count are insufficient for “when did episode N air?” style answers.
- Generic web fallback queries must include the concrete tracked title when they are generated from a follow-up; the app must not search for naked phrases such as “episode 10 original air date.”
- Runtime date guidance is part of the shared prompt, and the generic `compare_date_to_now` tool is available to compare air/release dates before choosing past or future tense.
- Bridges remain adapters over the single shared chat architecture; no bridge may implement independent factual grounding, date comparison, or media follow-up rules.

## Round 95 — Bridge progress gating, reply language, and future air-date cross-checks

Round 95 tightens the shared chat runner after Discord testing exposed three UX/reliability issues: trivial CHAT replies received long-turn status pings, media download language leaked into ordinary reply language, and future episode dates could be reported from a single region-sensitive metadata date.

1. **Progress pings are gated by routed intent.** `ChatSessionRunner` now performs a shared preflight intent route before emitting status. CHAT turns such as “Thanks!” do not receive an “I am working on it” status. SEARCH, DOWNLOAD, and CONFIG turns still acknowledge quickly and continue with periodic pings while work is active.
2. **Acknowledgements are generated when possible.** The first long-turn status line asks the configured LLM for a short persona/language-aware acknowledgement, with deterministic localized fallback templates only if generation fails. Bridge code must not hard-code English acknowledgement text.
3. **The current message language wins.** `PromptBuilder` receives a conservative current-message language hint. Configured item language remains a torrent/search constraint only; it must not be injected into SEARCH metadata plans or interpreted as the assistant’s reply language.
4. **Broad episode-count questions stay broad.** Follow-up coordinate repair may copy a recent season into metadata lookup, but it may not copy a previous episode number unless the current user message explicitly asks about that episode. “How many episodes still need to be aired?” must not inherit `S05E07` from a previous download receipt.
5. **Upcoming air dates get stronger sourcing.** Future/upcoming episode-date questions receive a title-bound official-source web cross-check in addition to metadata lookup when web search is available. One-day differences between TMDB/community calendars and official streamer/regional pages must be surfaced or resolved toward the official regional source.

Executable trace: `scripts/round95_bridge_language_airdate_tests.py`.

## Round 97 — Item-Scoped Import Reconciliation

Completed-download imports are app-owned, item-scoped library mutations. The download completion handler receives the category id, item id/import context, and destination path, so it must not wait for the generic filesystem watcher to discover the change and launch a full-library scan. Instead it marks a managed mutation, performs blocking hardlink/copy/move work off the event loop, and asks `MediaScheduler.reconcile_library_item_from_path()` to rebuild only the affected category item through the category scanner/canonical-object hooks.

The filesystem watcher remains a coarse fallback for external/manual library changes. It may trigger a full scan only when no managed import is active. Known item mutations should refresh canonical units, lifecycle state, and suggestions for that item only.

Recovered category-config path failures are not import failures. If a private naming config proposes an unsafe destination but the category fallback produces a safe target, the event is informational; only failure of both target plans or the actual file operation is an error.

## Round 98 — Ready Import Paths and Diagnostics

Ready-time import is now explicitly separate from library consolidation. For TV downloads, `download_target_for_item()` returns the conservative category-owned import path directly: category root, preferred show folder, preferred season folder, original source filename. The configurable naming template is still valid for preview/consolidation flows, but it must not be used for the first ready-time hardlink/copy while the torrent may still be seeding.

This removes the recurring unsafe primary path such as `Media/Season 5/For All Mankind.mkv`: that path was a template/consolidation-style output being attempted in the wrong lifecycle phase. The security fallback was correct, but relying on it as the normal path produced noisy logs and made a successful import look suspicious.

The Helm Voyage Logs UI now exposes an actionable diagnostics filter. `/api/system/logs` accepts `level=all`, `level=warnings`, and `level=errors`; the UI provides an **All** tab plus a **Warnings & Errors** tab so users can isolate real problems without scanning debug noise.

## Round 99 — Pack-first search phases and chat-result presentation

Whole-unit download requests use staged category-owned search phases. The shared assistant may pass a category-neutral `search_scope` such as `season_pack_preferred`, but it must not decide TV/movie/book/game semantics itself. A pack-preferred request means “try the bundle/full-unit candidate first, then fall back to scoped individual units if the owning category cannot find an acceptable pack.” It is not silently pack-only unless the user explicitly says so.

`search_media_torrents` results must include compact presentation metadata (`search_scope`, `candidate_count`, `search_summary`) and `ToolResultCompactor` must preserve those fields. Assistant replies should clearly separate pack candidates from fallback single-unit candidates and should never imply that omitted candidates are unavailable.

Communication bridges continue to be adapters over `ChatSessionRunner`. Progress pings are shared-runner events with backoff/caps; bridges must not implement their own repeating status loops or hard-code acknowledgement cycles.

Executable trace: `scripts/round99_pack_first_agent_flow_tests.py`.

## Round 100 — Tool contract audits and torrent candidate workspace

The assistant tool surface is now audited as a contract: every tool name exposed by `AgentToolPolicy`, prompts, or memory guidance must be registered in the active `ToolRegistry`. `suggestions_list` is a real read-only tool backed by persisted category suggestions, so the model can inspect full suggestion evidence instead of relying on truncated memory summaries. Historical/mistyped aliases are handled only at the executor boundary and only when the canonical tool is already allowed for the current intent.

Browser tools must fail soft when optional browser dependencies are missing. A missing browser runtime returns a structured unavailable error; it must not crash the agent loop with `NoneType` attribute errors.

Torrent season-pack search is a category-owned candidate workspace, not a literal text phrase. The TV category generates search schemas such as `S05`, `Season 5`, `S05 Complete`, dynamic metadata-derived ranges like `S05E01-E{episode_count}`, and whole-series container queries. The terminal episode comes from provider/cached metadata for that specific season; generic planner code must never hardcode it.

`search_media_torrents` keeps full result records in the result-set cache and exposes a compact `candidate_picker` to the LLM: stable ID, title, size, seeders, and bundle hints. The model can select candidate IDs, request more details/file lists, or queue by cached IDs. Raw tracker payloads and large arrays stay out of iterative chat context. Storage and queue placeholders resolve against this compact surface (`results_total_size_gb`, `candidate_picker`, and bundle-first candidate IDs).

Executable trace: `scripts/round100_tool_contract_pack_candidate_tests.py`.


## Round 101 — Agent contract hardening after repeated planner/tool failures

The repeated failures were traced to a structural seam, not to isolated bad paths: the LLM planner was allowed to invent JSON paths into future tool results, and the deterministic executor treated those invented paths as hard runtime dependencies. That produced an endless sequence of one-off alias patches (`results.episode.air_date`, `result.seasons`, `results_total_size_gb`, `results.latest_season`, etc.) and surfaced internal contract errors to the user.

Round 101 changes the architecture boundary for fresh DOWNLOAD discovery:

1. **Canonical download discovery plan.** Any fresh DOWNLOAD plan that includes `search_media_torrents` is reduced before execution to one category-owned search call with literal arguments only: name, language, explicit numeric season/episode when present, and `search_scope`. Metadata lookup, storage preflight, and queue placeholders are removed from that pre-plan.
2. **Category-owned latest/pack semantics.** If the user asks for the latest/last/current season without a concrete number, the shared planner omits `season`. The owning category resolves the latest season through provider metadata/cache and then generates its own pack schemas and fallback unit searches.
3. **Placeholder resolver as safety net.** `PlanExecutor` still resolves broad aliases for latest/current season and episode facts, but this is defensive compatibility for SEARCH/fact workflows and older plans. It is not the intended DOWNLOAD flow.
4. **Pack preference remains staged.** Pack-preferred is not pack-only unless the user's wording explicitly requests only/exclusive pack behavior. Internal constraint strings are never scanned as user intent.
5. **Regression as contract.** `scripts/round101_agent_contract_hardening_tests.py` captures the exact Round 100 crash shape, storage placeholder crash shape, registered-tool contract, and dynamic TV pack range behavior.

Executable trace: `scripts/round101_agent_contract_hardening_tests.py`.

## Round 102 — LLM-led, contract-bound agent runtime

Round 102 keeps the flexible agent model while removing the failure mode that caused repeated Discord crashes. Intent routing remains an LLM-based multilingual classification step. The assistant may still reason through category capabilities and call tools naturally; the application now treats concrete tool execution as a typed contract.

Fresh DOWNLOAD turns no longer enter the placeholder-based structured pre-plan. Instead, they use the normal tool-calling loop with an explicit download contract in the system prompt. `ToolCallExecutor` validates every LLM-authored tool call against the registered tool schema through `ToolContractValidator`. Invalid names, disallowed tools, model-authored placeholders, missing required arguments, bad enums, and bad primitive types return compact typed errors (`ok=false`, `error_code`, `recoverable`, `next_actions`) instead of surfacing Python/runtime crashes to the user.

Torrent discovery is represented as a candidate workspace. `search_media_torrents` stores the complete result set internally and exposes only compact, stable handles to the LLM: `result_set_id`, `candidate_picker`, `result_handle`, `search_summary`, and `next_actions`. The assistant can queue by `candidate_id`/`result_set_id`, show the best compact options, or call `inspect_torrent_candidate` to inspect one cached candidate when a season pack/full-series bundle may need file-list or coverage verification. Raw magnets and huge tracker payloads remain out of iterative chat context.

Active goal state is stored per session for SEARCH/DOWNLOAD/CONFIG turns. It records the user's goal, resolved category id, recent result-set handles, and valid next actions so follow-ups such as “search for a full pack” or “use the second one” attach to task state rather than depending on long transcript replay. Ordinary CHAT turns do not mutate this state.

This architecture preserves category extensibility: generic runtime code validates contracts and stores handles, while categories own release schemas, metadata resolution, bundle/pack semantics, unit descriptors, and fallback expansion. TV can generate dynamic `S05E01-E{episode_count}` queries from provider/cached metadata; another category can declare different schemas without changing the shared agent runtime.

Executable trace: `scripts/round102_llm_led_contract_tests.py`.

## Round 103 note: General category boundary and fallback context limits

A future `general` category may be added for explicit user-requested files or
release strings that do not fit richer categories.  It must be a normal category
with its own manifest, allowed file-format policy, exact-query search builder,
candidate evaluator, import layout, and candidate-workspace flow.  It must not
act as a generic escape hatch for failed TV/movie/book/game classification; rich
categories keep ownership of their domains even when metadata lookup is hard.

Endpoint context-window metadata is now tracked separately from the numeric
fallback.  A provider-reported context maximum is a hard clamp for user caps;
the app's conservative fallback is only the automatic default for endpoints that
do not expose metadata, so explicit user caps may exceed it up to the manual
safety limit.

## Round 104 — General Files as a first-class category

`general` is now a built-in category named **General Files**. It is intentionally narrow: it handles exact, user-named miscellaneous payloads such as documents, archives, datasets, manuals, lectures, audio files, or other one-off files that do not belong to a richer installed category. It is not a generic fallback for failed TV/movie/book/game searches.

General Files follows the same category contract as TV and Movies: it declares its own manifest/profile, LLM prompt, setup requirements, search query builder, candidate validator, import layout, file scanner, unit descriptors, and bundle/file-list affordances. The normal path control is an optional `library_path` override; when it is blank, completed payloads are stored under `settings.library_root/General` in a safe title folder while preserving the original filename.

The assistant must explicitly opt into the category by passing `category_id: "general"` to `search_media_torrents`. Existing rich categories continue to win whenever their router vocabulary or tracked items apply. Generic search planning must not switch a failed TV/movie request into General unless the user clearly asks for a miscellaneous exact file target or approves the category switch.

Frontend onboarding remains manifest-driven. `/api/setup/requirements` exposes the new category and its required path; the web UI checks category manifests at startup and shows a one-time prompt when a new category appears or when a required category setting is missing. Fresh installs see General Files in the setup/category settings flow automatically.

### Round 105 — Compass category settings ownership

Compass now follows the category-owned configuration model more strictly:

- One global `library_root` is the default parent for all concrete category libraries. Per-category paths are optional overrides only; blank category paths resolve to `library_root/<category default folder>` and setup/path saves create those folders on a best-effort basis. Naming templates, cadence values, provider toggles, scheduler participation, storage declarations, and lifecycle summaries are rendered in **Library Categories** from category manifests plus ignored live `config/categories/<category_id>.yaml` files bootstrapped from tracked `config/category-config-templates/<category_id>.yaml` templates, while shareable category behavior lives in `config/category-definitions/<category_id>.yaml`.
- TMDB, TVMaze, Trakt, Plex, and OpenSubtitles are no longer presented as one generic "Metadata and watch state" block. Categories that use those services declare setup requirements, and the UI surfaces the controls under the owning category.
- **Content Selection** writes shared TV/Movie candidate preferences to the private abstract `media.download_profile`; it is not a global UI-language or one-off torrent-quality bucket.
- **Shared Torrent Search & Indexers** owns only category-agnostic torrent infrastructure such as Jackett and direct scraper fallback.
- **Advanced Category Contracts** is read-only diagnostics. It shows the backend contract exposed to the UI and LLM so generic code does not hardcode TV/movie behavior; it is not an editable settings surface.
- Runtime code must honor category YAML sections through category helpers: `metadata.providers.<provider>.enabled`, `scheduler.enabled`, `storage.*`, and `lifecycle_policy`.

When adding a category, add editable user settings through `get_properties()`, service/setup needs through `provider_setup_requirements()` / `setup_requirements()`, and shareable definitions through `config/category-definitions/<category_id>.yaml` and blank private defaults through `config/category-config-templates/<category_id>.yaml`; live user path overrides/keys belong only in ignored `config/categories/<category_id>.yaml`. Do not prefill category templates with `./library/<Category>` paths: that is computed from the global root at runtime. Do not add category-specific fields to a global Compass section.

## Category-owned settings, inheritance, services, tools, and LLM guidance

Categories are the authority for domain behavior, and category files are deliberately split by audience:

- `config/category-definitions/<category_id>.yaml` is tracked and shareable. It defines the category contract: inherited base, services, executable tool/workflow declarations, LLM guidance, filename examples, accepted/rejected formats, and lifecycle semantics.
- `config/category-config-templates/<category_id>.yaml` is tracked but intentionally boring: blank/safe first-launch defaults for private config.
- `config/categories/<category_id>.yaml` is ignored and stores only local user values such as library paths, API keys/tokens, provider enable flags, scheduler/storage toggles, and personal download preferences.

Fresh installs use ignored local files for live category values. Global settings do not own media service credentials. Runtime settings see one effective deep-merged view, but save-time filtering writes only private config fields back to `config/categories` so people can improve/share category definitions without exposing personal configuration.

First-run setup must follow the same split. It may present Media services near the LLM controls because that is friendlier for new users, but TMDB/Trakt values are saved to the private abstract `media` category config; TV/Movie library folders are saved to their own private category configs; and shared TV/Movie search defaults such as language/resolution/size mode are saved to `media.download_profile`. Setup must not call generic Compass endpoints as a shortcut for category-owned values.

Trakt is special inside that category-owned service model: the bundled LJS Trakt Client ID is public app configuration shipped in `src/integrations/trakt_defaults.py`, not a user secret or setup requirement. Normal users leave the Client ID blank and use the Trakt out-of-band PIN/code login flow, which exchanges the code with redirect URI `urn:ietf:wg:oauth:2.0:oob`. Only user-specific access/refresh tokens are private. A custom Client ID is an advanced override and must use a matching callback redirect for that user's own Trakt developer application.

The built-in audiovisual hierarchy uses an abstract shared `media` definition and base class. `config/category-definitions/media.yaml` declares shared media services and defaults such as TMDB, Trakt, Plex, OpenSubtitles, media file-format rules, generic media tools, and common LLM guidance. Concrete categories such as TV Shows and Movies set `extends: media`, so runtime settings see inherited services/tools/guidance while their private YAML files only contain category-specific local values. TV adds TVMaze, season/episode workflows, pack-search examples, and episode-level rules. Movies add movie identity/year rules, movie workflows, and movie filename examples.

A category defines external services in YAML under `services.<service_id>` and consumes those values through `category_service_config()`, `category_service_enabled()`, and `category_service_secret()`. Shared services belong in the nearest meaningful parent config, not duplicated in every child. A new media-like category should inherit from `media` when it needs TMDB/Trakt/Plex/OpenSubtitles behavior; a non-media category should declare only its own domain services.

A category defines executable LLM tools through `declare_actions()` and `declare_workflows()`. `CategoryToolFactory` registers those declarations with the shared tool registry, while `AgentToolPolicy` exposes them only for the active category and only when the intent/risk gates allow them. YAML `tools` entries can narrow/document exposure but cannot invent executable tools. Shared workflows/tools should live on the closest reusable category base class; child categories add only their domain-specific workflows.

Natural-language category behavior belongs in `llm_profile()`, `src/core/categories/prompts/<category_id>.md`, and live YAML `llm_guidance`. The global prompt builder injects only the active category's compact guidance and context packet, including inherited media guidance where relevant, and must not dump unrelated library/category blobs into short follow-ups.



## Definition-Backed Category Extension Path

Category definitions in `config/category-definitions/*.yaml` are now capable of
creating real runtime categories when a dedicated Python subclass is not yet
needed. The registry loads concrete, non-abstract definitions through
`DefinitionBackedCategory`; dedicated subclasses still win when they exist.

This is the baseline extension path:

- use `extends` for an is-a base, such as `music extends audio` or `ebooks extends book`;
- use `mixins` for additive shared capabilities, such as `audiobooks extends book` and `mixins: [audio]`;
- declare `services` as metadata/setup contracts, not as working provider adapters;
- declare `runtime_dependencies` for local binaries/packages such as FFmpeg;
- declare `formats.accepted_file_patterns`, release terms, `download_profile`, `llm_profile`, `llm_guidance`, `tools`, and `lifecycle_policy` in the shareable definition;
- keep user paths, toggles, credentials, and personal download preferences in ignored `config/categories/<category_id>.yaml` bootstrapped from `config/category-config-templates/<category_id>.yaml`.

The generic runtime provides manifests, routing vocabulary, neutral format-based
scanning, safe import targets, conservative torrent candidate checks, and honest
workflow receipts. It does **not** pretend YAML has implemented rich provider
adapters. Metadata ingestion for MusicBrainz, Open Library, LibriVox, Gutendex,
Google Books, or other services still belongs in category-owned adapters and
workflows.

### Runtime Dependencies and Tools

Runtime dependencies are preflight facts, not executable code. A dependency like
FFmpeg can appear in setup manifests with availability and install hints, and a
category workflow may validate it before doing work. The audio conversion
workflow uses `CommandPolicy.create_subprocess_exec()` with an argv list and
`SafePathResolver`; it must not build shell strings from LLM/user input.

### Fallback Routing

Catch-all categories such as General Files should set a lower `router_priority`
than rich domain categories. This lets a request like `Kind of Blue FLAC` route
to Music and `The Left Hand of Darkness EPUB` route to Ebooks instead of being
captured by the generic file bucket. The tie-breaker is a category contract, not
a hardcoded list of new media types.


## Round 118 category review notes: metadata adapters, post-import hooks, and Soulseek

Definition-backed categories may now implement read-only metadata workflows through category-owned provider adapters. The shared scheduler and assistant must continue to invoke them through `CategoryWorkflowContext` and `category.execute_workflow(...)`; they must not import MusicBrainz/Open Library/LibriVox/etc. directly.

Completed-download orchestration may call the generic `after_library_file_imported(...)` hook and reconcile any returned sidecar paths. Category-specific side effects such as Music FLAC -> ALAC/M4A or Audiobook FLAC -> M4B conversion belong behind this hook, not inside torrent download plumbing.

Audio conversion policy is preference-driven and conservative: keep source files, preserve metadata/chapter/cover streams where FFmpeg supports them, and avoid automatic lossy-to-lossy transcoding. Ebooks intentionally have no automatic conversion pipeline yet.

Soulseek is not a torrent provider. A future integration should be a separate source-provider adapter, probably targeting slskd, with explicit setup for credentials, sharing roots, staging paths, queue policy, and legal notices. Do not implement the reverse-engineered Soulseek protocol directly in LJS.

### Round 121 stabilization note

The simple Ebook format selector persists to `download_profile.preferred_ebook_format`; richer `preferred_ebook_formats` and `format_priority` remain available for future category ranking. Download ready-time hooks must run in both hardlink/copy import mode and seed-in-place mode, because Music/Audiobook sidecars are category-owned post-import behavior rather than torrent orchestration logic.


## Round 122/123 category search-policy note

- Definition-backed categories can declare `search_policy` fields such as `language_relevant`, `use_global_quality_profile`, and bundle terms so new domains do not inherit TV/movie search semantics. Category-specific reject terms are allowed only when they truly belong to that category. Cross-category release vocabulary is handled by the generic boundary-signature index derived from other category definitions; Music must not list video tokens, Ebooks must not list audio tokens, etc.
- Generic assistant prompt code must not duplicate category-specific search vocabulary. Terms such as Music `discography`, `OST`/`Original Soundtrack`, or `bootleg` belong in the Music category profile and are injected through `category.build_prompt_guidance()`. The generic prompt may instruct the model to obey the active category profile, but it must not contain category branches that recreate those rules.
- Ignored live files in `config/categories/*.yaml` must remain user/machine config only. `CategoryConfigStore.save_all()` must not write tracked definition fields such as `display_name`, `llm_profile`, `search_policy`, service labels, purposes, or help URLs into private config. If old local configs contain such polluted fields, save-time filtering should clean them.

## Round 125 category-runtime cleanup note

Definition-backed categories now coordinate smaller collaborators for concerns that had started to make the bootstrap class change for unrelated reasons:

- `DefinitionCandidateValidator` owns generic search-result safety, category reject terms, bundle/discography candidate checks, and foreign release-signature rejection.
- `AudioConversionService` owns FFmpeg command construction/execution, safe conversion path handling, and preference-driven post-import sidecars.
- `router_matching` provides boundary-aware router-token matching so short category vocabulary such as `EP` or `TV` cannot match arbitrary substrings inside user prose.
- `CategoryMetadataResolver` uses declarative provider profiles instead of category-specific provider-selection branches.

Keep this pattern for future cleanup: split a responsibility only when there is a real policy or execution seam, and keep the category object as the owner of category definitions, safe roots, and lifecycle contracts.

## Round 126 metadata cache, stable IDs, and LLM-assisted disambiguation

Music/Book metadata adapters now use a shared resolver boundary with four explicit concerns:

- **Persistent provider-cache rows** in `category_metadata_cache`, keyed by category/provider/query/profile. This avoids re-querying free public services on every agent turn and gives future refresh jobs a clear TTL/expiry boundary.
- **Provider rate-limit state** in `provider_rate_limits`, plus conservative in-process throttling. Provider adapters must call through the resolver helper so MusicBrainz, Apple Search, Discogs, and similar APIs are not hammered by loops or repeated LLM attempts.
- **Stable external IDs** on every normalized result. Use provider-native identifiers first (`musicbrainz_release_id`, `openlibrary_work_key`, `openlibrary_edition_key`, `isbn_13`, `librivox_id`, `google_books_id`, etc.) and a deterministic fallback fingerprint only when the provider gives no durable identifier.
- **Conflict/disambiguation reports** instead of pretending deterministic ranking is enough. The resolver returns scores, evidence, selection facets, conflict groups, and an `llm_selection_instruction`. The LLM should apply user constraints such as narrator, edition, language, release type, format, year, and track/chapter completeness before selecting/pruning candidates.

Canonical Music/Book object models live in `src/core/category_object_models.py`. They are provider-neutral shapes used by adapters:

- Music follows the MusicBrainz-style hierarchy of artist credit, release group, release, medium, track, and recording.
- Ebooks distinguish work-level identity from edition/ISBN/translator/language/format identity.
- Audiobooks extend book identity with narrator/reader, abridgement, duration, chapter count, chapter metadata, and audio format.

Category YAML may declare `object_model` to document canonical types, stable identifiers, and LLM disambiguation facets, but this is definition-only contract data. It must never be persisted into ignored private category config.


### Round 127 metadata disambiguation cleanup

Metadata lookups now use a dedicated disambiguation boundary for cross-provider grouping, conflict reporting, and LLM selection packets. The persistent cache can explicitly reuse stale rows after provider failures, marked as `stale_on_error`, and provider `Retry-After` parsing handles numeric and HTTP-date forms. Music/book/audiobook object models gained small but important selection facets such as disc/track counts, series/volume, source level, narrator/reader, and abridgement.

### Round 128 provider adapter, local-object, and refresh cleanup

Provider-specific metadata parsing now lives outside the resolver in `src/integrations/metadata_providers/`. `CategoryMetadataResolver` should remain an orchestration boundary for cache lookup, rate-limit/backoff, adapter invocation, stale-on-error reuse, and disambiguation. Do not add provider URLs or provider JSON parsing back into the resolver.

Definition-backed category scans now carry lightweight local object evidence:

- Music scans reconstruct local artist/catalog → album → track evidence from path/file structure and persist track units instead of generic file units.
- Audiobook scans reconstruct narrated book files and chapter-file evidence from leaf folders or single-file M4B/M4A/MP3 releases.
- Ebook scans treat files/multi-format edition groups as local edition evidence and keep comics archives as explicit `comic_archive` units.

These reconstructed models are local evidence only. Provider metadata and LLM disambiguation remain authoritative for ambiguous identity choices such as exact release, narrator, translator, edition, or series order.

Library metadata refresh now uses category-owned stable snapshot policy. When a definition-backed metadata workflow resolves a library item, it persists the best provider snapshot with `stable_id`, `external_id`, cover URL, object model, and `metadata_refresh_policy`. The scheduler interprets only the generic policy envelope (`refresh_after_days`, stable ID presence, last refresh time) so it does not learn MusicBrainz/Open Library/LibriVox semantics. Fresh stable snapshots are not re-queried on every boot.

## Round 129 fallback and degraded-provider rule

Recoverable provider misses are not terminal plan errors. When a tool such as `metadata_lookup` returns `ok=false` because no configured provider result is available, the executor should preserve that tool result as context and let the agent continue with web/library fallback or a graceful limitation. Terminal planned-step errors should be reserved for invalid arguments, unsafe actions, missing required dependencies, or queue/download failures that cannot safely continue.

Concrete media categories inherit abstract `media` service credentials. During setup and Compass hot-saves, the in-memory settings object may contain the user-owned TMDB key only under `media.services.tmdb.api_key` while `movie` and `tv` contain only sparse private overrides. Runtime metadata client resolution must therefore check the concrete category first and then the abstract parent rather than clearing a working client.

Jackett search and Jackett indexer administration are separate health surfaces. A `/UI/Login` redirect from an indexer administration endpoint means indexer auto-configuration is degraded/action-required; it does not by itself mean every torrent search path should crash. Search health must reject UI-login redirects, report degraded diagnostics, and let explicit fallback providers run when primary search returns no usable results.

## Soulseek / slskd source boundary

Soulseek is modeled as a companion source provider through slskd, not as a Jackett/torrent fallback. `SoulseekSettings` stores the endpoint, API key, Soulseek credentials, and sharing policy. `slskd_config.py` computes the effective share plan, and `slskd_client.py` owns API/search/queue calls. Agent tools use `search_soulseek` and `enqueue_soulseek_download`; they must not pass Soulseek candidates to `queue_download`. A future transfer-monitor boundary should import completed slskd downloads through category hooks.

Automatic torrent companion searches still respect `SoulseekSettings.search_enabled_categories`, but direct user-invoked `search_soulseek` calls are exploratory and non-queueing. A stale legacy settings file that lists only `music`, `audiobooks`, and `ebooks` must be migrated to include `tv`, `movie`, and `general`, and direct explicit Soulseek requests should return category-filtered evidence rather than failing before any search is attempted.


## Round 193 macOS Jackett readiness rule

Managed Jackett must be treated as search-ready only after LJS has verified real configured indexers. The `all` aggregate endpoint queries configured indexers, and Jackett filter/virtual indexers also query configured indexers; therefore filter selectors are not an acceptable substitute for a working configured-indexer setup.

On macOS, managed Jackett configuration must probe and repair every LJS-owned path Jackett may use (`data/jackett_state/config/Jackett`, lowercase variants, and the managed `Library/Application Support/Jackett` compatibility path). Password repair must set `AdminPassword` to JSON `null`, not an empty string. LJS must pre-create a localhost-only managed `ServerConfig.json` before first start when none exists, then log a compact config-path diagnostic matrix before and after startup.

If the admin/indexer API remains login-gated or indexer configuration still produces zero configured indexers, LJS must not register Jackett as a torrent provider and must not hide the problem behind filter-indexer mode or direct-scraper fallback. The UI/settings diagnostics should show the exact managed config paths, admin probe status, and configure-indexer failure state so the next action is visible. Private tracker support remains the normal Jackett schema/config path; it is not replaced by direct scrapers or category code.

## Category-owned release watches

`ReleaseWatchRepository` and the scheduler provide only category-neutral retry
plumbing.  A release watch is a durable request to keep looking for one concrete
category unit until it is queued, completed, cancelled, or expired.  The base
schema stores generic fields such as `unit_key`, `next_check_at`,
`watch_start_at`, `expires_at`, `cadence_profile`, `requirements_json`, and
`payload_json`; it does not interpret TV episodes, sports matches, books, albums,
or any future category domain.

The owning category computes the watch semantics through its watch-policy hook.
For TV, `TvShowCategory.build_watch_plan()` derives the next episode, expected
air time, cadence profile, retry interval, RSS window, and requirement snapshot
from TMDB/TVMaze metadata plus the item/user settings.  The scheduler persists
that plan and later calls the normal category-aware search/discovery pipeline. A
retry timeout or missing candidate is recorded as a retryable state, not as a
successful empty result.

Important invariants:

- Category-specific concepts such as `S01E02`, air dates, season packs, and
  selective torrent-file download remain in the TV category extension.
- Generic services may store and retry watches but must not hardcode TV rules.
- Future categories such as sports events should implement their own watch-policy
  hook and reuse the same release-watch plumbing for replay availability windows.
- A watch with a future `watch_start_at` must not poll frequently until that
  release window opens.
- A queued watch is not completed until a category/import/library path confirms
  the requested unit is actually present.

## Round 217 coordinator/watch-policy review and UI boundary

Round 217 rechecked the Round 213-216 category-item/watch-policy work against the
intended category-centric architecture.  The resulting rule is that the browser
may display release-watch state, but it must not interpret category semantics.
The `/api/release-watches` endpoint and `ReleaseWatchPanel` therefore expose and
render the generic row state (`category_id`, `item_id`, `unit_key`, `status`,
`next_check_at`, `requirements`, `payload`, `last_outcome`) without adding TV
branches in the frontend shell.

Architecture responsibilities remain:

- `CategoryItemCoordinator` is the only normal write path for UI, assistant,
  automation, and library-discovery category item mutations.
- Categories own metadata enrichment and watch-plan semantics through
  `enrich_item_on_add()` and `build_watch_plan()`.
- The scheduler/RSS/release-watch services own generic persistence, retry,
  status, and sync plumbing only.
- UI surfaces may show category-provided watch state and payloads, but must not
  decide that an `SxxEyy` string is a TV episode or that a match replay belongs
  to sports. Those decisions remain in category extensions.

The mobile shell is also an architecture boundary: panel components should render
semantic sections that can be reflowed by CSS.  Components should avoid fixed
pixel widths and viewport assumptions.  The shell uses width and aspect-ratio
breakpoints because mobile browser/device emulation can report a wider CSS
viewport than the visible frame.

## Round 218 managed SearXNG web-research boundary

SearXNG is an optional managed web-research sidecar, not an acquisition backend.
Managed mode owns an LJS-local source checkout, virtual environment, generated
`settings.yml`, logs, and child process.  First-run setup and Compass may install
or repair this sidecar, but the manager must not adopt an already-running system
or user SearXNG process; if the preferred localhost port is occupied, managed
mode chooses another port and persists that endpoint.

The only normal backend path is:

```text
SearXNGSearchProvider -> WebSearchService -> future WebResearchService/evidence bundle
    -> category-owned interpretation hooks -> CategoryItemCoordinator/category lifecycle
```

SearXNG search hits are public-source candidates. They are not queueable release
candidates, torrent indexers, structured metadata records, or durable facts. The
download/search acquisition path remains Jackett/Torznab, Soulseek/slskd, and
category-owned providers. Generic web-search code must not interpret TV air
windows, episode labels, sports replays, book editions, music releases, or any
other category semantics.

Setup and Compass expose both automatic local SearXNG and manual/existing
SearXNG configuration. Automatic local setup is tested first because manual mode
can hide installer bugs behind a pre-existing service.

## Round 219 web-research evidence provenance

`WebResearchService` is the category-neutral boundary between configured public
web search providers and category interpretation.  It may plan bounded searches,
dedupe/canonicalize result URLs, fetch pages through `WebReader`, classify broad
source kinds, score evidence conservatively, and persist provenance in
`web_research_query_log` and `web_source_evidence`.  It must not decide that a
source proves a TV air date, album release, book edition, sports fixture, or any
other category fact.

The persisted `category_fact_provenance` table exists for category-owned hooks.
Only a category interpretation step should write durable facts there, and any
state change based on those facts must still go through `CategoryItemCoordinator`
or the owning category lifecycle/watch path.  Raw web-search snippets may be
stored as discovery context, but they are never authoritative facts and must not
be used directly to mutate item state, release watches, suggestions, or download
queues.

The normal public-web path is now:

```text
SearXNG/other WebSearchProvider
    -> WebSearchService
    -> WebResearchService evidence bundle
    -> category-owned interpretation hook
    -> CategoryItemCoordinator / lifecycle persistence when accepted
```

The `web_research` assistant tool returns evidence bundles with
`facts_authoritative=false`.  It is a safer follow-up to `web_search` when a task
requires fetched pages rather than snippets.  Existing media download flows still
use metadata/category context, `search_media_torrents`, candidate handles, and
`queue_download`; web-research results are not queueable candidates.

## Round 220 category-owned web-research hooks

Category web research is now a two-step contract.  Generic orchestration asks a
category to build a public-web research plan, runs the existing evidence
collector, then asks that same category to interpret the fetched evidence.  The
orchestrator lives in `src/search/web/category_research.py` and must stay free of
category-specific branches, acquisition provider calls, and queue/download
operations.

The category hook contract is:

```text
CategoryWebResearchInput
    -> category.build_web_research_plan(...)
    -> WebResearchService.collect_evidence(...)
    -> category.interpret_web_evidence(...)
    -> category_fact_provenance rows only
```

The base category default returns no searches and no durable facts.  Concrete
categories opt in by overriding the hook methods.  TV now implements these hooks
in `src/core/categories/tv_web_research.py` for official/primary source
discovery, air-date corroboration signals, and delay/postponement news signals.
Those interpreted facts are provenance records, not item mutations, and
`can_mutate_item` remains false until a separate category-owned workflow or
coordinator path deliberately applies a supported fact.

The `category_web_research` assistant tool and
`/api/category-web-research/test` diagnostic endpoint expose this hook path. They
must warn callers that category web research does not authorize downloads and
does not make search snippets authoritative.  Future music, books, sports, or
custom categories should add equivalent category-owned hook implementations
rather than extending the web-search core.

## Round 221 managed SearXNG hardening and rollback

Managed SearXNG must use the same project-level `data/` root pattern as other
managed runtimes.  Because `src/search/web/searxng_manager.py` is one package
level deeper than `src/search/jackett_manager.py`, its project-root calculation
must resolve to the repository root, not `src/data`.  Clean-machine installer
checks should fail if managed SearXNG drifts into `src/data/searxng`.

Managed upgrade is a sidecar lifecycle operation, not a web-search query.  The
manager must stop only the LJS-owned child process, create a backup of the
LJS-owned source checkout, venv, generated settings, and state, then reinstall
from the configured source ref.  If upgrade/start/JSON health fails, rollback
restores the backup before reporting status.  Rollback and uninstall also affect
only the LJS-owned managed runtime directories; manual/external SearXNG
endpoints are never deleted or adopted.

Compass may expose install, upgrade, rollback, repair, stop, and health controls,
but these remain system actions routed through `ActionGateway`.  They must not
bypass the managed-service boundary and must not register SearXNG with torrent
aggregation, candidate workspaces, or download queueing.

## Round 222 — Web Research Logging and Proactive Information Watches

Managed SearXNG and public web research must be observable before it ships to real devices. The managed installer writes process logs plus `manager-events.jsonl` under the LJS-owned SearXNG logs directory; search and research services log provider choice, health checks, fallback decisions, source dedupe, page-fetch status, evidence persistence, and category interpretation counts. Secret-looking fields must be redacted in structured trace events.

Compass/Settings and the first-run setup panel both expose automatic local SearXNG installation. Compass must save the visible web-search settings before invoking `/api/searxng/install` so source-ref/fallback/mode changes are respected. DuckDuckGo HTML is a separate degraded web-research fallback control and must not reuse the torrent direct-scraper fallback toggle.

Future proactive public-information tracking should reuse existing systems:

- user-authored recurring checks go through `PromptScheduler` and `create_scheduled_task` with `task_type=condition_check`;
- category-owned item news/rumor watches should be category lifecycle/watch hooks, not generic category branches;
- SearXNG/WebResearchService discover and fetch evidence;
- LLM evaluation may decide whether an update is worth notifying the user, but must not silently mutate category items or queue downloads.

This remains separate from media acquisition. Web research tracks public knowledge, patch notes, rumors, release-date/news evidence, and metadata gaps; Jackett/Soulseek/category acquisition providers remain responsible for downloadable candidates.

## Round 223 — First-Class Web Information Watches

Web information watches are now a durable, opt-in layer for proactive public-information tracking. A watch stores the user's objective, cadence, optional category/item scope, bounded query plan, notification policy, and last evidence signature. Watch events store run outcomes and evidence/query-log references. The watch layer is category-neutral: it may call `WebResearchService` for generic public topics or `CategoryWebResearchService` when a category/item scope is present, but it must not interpret category semantics in core code.

The watch service is not a media acquisition path. It must not call torrent search, Soulseek search, `queue_download`, or category-item mutation APIs. It may remember that the original user explicitly allowed future download queueing (`allow_download_queueing=true`), but scheduled runs must still prove release/availability through category/download tools before any queue action. Public web evidence alone never authorizes a download.

Assistant-created watches use `create_web_information_watch`, which creates the durable watch and schedules a bounded `PromptScheduler` condition check. The scheduled prompt first calls `run_web_information_watch`; if the evidence has not changed and no meaningful condition is met, the assistant replies exactly `LJS_NO_NOTIFICATION`, which the scheduler suppresses instead of notifying the user. This prevents weekly checks from producing noise.

When the user asks for a future item workflow such as “find out when the next season of show X starts and start downloading/tracking it,” the allowed flow is:

```text
metadata_lookup / category_web_research
        ↓
track_category_item, if the item is not already tracked
        ↓
create_web_information_watch(intent=next_season_start_tracking, allow_download_queueing=true)
        ↓
scheduled run_web_information_watch collects evidence
        ↓
LLM evaluates novelty/safety
        ↓
only if released/available, use generic category download tools to search/select/queue
```

The durable tracking mutation is owned by `CategoryItemCoordinator` through the generic `track_category_item` tool. TV-specific next-season/news/rumor query wording lives in the TV category web-research hook, not in the watch service or other core layers.

## Round 228 public-web source sufficiency for agent research

Managed SearXNG installation success is not enough by itself: the agent must
also expose and select the public-web evidence tools when the user's already
routed SEARCH/DOWNLOAD turn asks for live public information.  Category YAML may
narrow ordinary media/download tools, but it must not hide `web_search`,
`web_research`, `category_web_research`, or the web-information watch tools from
turns where they are otherwise allowed by intent/risk policy.

Structured metadata services remain the first source for stable catalogue facts
such as show identity, seasons, cast, and known air dates.  They are not
sufficient for questions about rumours, current news, leaks, production reports,
renewal/cancellation reports, patch notes, roadmaps, or public discussion.  In
those cases, a metadata-only answer is considered source-incomplete even when
`metadata_lookup` succeeds.  The advisory planner should include a non-mutating
public evidence step: prefer `category_web_research` for category items so the
owning category can interpret evidence; otherwise use `web_research` and only
fall back to raw `web_search` as candidate-source discovery.

This is a source-sufficiency rule, not natural-language intent routing.  Intent
classification remains LLM-owned.  The rule applies after a turn has already
been routed into SEARCH/DOWNLOAD research and prevents the LLM from treating
metadata snapshots as proof that no current public rumours/news exist.

### Round 229 — Public web evidence quality and managed SearXNG runtime

Managed SearXNG is now part of the runtime contract, not only the installer contract. If `web_search.provider=searxng` and `web_search.mode=managed`, LJS starts the managed sidecar after the web UI readiness gate and web tools may make one lazy start attempt before reporting provider failure or using an explicit degraded fallback. A previously installed but stopped SearXNG process must not silently cause every agent research turn to fall back to DuckDuckGo HTML.

Public web evidence remains category-neutral until category hooks interpret it, but the assistant must apply source-quality judgement. Search snippets are leads, not facts. Fetched official/provider/trade/reference sources beat unfetched snippets, fan calendars, social posts, SEO schedule pages, or fallback-only results. For current/rumour/news/future-schedule questions, the LLM must search with recency and source intent in mind, and must report uncertainty rather than extrapolating schedules or claiming no official information from weak or stale results.

For TV, category hooks own search planning and interpretation: next-season/rumour searches preserve the user query, include current-year and production/renewal/interview terms, and use recency windows. Episode schedule answers must be grounded in provider episode lists or high-confidence title-bound web sources; local downloaded episode lists and generic weekly assumptions are not schedule evidence.


## Round 230 LLM-planned category web research

Category public web research is now LLM-planned by default.  Generic code must not enumerate every natural-language synonym for research objectives such as rumors, renewal chatter, interviews, patch notes, or production updates.  The category exposes a `web_research_contract()` describing capabilities, evidence expectations, and source-quality rules; the LLM receives that contract plus the user's exact query/focus and produces a bounded `CategoryWebResearchPlan`.

Deterministic code still owns provider management, budgets, URL fetching, provenance storage, and safety gates.  Category deterministic plans remain fallback behavior when the LLM planner is unavailable or returns invalid output.  They are not the primary intent-mapping mechanism.

`category_web_research` may receive a free-form semantic `intent`.  Exact enum-style labels are not required.  The `query` argument is the important carrier of user intent and must be preserved into category planning.


## Round 231 — Public web research prompt guidance and source-planning context

Public web research prompt/context is a shared contract. Generic code must provide the LLM with current runtime date, source-quality rules, freshness controls, and evidence sufficiency criteria; category code must add domain-specific research guidance through category prompts/contracts. Do not solve future web-research misses by adding one deterministic natural-language synonym at a time.

The reusable category-neutral guidance lives in `src/search/web/research_guidance.py` and is injected into:

- the main assistant SEARCH/DOWNLOAD prompt through `PromptBuilder`;
- advisory structured planning through `ReasoningPlanner`;
- category web-research planning through `LLMCategoryWebResearchPlanner`;
- scheduled web-information watch prompts.

Category-specific web-research rules belong in category-owned surfaces such as `src/core/categories/prompts/<category>.md` and `CategoryContractMixin.web_research_contract()`. For TV, the contract describes current-news/rumour/production/interview/source-quality/freshness behavior, but the generic planner still treats intent labels as semantic hints rather than enum strings.

When tools expose provider controls, the LLM must be told to use them: categories such as `news`/`general`, `time_range` values such as `day`/`month`/`year`, exact title phrases, and targeted source operators such as `site:` for official/source-of-record checks. Search snippets remain leads; fetched pages and category interpretation are required before confident current/future claims. Negative claims such as “no official word” require suitable current official/reference/trade coverage and must not be made from stale, degraded, or snippet-only results.

`web_search` now exposes optional `categories`, `language`, and `time_range` parameters so the LLM can use the same freshness/source controls available to `web_research` and `category_web_research`. `WebResearchService` may reorder candidate fetches within the bounded fetch budget to prefer likely official/trade/reference/news evidence over social/fan/SEO pages, but this remains category-neutral and does not turn search snippets into facts.

## Round 232 LLM Prompt Guidance Rule

LLM-facing behavior must be driven by concise shared task guidance plus category-owned context, not by scattered long rule blobs or hard-coded phrase mappings.

- Generic operating discipline lives in `src/ai/task_prompt_guidance.py`.
- Public web/source-quality discipline lives in `src/search/web/research_guidance.py`.
- Category-specific prompt guidance and research/download rules live in category prompt files, category YAML/contracts, and category hooks.
- Main chat prompts, advisory planner prompts, scheduled task wrappers, and web-information watch prompts should reuse the shared generic guidance so smaller models receive consistent instructions.
- Tool schemas should describe semantic objectives and stable handles clearly. They should not imply exact enum labels for natural-language intent unless the receiving tool actually requires an enum.
- The LLM decides semantic research/action strategy from the user wording and current context. Deterministic code validates available tools, schemas, candidate IDs, budgets, evidence provenance, confirmation gates, and side-effect safety.
- Public web evidence can inform category/download decisions, but category/download tools must still prove release/availability before any queueing action.

### Round 233 TV Download Search Quality Rule

Assistant torrent search must preserve the user's literal media title and let the owning category build release-query schemas. Generic agent code may carry the current user prompt for auditing/repair and may enforce category-neutral safety rules, but it must not hard-code TV episode or pack semantics.

For TV:

- exact episode searches must not stop after broad non-language results when a configured/requested media language exists;
- season/show requests should start from category-owned bundle/pack search when no specific episode is requested;
- pack queries should include episode-range naming patterns such as `S01E01-E06` / `S01E01-06` when provider metadata knows the season length;
- broad title-only results must not be converted into multi-unit batch recommendations without a declared season/unit scope.

## Round 234 Agent Rule — Fresh download requests must not reuse stale candidate context

A fresh media acquisition request such as “grab/download/search <title> in <language>” must not be answered from old pending torrent candidates or an inherited active-goal result set. Pending candidate context is valid only when the current user message semantically selects, inspects, refines, confirms, or queues a previous result set/candidate.

The LLM remains responsible for semantic download choices, but the runtime must enforce these guardrails:

- suppress stale pending result-set context for fresh acquisition requests before building the main tool prompt;
- start a fresh active goal without inherited result sets for fresh DOWNLOAD turns;
- require at least one real download/search/queue-management tool call before a DOWNLOAD turn can produce a user-facing answer;
- if the LLM tries to answer a DOWNLOAD turn from memory or stale context before using tools, suppress that prose and reprompt it to call the appropriate registered tool;
- use old `result_set_id` / `candidate_id` handles only for real follow-ups such as choosing “the first one,” inspecting a candidate, or confirming queueing.

Verify this with `scripts/round234_fresh_download_context_tests.py` after touching pending-action context, active-goal state, streaming agent loops, or download prompt guidance.

## Round 235 TV Pack Recall Rule — Title Stopwords Are Not Semantic Barriers

Torrent search must not lose valid category-owned candidates merely because the LLM turned a literal title into a search-shaped title and dropped a small inner title word such as `of`. The downloader may still use exact phrase matching first, but the owning category must validate TV pack relevance with a conservative significant-token matcher so `A Knight the Seven Kingdoms` can match `A Knight of the Seven Kingdoms` while short/article titles such as `The Boys` still do not overmatch unrelated titles such as `The Hardy Boys`.

For fresh TV season/full-season requests, episode-range torrents such as `S01E01-06` are first-class season-pack candidates. They must survive category validation when they match the requested show title and season, especially when they advertise the requested language. If a search log contains such candidates in raw Jackett/Torznab results, they must not be filtered out before reaching the LLM candidate workspace unless the category can explain a hard rejection reason.

Literal-title repair remains a generic pre-search helper, but it is not the only safety net. Category validation must also tolerate harmless missing title stopwords because not every agent/tool route is guaranteed to preserve the exact user span.

## Round 236 LLM Candidate Adjudication Rule — Deterministic Filters Are Not the Semantic Judge

Torrent candidate discovery must treat deterministic category code as a bounded workspace builder, not as the final semantic authority. Hard deterministic filtering is allowed for safety and queueability only: no magnet, blacklisted release type, impossible category shape, or explicit user-blocking constraints. Messy release-name judgments such as title connector words, language tags, episode-range pack naming, edition names, and whether a season pack is a better match than scattered individual episodes must be exposed to the LLM through the compact candidate workspace whenever the row is plausibly related.

`search_media_torrents` now performs an LLM candidate-adjudication pass after category-owned search and safety annotations. The pass receives the exact user prompt, effective tool arguments, category selection guidance, and compact candidate rows. It may recommend/reorder candidates and provide an answer hint, but it must not queue anything and must not delete candidates. Queueing still requires stable `candidate_id`/`result_set_id` handles and normal queue validation.

TV season-pack searches must not silently discard plausible range/pack results before LLM review merely because a title matcher is uncertain or an LLM-generated tool argument lost an inner title word. For example, an `S01E01-06` result with `ITA` evidence must reach the LLM candidate workspace for a full-season Italian request even if deterministic parsing is imperfect. The LLM then compares title, requested season, language, pack coverage, seeders, and warnings.

## Round 238 Download Search Review Rule — title preservation and full-candidate LLM adjudication

Fresh media-download discovery must preserve the user's literal media title all the way from the agent tool call through scheduler/category search. Generic structured-unit parsing may remove season/episode phrases, but it must not delete connector words inside titles such as `of`, `the`, or `a`. If a phrase like `season 1 of <title>` leaves a leading connector after coordinate extraction, strip only the leading connector and never perform global title-word deletion.

Torrent candidate adjudication is LLM-led after deterministic hard guards. Category ranking/filtering may remove non-downloadable rows and obvious hard safety failures, but plausible title/language/pack candidates must stay visible to the LLM candidate reviewer. When candidate lists exceed a model's context, the reviewer must cover the full list through bounded chunks and recursive tournament rounds; it must not silently omit tail finalists from the final comparison.

Search-result payloads should expose whether LLM candidate review actually ran (`llm_candidate_review_status`) so failures to instantiate the task LLM, empty candidate sets, or review errors are visible in logs/tool output instead of masquerading as a complete semantic review.

Verify with `scripts/round238_search_pipeline_deep_review_tests.py` after changing scheduler title parsing, TV pack gates, `search_media_torrents`, candidate adjudication, or candidate result compaction.

## Round 239 Agent Rule — Candidate adjudication must survive result compaction

When `search_media_torrents` runs LLM torrent-candidate adjudication, the final
chat model must see that adjudication happened. Tool-result compaction may
remove magnets and raw tracker rows, but it must preserve:

- `llm_candidate_review_status`;
- compact `llm_candidate_review` metadata;
- `recommended_candidate_id` and `recommended_candidate_ids`;
- per-row `llm_recommended` markers in both the compact picker and selected
  candidate details.

This prevents a reviewed torrent workspace from collapsing back into opaque
provider ordering. If the review was skipped or failed, that status must also be
visible so the assistant does not imply that the task LLM semantically reviewed
the candidates. Verify with `scripts/round239_llm_candidate_visibility_tests.py`
after changing `DownloadCandidateAdjudicator`, `search_media_torrents`, or
`ToolResultCompactor`.

## Round 240 Agent Rule — LLM candidate recommendations must stay visible in every picker surface

The torrent candidate reviewer may correctly identify a semantic match, but that
work is wasted if the compact candidate workspace hides the recommendation from
the final chat model.  Every LLM-visible candidate picker produced after
`search_media_torrents` must preserve both stable candidate handles and explicit
review markers:

- include `candidate_id` alongside any short `id` alias;
- preserve `llm_recommended=true` for candidates recommended by the task LLM;
- keep compact warnings/blockers that affect whether the model should inspect,
  queue, or ask the user;
- ensure candidates named in `llm_candidate_review.recommended_candidate_ids`
  remain in the compact `candidates` list even if they are outside the normal
  top-N provider/ranker window.

This keeps the architecture LLM-led at the semantic selection layer: category
code and deterministic filters build a safe bounded workspace, the task LLM
adjudicates torrent relevance, and the final chat LLM receives that adjudication
as visible decision evidence rather than opaque reordered rows.  Verify with
`scripts/round240_candidate_visibility_followup_tests.py` after changing
`_candidate_picker_rows`, `ToolResultCompactor`, or candidate-adjudication
metadata.

## Round 241 Review Notes — LLM Season-Pack Selection Must Not Be Contradicted by Deterministic Batch Groups

When `search_media_torrents` returns a clear LLM-reviewed season-pack candidate for a full-season request, deterministic batch grouping must not also expose per-episode alternatives as an apparent multi-unit recommendation.  Batch recommendations are useful only when no suitable bundle/season-pack candidate exists or when the user explicitly requested individual units.  Otherwise they can mislead the final chat model into inventing missing/extra episodes from broad provider noise.

For TV season-pack requests, category-owned search may carry provider-derived expected episode counts from the generated range query (for example `S01E01-E06`).  The tool result should preserve that compact evidence and annotate candidates whose episode range covers the full requested season.  The final LLM must treat `requested_season_coverage=full_requested_season` as the season candidate and must not describe that pack as partial or infer additional episodes from unrelated candidates.

If the LLM candidate adjudicator returns a recommended candidate with `should_queue_now=true`, the tool result should place a direct `queue_download` affordance first and should not let lower-ranked batch/episode alternatives override `llm_next_action`.

## Round 242 Download Quality Choice Rule

Torrent selection must not collapse materially different quality/size choices
into a single proposal merely because one candidate is queueable. Deterministic
code builds a bounded candidate workspace and annotates hard facts such as
language, coverage, seeders, resolution, codec, per-episode size, and estimated
bitrate. When multiple candidates satisfy the requested unit/language/coverage
but trade resolution, codec, bitrate, size, or seeder health, the tool result
must preserve those choices and the final assistant must present them to the
user instead of silently queueing the first or smallest match.

For TV full-season requests, compare matching season/range packs across
resolution and codec as well as within a single resolution. A compact 1080p HEVC
pack and a larger 720p/x264 pack are not interchangeable; without a saved or
explicit bitrate/size preference, the LLM should review them as viable options,
set `should_queue_now=false`, and ask the user to choose the preferred
quality-size tradeoff. Generic code may block auto-queue on this ambiguity but
must not make the semantic preference decision itself.

## Round 243 Search Audit Rule — raw results and filter decisions must be debuggable

Torrent-search quality failures must be diagnosable from logs without guessing.
Every provider query handled by `SearchAggregator` must write both a readable
`logs/searches.log` entry and a structured `logs/searches.jsonl` record that
captures query text, category, provider diagnostics, raw/deduped/accepted/ranked
counts, and redacted result rows. Magnets and private tracker passkeys must never
be logged; store only `magnet_present` and a short info-hash prefix.

Category-owned filtering must also be auditable. TV pack and exact-episode query
ladders must log a structured `TV_SEARCH_FILTER_AUDIT` line for each query,
including the item, language, season/label, counts, accepted rows, rejected row
samples, and reason codes such as `accept_structural_season_pack`,
`accept_llm_review_plausible_pack`, `reject_title_mismatch`, or
`reject_not_detected_as_requested_season_pack`. This bridges the gap between raw
tracker results and the final LLM candidate workspace.

`search_media_torrents` must log a `SEARCH_MEDIA_TORRENTS_WORKSPACE_AUDIT` line
that shows the final cached candidate workspace, quality-choice policy, LLM
candidate-review status, recommended candidate IDs, and next-action affordances.
This is the support/debug boundary: if the user says a result was missed, logs
must show whether it was absent from providers, removed by quality/blacklist,
rejected by category gates, hidden by compaction, or rejected/ignored by the LLM.

Verify with `scripts/round243_search_audit_logging_tests.py` after changing
search providers, aggregation, TV search gates, `search_media_torrents`,
candidate adjudication, or compaction.

## Round 245 Download Follow-up and Telemetry Stability Rule

Progress/status acknowledgements for DOWNLOAD and CONFIG turns are deterministic
persona messages. They must not use a free-form LLM completion before tool
evidence exists, because a cosmetic acknowledgement can otherwise become a
visible refusal or unsupported answer.

Recent torrent candidate context must remain visible for short follow-up
constraint selections such as a resolution/codec/quality choice. Fresh media
requests may suppress stale result sets, but a short format/quality selector is
a continuation of the visible candidate workspace and should let the LLM queue
or inspect the selected candidate by stable `candidate_id`.

Quality-choice prompts should ask users about real tradeoffs only. Equivalent
mirror candidates with the same coverage, language, resolution, codec, and
materially similar size/bitrate must be collapsed for choice presentation, with
higher seeder health preferred. Asking the user to choose between equivalent
mirrors is noise; deterministic code may pick the healthier mirror once the user
has selected the quality tier.

Download telemetry shown in Helm/Hold must avoid UI-only oscillation caused by
interleaving live websocket samples with slower polling snapshots. Polling may
refresh structural state, but it must preserve recent non-zero live speed and
swarm values for active downloads unless the backend reports a true lifecycle
change or the grace window expires.

## Round 246 Runtime Date Grounding Rule

All agent-facing tools that expose air dates, release dates, publication dates,
source dates, or future/current public-source claims must carry runtime date
context in the tool result. Prompt-only date guidance is not sufficient: compact
result summaries and small LLMs need the current date adjacent to the evidence
being evaluated.

For SEARCH and category web-research turns, “next”, “upcoming”, “future”,
“latest”, “current”, and equivalent localized wording must be anchored to the
runtime current date before answering. A source or metadata page that says a
season is “upcoming” in a year before the runtime year is stale background, not
current upcoming evidence. If season 3 aired in 2025 and the runtime date is in
2026, a user asking about the next/upcoming season is asking about a later
season, unless they explicitly named season 3.

Structured metadata remains useful for known seasons/episodes, but it must not
terminate a next/upcoming-season public-information question by itself. Those
questions require category/web research unless structured metadata contains a
fresh exact future schedule. Metadata results should include date relations for
season, episode, and show dates where available, plus a source-sufficiency
warning when public evidence is required.

Fetched web evidence should be annotated or warned when a source uses
future/upcoming wording tied to a past year or when a publication/update date is
stale for a current-public query. The final assistant must not make negative
claims or future-schedule claims from stale, undated, degraded, or snippet-only
evidence without stating the limitation and/or searching again.

Verify with `scripts/round246_runtime_date_grounding_tests.py` after changing
metadata lookup, public-web evidence policy, category web research, web-research
fetching, or current-date prompt guidance.

## Round 247 — Universal LLM Runtime Date Prompt Context

Round 246 put runtime-date grounding next to metadata and web evidence, but some
auxiliary model calls still constructed one-off user-only prompts: intent
routing, summaries, candidate adjudication, taste extraction, legacy direct
provider calls, and similar helper tasks.  Runtime date/time grounding is now a
provider-boundary invariant as well as a prompt-builder convention.

`src/utils/runtime_prompt_context.py` owns the shared LLM-facing runtime date/time
block.  `TaskLLMClient.completion()` and the lower-level `LLMClient.completion()`
call `RuntimePromptContext.ensure_messages()` before logging or sending messages,
so every production LLM call receives current datetime, date, year, timezone,
and explicit instructions for resolving today/tomorrow/yesterday/next/upcoming/
latest/current/recent wording.  Existing prompt builders still reuse the same
block through `WebResearchPromptGuidance.runtime_context()`, and
`RuntimeDateGrounding.runtime_context()` now uses the same payload so prompt text
and tool-result payloads cannot drift.

Legacy direct `litellm.acompletion` paths that bypass the task-aware client must
also call `RuntimePromptContext.ensure_messages()`.  New LLM call sites should use
`TaskLLMClient` where possible; if a direct provider call remains necessary, it
must explicitly apply the runtime prompt context before sending messages.

Verify with `scripts/round247_universal_runtime_date_prompt_tests.py` after
changing LLM provider clients, runtime date guidance, direct legacy LLM calls, or
prompt construction utilities.

## Round 248 — Missing Media Drive Startup Resilience

Configured payload storage paths may point to removable or external volumes. A
missing removable volume must be treated as a runtime storage-unavailable state,
not as an instruction to create the missing mount directory under a parent such
as `/Volumes`, `/mnt`, `/media`, or `/run/media`. Creating that directory can hide
an unplugged-drive problem and redirect downloads to the wrong disk.

`src/core/storage_path_availability.py` owns writable storage-path probing and
safe directory creation. Startup constructors for the torrent engine and
download manager may best-effort prepare the configured download directory, but
they must never raise a fatal startup exception just because a media drive is
unplugged. LJS should still launch its UI/API so the user can see and fix the
configuration.

Storage reports and the `check_storage_capacity` tool must surface unavailable
configured roots as critical, with a clear reason such as a missing/unplugged
volume. Queueing or starting a torrent must call the guard before handing a save
path to libtorrent. If a recovered/queued download cannot start because the
configured storage target is unavailable, the item should be held/stalled with a
storage-unavailable reason rather than crashing startup or marking the payload as
a normal torrent failure.

`src/core/download_storage_recovery.py` owns reconnect recovery for these held
items. It polls storage-stalled downloads, checks their configured save path
without faking mount roots, and requeues only the rows whose target path has
become writable again. The ordinary queue gate still decides when those rows may
consume slots, so reconnect recovery must not bypass auto-download settings,
explicit user approvals, priorities, or category-owned save-path choices.

Verify with `scripts/round248_missing_media_drive_startup_tests.py` after
changing storage monitoring, download startup/recovery, torrent engine save-path
creation, or setup/settings path handling.

## Round 249 — Torrent Scope, Language, and Quality Candidate Hygiene

Torrent candidate workspaces must be scope-clean before the LLM and user see
options. Category-owned TV search may keep messy but plausible range/pack names
visible for LLM review, but a row that is explicitly for the wrong season or a
single unrelated unit is not a valid alternative for a requested season pack.
For example, an `S04E01` or `S05E01` single episode must never be presented as an
option for a `Season 1` request merely because the broad tracker query returned
it and the show title matched.

TV owns tracker-specific season-pack parsing. Public trackers may express full
or partial season ranges as adjacent numbers such as `S01e01 10` as well as
`S01E01-E10` or `S01e01-10`; those forms must be recognized as episode-range
season packs when the range is sane. The parser must also avoid treating
resolution tokens such as `S01E01 1080p` as episode ranges.

Configured media language is a constraint, not a conversational prompt. If the
effective media language is English and a candidate advertises `ITA+ENG`,
`dual`, or `MULTI`, the assistant may mention that extra audio exists as a fact,
but it must not ask whether the user wants Italian unless the user explicitly
requested Italian or the configured language cannot be satisfied.

Quality-choice prompts must compare equivalent logical units only. Different
episodes from the same season are not bitrate alternatives to one another. For
a season request that falls back to individual files, the tool may report that
coverage is scattered or incomplete, but it must not use S01E01, S01E04, and
S01E08 as a fake same-resolution quality spread. Same-episode variants and
matching season-pack variants may still trigger a quality/size choice when their
size, bitrate, resolution, or codec differences are material.

When the user replies to a visible quality-choice prompt with a preference such
as higher quality, smaller size, or a specific resolution, the assistant should
resolve that preference against the pending `result_set_id` / `candidate_id`
workspace first. It should not launch a fresh broad search that can pollute the
conversation with wrong-season rows unless the pending workspace cannot satisfy
the requested scope.

Verify with `scripts/round249_torrent_scope_language_quality_tests.py` after
changing TV pack parsing, TV torrent gates, torrent candidate quality-choice
policy, download prompt guidance, or the LLM candidate adjudicator.

## Round 250 Review Notes — Torrent Candidate Scope, Language, Seeder Health, and File Progress

Round 250 fixed a live torrent-selection failure where a TV season request surfaced wrong-season single episodes, promoted weak dual-audio rows too highly, and displayed blank per-file progress despite non-zero parent torrent progress.

The rules added here are architectural, not title-specific:

1. **Final candidate payload cleanup is category-owned.** `search_media_torrents` may call an optional category hook such as `filter_agent_candidate_payloads_for_request()` after generic candidate projection. Generic code still only passes neutral payload fields and conventional descriptors; the owning category decides whether a row is the requested season, requested episode, containing bundle, or fallback.
2. **TV bundle sizing must use the TV bundle parser everywhere.** Tracker-style ranges such as `S01e01 10` are season/range-pack evidence and must be divided by the detected episode count before estimating useful per-episode size or bitrate. No ranking path should treat such a range pack as one giant episode.
3. **Language satisfaction is not a bonus for extra languages.** When the configured media language is English, `ITA+ENG` or `MULTI` can be acceptable because it contains English, but it ranks behind English-only or language-unknown scene releases when those have comparable scope and better swarm health. The assistant should not ask whether the user wants Italian just because a dual-audio candidate exists.
4. **Seeder health comes before marginal quality differences.** After requested unit/pack coverage and acceptable language are satisfied, materially higher seeder availability outranks small bitrate/size differences and extra audio tracks. Quality-choice prompts may still show distinct tradeoffs, but their ordering must not make a weak swarm look like the preferred result.
5. **Expanded file progress may be estimated from parent progress.** If multi-file torrent rows have no exact per-file counters yet but the parent torrent has reliable aggregate progress, the UI view model may mark selected file rows with `progress_estimated=true` and `progress_basis=estimated_from_parent_torrent_progress`. Exact file-progress cache updates always replace that estimate.

Regression coverage lives in `scripts/round250_torrent_language_scope_session_recovery_tests.py` and `scripts/round250_torrent_language_seed_scope_progress_tests.py`.

## Round 251 — Download Chat Session-State and Streaming Stability

Download correction/refinement turns must not erase the immediately previous
machine state. Fresh-request heuristics may guard old torrent result sets so the
LLM does not queue them for an unrelated new title, but they must not hide
structured handles or the latest state-changing conversation facts. The pending
result-set packet now stays visible with `fresh_request_guard=true`; this tells
the LLM that the prior workspace is usable for corrections, refinements,
complaints, confirmations, and stable `result_set_id`/`candidate_id` actions,
but not as a substitute for an unrelated fresh search.

Active goal state follows the same rule. If a session has actionable recent
result sets, a fresh-looking DOWNLOAD prompt may start a new search semantically,
but the runtime must still expose the active result-set context so the model can
understand what it is correcting or replacing. Conversation context trimming for
fresh-looking DOWNLOAD turns keeps the immediate recent tail and drops only older
compressed/semantic recalls. This prevents the model from forgetting a torrent it
just queued, cancelled, paused, or otherwise changed.

Streaming tool loops must never show assistant prose from an iteration that also
emits tool calls. Providers can stream a candidate table or partial explanation
before the tool-call delta appears; displaying that text causes duplicate or
contradictory chat messages when later iterations run more tools and produce a
new answer. The streaming loop therefore buffers each iteration. If tool calls or
bare-tool recovery are present, buffered prose is suppressed and only the final
no-tool response is emitted and recorded in conversation memory.

State-changing download tools are receipt-bound. After any queue, cancel,
remove, pause, resume, restart, movement, or priority mutation, the final answer
must report what the latest tool result says, including any download IDs/statuses
returned. The agent must not claim a queue/cancel happened without tool evidence,
and must not silently cancel/remove an active download merely because the user is
asking for a better match or correcting constraints; that is a search/refinement
until the user explicitly asks to cancel/remove or confirms a confirmation gate.

Verify with `scripts/round251_chat_stream_context_action_tests.py` after changing
pending-action context, active-goal state, conversation trimming, streaming agent
loops, or download prompt/tool guidance.

## Round 252 — TV Aired-Missing Release Watches and Provider-Outage Suggestion Safety

TV release monitoring must not depend only on provider ``next_episode`` fields.
Some active shows expose a reliable aired episode list while no future
``nextepisode`` object is available. In that state, the TV suggestion workflow
can correctly detect aired missing episodes, but the generic release-watch retry
job has nothing to search unless the TV category creates concrete watches for
the aired missing frontier.

The TV category now owns both forms of release watch:

1. **Already-aired missing frontier watches.** When TVMaze episode-guide data
   shows aired episodes after the latest local/progress coordinate, and those
   SxxEyy units are not in the canonical downloaded set, `TvShowCategory` emits
   concrete `CategoryReleaseWatchSpec` rows for those units. The generic
   scheduler still only persists and retries neutral unit watches; it does not
   learn what a season or episode means.
2. **Future next-episode watches.** If provider metadata also exposes a future
   next episode, TV adds that watch too, deduped by unit key.

Suggestion compilation is passive UI evidence and must not be mistaken for an
automatic search/queue mechanism. If an aired episode is missing and automation
is enabled, the release-watch path must own retrying search/discovery. If
automation is disabled, the same watch may still search/notify according to the
scheduler policy, but it must be a real durable watch row rather than only a
suggestion card.

Provider outages must not erase useful existing suggestions. TVMaze/TMDB errors
or empty results caused by provider failure should mark the episode guide as
unavailable and preserve existing suggestions instead of clearing the item and
making a show appear fully up to date. An empty provider response with a recorded
provider error is an outage signal, not proof that there are zero aired episodes.

Verify with `scripts/round252_star_city_release_watch_tests.py` after changing TV
watch planning, TV suggestion compilation, TVMaze error handling, scheduler
release-watch syncing, or release-watch retry behavior.

## Round 253 / Round 258 — TV Show New-Episode Automation Checkbox and Safe Default

TV new-episode automation is an item-owned policy exposed in every tracked TV
show detail/inspector payload as a simple enabled/disabled checkbox labeled for
new episodes.  After the Round 258 disk-space incident review, the safe default
is **disabled** for library shows: missing or legacy `null` TV values are
normalized to disabled, and release-watch retries for that show may notify or
search but must not auto-queue until the user explicitly opts in.

TV may auto-enable the per-show switch only after a user explicitly downloads an
episode or season unit from the currently active/airing season of that same show.
Historical backfills, completed/ended shows, whole-library imports, ambiguous
metadata, and existing library entries must remain opt-in.  The category owns the
active-airing decision from provider metadata (`next_episode`, status, and
episode airdate rows); generic download/queue code only calls the category hook
and must not interpret TV seasons or episodes itself.

The generic scheduler still does not interpret seasons or episodes.  It reads
the current category item state plus category-provided release-watch requirement
snapshots and then calls the existing category-aware discovery path.  The current
item value is authoritative: a stale queued/release-watch row whose snapshot says
`auto_download=True` must not override a current `TvShowItem.auto_download=False`.
The frontend saves the checkbox through the generic category item update endpoint
so `CategoryItemCoordinator` persists the item and immediately resynchronizes
the category watch policy.

Important invariants:

- New and legacy TV shows default to notify/search-only behavior, not automatic
  background downloads.
- A user-set `auto_download: false` on one show must be preserved and must veto
  stale watch snapshots created by older default-on builds.
- Manual user-approved downloads of an active-airing season may set that show to
  `auto_download=True`; manual old-season backfills must not.
- Duplicate queued/download rows for the same category item and category-owned
  unit descriptor must be rejected or cancelled safely before queue workers start
  them, including provider-light rows that lack TMDB/TVMaze IDs.
- UI code may render the checkbox for TV/episodic category detail payloads, but
  it must save through category item mutation APIs rather than editing settings
  directly.

Verify with `scripts/round253_tv_auto_download_inspector_tests.py` and
`scripts/round258_tv_auto_download_safety_and_duplicate_queue_tests.py` after
changing TV item models, TV watch planning, category item update flows,
download queue identity, or the TV library inspector.

## Round 254 — TV Torrent Recall: Title Variants, Explicit-Language Ranking, and Bounded Fallbacks

TV torrent search must treat small title-normalization differences as recall issues, not as proof that a candidate is unrelated. Tracker/provider titles may represent possessives and plural forms differently from user prompts: for example, a user may ask for `Widow Bay` while provider metadata and torrent rows say `Widow's Bay` or `Widows Bay`. TV title matching may allow narrow singular/plural token equivalence inside an otherwise ordered multi-token title window, but it must still reject unsafe one-token/article-title collisions such as `The Boys` versus `The Hardy Boys`.

Explicit media-language requests are stricter than configured-language defaults. For an English-configured install, unknown-language scene releases may remain preferable to weak `ITA+ENG`/`MULTI` fallbacks when English is merely the default. When the user explicitly asks for Italian, French, or another non-English media language, candidates that actually advertise that language must outrank unknown-language rows even if those unknown rows have more seeders. Unknown-language rows may remain visible as fallbacks, but they must not be auto-queueable for explicit non-English requests.

A full-season TV request made before a full-season pack exists must not degrade into “nothing found” when partial episode-range packs or same-season explicit-language single episodes are already present in broad season searches. The TV pack workspace may include same-season single-episode rows as fallback coverage when they advertise the explicit requested language. This keeps the LLM candidate reviewer informed about realistic partial coverage such as `S01E01-06` packs plus `S01E07`/`S01E08` singles, without launching a long per-episode query storm or pretending that the unaired/unreleased tail of the season is available.

Verify with `scripts/round254_widows_bay_recall_ui_responsiveness_tests.py` after changing TV title matching, TV pack gates, explicit-language ranking, candidate payload sorting, or the `search_media_torrents` selection annotation policy.

## Round 256 — Release-Watch Queue Start and Blocked-Download Visibility

Release-watch auto-download is a two-step contract: the category/scheduler may
approve a watch for automatic discovery, but the queued torrent must still pass
through the generic download queue start gate.  That gate must honor the
category item envelope's conventional `auto_download` value before falling back
to the global automation setting.  A tracked TV show whose inspector checkbox is
enabled must not be held merely because the global legacy setting is false;
conversely, a per-item `auto_download: false` must keep background release-watch
rows in notify/search-only behavior.

Duplicate torrent rows are part of the same contract.  A release-watch retry may
find the same magnet or same category-owned import identity that was already
persisted as `queued`.  The duplicate path must not return early forever; if the
existing queued row's tracked item allows automation, it should be promoted for
normal queue processing.  Paused/stalled rows still require their existing
control policies and must not be force-started by duplicate discovery.

When automatic release-watch queueing fails after a candidate is found, users
need a visible explanation, not only an internal retry ledger.  Storage and
availability failures such as missing drives, unwritable paths, or critically low
disk should create a deduplicated `release_watch_queue_blocked` notification that
says the release was found but blocked.  The retry row remains retryable, but the
UI/bridge surface must not make this look like “new episode found” with no reason
for the missing download.

Verify with `scripts/round256_release_watch_autostart_tests.py` after changing
download queue start gates, duplicate magnet handling, release-watch retry
error handling, or per-item automation policy handoff.

## Download Cleanup Delete Semantics

Download lifecycle cleanup is intentionally different from an explicit
recoverable archive workflow. When a completed import moves or links a payload
into the library, when a cancelled download cleans partial files, or when a
racing/fallback torrent is discarded, the download-root source files are
permanently deleted after `SafePathResolver` verifies they are inside an allowed
root. They must not be moved into hidden in-place folders such as `.ljs-trash`,
because that leaves the UI reporting successful cleanup while disk usage grows
invisibly.

`SafePathResolver` still supports explicit quarantine for workflows that request
it deliberately, but generic download cleanup and confirmed library-file deletes
now pass an explicit permanent-delete policy.

## Frontend Performance and Browser Tab Lifecycle

The browser dashboard is a long-running control surface, not a short-lived page.
Frontend code must therefore treat tab visibility, active view state, and
reduced-motion preferences as first-class runtime constraints.

`FrontendPerformanceCoordinator` (`src/web/static/js/core/performanceCoordinator.js`)
owns the shared UI lifecycle signal. Components that poll APIs, render large DOM
lists, or run decorative animation loops should ask this coordinator whether the
browser tab is visible, whether their view is active, and whether low-power mode
is in effect. New components should prefer adaptive intervals or frame-scheduled
updates through this coordinator over raw `setInterval` loops.

Rules:

- Decorative animation such as ocean bubbles, slow background rotation, progress
  shimmer, avatar sway, and compass cursor tracking must stop in hidden tabs and
  under reduced-motion preferences.
- Inactive views should not keep painting heavy subtrees. The active view is the
  only view that should run high-frequency DOM work.
- Download telemetry may arrive frequently over WebSocket, but UI updates must be
  batched to a frame and patched in place when possible. Full download-card
  rebuilds are reserved for structural changes such as new/removed rows, file
  lists appearing, or status group changes.
- API polling should be visibility-aware and non-overlapping. A background tab may
  keep slow safety refreshes where useful, but it must not keep 5-second active
  dashboard polling alive indefinitely.
- Hidden/collapsed detail rows, such as expanded torrent file lists, must not be
  patched on every telemetry tick.
- Library/catalog and release-watch panels should coalesce in-flight loads and
  render into document fragments so large libraries do not cause repeated layout
  storms.

This is still a generic UI rule. Do not solve performance by adding category-
specific frontend branches; category-specific rendering belongs behind manifest
or detail payload contracts.

## Round 264 Background Automation, TV Candidate Proof, and Movie Collection Import Rules

Background automation remains category-owned. Core schedulers and download
services may execute searches, queues, and imports, but they must ask the owning
category whether unattended work is allowed for that item/unit. `background_discovery_allowed(...)`
and `release_watch_search_allowed(...)` are now separate hooks: a category may
allow notification-only release checks for some domains while refusing them for
high-false-positive domains such as TV.

TV release watching is strict opt-in. A TV item with `auto_download` missing,
`None`, or `False` must not create episode release-watch rows, perform
notification-only torrent searches, or queue downloads just because metadata says
episodes are missing. Watch-plan rebuilds should retire stale episode rows by
omitting them; due rows that survive from older builds are cancelled before any
provider/indexer call when the category says background search is disabled.

TV candidate validation must prove both series identity and language before
automatic queueing. Series aliases are matched only against the release-title
scope before TV unit markers such as `S01E06`; a show name appearing only in the
episode-title suffix is not a series match. Automatic TV queueing also fails
closed on language: visible non-preferred audio and unknown audio evidence both
require user approval unless the tracked preference is explicitly matched by the
release title.

Movie bundle imports must preserve collection identity. For downloads whose
payload contains multiple distinct primary movie files, the movie category maps
files under a stable collection folder and keeps each source movie filename. This
decision is based on file-list/payload structure, not release-title marketing
words. The download handler asks `ready_import_file_allowed(...)` before exposing
or moving completed payload files; the movie category allows video files as
primary movie payloads and skips covers/screenshots/JPGs instead of importing
them as standalone movies.

Verify with `scripts/round264_automation_language_collection_import_tests.py`
after changing TV watch plans, release-watch retry policy, TV torrent candidate
validation, movie collection detection, or download ready/completion import
handoffs.

## Round 265 — Payload-based movie collections and staged torrent file priority

Movie collection handling must be based on payload structure, not marketing words in a torrent or folder title. The movie category may treat a download as a collection only when file-list or persisted download metadata shows multiple primary video payloads with distinct parsed movie identities. Cached tracker file lists may provide this evidence before queueing; otherwise the decision is made after torrent metadata is available. Generic code must not detect movie collections by terms such as pack, collection, trilogy, or similar title markers.

For multi-file torrents, displayed per-file priority must match actual libtorrent file priority. After metadata parsing, the download lifecycle applies persisted `DownloadFileInfo.priority` values to the live handle. Distinct positive file priorities are staged: only the highest unfinished priority band is enabled, and lower bands remain priority 0 until earlier files complete. Equal positive priorities remain parallel. This makes file priority a real progressive-availability control instead of a cosmetic UI field.

## Round 267 — TV consent, stale RSS safety, and scoped TV candidate selection

TV background automation is explicit-checkbox only. The TV category must not infer
consent from manual downloads, active-airing metadata, global automation, stale
release-watch requirements, RSS events, or legacy runtime rows. Before any
unattended scheduler, release-watch, RSS, discovery, queue-creation, or queue-start
path runs, generic orchestration asks the owning category to reconcile runtime
settings with the persisted category item row. For TV, literal JSON/Python
`true` must be confirmed by the persisted category item payload; nested visible
configuration fields such as `properties.auto_download` are authoritative off
signals when false, null, string, integer, or missing.

Download row creation is also a policy boundary. Generic downloader code may
create/start rows for explicit user/manual approvals, but background rows must be
accepted by the owning category policy first. If a category declares a
`queued_background_start_allowed(...)` policy and no matching tracked item is
found, the safe generic behavior is to deny unattended queue creation/start rather
than inherit global automation.

Stale RSS state must fail closed. Watch-policy rebuilds must update the RSS
monitor from the newly computed plans and must not leave old feed URLs active
because one item crashed while building a plan or URL. TV release events that
arrive from stale feeds must re-check category background-search permission before
creating release-watch rows, notifications, or downloads.

TV torrent selection has a deterministic structural guard before LLM ranking.
For exact SxxEyy requests, TV keeps exact episode releases and verified
season/series bundles that can contain that episode; wrong episodes and releases
whose series title does not match before the TV unit marker are rejected before
the model sees them. For broad TV show searches without a concrete unit, verified
season/series bundles are preferred over random individual episode rows when such
bundles are available. This remains category-owned: core search and LLM ranking
pass candidates through category hooks and do not interpret seasons, episodes, or
TV pack semantics themselves.

Verify with `scripts/round267_tv_background_consent_and_search_scope_tests.py`
after changing TV consent reconciliation, RSS/watch-policy handling, downloader
background gates, or TV torrent candidate filtering.

## Round 268 — Category-owned LLM skill guidance and generic prompt hygiene

Category-specific release-name knowledge belongs in category-owned prompt and
selection guidance, not in generic AI prompt builders. Each category may provide a
prompt file under `src/core/categories/prompts/` and a
`build_torrent_selection_guidance()` implementation that teaches the LLM how to
interpret that category's release names, bundle/range notation, language tags,
unit coverage, and fallback strategy. Main assistant prompts and torrent
candidate adjudication must inject those category skills when a category is
active.

Hardcoded TV/movie torrent vocabulary must not live in generic LLM prompt code.
Generic download/adjudication prompts may tell the model to preserve titles,
respect tool/category annotations, use candidate IDs, handle quality-choice
policies, and treat seeders as evidence. They must delegate examples such as TV
`SxxEyy`/season-range notation, compressed TV language tags, or movie collection
payload behavior to the owning category. This prevents a fix for one category
from leaking incorrect semantics into another category and keeps the LLM as the
semantic reviewer inside category-provided evidence rather than a pile of global
special cases.

TV's category skill describes common exact-episode formats, season/range-pack
formats, title-prefix validation before episode markers, compressed language
codes, multi-audio evidence, and manual-vs-background automation separation.
Movie's category skill describes title/year identity, release-quality rejection,
compressed audio-language evidence, and evidence-based multi-film collection
handling.

Verify with `scripts/round268_category_llm_skill_prompt_tests.py` after changing
category prompt files, `build_prompt_guidance(...)`, torrent-selection guidance,
`DownloadCandidateAdjudicator`, or generic download task guidance.

## Round 269 — Definition-backed category skill files and category-aware torrent quality

Definition-backed categories must get the same LLM skill-file mechanism as
handwritten categories. A concrete YAML-backed category may declare
`prompt_file`, and otherwise LJS loads `src/core/categories/prompts/<category_id>.md`
when it exists. These prompt files are category-owned teaching material: common
release-name shapes, file formats, edition/identity fields, bundle semantics,
language relevance, safe sidecars, and rejection boundaries. Generic prompt code
must not copy those examples into global AI modules.

Torrent selection must also remain category-aware. Video quality guides such as
REMUX/WEB-DL/BluRay, resolution ladders, HDR/DV, and codec ranking apply only to
categories that opt into the global video quality profile. Definition-backed
non-video categories such as Music, Ebooks, and Audiobooks instead use their own
format/edition/narrator/track/chapter guidance plus seed availability and safe
payload checks. Generic torrent prompts may ask the model to follow the owning
category skill, use candidate IDs, respect seeders, and fail closed on unclear
identity/language/format evidence; they must not smuggle movie/TV quality rules
into unrelated domains.

Research-backed category skill updates currently cover:

- TV: SxxEyy/1x02 variants, multi-episode files, season/range packs, complete-series containers, title-prefix validation, compressed language tags, subtitles vs audio, and manual-vs-background automation separation.
- Movie: title/year identity, remakes/local titles, edition/cut tags, source/quality/HDR/audio/video tags, language evidence, auxiliary files, and payload-structure collection handling.
- Music: artist/album/release-group vs exact-release identity, remaster/edition/medium/catalog facets, lossless/lossy/sample-rate tags, complete album/discography handling, track-order/sidecar preservation, and language irrelevance unless explicit.
- Ebooks: author/title/series/translator/ISBN/edition identity, EPUB/AZW3/MOBI/PDF/DJVU/CBZ/CBR differences, retail/scan/OCR/omnibus terms, multi-format bundles, and language/translation handling.
- Audiobooks: author/title/narrator identity, abridged/unabridged status, M4B/M4A/MP3 folder shapes, chaptering, duration/language, and source-preserving conversion safety.

Verify with `scripts/round269_category_torrent_skill_expansion_tests.py` after
changing definition-backed category prompt loading, category prompt files,
`TorrentSelectionService.build_quality_reference(...)`, or generic torrent
selection prompt wording.

## Round 270 — Prompt-file skills are the torrent-selection source of truth

Category prompt files under `src/core/categories/prompts/` are now the canonical
place for category LLM teaching material.  Main assistant prompts may include the
full file, while torrent candidate review must use the search/download-relevant
sections extracted from those same files: release-name skills, language/format
skills, collection or bundle semantics, import safety, and automation safety.
Concrete categories must not duplicate long release examples inside Python
`build_torrent_selection_guidance()` methods.  Duplicating examples such as TV
range-pack notation or movie collection evidence in code lets the main prompt and
torrent reviewer drift apart.  Category code should call
`prompt_file_torrent_skill()` or otherwise derive guidance from the prompt file,
not copy/paste a second independent skill block.

Generic torrent prompts remain category-neutral.  They may say to preserve the
user's requested title/unit/language, obey the owning category guidance, respect
candidate IDs and category annotations, and prefer healthier seeders among
equivalent candidates.  They must not assume that every language-relevant
category is audiovisual: ebooks and audiobooks use language differently from TV
or movies, and music normally ignores global spoken-language defaults unless the
user explicitly asks for language-specific content.

`DownloadCandidateAdjudicator` keeps enough category-guidance budget for the full
search-relevant TV/movie/music/book/audiobook skill to reach the LLM.  If a
future prompt file grows too large, extract tighter sections in the category
prompt file rather than truncating away tail safety rules.

Verify with `scripts/round270_category_skill_source_of_truth_tests.py` after
changing category prompt files, `prompt_file_torrent_skill()`, concrete
`build_torrent_selection_guidance()` methods, torrent prompt language rules, or
candidate-adjudicator guidance budgets.

## Round 271 — Prompt architecture drift cleanup and TV unattended-unit hard stop

Round 271 closes two remaining drift paths discovered during a broad prompt and
architecture review.

First, unattended TV update paths must use the same category watch-plan contract
as release-watch retry and scheduled checks.  TV code must not synthesize a new
search unit from local progress such as `last local episode + one`.  Local
progress is evidence for canonical state and display, not permission to invent a
provider/release unit.  When unattended TV background work is due, the category
must ask `build_watch_plan(...)` for concrete release-watch units and run
discovery only for a unit the plan exposes and only when the per-show TV
automation policy allows background discovery.  If the watch plan has no unit,
the correct behavior is no search.

Second, generic DOWNLOAD recovery must remain category-neutral.  If the live LLM
fails to emit a tool call, fallback code may force one conservative
`search_media_torrents` call with literal user text and the active category, but
it must not parse languages, seasons, episodes, packs, editions, or formats from
English/Italian regexes.  Those meanings belong to category context, prompt-file
skills, and the LLM candidate reviewer.  This keeps fallback recovery from
becoming a second hidden category parser that drifts away from category-owned
skills.

Downloader policy helpers must read tracked items from both the modern
`ItemList.items` container and legacy plain-list test/runtime settings.  Missing
a matched tracked item for a category that owns queue policy is a safety signal:
for TV and other strict categories, background queue creation/start must fail
closed rather than silently bypassing item policy because a settings container
shape differed.

Concrete categories should rely on the base prompt-file injection unless they are
adding real behavior.  No-op overrides of `build_prompt_guidance(...)` are
unnecessary maintenance points and should be removed.  Category prompt files and
`prompt_file_torrent_skill()` remain the source of truth for category LLM skills.

Verify with `scripts/round271_prompt_architecture_drift_cleanup_tests.py` after
changing TV update workflows, generic DOWNLOAD recovery, tracked-item iteration,
category prompt injection, or prompt architecture audits.

## Round 272 — Search-scope and follow-up parser drift cleanup

Search scope is a category-neutral phase hint, not a hidden TV parser. Generic
AI, scheduling, and plan-execution code must normalize scope labels through
`SearchScopePolicy` (`src/core/categories/search_scope.py`) instead of carrying
legacy literals such as `season_pack_preferred` or `pack_only` in multiple
modules. The policy may preserve legacy aliases at the boundary, but downstream
code should reason only about canonical scopes: `default`, `bundle_preferred`,
`bundle_only`, and `individual_units_only`. Categories then interpret those
canonical scopes in their own domain: TV may treat a bundle as a season pack,
music as an album/discography, ebooks as a series/omnibus, and so on.

The plan coordinator must not derive bundle/pack scope from natural-language
phrases such as “latest season”, “whole collection”, or “solo pacchetto”. That
interpretation belongs to the LLM, category prompt-file skills, and structured
tool arguments. The coordinator may preserve a structured `search_scope` already
emitted by the planner/tool arguments and normalize legacy aliases, but it must
not become a second keyword parser for category semantics.

Pending torrent candidate context must also avoid natural-language follow-up
phrase lists. `DownloadContextPolicy` may keep candidate context for stable
handles such as `candidate_id`, `result_set_id`, raw candidate hashes, `#1`, or
short quality refinements like `720p`; it must not special-case “yes”, “ok”,
“first one”, “queue it”, or language-specific equivalents. The user-facing
candidate picker and final assistant responses should encourage stable
candidate IDs/handles, because those are portable across languages and do not
let stale result sets satisfy fresh acquisition requests.

Verify with `scripts/round272_search_scope_and_followup_drift_tests.py` after
changing search-scope normalization, plan download normalization,
`DownloadContextPolicy`, candidate pending-action context, or torrent-result next
actions.

## Round 273 — Category language/quality drift cleanup

Torrent language-token handling is shared plumbing, not a place for each agent,
TV helper, and scheduling helper to grow separate alias tables. Common release
language aliases and bounded title-token matching live in
`LanguageTokenPolicy` (`src/core/categories/language.py`). Generic AI/tool code
may use that policy only as normalization support; the owning category still
decides whether language is relevant and whether a token means audio, subtitle,
translation, ebook language, vocal language, or something else.

Generic torrent selection and batch recommendation scoring must respect category
hooks before applying media preferences. If a category reports
`language_is_search_relevant() == False`, generic tools must not reward or block
candidates because a title contains `ITA`, `ENG`, `dual`, or similar release
language tokens. If a category reports `uses_global_quality_profile() == False`,
generic tools must not reward video resolution/codec fields or run the global
video quality-choice policy for that category. Non-video categories should rank
by category-owned identity/format evidence, safe payloads, plausible size, and
seeder availability unless their own category guidance/profile opts into a
specific quality model.

Base category torrent guidance must stay category-neutral. It should reject
cross-category and unsafe executable/software payloads, but it must not globally
ban adult-rated media, archives, multi-file payloads, books, audio, games, or
sidecars. Those are category/user-target questions. Concrete categories and
prompt-file skills remain the source of truth for whether such payload shapes are
valid.

Source comments and docs inside `src/` should avoid incident-specific show/movie
names from past debugging sessions. Regression tests may use synthetic fixture
names, but production source should describe the generic failure class instead of
leaving historical sample names that invite future per-case fixes.

Verify with `scripts/round273_category_language_quality_drift_tests.py` after
changing language-token helpers, torrent scoring/ranking, search-media tool
schemas, base torrent guidance, or source-level drift audits.

## Round 275 — Structured Planner Signal and Definition-Backed Local Model Cleanup

Generic planner repair must not become a second natural-language parser for
category semantics. `PlanCoordinator` may preserve structured planner/tool
fields such as `search_scope`, `requested_unit_scope`, `requested_units`, or
`multi_unit_scope`, but it must not scan user prose for category unit words such
as episodes, seasons, tracks, volumes, or language-specific equivalents in order
to rewrite download searches. If a weak planner collapses a multi-unit request
into a guessed single unit, the fix belongs in LLM/category prompt context and
structured tool arguments, not in hidden keyword lists inside coordinator core.

Definition-backed local object reconstruction is owned by
`LocalObjectReconstructor` and focused builder collaborators in
`src/core/categories/local_object_reconstruction.py`. Scanner/core code still
emits neutral file observations; the definition-backed category asks this
category-owned collaborator to shape Music, Ebooks, and Audiobooks into local
object evidence and unit rows. Do not re-add module-level reconstruction
functions or duplicate local file heuristics in scanner, scheduler, or web code.

Definition-backed provider/source strategy must be declarative category data.
For example, Music can prefer Soulseek for normal album/track searches and
prefer torrents for large discography-style requests by declaring a
`source_strategy` section in its category definition. `DefinitionBackedCategory`
reads that section generically; it must not branch on concrete category ids to
hard-code provider preferences.

Verify with `scripts/round275_structural_cleanup_drift_tests.py` after changing
planner download normalization, definition-backed local scan reconstruction, or
Soulseek/torrent source-preference strategy for definition-backed categories.

## Round 276 — Category-Owned Search Workspace Annotation Cleanup

Search workspace annotations are category facts, not scheduler facts. The shared
scheduler may assemble a response payload, preserve structured tool arguments,
and call category hooks, but it must not parse category release-name notation to
infer counts, coverage, disc ranges, episode ranges, edition spans, or similar
meaning.  For TV specifically, fields such as `expected_episode_count`,
`requested_season_coverage`, and coverage notes must be produced by the TV
category from TV-owned query labels and bundle parsing.  The scheduler only
carries those opaque facts into the candidate workspace for the LLM.

Transitional structured tool arguments such as `season` and `episode` may still
exist in public schemas for compatibility, but generic services must not format
them as TV labels.  The scheduler now asks `agent_unit_label_from_args(...)` for
an opaque label before running fallback searches, Soulseek companion searches,
and candidate descriptor hooks.  Categories that do not accept those arguments
return no label rather than inheriting `Season N` or `SxxEyy` formatting.

Definition-backed local object reconstruction should remain builder-owned after
Round 275. Compact local counters such as music track counts, audiobook chapter
counts, or ebook format counts are now emitted by each local-object builder via
`enrich_properties(...)`; `LocalObjectReconstructor` coordinates builders but no
longer branches on concrete category ids to attach those counters.
Definition-backed scan progress should also key off the reconstructed local
model evidence itself, not off a hardcoded set of category ids.

Verify with `scripts/round276_category_response_annotation_cleanup_tests.py`
after changing scheduler search responses, category agent-search hooks,
definition-backed local object reconstruction, or candidate workspace payloads.

## Round 277 — Provider registry, audio conversion policy, and bundle workspace cleanup

Metadata provider lookup for definition-backed categories is provider-keyed, not category-id-keyed. `MetadataProviderRegistry` reads the active category definition's `metadata.providers` map, applies provider defaults from a capability registry, and applies per-category kwargs such as Internet Archive `mediatype` or Apple Search `media` from YAML. `CategoryMetadataResolver` depends on that registry collaborator and must not recreate procedural branches such as "if Music, use MusicBrainz/Discogs" or "if Ebooks/Audiobooks, use book adapters." Provider adapter families remain explicit implementation classes, but provider membership and provider call details belong to category definitions.

Audio conversion policy is now profile-driven. `AudioConversionPolicy` interprets `download_profile` fields such as `preferred_lossless_format`, `preferred_audio_format`, `preferred_lossy_format`, and `auto_convert_lossless_to_preferred`; `AudioConversionService` owns FFmpeg execution and safe paths only. Do not add concrete category-id branches for Music or Audiobooks to select M4A/M4B sidecars. A future audio-capable category should opt into the same behavior by declaring the appropriate profile fields.

Search workspace quality-choice policy now consumes the generic `requested_bundle_coverage` annotation first. TV may still expose compatibility facts such as `requested_season_coverage` and `expected_episode_count`, but generic candidate/quality code should reason in terms of category-owned bundles, containers, and logical units instead of TV-specific examples or reason names. Category hooks may add both generic and domain-specific annotations when the UI or older tests still need the legacy fields.

## Round 278 — Notification-only unmatched-search retry cleanup

Unmatched-search retry is a notification mechanism, not an acquisition grant. If
a user-initiated torrent/Soulseek search returns zero candidates, the assistant
may schedule one deduplicated follow-up search because peer-to-peer availability
can change, but that scheduled task must never queue, start, or auto-download a
candidate by itself. The retry prompt must surface stable candidate IDs, source,
size, seeders, and category-owned language/quality warnings for a later explicit
LLM/user queue decision.

The retry policy lives in `src/ai/tools/search_retry.py` as
`UnmatchedSearchRetryScheduler`. Individual list/schedule tools must not carry
copies of missed-search retry behavior, and future retry variants must keep the
same notification-only contract unless a category-owned workflow and explicit
user consent provide a separate automation policy.

Verify with `scripts/round278_retry_policy_cleanup_tests.py` after changing
unmatched-search retry scheduling, `search_media_torrents`, prompt-scheduler
condition checks, or deferred search retry wording.

## Round 279 — Search workspace extraction and generic bundle cleanup

`search_media_torrents` must remain an agent boundary, not a policy dumping ground. Candidate row formatting, batch recommendation scoring, quality-choice annotation, next-action construction, and audit logging now live in `src/ai/tools/search_workspace.py` as focused collaborators. `src/ai/tools/scheduling.py` wires the tool and delegates to those collaborators; it must not grow new private workspace engines or module-level helper functions.

Generic search workspace code may read category-published facts such as `unit_descriptor`, `requested_bundle_coverage`, `selection_warnings`, `selection_blockers`, language relevance hooks, and global-quality-profile hooks. It must not recover TV, album, ebook, or other category semantics by inspecting legacy `season`/`episode` fields, descriptor coordinates, or title words. If a category needs a bundle coverage fact or logical unit label, add or improve the category hook that publishes that fact.

Quality-choice grouping is descriptor-first. When category descriptors are present, the workspace groups candidates by category-owned stable unit keys. When descriptors are missing, the quality policy may compare alternatives inside the current result set but must not invent a logical unit identity from generic fields. This keeps interactive quality alternatives useful without reintroducing hidden TV parsers.

Verify with `scripts/round279_search_workspace_extraction_tests.py` after changing `search_media_torrents`, candidate workspace payloads, quality-choice/batch recommendation policy, or scheduler search response assembly.

## Round 280 — Metadata helper, media probe, and prompt-example drift cleanup

Generic metadata tools must not duplicate agent argument parsing helpers across
research modules. `MetadataLookupArgumentNormalizer` in
`src/ai/tools/metadata_lookup_support.py` is now the single class-owned boundary
for title argument resolution and safe integer coercion used by both
`metadata_lookup` and the legacy IMDb details tool. Future metadata/research
tools should reuse or extend that collaborator instead of reintroducing
module-level `_resolve_title` / `_safe_int` helpers in `src/ai/tools`.

`src/core/categories/media_probe.py` keeps its backwards-compatible public
function API for scanners and older regression scripts, but stream parsing,
language normalization, resolution labeling, cache freshness, and serialized
ffprobe execution now live on named collaborators: `MediaProbeValueParser`,
`MediaProbeLanguageNormalizer`, `MediaProbeResolution`, and
`MediaProbeService`. New media-probe behavior should be added to those classes,
not to module-level helper state. The public wrappers should remain thin
compatibility shims.

Active prompt/config examples must not teach from troubleshooting titles or
single-run fixtures. TV search examples in `config/category-definitions/tv.yaml`
now use neutral `Series Title ...` patterns, while TV-specific release-name
teaching remains in the TV-owned YAML/prompt files. Generic router prompt wording
uses bundle/range terminology rather than TV-specific season-pack wording.

Verify with `scripts/round280_metadata_prompt_drift_tests.py` after changing
metadata research tools, media probing, TV category definition prompt examples,
or the generic intent-router download follow-up wording.

## Round 283 — Durable Commands, Operational Ledger, and State Authority

All state-changing flows are converging on one durable command contract. A
command carries `command_id`, `correlation_id`, optional `idempotency_key`,
actor/source identity, arguments, and creation time. Execution returns an
authoritative receipt with status, timestamps, result data, and error details.
The receipt—not model prose, search metadata, or historical state—is the source
of truth for what the application actually did.

`ActionGateway` persists three complementary command records:

- `action_command_claims`: one atomic idempotency ownership record per scoped operation;
- `action_command_receipts`: one immutable durable final receipt per command;
- `operational_events`: an append-only command lifecycle timeline.

The existing `action_events` table remains as a compatibility/read-model
projection for memory and older consumers. New operational debugging and
reconciliation must use command receipts and the ledger. Completed claims are
not silently recycled: transport retries replay the immutable original receipt,
while a later intentional operation must receive a new semantic idempotency key.

The first production vertical slice is `queue_download` from the assistant tool
surface. It executes through the same command contract and returns command
receipt metadata alongside the existing queue receipt. Other mutations should
be migrated incrementally without introducing parallel command frameworks.

State questions must use the authority declared by `StateAuthorityRegistry`:

| Fact | Authority |
|---|---|
| Logical unit present locally | Canonical library object |
| Transfer active now | Live downloader projection, with durable active queue fallback |
| Transfer attempted previously | Download history |
| Unattended action permitted | Reconciled item policy/category watch plan |
| Candidate selected by user/model | Pending result set |
| Mutation actually happened | Command receipt/operational ledger |
| Provider says a unit exists | Timestamped provider snapshot |

Typed `FactVerdict` values distinguish verified, unknown, and stale evidence.
Consumers must not substitute a non-authoritative source merely because the
proper authority is unavailable. The registry is currently a declared contract
and is enforced in selected flows; it is not yet a universal runtime gate over
every historical reader. New code must consult it, and existing flows should be
migrated incrementally rather than claiming global enforcement prematurely.

`DownloadStateReconciler` is the first read-only recovery diagnostic. It finds
terminal completed rows whose category-owned canonical target is verified
absent. It reports a previewable repair action but does not mutate history yet.
Repair execution will be added through the same command/receipt contract.


## Round 284 — Command integrity, truthful uncertainty, and security capabilities

The durable command foundation must reserve idempotency **before** invoking a
mutation. Claims are scoped by action name, source, user, session, and semantic
idempotency key. The complete normalized argument set is hashed into a request
fingerprint; reusing the same key with different arguments is a conflict, not a
replay. A concurrent duplicate either waits briefly for the owner's immutable
receipt or returns an in-progress result. An expired running lease without a
receipt becomes `uncertain` and blocks automatic re-execution.

A receipt insert and claim finalization happen in one database transaction.
Receipts are append-only and must never use replacement semantics. If the
underlying mutation returns but the receipt cannot be persisted, the outward
result is `ok=false`, `status=uncertain`, with wording that the mutation may have
executed and current state must be verified before retrying. Agent final-response
guards must reject queue success prose unless the queue identifiers are present
**and** `command_receipt.receipt_persisted` is true.

Operational arguments, results, and free-text errors are sanitized and bounded
before durable persistence. Magnet tracker URLs, passkeys, tokens, credentials,
and URL query strings must not enter command receipts, operational events,
legacy action projections, or support bundles. Browser action endpoints own
actor/source/user/session and durable IDs server-side; client payloads cannot
impersonate scheduler or system activity.

The first queue slice also has an execution-level start reservation. Database
deduplication alone is insufficient because two callers could start the same
magnet before either row becomes visible. `TorrentStartCoordinator` reserves one
engine-start attempt per download identity and releases it on failure or terminal
cleanup.

Privileged process and filesystem operations are capability-owned. Autostart,
managed slskd, and managed SearXNG code must execute commands through
`CommandPolicy` and perform deletion/copy/rollback through `SafePathResolver`
with explicit managed roots. Direct subprocess calls or destructive path
operations in those managers are architecture violations, not convenience
shortcuts.


Background asynchronous work follows an ownership-before-construction rule.
`TaskSupervisor` accepts coroutine factories for restartable and one-shot work and
creates the coroutine only inside the task it owns. Event broadcasts and download
statistics broadcasts must pass factories rather than pre-created coroutine
objects. This prevents immediate shutdown, rejected scheduling, or mocked
supervisors from leaking unawaited coroutines and makes cancellation ownership
explicit.

Current limitation: command leases do not yet have heartbeat renewal. Do not
migrate long-running mutations whose normal execution can exceed the lease until
lease ownership can be renewed safely. The queue command is the mature semantic
idempotency slice; scheduler, import, repair, configuration, and suggestion
mutations must move through the same gateway incrementally.

## Round 285 — Evidence-based category identity and localized TV recall

Unknown media titles must resolve to a concrete owning category before provider
discovery, but natural-language vocabulary is never category truth. Users may
speak any language, titles can exist as several media forms, and the model may
supply an incorrect or omitted category argument. The resolution order is:

1. exact tracked-item or canonical-library identity;
2. weak model/router semantic evidence selects one category-owned verifier to run first;
3. a strong exact result from that verifier resolves immediately;
4. when the selected verifier is weak or empty, its bounded category-owned web fallback runs;
5. only then are remaining installed categories compared for ambiguity;
6. a unique strong category match proceeds, otherwise a structured clarification ends the turn.

The generic ``CategoryIdentityResolver`` must not know that a TMDB result is TV,
a movie, a book, music, or any future domain. ``MediaCategory.identify_agent_item``
is the extension point: TV owns TMDB-TV/TVMaze probing, Movies own TMDB-movie
probing, and definition-backed categories own their declared metadata resolvers.
Evidence returned for another category is discarded. Hints select a verifier but
never authorize search. Strong exact evidence from the selected verifier prevents
unrelated category probes; only weak/absent evidence fans out to a bounded
cross-category comparison. Absence, weak evidence, or plausible cross-category matches produce
``category_resolution_required``/``category_ambiguous`` with a clarification
question. A model argument, ``ToolExecutionContext.category_id``, singular/plural
router term, or chat language can never authorize search by itself. When metadata
is unavailable the response may recommend web research, but LJS asks rather than
silently falling back to abstract ``media``.

Ordinary DOWNLOAD turns expose the category-owned ``search_media_torrents``
workspace and not raw torrent or raw Soulseek discovery tools. Raw provider
search may remain registered for explicit internal/direct diagnostics, but it
is not an LLM escape hatch around category metadata, unit discovery, language
evidence, pack interpretation, result caching, and queue validation.

Multilingual unit intent is model-owned and structured. ``search_media_torrents``
accepts the category-neutral ``unit_scope`` values ``available_units``,
``missing_units``, and ``all_units``. Categories interpret those values in their
own domain; generic code and TV do not use English/Italian phrase lists as an
authority. Structured constraints and the literal request text must survive the
whole tool → scheduler → category handoff; an intermediate normalizer may not
drop them and reconstruct intent from English words. TV enquiry returns
recommended search arguments when provider-backed missing aired episodes are
known. A broad TV title with no season, episode, bundle scope, or structured
unit scope fails closed and asks which units are wanted instead of running a
broad title query that can select an unrelated or future episode.

For non-English TV requests, an untagged or wrong-language season pack is not
evidence that localized releases do not exist. The TV-owned query ladder must
continue through exact aired episode/unit queries, including configured language
aliases such as ``ITA`` and ``Italian``, until useful candidates are found or the
known aired frontier is exhausted. Provider catalogue totals and future
announced seasons are not download frontiers: dated future seasons are excluded,
and undated episode counts alone do not invent searchable units.

Pack evidence remains structurally subordinate to direct unit evidence. A
single episode returned by a season/range query is not a season pack and cannot
stop a multi-unit fallback. During an exact episode ladder, direct requested-
language releases and bundles containing the episode are tracked separately.
When a direct requested-language release exists, an untagged or wrong-language
bundle is suppressed rather than presented as equivalent; a requested-language
bundle may remain, and bundle fallback remains available when no direct release
exists.

Both LLM ranking stages are advisory after provider/category filtering and are
hard-bounded. Interactive TV search disables the legacy pipeline ranker and uses
one final candidate review; compatibility paths time out to deterministic
category-filtered candidates. A slow task model must not hold an interactive
search turn for repeated multi-minute retry windows.

Verify with ``scripts/round285_silo_tv_category_recall_tests.py`` after changing
category identity hooks, enquiry/search resolution, abstract-media fallbacks,
structured unit scope, TV localized pack/episode ladders, aired-season selection,
DOWNLOAD tool policy, or candidate-adjudication timeout behavior.


## Round 286 — Observable and cancellable LLM activity

Every task-aware model call crosses `TaskLLMClient` and is registered with the
bounded `LLMActivityMonitor`. The monitor links calls to the originating chat
session/turn and records task, provider, model, streaming state, start/end time,
duration, retry attempts, error/cancellation state, generation controls, exact
messages, exact function schemas, payload character counts, conservative token
estimates, and provider-reported prompt/completion usage when available. The web
API exposes a cheap summary endpoint for polling and a per-call detail endpoint
for exact context inspection. Exact context remains local, authenticated, and
bounded in memory.

Context budgeting includes serialized tool schemas. Task-specific values are
**soft assembly targets**, not replacements for the endpoint/user-selected
context window: routing/progress calls target 8k, ordinary
chat/search/download/tool calls target 32k, planning/ranking/final calls target
24k, and web research targets 48k. The endpoint or explicit user cap remains the
hard authority, with the configured safety percentage applied once. History and
optional packets are compacted toward the soft target; an irreducible prompt may
exceed it and continue while still below the hard ceiling. The runtime logs an
`LLM_PAYLOAD_BUDGET` line for the payload actually sent, distinguishing selected
window, usable hard ceiling, soft target, estimated messages, schemas, output
reserve, and total. Only a payload exceeding the usable hard ceiling fails before
provider I/O. `llm_context.log` stores exact message and schema JSON with measured
character counts and labelled estimates.

The browser chat transport permits one active turn per WebSocket. The input is
locked while a turn runs; the Send control becomes a visible Stop control. A
second message receives a typed `busy` response. Stop cancels the server-side
assistant task, propagates cancellation through provider HTTP/stream awaits, and
returns a typed `cancelled` event. Disconnect also cancels the owned turn. Bridge
code must not fake cancellation by merely hiding UI state.

The Helm exposes LLM state at a glance (idle/running/failed, duration, and context
size) and a detail drawer with recent calls, attempts/retries, latency, provider
usage, exact messages, and exact tool schemas. Header status text is a single
bounded flex row with ellipsis; intermediate widths reduce avatar/navigation
footprint before the existing compact breakpoint so status labels cannot stack
outside the header.

Category identity now treats season/episode/latest-season semantics as verifier
selection evidence in any language. Once TV metadata strongly confirms a literal
title, unrelated ebook/audiobook probes do not manufacture ambiguity. If TV
metadata is unavailable or empty, only TV runs a bounded public-web identity
fallback; it requires a trusted TV/reference host or independent corroboration.
Unresolved and ambiguous identity packets both terminate the current agent loop
with their clarification question rather than triggering repeated model calls.
Definition-backed book metadata adapters also fail soft under legacy private
config drift: Internet Archive recovers a category-owned media type when provider
kwargs are missing, and LibriVox uses its canonical trailing-slash feed endpoint.

Verify with `tests/test_round286_llm_observability_cancel_and_tv_identity.py`
after changing LLM provider boundaries, context budgeting, chat WebSockets,
activity UI/API, responsive header rules, or category identity fallback.


## Round 287 — Context authority, soft-target resilience, and post-turn latency

Round 286 incorrectly implemented the task-specific context target as a hard
replacement for the configured/provider context window and then applied the
context safety percentage to that replacement. On a provider-confirmed 128k
model, DOWNLOAD became a 32,768-token hard cap and then a 27,852-token usable
cap. The incident payload was estimated at 28,700 tokens, so a basic request was
rejected before provider I/O even though the real configured window had ample
space.

The corrected authority chain is:

1. provider-reported/model metadata establishes the model maximum;
2. an explicit user cap may narrow it, while a null cap means endpoint maximum;
3. `context_budget_percent` applies once to produce the usable hard ceiling;
4. task-specific values are soft assembly targets beneath that hard ceiling;
5. output reserve and serialized tool schemas are measured explicitly;
6. optional history/tool results are compressed toward the target;
7. an irreducible payload may spill above the target but must remain below the
   usable hard ceiling;
8. only the usable hard ceiling can block provider I/O.

For the logged NVIDIA NIM configuration this means: selected/model window
128,000; usable hard ceiling 108,800 at 85%; ordinary DOWNLOAD target 32,768;
target prompt space 31,744 after a 1,024 output reserve. The target is not
multiplied by 85% a second time.

Ordinary DOWNLOAD tool exposure is also bounded at the policy source. It keeps
one generic control surface and one category-owned discovery chain:
`list_downloads`, `manage_downloads`, `set_download_priority`,
`get_storage_status`, `inspect_torrent_candidate`, `enquire_about_media`,
`metadata_lookup`, `search_media_torrents`, `queue_download`, and
`enqueue_soulseek_download`. Alias controls, broad library/configuration helpers,
tracking/watch schemas, and unrelated Soulseek administration are not serialized
into every basic DOWNLOAD call. The full public-web handbook is injected only
for SEARCH, because ordinary DOWNLOAD does not expose that execution surface.

The activity inspector displays selected/model window, usable hard ceiling,
interactive target, measured prompt/total, cap source, and whether a call stayed
within target, spilled safely above target, or was rejected at the hard ceiling.
Pre-provider hard-budget failures are recorded as normal failed activity entries
with exact local messages/tool schemas.

Routing provider calls use two observable attempts with a 90-second timeout
per attempt. The chat publishes an early routing-progress state after five seconds,
so tolerating a slower reasoning-capable provider does not make the UI silent. NVIDIA NIM and the generic provider path retry only concrete timeouts, transport
failures, rate limits, or explicit transient HTTP status codes; generic words such
as `API` or `HTTP` do not make an invalid request retryable. Post-turn taste
extraction is dispatched through `TaskSupervisor.spawn_one_shot` in production,
so it cannot keep the WebSocket turn busy or leave Send/Stop locked after the
visible response has completed. Routine DOWNLOAD engagement is already recorded
by the behavior recorder and therefore does not launch a second taste-extraction
LLM call unless the user explicitly expresses preference evidence.

Verify with `pytest -q tests/test_round287_context_budget_resilience.py` and
`python scripts/round287_context_budget_resilience_tests.py` after changing
context-window resolution, tool-schema accounting, DOWNLOAD tool policy,
activity telemetry, provider retry envelopes, or post-turn taste ingestion.


## Round 288 — Reasoning-safe routing and authoritative live diagnostics

The Round 287 live incident proved that the intent router was not failing because
the user request was ambiguous. Its NVIDIA NIM call was terminated by an
aggressive 15-second timeout, and the direct provider adapter had discarded the
router's per-call generation settings. The fallback then mislabeled a provider
failure as `CLARIFY` with fabricated confidence `1.00`, preventing category
identity, metadata, search, and download code from running.

Routing remains a small semantic classification stage used to select a compact
intent-owned tool surface, but its execution contract is now provider-aware:

1. `IntentRouter` requests a narrow label response and does not set artificial
   `max_tokens` or temperature values. Task/user generation configuration remains
   authoritative.
2. `LLMGenerationPolicy` merges task/user and per-call generation settings once.
3. LiteLLM and direct NVIDIA NIM adapters consume that same effective contract;
   direct adapters filter only fields the endpoint does not support.
4. GPT-OSS routing receives its documented low-reasoning instruction rather than
   an output-token starvation trick.
5. `LLMCallPolicy` separates retry/timeout transport controls from generation
   settings. Routing currently permits two 90-second attempts.
6. `ChatSessionRunner` publishes a visible classification-progress message after
   five seconds while the provider call continues.
7. Provider/circuit failure returns confidence zero and an explicit infrastructure
   failure status. It is never rewritten as semantic certainty or user ambiguity.

`LLMActivityMonitor` is the sole authority for live call state. It records exact
messages/tool schemas in bounded local history and emits compact safe events for
each attempt timeout/failure, retry start, terminal failure, context rejection,
rate limit, authentication error, and cancellation. `LLMActivityBroadcaster`
forwards those events through `ShipEventBus`; the browser's `LLMProblemCards`
subscribes to that stream. Bounded activity snapshots reconcile any events missed
during a temporary WebSocket interruption using stable call/attempt keys, without
duplicating cards already delivered live. Each attempt remains individually
visible, and clicking a card opens the related call in `LLMActivityPanel`.

The former side drawer is now a dedicated full-screen LLM Diagnostics workspace
with five authenticated views:

- Activity: call history, status, latency, attempts, task/provider/model, measured
  context, hard/soft budget values, generation settings, exact selected-call
  messages/tool schemas, and provider token usage.
- Context log: bounded secret-redacted `logs/llm_context.log`.
- Raw responses: bounded secret-redacted `logs/llm_raw_response.log`.
- Routing log: bounded secret-redacted `logs/structured_replies.log`, including
  intent, confidence, operational status, and routing errors.
- LLM application log: bounded, filtered, secret-redacted LLM/provider/routing
  rows from `logs/ljs.log`, including nearby traceback continuations.

Large exact payloads remain lazy-loaded only when a call is selected. Raw log
endpoints are bounded to 2,000 lines and use normal dashboard authentication.
Provider/attempt errors are also redacted before activity snapshots reach the
browser, so snapshot-based notification recovery cannot expose credentials.
Streaming telemetry remains active until iteration completes, fails, or is
cancelled; creating a stream iterator is not completion.

Verify with `tests/test_round288_reasoning_router_live_diagnostics.py`,
`scripts/round288_reasoning_router_live_diagnostics_tests.py`, and the standard
AI intent, AI context, security, public-document, and architecture checks.


## Round 289 — Effective model ownership, durable browser diagnostics, and server-authoritative chat state

The Round 288 target-machine logs exposed three integration failures that unit
coverage had not exercised. The settings interface probed a newly selected
`openai/gpt-oss-20b` model, but the next `intent_routing` call still resolved to
`openai/gpt-oss-120b`; the old two-step settings workflow hot-reloaded twice
while that call was running, and its retry remained bound to the captured old
route. At the same time, the Python backend contained LLM-card and Send/Stop code
that the browser did not visibly execute. The release had therefore validated
individual modules without proving route ownership, browser-bundle coherence,
or the complete server/UI state path.

### Field-by-field route authority

`LLMConfig` resolves each `TaskModelConfig` field independently through
per-task → tier → global fallback. A task object that configures only
`max_tokens`, temperature, or context no longer erases the tier's provider or
model. `route_source()` reports the exact winning layer for every field, and
`TaskLLMClient.effective_routes()` exposes a credential-free task map for the
settings UI and diagnostics.

A base-route save is one atomic mutation. Both the Compass settings panel and
the standalone `/settings` page send the base route and visible tier routes to
`/api/settings/llm` in one request. When the user selects “use this base route
for every task,” `clear_route_overrides()` clears provider/model/api-base/api-key
identity from chat tiers and task overrides while retaining generation/context
tuning and the explicit embedding route. The retired two-step
`/api/settings/tiers` mutation returns a reload instruction instead of allowing
an obsolete browser to reapply a stale lightweight route.

Every runtime route configuration has a monotonically increasing revision.
`TaskLLMClient` stamps calls with the revision and winning route sources. A
settings reload cancels active completion/stream consumers that captured the
superseded revision, records a `route_configuration_changed` diagnostic event,
and preserves the truthful cancellation reason. The next call resolves from the
new settings object; retries cannot continue on an old model after a route save.

### Durable problem events and exact browser recovery

`LLMActivityMonitor` owns a bounded event ledger in addition to call history.
Events are persisted before live delivery, included in the first authenticated
activity snapshot, and reconciled by stable call/event/attempt keys. This means
a timeout that occurred before the page connected—or during a temporary
`/ws/events` gap—still produces one compact notification card without duplicate
live/recovered cards. Cards and the full diagnostics workspace are generated
from the same monitor records and each card opens its exact call id.

The monitor exposes a constant-size `status(call_id)` lookup for provider cleanup.
Provider teardown must not copy exact messages and serialized tool schemas merely
to determine whether an activity record is still running.

### Server-authoritative chat state

The browser enters busy state synchronously before sending a turn: input is
disabled, Send becomes Stop, and the command status becomes `LLM working`.
`ChatTurnStateBroadcaster` also publishes session/turn-scoped working, stopping,
failed, cancelled, and idle states through the shared event bus. The controller
accepts only matching-session state, rejects a concurrent send, and Stop cancels
the real server task. Local optimistic state gives immediate feedback; server
state repairs disconnect/reconnect or terminal-path drift.

### Browser/backend bundle coherence

All local CSS/JavaScript URLs carry one content-derived static-bundle fingerprint.
HTML responses are `no-store` and expose the active fingerprint. Browser chat
messages and cancellations include their loaded asset version. A browser-origin
request with a missing or obsolete version is rejected before LLM execution with
a visible reload instruction; non-browser API clients remain compatible. This
prevents a new Python backend from silently operating with an older cached chat,
settings, or diagnostics controller.

Verify with:

```bash
pytest -q tests/test_round289_effective_routing_live_ui.py
node scripts/round289_frontend_contract_harness.js .
python scripts/round289_effective_model_routes_live_ui_tests.py
```

Also run the standard AI intent/context, security, public-document,
compatibility, and complete architecture gates after changing these boundaries.


## Round 290 — Complete HTTP readiness and exact deployed-build identity

The Round 289 target-machine package did not reach normal runtime. Uvicorn
returned a valid `200 OK` from `/api/live`, but the startup probe performed one
unframed `reader.read(512)` and searched that arbitrary TCP fragment for the
`ljs-live` marker. On the observed machine, headers arrived before the 36-byte
JSON body. LJS therefore rejected its own healthy server for 15 seconds and
shut the new process down. The prior runtime/UI was consequently the only build
the user had actually exercised, which made model routing, cards, and Send/Stop
appear unchanged even though their Round 289 code had never started.

`LJSWebReadinessGate` (`src/web/readiness.py`) now owns startup verification. It:

1. reads HTTP headers through `\r\n\r\n` rather than assuming one TCP read is a
   complete response;
2. parses and bounds `Content-Length`;
3. reads the exact complete body before decoding JSON;
4. requires HTTP 200 plus the dependency-free `status=ok` / `service=ljs-live`
   contract;
5. verifies both the browser-asset fingerprint and a backend/runtime build ID.

`RuntimeBuildIdentityResolver` hashes shipped backend sources, templates/static
assets, and category definitions. `/api/live` returns this `build_id` alongside
`asset_version`, and `main.py` requires both to match the app instance it just
launched. A stale process on the same port can no longer satisfy readiness merely
because it exposes an older LJS liveness marker. The exact build ID is logged at
startup and embedded in rendered HTML/LLM Diagnostics so users and support logs
can prove which package is active.

The liveness endpoint remains dependency-free: it reads only immutable values
stored on `app.state`; it does not touch databases, providers, storage, browsers,
or library services.

Verify with:

```bash
pytest -q tests/test_round290_startup_build_identity.py
python scripts/round290_complete_readiness_build_identity_tests.py
```

The regression test deliberately splits HTTP headers and JSON body across
multiple writes and also starts a real uvicorn server over TCP. Never replace
this parser with a single `read()` or remove exact build/asset matching.

## Round 291 — TV release-frontier truth and direct download completion

The July 13 Silo incident exposed a semantic contradiction inside the TV search
workspace. Provider catalogue metadata said Season 3 had ten ordered episodes,
while TVMaze/TMDB air-date evidence and exact torrent fallback identified only
S03E01 and S03E02 as released. The old response published the catalogue total as
`expected_episode_count=10`, omitted the aired/target frontier, and returned no
multi-unit recommendation. The final model consequently described ten episodes
as available and asked the user to choose whether to proceed. A later fully
successful two-item queue receipt was then falsely rendered as a partial failure
because the outcome guard treated the success status `queued` as an error detail.

### One category-owned release snapshot

`CategoryWorkflowContext.agent_search_facts` transports opaque category facts
from category search through scheduler response assembly. Generic scheduler/tool
code may carry and display those facts, but it must not infer TV air dates,
episode counts, or release coordinates.

TV builds one season availability snapshot before pack/fallback search:

- `season_total_episode_count` is the provider catalogue/order size;
- `aired_episode_count` and `aired_unit_labels` are dated release evidence;
- `release_frontier_episode` is the highest currently aired episode;
- `target_unit_count` and `target_unit_labels` are the released units still
  targeted by the download search;
- `season_release_state` distinguishes currently airing from complete/unknown;
- compatibility `expected_episode_count` means released/searchable bundle
  coverage, never a future catalogue order.

If dated release evidence is unavailable, released/searchable coverage remains
unknown. Compatibility fields must not fall back to the provider's catalogue
total merely to produce a number.

Pack range queries use the released frontier. A season with ten ordered episodes
but only E01/E02 aired may query `S03E01-E02`; it must not query or advertise
`S03E01-E10` as the current downloadable range. Exact fallback remains limited
to provider-aired/local evidence. This rule is category-owned and does not use
English or Italian phrase detection.

### Complete deterministic batch, no redundant menu

After category filtering and language/selection annotations,
`SearchBatchRecommendationBuilder` may build one candidate per concrete unit even
when the original scope was `bundle_preferred`, provided no acceptable bundle is
present. `bundle_only` still forbids individual-unit batch fallback. Candidates
blocked by language, identity, quality, or queue safety cannot enter the batch.
Category-published `target_unit_labels` also filter the batch, so unrelated or
future units cannot be included merely because an indexer happened to return
them.

`SearchWorkspaceCompletionContractBuilder` publishes whether the structured
workspace fully covers the current target. When it says
`follow_up_required=false` and `action_required=queue_download`, the user's
existing download request is already sufficient authority. The main agent must
call `queue_download` with the supplied stable IDs; it must not ask for another
confirmation or present a candidate menu. The loop enforces this by suppressing
premature prose and issuing one structured queue-followthrough reprompt. This is
not hidden deterministic queueing: the LLM still performs the explicit tool
call, while generic code validates stable IDs and receipts.

Completion is exact, not count-based: recommended group labels must equal the
category-published target labels. Two candidate groups do not satisfy a
two-unit target if they are E01 and E03 while the target is E01 and E02.

A complete deterministic batch skips the separate advisory torrent-ranker LLM
call. The main agent remains the model decision boundary, and the app avoids an
extra slow/blank ranking request when category evidence already provides one
safe candidate for every target unit.

### Human success and truthful failure presentation

Search tool output preserves total-order, aired, and target fields in compact
LLM context and audit logs. Prompt guidance requires brief human-language queue
summaries using title, unit labels/count, requested language, and actual queue
status. Internal result-set IDs, policy names, and receipt jargon are omitted
unless needed to explain a real problem.

`ToolOutcomeLedger` no longer converts successful statuses such as `queued` or
`already_active` into failure details. A receipt with two verified successes and
zero errors is a full success, not a partial failure.

Provider control-token leakage appended to an otherwise valid tool name (for
example `inspect_torrent_candidate<|channel|>commentary`) is stripped only at the
known terminal channel-token boundary before allow-list validation. Arbitrary
unknown tool names remain blocked.

Verify with:

```bash
pytest -q tests/test_round291_silo_airing_frontier_and_human_flow.py
python scripts/round291_silo_airing_frontier_direct_queue_tests.py
```

Also rerun Round 241, 276, 279, 285, and the standard AI/category/security and
complete architecture gates after changing these contracts.

## Round 292 — Search truth, acquisition continuity, TV tracking defaults, and lifecycle cadence

The August 10 long-session logs exposed a set of regressions that shared one
architectural cause: category/provider truth was being weakened or reinterpreted
in later generic layers, while periodic coordinators performed expensive work
before asking the lifecycle ledger whether an item was due.

### Category-owned movie identity survives the whole search workspace

A provider-confirmed one-word movie title must not be forced through a generic
release-token whitelist after the category has already established title/year
identity.  For example, TMDB-backed `Oppenheimer (2023)` is strong enough to
validate an exact `Oppenheimer 2023 ...` release prefix; normal codec, language,
IMAX, group, and audio metadata after that prefix is not title ambiguity.
Without an independently verified year, one-word aliases retain the conservative
suffix-only rule.

`MovieCategory` therefore owns the title/year verdict and publishes it in the
candidate payload as opaque `title_identity` evidence.  Generic workspace code
carries that verdict instead of re-parsing the release name with weaker rules.
Movie interactive search also stops its query ladder only once a useful pool of
validated canonical results exists; when the user requested a language, that
pool must contain explicit preferred-language evidence.  This avoids both
low-value alias fanout and premature stopping on dozens of title-correct but
constraint-wrong rows.

### Source strategy belongs to the category

Torrent and Soulseek are independent discovery backends, but they do not need to
block one another equally for every category.  Categories may publish a
`soulseek_source_strategy()` hint that controls foreground companion behavior.
Movies use `fallback_if_primary_empty`: a successful torrent search returns
immediately and Soulseek is attempted only when the primary torrent result set is
empty.  Categories that genuinely benefit from concurrent source discovery may
retain `parallel` behavior.  Generic scheduler code interprets only this source
strategy contract; it does not encode movie semantics.

### Acquisition continuity uses structured goal state, not generic web search

A terse follow-up such as "search harder" after a media acquisition request must
reuse the active goal's category and result-set state.  `AgentGoalStateManager`
exposes the current structured goal, and `AIAssistant` marks SEARCH turns that
continue an acquisition result set.  Those turns receive a deliberately compact
media-search tool surface including `search_media_torrents` and inspection
helpers, but not queue mutation authority.  Public web research may contribute
identity/evidence; raw URLs or magnets from web search do not become queueable
media candidates.  Stable category-owned result-set/candidate handles remain the
only acquisition continuation authority.

### One active assistant turn per chat session, independent of transport

`ChatTurnRegistry` is the process-local authority for at most one live assistant
task per `session_id`.  Both `/ws/chat` and `/api/chat` acquire the same registry
before starting work.  A reconnect, second browser tab, or REST fallback cannot
start a competing turn while the prior WebSocket task is still running.  Stop
and disconnect cancel/release only the matching owned turn.  This prevents
crossed result sets and the user-visible failure mode where one turn says
"nothing found" while an older overlapping turn continues searching or queues a
download later.

### TV initial episode tracking is lifecycle-owned and tri-state

`TvShowItem.auto_download` is tri-state.  A newly discovered show stays `None`
until the TV category has enough provider lifecycle evidence.  During watch-plan
construction, TV may perform one category-owned lifecycle metadata lookup when
cached lifecycle data is absent.  Active/returning/in-production shows default
to `True`; ended/cancelled/finished shows default to `False`; unknown state stays
unset.  Once the user sets a literal boolean, that choice is authoritative and
is never overwritten by the defaulting path.

`CategoryWatchPlan.item_updates` transports these category-owned initial values
back through the generic scheduler without teaching core code what TV statuses
mean.  The scheduler persists the opaque item fields and invalidates lifecycle
state once so normal reconciliation observes the change.

### Lifecycle due checks happen before expensive reconciliation

The lifecycle ledger is the first gate for periodic work.  A future-dated clean
row is sufficient to leave an item dormant; explicit invalidation or a due row
enters the authoritative fingerprint/category-policy path.  The cheap
`scheduled_work_is_due()` preflight intentionally performs no canonical-library
build, metadata fingerprint, provider, suggestion, taste, or LLM work.

Tracked-item scheduled updates and suggestion compilation both use this preflight
before expensive processing.  `reconcile_item()` also checks for an existing
ledger row before computing first-time fingerprints.  This preserves event-driven
invalidations while preventing the hourly scheduler from rebuilding unchanged
library state merely to discover that the category is not due.

Stable, locally present movies use the category's long 180-day lifecycle cadence;
missing or metadata-incomplete movies retain shorter category-owned repair
cadences.  The full forced library crawl is a daily safety net rather than an
hourly invalidation source.  The existing 120-second filesystem watcher and
targeted managed-import reconciliation remain responsible for prompt reaction to
real library changes.

Verify the incident contracts with:

```bash
PYTHONPATH=. python scripts/round292_search_truth_tracking_cadence_tests.py
pytest -q tests/test_round292_search_truth_tracking_cadence.py
python scripts/check_category_architecture.py
python scripts/check_ai_intent_architecture.py
python scripts/check_ai_context_architecture.py
python scripts/check_architecture.py
```

The executable harness is dependency-light so the core incident contracts can be
checked even on a packaging host without LJS's optional/runtime provider stack.
The pytest regression file remains the authoritative in-project test when the
normal declared Python environment is available.

## Round 293 — Search-result truth, exact candidate confirmation, and cancellation ownership

The August 11 Ella Enchanted incident exposed a different failure chain from the
Round 292 Oppenheimer case. The first request was manually stopped by the user;
its later timestamps must therefore not be interpreted as a single long-running
foreground search. Instead, the logs show provider/search work continuing after
the browser considered the turn stopped. A later fresh request did find an
explicit Italian candidate, but several generic layers weakened that success
before queueing.

### Categorized media search does not need a second advisory planner

A SEARCH turn whose category has already been resolved and whose allowed tool
surface contains `search_media_torrents` runs the ordinary registered tool loop
directly. DOWNLOAD does the same. The old advisory pre-planner added extra model
calls before the model could perform an already-obvious media search and could
fail independently without adding execution authority. Generic SEARCH turns
without a category-owned media-search tool retain their existing planner path.

Tool argument normalization also treats optional JSON `null` values as omitted
after required-field checks. An unset optional field such as `target_size_gb`
must not invalidate an otherwise legal tool call and consume another LLM turn.
Known terminal provider control tokens accidentally appended to a valid tool
name are stripped at the executor boundary before normal allow-list validation;
arbitrary unknown tool names remain forbidden.

A complete new acquisition request also starts a fresh structured goal whether
the router labels it SEARCH or DOWNLOAD. This reuses the existing generic
context-freshness decision: terse refinements such as `search harder`, quality
selectors, and stable candidate/result handles remain continuations, while a
new concrete title request cannot inherit stale result sets from an older goal.

### Explicit-language evidence ends the useful movie query ladder

Movie query expansion remains category-owned. When a language is explicit,
`CategoryTitleAuthority` builds search aliases from the canonical/provider title,
the user's title, and translations specifically associated with the requested
locale. Arbitrary translations from unrelated locales are excluded from that
explicit-language ladder.

Search sufficiency is based on constraint satisfaction rather than an arbitrary
volume target. For an explicit-language movie request, one provider/year-backed,
title-valid candidate that explicitly advertises the requested language is
useful evidence and ends further alias fan-out. Low swarm health is a selection
warning, not proof that search failed. When no requested-language evidence
exists, the bounded fallback ladder may continue as before.

The candidate workspace keeps unknown-language releases only as fallback
evidence while no explicit requested-language release exists. Once explicit
preferred-language candidates are present, the LLM-facing/cached selection
workspace is narrowed to those candidates so a higher-seeder unknown-language
release cannot displace the user's hard language constraint.

### Stable result-set selection confirms only that candidate's soft warning

Search result sets persist their origin prompt, stable candidate IDs, and whether
the UI was awaiting a user choice. A later turn that resolves to one of those
stable candidates is provenance that the user selected the displayed candidate;
no English phrase or numeric-string parser is used as confirmation authority.

Soft warnings such as very low/no reported seeders may therefore be confirmed by
that later exact-candidate selection. Hard blockers such as explicit-language
mismatch, identity failure, or other request-constraint violations remain
non-overridable. Confirmation is never inherited by a substitute candidate.

Queue fallback is correspondingly narrow: confirmation/policy failures are
terminal for that selected candidate and do not launch a spray of alternate
cached releases. Automated first-turn or deterministic batch queueing may try an
alternate only after an operational error explicitly marked fallback-eligible,
and that alternate is evaluated under its own automatic policy without
inheriting the original candidate's confirmation. A later explicit user choice
of one stable cached candidate is different: once that exact candidate has been
selected, an operational failure of that queue attempt is reported for that
candidate and must not silently substitute another release.

### Stop owns the server task, not merely the browser request

`ChatTurnRegistry` remains the session-scoped owner of one assistant task. Both
WebSocket and REST Stop paths cancel that same server task. The REST browser does
not abort its local `/api/chat` fetch until `/api/chat/cancel` confirms the
server task settled. If cancellation is still unwinding, the UI remains in a
truthful `stopping` state rather than pretending the turn ended while provider
work continues in the background.

Search providers are children of that task. `JackettSearch` now cancels and
awaits aggregate/manual-parity child tasks in `finally`, including direct-indexer
probe children. This makes cancellation propagation an ownership rule rather
than a best-effort UI behavior.

The generic operation trace carries session ID, turn ID, and turn-relative
elapsed time through LLM, search, and structured logs, including the ordinary
`ljs.log` sink. A dedicated turn audit records
received/started/cancel-requested/settled/completed/failed transitions. Search
logging records `torrent_search_started` before provider I/O and an explicit
cancelled/failed terminal event, while the historical completion record now
also carries `terminal_state=completed`. Authenticated diagnostics expose
bounded **Turn lifecycle** and **Searches** views. Future incident analysis can
therefore distinguish user cancellation, provider latency, post-cancel cleanup,
and a later independent request without inferring duration from unrelated
wall-clock gaps.

### Plain-language outcomes

Deterministic queue and error presentation reports what happened directly:
queued downloads, failed candidates, confirmation requirements, or provider
errors. The old theatrical cargo/captain/parrot wording is removed from these
system outcomes because it obscured operational truth during failure recovery.

Verify with:

```bash
PYTHONPATH=. python scripts/round293_ella_search_selection_cancel_tests.py
pytest -q tests/test_round293_ella_search_selection_cancel.py
python scripts/round290_complete_readiness_build_identity_tests.py
python scripts/round291_silo_airing_frontier_direct_queue_tests.py
python scripts/round292_search_truth_tracking_cadence_tests.py
python scripts/check_category_architecture.py
python scripts/check_ai_intent_architecture.py
python scripts/check_ai_context_architecture.py
python scripts/check_security_architecture.py
python scripts/check_architecture.py
```

The dependency-light executable harness exists specifically for packaging hosts
that do not have the complete declared LJS runtime installed. The in-project
pytest file remains the normal authoritative regression when that environment is
available.

## External control plane and MCP agent delegation (2026-08-15, security/truth repair)

LJS exposes external automation through an **application-owned public control
plane**. MCP is only a transport adapter over that control plane; it does not
own domain reasoning, category semantics, conversation state, mutation truth,
downloader state, or scheduler/runtime lifecycle.

### Authority and lifecycle boundaries

The external-control architecture keeps distinct identities/lifecycles separate:

- `conversation_id` is a high-entropy server-minted public handle. It maps
  privately to an existing LJS session, is bound to principal/client/user,
  expires after inactivity (30-day default), is quota-bounded (100 active per
  principal/client by default), and is explicitly revocable with
  `ljs.agent_close`. Expiry/revocation cleans the private external session and its
  conversation history. External callers never choose internal `session_id` values.
- `turn_id` is the foreground assistant-execution identity owned by
  `src/ai/chat_turn_registry.py`. Web/REST/MCP delegation share that same
  process-local cancellation authority. Public cancellation truth distinguishes
  `not_running`, `cancelling`, and settled `cancelled`.
- `command_id` / `correlation_id` remain durable mutation/receipt identities
  owned by `ActionGateway` and command-producing tools.
- A detached long-running `work_id` still does **not** exist. It should be added
  only if real interoperability proves request-scoped execution insufficient;
  it must not replace conversation/turn/command identities.

`ChatSessionRunner` remains the semantic entry point for interactive assistant
turns. `AgentDelegationService` adapts an external principal and opaque handle
into that existing runner. `AgentTurnControlService` owns cancellation/close
truth, and `AgentDelegationAdmissionGate` bounds external provider-backed
parallelism (4 active turns per principal/client by default). Delegated messages
are bounded to 65,536 characters. These controls do not create a second agent
loop.

`ChatTurnRegistry` is intentionally process-local because LJS is currently a
single-process runtime. Any future multi-worker deployment must introduce a
shared turn-ownership/lease authority before claiming cross-worker cancellation.
Transport-session affinity must not be used as a substitute.

### Invocation identity and application capabilities

`InvocationPrincipal` and `InvocationContext` are protocol-neutral application
models. Trusted first-party surfaces retain their historical tool surface.
Constrained external principals carry explicit capabilities.

Authorization domain and operational risk are separate concepts. Category
`risk_level`, destructive labels and confirmation policy answer how an action is
handled; they **do not grant authority**. `CategoryActionDeclaration` and
`CategoryWorkflowDeclaration` carry explicit `invocation_capabilities_required`
for concrete application domains such as:

- `library.read` / `library.write`;
- `library.files.delete`;
- `downloads.read` / `downloads.write`;
- `tracking.write`;
- `config.write`;
- `config.llm.read` / `config.llm.probe` / `config.llm.write` /
  `config.llm.endpoint.write`.

Current TV/movie/base/definition-backed mutations declare the authority they
actually require. The generic cross-domain category dispatcher and any future
mutable tool lacking explicit authorization metadata fail closed to `admin` for
constrained principals rather than being guessed from English tool names or
`risk_level`.

Definition-backed workflows receive the originating `ToolExecutionContext`.
This closes hidden-mutation paths where a normally read-only workflow can
persist when scheduler/internal-only arguments (for example `item_id`) are
present: constrained external callers need the write capability for the hidden
persistence path as well.

Agent-visible category contracts must also be executable truth. Generic
`scan_library` / `consolidate_library` declarations that do not own a concrete
category executor are not advertised as LLM tools. TV/movie delete workflows
are concrete two-phase, token-bound workflows: untracking requires
`library.write`, while `delete_files=true` additionally requires
`library.files.delete` before local path evidence is computed. Ordinary
delegated assistant turns still hide destructive category tools because the
assistant has no canonical policy-level `confirmed` continuation state yet; an
explicit category caller can use the workflow confirmation contract directly.
Do not invent a tool-name or prose-based confirmation bypass to expose them.

Capability enforcement is defense in depth:

1. `AIAssistant` intersects intent/category tool policy with invocation
   capabilities **before** tool definitions are shown to the LLM.
2. `ToolRegistry.execute()` rechecks capabilities at execution.
3. Delegated calls default to `allow_actions=false`, which removes write
   capabilities even from a more privileged credential for that turn.
4. Unknown/unannotated mutable private tools fail closed for constrained
   principals.

The private `ToolRegistry` is not the MCP catalog. MCP must not export it, a
`execute_action(name,args)` escape hatch, or raw search/download micro-tools.

### Structured delegated outcomes

A delegated turn returns `ChatTurnOutcome` / `AgentDelegationResult` with public
conversation handle, `turn_id`, status/text, stable result-set/candidate IDs in
observation order, and IDs of **persisted** action receipts. The structural
evidence collector does not parse assistant prose or media vocabulary.

Confirmation-required tool results (`requires_confirmation`,
`confirmation_required`, `needs_confirmation`) keep the delegated turn in
`needs_input`; they are not mistaken for completion. Unhandled runner failures
become a bounded `failed` result whose public message directs callers to the
separately authorized diagnostics surface rather than exposing private
exception text.

### Public control plane

`src/core/public_control_plane.py` contains bounded query/control services for:

- application/storage status with private exception redaction;
- tracked canonical library summaries and exact item lookup, recursively
  redacting host-local path fields;
- active-download summaries;
- configured/effective LLM routing;
- an explicit outbound provider/model probe;
- curated LLM routing changes through `ActionGateway`;
- redacted recent LLM diagnostics.

The facade in `src/core/public_control_plane_facade.py` is protocol-neutral and
has no MCP request dependency or arbitrary gateway-registration escape hatch.

### LLM route and credential ownership

Provider/model configuration, endpoint authority and credentials are distinct.
The public capabilities intentionally separate them:

- `config.llm.read` reads configured/effective routing;
- `config.llm.probe` allows the outbound authenticated provider/model catalog
  request performed by `ljs.llm_test`;
- `config.llm.write` changes ordinary provider/model/tier routing;
- `config.llm.endpoint.write` is additionally required whenever an MCP update
  contains an `api_base` change.

The MCP surface never accepts API-key fields. Canonical mutation is implemented
by `LLMSettingsMutationService` using a detached candidate configuration. A
provider transition resets the route to the selected provider's configured
endpoint and clears an incompatible global/tier secret; a custom endpoint change
also clears inherited route secrets. Persistence and runtime reload are treated
as one mutation: failures trigger best-effort restoration of the previous
persisted and runtime configuration before the command can be reported as
successful.

`ProviderCredentialPolicy` controls automatic KeyStore secret attachment. A
stored provider credential may be auto-attached only when the resolved endpoint
is that provider preset's canonical endpoint. Operator/registry endpoint
overrides are honored for routing but are **not** credential-owned, so provider
secrets do not silently follow them. Explicit task/tier custom endpoints also do
not inherit the global provider secret.

### MCP transport, Settings ownership, and live lifecycle

Local MCP is opt-in Streamable HTTP mounted at `/mcp` in the **already-running
LJS FastAPI process**. MCP configuration is canonical application Settings
(`Settings.mcp`), persisted in ignored `config/settings.local.yaml`; environment
variables no longer decide whether the server exists.

`/mcp` itself is mounted once through `MCPDynamicMount`. The top-level FastAPI
lifespan starts one `MCPRuntimeController` worker. This worker owns every
`session_manager.run()` enter/exit for the lifetime of the process. A Compass
Settings mutation sends an apply/disable command to that worker and awaits the
actual transition. This avoids crossing AnyIO/SDK context ownership between
unrelated HTTP request tasks while still making the switch live without a
process restart.

Runtime replacement is sequential rather than nested: the owner worker detaches
and closes the current SDK context before entering a replacement context. The
controller owns the transaction around that worker transition: if replacement,
persistence, or the initiating Settings request fails, it best-effort restores
the previous persisted configuration and previous runtime. The worker still
finishes any already-queued SDK transition before cancellation propagates, so
live runtime and persisted Settings do not split-brain.

The Settings surface is `Compass → MCP — External LLM Control`. It exposes live
status, exact loopback endpoint based on the actual `LJS_PORT`, dedicated token,
user binding, bounded capability controls and generic Streamable-HTTP client
instructions. First enable generates a strong token automatically. Generic
`/api/settings` redacts that secret; `/api/settings/mcp` is the dedicated
Settings UI contract.

The transport remains local-only. `LocalMCPNetworkBoundary` rejects non-loopback
clients and missing/unknown ASGI client origins even when the rest of LJS is
bound to `0.0.0.0`. `MCPAuthenticationBoundary` authenticates once at the outer
ASGI boundary and propagates the immutable validated principal through
`MCPRequestPrincipalContext`.

Local MCP v1 accepts only the dedicated persisted bearer token. Generic LJS Web
JWTs are not MCP credentials. The configured `user_id` selects the canonical LJS
user whose preferences/history are used by delegated turns; any non-`local` id
must already exist before activation. One token/principal/client tuple represents
one local external agent.

Remote MCP is a separate future feature and requires a real TLS/OAuth resource
server design. LJS must not publish fake discovery metadata or weaken the local
boundary to simulate it.

Current public MCP tools remain deliberately small:

- `ljs.agent_message`, `ljs.agent_cancel`, `ljs.agent_close`;
- `ljs.status`, `ljs.capabilities`;
- `ljs.library_list`, `ljs.library_get`;
- `ljs.downloads_list`;
- `ljs.llm_get`, `ljs.llm_test`, `ljs.llm_set`;
- `ljs.diagnostics_recent`.

Bounded JSON resources expose status, capabilities, a first library summary
page, active downloads, and LLM configuration. These are **static MCP resources**:
their registered handlers are zero-argument methods and must not request SDK
`Context` injection. Authentication still happens at the outer ASGI boundary;
the already-validated principal is read from `MCPRequestPrincipalContext` inside
the resource handler. This keeps per-request authorization without violating the
MCP Python SDK contract that reserves injected `Context` for tools/prompts and
resource templates with URI variables. `check_mcp_architecture.py` and the
dependency-light MCP acceptance registrar both fail if a static resource handler
acquires parameters again.

No stdio runtime owner, remote MCP, public raw acquisition tools, arbitrary
action executor, or MCP Tasks clone is part of this slice.

Verify this boundary with:

```bash
python -m compileall -q src scripts main.py
node --check src/web/static/js/components/settingsPanel.js
PYTHONPATH=. python scripts/mcp_control_plane_tests.py
PYTHONPATH=. python scripts/check_mcp_architecture.py
PYTHONPATH=. python scripts/round293_ella_search_selection_cancel_tests.py
PYTHONPATH=. python scripts/check_ai_intent_architecture.py
PYTHONPATH=. python scripts/check_ai_context_architecture.py
PYTHONPATH=. python scripts/check_security_architecture.py
PYTHONPATH=. python scripts/check_category_architecture.py
PYTHONPATH=. python scripts/check_public_docs.py
PYTHONPATH=. python scripts/check_model_facade_imports.py
PYTHONPATH=. python scripts/check_compatibility_shims.py
PYTHONPATH=. python scripts/check_architecture.py
PYTHONPATH=. pytest -q
```
