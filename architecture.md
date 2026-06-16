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
window of another. Public web research can be used for title-ambiguity
resolution when metadata providers cannot identify a title, but fetched public
evidence should establish aliases/context rather than becoming a torrent
queueing shortcut.

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

## Round 253 — TV Show New-Episode Automation Checkbox

TV new-episode automation is now an item-owned policy with a default-on user
experience.  Every tracked TV show detail/inspector payload must expose the
show's `auto_download` state as a simple enabled/disabled checkbox labeled for
new episodes.  Missing or legacy `null` TV values mean enabled; users can turn a
specific show off, at which point release-watch retries for that show switch to
notify/search-only behavior instead of automatic queueing.

The generic scheduler still does not interpret seasons or episodes.  It only
reads the category-provided release-watch `requirements.auto_download` snapshot
and then calls the existing category-aware discovery path.  TV owns the default
and the requirement snapshot through `TvShowItem`, `TvShowCategory.create_item()`,
`TvShowCategory._release_watch_requirements()`, and the TV detail payload.  The
frontend saves the checkbox through the generic category item update endpoint so
`CategoryItemCoordinator` persists the item and immediately resynchronizes the
category watch policy.

Important invariants:

- New TV shows default to auto-downloading newly released episodes.
- Legacy TV items with missing/null `auto_download` are normalized to enabled.
- A user-set `auto_download: false` on one show must be preserved and must keep
  that show in notify-only release-watch behavior.
- UI code may render the checkbox for TV/episodic category detail payloads, but
  it must save through category item mutation APIs rather than editing settings
  directly.

Verify with `scripts/round253_tv_auto_download_inspector_tests.py` after changing
TV item models, TV watch planning, category item update flows, or the TV library
inspector.

## Round 254 — TV Torrent Recall: Title Variants, Explicit-Language Ranking, and Bounded Fallbacks

TV torrent search must treat small title-normalization differences as recall issues, not as proof that a candidate is unrelated. Tracker/provider titles may represent possessives and plural forms differently from user prompts: for example, a user may ask for `Widow Bay` while provider metadata and torrent rows say `Widow's Bay` or `Widows Bay`. TV title matching may allow narrow singular/plural token equivalence inside an otherwise ordered multi-token title window, but it must still reject unsafe one-token/article-title collisions such as `The Boys` versus `The Hardy Boys`.

Explicit media-language requests are stricter than configured-language defaults. For an English-configured install, unknown-language scene releases may remain preferable to weak `ITA+ENG`/`MULTI` fallbacks when English is merely the default. When the user explicitly asks for Italian, French, or another non-English media language, candidates that actually advertise that language must outrank unknown-language rows even if those unknown rows have more seeders. Unknown-language rows may remain visible as fallbacks, but they must not be auto-queueable for explicit non-English requests.

A full-season TV request made before a full-season pack exists must not degrade into “nothing found” when partial episode-range packs or same-season explicit-language single episodes are already present in broad season searches. The TV pack workspace may include same-season single-episode rows as fallback coverage when they advertise the explicit requested language. This keeps the LLM candidate reviewer informed about realistic partial coverage such as `S01E01-06` packs plus `S01E07`/`S01E08` singles, without launching a long per-episode query storm or pretending that the unaired/unreleased tail of the season is available.

Verify with `scripts/round254_widows_bay_recall_ui_responsiveness_tests.py` after changing TV title matching, TV pack gates, explicit-language ranking, candidate payload sorting, or the `search_media_torrents` selection annotation policy.
