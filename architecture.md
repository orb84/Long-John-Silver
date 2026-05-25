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

## UI Access

The UI should render canonical library objects and category manifests. Generic screens can display common fields (`display_name`, `computed`, `units`, artwork) and pass through category-specific nested sections. Category-specific UI components may exist, but they belong to the category or are selected by manifest/component declaration.

Detail payloads must include the category-built canonical object. They may mirror common sections from the canonical object for legacy components, but those mirrors must be copied from the canonical object according to the category specification, not rebuilt independently.

## Naming, Consolidation, and Library Exposure

Naming templates are category settings. The generic consolidator may walk files and perform safe moves, but it must ask the category to map a parsed local file into a target path through `consolidation_target_for_file()`. Download completion should ask the category for normal target planning and, if that fails, a category-owned `fallback_library_path()` before using a flat generic safety fallback.

This matters because the same canonical object contract must govern all library interfaces: scanner, consolidation, download completion, UI, suggestions, and agent tools. Never fix one of those systems in isolation without checking the others.

## Category Schedules

The scheduler does not know what an air date, release date, patch date, chapter publication date, or DLC drop means. It calls `category.next_scheduled_unit(item, context)` and persists the category-owned `state_updates` returned by that hook.

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

Recovered category-template path failures are not import failures. If a template proposes an unsafe destination but the category fallback produces a safe target, the event is informational; only failure of both target plans or the actual file operation is an error.

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

General Files follows the same category contract as TV and Movies: it declares its own manifest/profile, LLM prompt, setup requirements, search query builder, candidate validator, import layout, file scanner, unit descriptors, and bundle/file-list affordances. The only normal setup field is its `library_path`; completed payloads are stored below that root in a safe title folder while preserving the original filename.

The assistant must explicitly opt into the category by passing `category_id: "general"` to `search_media_torrents`. Existing rich categories continue to win whenever their router vocabulary or tracked items apply. Generic search planning must not switch a failed TV/movie request into General unless the user clearly asks for a miscellaneous exact file target or approves the category switch.

Frontend onboarding remains manifest-driven. `/api/setup/requirements` exposes the new category and its required path; the web UI checks category manifests at startup and shows a one-time prompt when a new category appears or when a required category setting is missing. Fresh installs see General Files in the setup/category settings flow automatically.

### Round 105 — Compass category settings ownership

Compass now follows the category-owned configuration model more strictly:

- Per-category paths, naming templates, cadence values, provider toggles, scheduler participation, storage declarations, and lifecycle summaries are rendered in **Library Categories** from category manifests plus ignored live `config/categories/<category_id>.yaml` files bootstrapped from tracked `config/category-templates/<category_id>.yaml` templates.
- TMDB, TVMaze, Trakt, Plex, and OpenSubtitles are no longer presented as one generic "Metadata and watch state" block. Categories that use those services declare setup requirements, and the UI surfaces the controls under the owning category.
- **Shared Torrent Search & Indexers** owns only category-agnostic torrent infrastructure such as Jackett and direct scraper fallback.
- **Advanced Category Contracts** is read-only diagnostics. It shows the backend contract exposed to the UI and LLM so generic code does not hardcode TV/movie behavior; it is not an editable settings surface.
- Runtime code must honor category YAML sections through category helpers: `metadata.providers.<provider>.enabled`, `scheduler.enabled`, `storage.*`, and `lifecycle_policy`.

When adding a category, add editable user settings through `get_properties()`, service/setup needs through `provider_setup_requirements()` / `setup_requirements()`, and public non-secret defaults through `config/category-templates/<category_id>.yaml`; live user paths/keys belong only in ignored `config/categories/<category_id>.yaml`. Do not add category-specific fields to a global Compass section.

