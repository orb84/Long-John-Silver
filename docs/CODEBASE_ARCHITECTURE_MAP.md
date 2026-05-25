# LJS Codebase Architecture Map

_Last updated for the category-first, security-hardened, storage-aware package._

This document is the practical map for finding code in LJS. It describes the runtime composition, package boundaries, important classes, request/tool flows, and the ownership rules that keep the project category-first.

## 1. Architectural North Star

LJS is a category-first media automation platform. The system should be read as a pipeline:

```text
User / scheduler / communication bridge
  -> AIAssistant or web API
  -> category resolution and tool policy
  -> category manifest / category workflow
  -> metadata and search providers
  -> torrent candidate normalization
  -> download manager
  -> librarian / category path planner
  -> safe filesystem operation
  -> ActionReceipt / UI event / notification
```

The important idea is that TV and Movie are not special global modes. They are built-in categories. Their special behavior belongs inside category classes, metadata providers, prompts, workflows, or tests that explicitly validate those categories.

## 2. Composition Roots

### `main.py`

`main.py` is the application composition root. It wires concrete services and starts the runtime.

Key responsibilities:

- Load configuration through `SettingsManager`.
- Initialize `Database`.
- Register built-in categories with `CategoryRegistry.register_defaults()`.
- Build `StorageMonitor` once the category registry exists.
- Start Jackett search if available.
- Build `SearchAggregator`.
- Initialize LLM runtime, vector store, conversation manager, preferences, downloader, scheduler, and assistant.
- Register LLM tools through `AgentToolCatalog`.
- Create the FastAPI app with `create_app()`.
- Start scheduler, communication bridges, RSS monitor, and web server.
- Shut down all services in a controlled order.

Important service wiring in `main.py`:

```text
SettingsManager -> Settings
Database -> repositories and persistent state
CategoryRegistry -> built-in/custom categories
StorageMonitor -> category-aware disk reports
SearchAggregator -> JackettSearch primary provider + optional direct fallback providers
TorrentSelectionService -> normalized/LLM-assisted torrent candidate selection
DownloadManager -> queue, torrent engine, storage checks, lifecycle callbacks
DownloadDependencies -> explicit downloader collaborator bundle
MediaScheduler -> category-driven automation loop
AIAssistant -> user/LLM runtime and tools
AgentToolCatalog -> ToolRegistry
create_app(...) -> FastAPI routers and WebDependencies
```

### `src/web/app.py`

`create_app()` is the web composition root. It receives already-built services from `main.py` and stores them inside `WebDependencies`.

Key responsibilities:

- Create the FastAPI application.
- Register HTTP and WebSocket routes.
- Mount static assets.
- Create `ActionGateway` so UI actions and LLM tools share the same execution/audit path.
- Register routers:

```text
DownloadsRouter
ActionsRouter
HealthRouter
PagesRouter
ProvidersRouter
SetupRouter
CategoriesRouter
SettingsRouter
CategoryItemsRouter
LibraryRouter
UpgradesRouter
SuggestionsRouter
SystemRouter
StorageRouter
```

### `src/web/dependencies.py`

`WebDependencies` is the web-layer dependency container. Routers should receive dependencies through this object instead of importing globals.

Important fields include:

```text
settings_manager, db, assistant, downloader, notifications, auth_service,
llm_manager, scanner, conversation_manager, behavior_tracker, suggestion_compiler,
recommender, release_group_tracker, scheduler, supervisor, comms_registry,
torrent_racer, search_aggregator, browser_runtime, jackett_manager,
storage_monitor, action_gateway, action_event_store, category_registry,
tool_registry, behavior_recorder, librarian
```

## 3. Source Tree Overview

```text
src/
  ai/                    LLM assistant runtime, tool policy, prompt building, tools.
  core/                  Domain services, models, config, database, scheduler, downloads.
    actions/             Shared action gateway/audit registration.
    categories/          Category-first media domain; base/TV contracts split into focused mixins.
    repositories/        Persistence access classes.
    security/            Safe paths, command policy, confirmations, security audit.
  integrations/          External APIs such as TMDB, TVMaze, Trakt, Plex.
  llm_providers/         LLM provider abstraction and task-aware client.
  search/                Torrent search providers and aggregation.
    web/                 General web-search provider abstraction.
  subtitles/             Subtitle integrations.
  utils/                 Shared utilities, browser runtime, auth, parser helpers.
  web/                   FastAPI app, routers, templates, static JS/CSS, comms bridges.
```

Root-level documents worth knowing:

```text
AGENTS.md       Coding rules and project standards.
docs/CODEBASE_ARCHITECTURE_MAP.md Current structure and class-location map.
docs/PROJECT_WORKING_PRACTICES.md Extension and maintenance guide.
SECURITY.md     Filesystem, shell, confirmation, and audit security model.
STORAGE.md      Category-aware disk/volume monitoring model.
PROGRESS.md     Change log and current known issues.
README.md       User-facing setup and operation guide.
```

## 4. Core Domain Models

### `src/core/models.py` and `src/core/domain_models/`

`src/core/models.py` is now a compatibility facade for existing imports. The actual domain models are split by responsibility under `src/core/domain_models/` so new models should be added to the smallest matching module and re-exported through the facade only when they cross package boundaries.

Important model groups:

#### Category models

```text
CategoryManifest
CategoryLlmProfile
CategoryCapability
CategoryProperty
CategoryUiSection
CategoryActionDeclaration
CategoryWorkflowDeclaration
CategorySetupRequirement
CategoryResolution
CategoryItem
MediaCategoryItem
TvShowItem
MovieItem
GenericMediaItem
ItemList
CategorySpec
CategorySpecProperty
CategorySpecUnit
CategoryScaffoldPreview
```

#### Action and safety models

```text
ActionReceipt
ActionSource
ActionCommand
ActionResult
SafePathDecision
SafeFileOperation
SecurityConfirmationRequest
SecurityAuditEvent
SecurityConfig
```

#### Storage models

```text
StorageConfig
StoragePathUsage
StorageVolumeUsage
StorageCapacityDecision
StorageReport
```

#### Search and download models

```text
SearchResult
SearchAggregateResult
ProviderSearchDiagnostics
TorrentScrapeCandidate
TorrentScrapeResult
NormalizedTorrentCandidate
DownloadItem
DownloadFileInfo
DownloadStatus
DownloadPriority
QualityProfile
```

#### Web/search/research models

```text
WebSearchConfig
WebSearchHit
WebSearchResult
WebSearchHealth
BrowserHealth
BrowserFetchRequest
BrowserFetchResult
PageLink
ChallengeDetection
WebEvidence
WebResearchReport
Fact
ExtractedFacts
```

#### Assistant/runtime models

```text
Intent
AgentBudget
AgentRunContext
PreparedAgentRun
AgentPlan
PlanStep
PlanExecutionResult
ToolExecutionContext
AgentStreamEvent
AgentLoopState
AssembledToolCall
```

## 5. Category Architecture

### Category package

```text
src/core/categories/
  base.py             Base abstractions and category lifecycle hooks.
  registry.py         Built-in/custom category registration and lookup.
  tv.py               Built-in TV category.
  movie.py            Built-in Movie category.
  path_planner.py     Category-owned naming/path planning.
  consolidator.py     Library consolidation/organization helpers.
  verifier.py         Media verification.
  language.py         Language tagging/detection helpers.
  search_patterns.py  Generic category search-pattern helpers.
  scaffold.py         Category scaffold/spec preview service.
  metadata/           Category metadata provider abstractions/implementations.
  prompts/            Category-specific prompt snippets.
  custom/             Custom category import/discovery target.
```

### Important category classes

#### `MediaCategory` — `src/core/categories/base.py`

Base class for all categories. Owns the contract for:

- Manifest creation.
- LLM profile/prompt exposure.
- Item creation/parsing.
- Search profile construction.
- Category actions/workflows.
- Unit semantics.
- Category-specific display and setup requirements.

#### `CategoryRegistry` — `src/core/categories/registry.py`

Registers built-ins and discovers category modules. Most code should ask the registry for category behavior instead of importing TV/Movie classes directly.

Use this when code needs to:

- List categories.
- Resolve `category_id` to a `MediaCategory`.
- Register defaults.
- Discover custom categories.

#### `TvShowCategory` — `src/core/categories/tv.py`

Owns TV-specific behavior, including episode/unit interpretation, TV metadata expectations, TV prompts, and TV actions/workflows. TV-specific terms like seasons, episodes, SxxExx, and TVMaze should remain here or in TV-owned metadata/workflow modules.

#### `MovieCategory` — `src/core/categories/movie.py`

Owns movie-specific behavior, including single-item movie semantics, movie metadata expectations, Movie prompts, and movie actions/workflows. Movie-specific deletion, upgrade, and metadata behavior belongs here or in movie-owned providers/workflows.

#### `CategoryPathPlanner` — `src/core/categories/path_planner.py`

Computes category-owned target paths for organized media. It should not perform unsafe raw filesystem writes. Path execution should pass through security helpers.

#### `CategoryScaffoldService` — `src/core/categories/scaffold.py`

Builds safe previews for generated/custom categories from `CategorySpec`. This is the controlled path for category creation rather than freehand LLM code generation.

### Category lifecycle and suggestion policy

`src/core/category_lifecycle.py` contains `CategoryLifecycleEngine`, the generic gate for item-scoped provider work and suggestion compilation. It computes fingerprints for metadata, library units, persisted taste snapshots, and saved suggestions, then stores due/validity state in the lifecycle ledger tables. The scheduler and suggestion compiler must ask this engine before running expensive category workflows.

The engine is deliberately category-neutral. Domain cadence belongs in category hooks:

```text
MediaCategory.lifecycle_policy()
MediaCategory.lifecycle_decision(item, context)
MediaCategory.suggestion_policy()
```

The existing `suggested_actions` table still backs the current UI. `category_item_suggestion_state` adds item-scoped validity, fingerprints, and policy-version tracking so suggestions can be reused until meaningful inputs change.


### Category-owned taste metadata

`src/core/taste_profiler.py` builds `TasteProfile` from generic category items and
metadata envelopes, but it must not know how built-in categories fetch metadata.
The profiler receives `CategoryRegistry`, resolves the owning category for each
item, and calls:

```text
MediaCategory.enrich_taste_metadata(item, context)
```

The default base-category implementation returns `None`. Built-in and custom
categories opt in by overriding that hook and choosing their own metadata
provider. For example, the Movie category may use feature metadata and the TV
category may use episodic metadata, but that decision stays inside
`src/core/categories/movie.py`, `src/core/categories/tv.py`, or category-owned
metadata modules.

Generic code must not call provider-specific methods such as feature/series
enrichers directly and must not branch on `movie` or `tv` to build taste data.
The architecture guard treats those provider names as category-bound symbols.

``TasteMetadataRuntimeContext`` is an opaque carrier for dependencies such as
metadata enrichers or provider clients. Categories may read collaborators they
understand; the profiler only passes the context through.

### Category ownership rule

Allowed places for TV/Movie-specific knowledge:

```text
src/core/categories/tv.py
src/core/categories/movie.py
src/core/categories/metadata/*
src/core/categories/prompts/*
tests that explicitly validate TV/Movie behavior
```

Disallowed places for permanent category-specific branching:

```text
src/ai/assistant.py
src/ai/tool_policy.py
src/ai/tool_catalog.py
src/core/database.py
src/core/repositories/*
src/core/scheduler.py
src/web/routers/*, except generic category endpoints
src/web/static/js/*, except manifest-driven rendering
```

## 6. Persistence and Repositories

### `src/core/database.py`

Owns SQLite initialization and low-level database setup. The fresh-install architecture uses category-first tables instead of legacy show/movie-specific tables.

Core generic category storage concepts:

```text
category_items
category_item_units
category_item_metadata
category_property_index
```

### `src/core/repositories/`

Repository files:

```text
base.py      Shared repository infrastructure.
media.py     Category item and unit persistence.
download.py  Download persistence.
system.py    System-level state.
user.py      User/session-related persistence.
```

Repository rule: use `category_id`, `item_id`, and `unit_key` for category identity. Do not introduce global `show_name`-style identity back into repositories.

### Fresh-install initialization order

The startup sequence in `main.py` must keep database setup before every stateful service:

```text
SettingsManager loads config
  -> Database.initialize()
     -> _create_base_schema()
     -> _get_schema_version()
     -> _run_migrations()
     -> repository objects are attached
  -> StateCoordinator.sync_category_items()
  -> preference/blacklist/vector/auth/scheduler services
  -> web app accepts requests
```

No service should insert, update, or query app tables before `Database.initialize()` completes. New startup services should receive `db` only after repositories have been attached.

## 7. Assistant Runtime and Tooling

### `src/ai/assistant.py`

Important classes:

```text
AgentDependencies
ExecutionContext
AIAssistant
```

`AIAssistant` coordinates user messages, context construction, tool use, streaming, and final responses. It should depend on injected services through `AgentDependencies`, not on globals.

### Assistant run preparation

Key files:

```text
run_preparer.py          AgentRunPreparer: builds per-run context.
intent_router.py         IntentRouter: fast/LLM fallback intent classification.
category_resolver.py     CategoryResolver: maps user intent to category.
prompt_builder.py        PromptBuilder: builds system/developer/user context.
memory_composer.py       PromptMemoryComposer: conversation/preference context.
token_budget.py          TokenBudgetManager: prompt/tool result budgeting.
```

The storage-aware package injects storage information into the assistant context when enabled, so the LLM can account for disk pressure without needing to call a tool first.

### Tool registry and policy

Key files:

```text
tool_catalog.py       AgentToolCatalog builds the registry from providers.
tool_registry.py      ToolDefinition and ToolRegistry.
tool_policy.py        AgentToolPolicy selects which tools are exposed.
tool_executor.py      ToolCallExecutor executes selected tools.
category_tool_factory.py CategoryScopedTool and category tool creation.
```

Tool providers live in `src/ai/tools/`:

```text
categories.py   Category item/action tools.
downloads.py    Download/search/start tools.
library.py      Library scan/organization tools.
preferences.py  Preference tools.
research.py     Metadata/research-oriented tools.
scheduling.py   Scheduler/prompt-scheduler tools.
storage.py      Storage report/capacity tools.
web.py          Web search/read/browser tools.
```

Tooling rule: the LLM should receive narrow typed tools, not raw shell or raw filesystem access.

### Browser and web research

Key files:

```text
src/ai/web_reader.py          Clean text/link extraction.
src/ai/browser_session.py     Bounded browsing session state.
src/ai/browser_tools.py       Browser tool provider.
src/ai/web_researcher.py      Higher-level research coordinator.
src/utils/browser/runtime.py  Playwright runtime.
src/utils/browser/domain_policy.py Per-domain browser concurrency/cooldowns.
src/utils/browser/challenge_detector.py Challenge/captcha/interstitial detection.
```

## 8. Search Architecture

### Torrent search

```text
src/search/base.py             SearchProvider abstraction.
src/search/aggregator.py       SearchAggregator.
src/search/jackett.py          Primary Jackett native JSON torrent provider.
src/search/torznab.py          Optional Torznab-compatible provider for advanced non-default deployments.
src/search/jackett_manager.py  Install/start/configuration support for local Jackett.
src/search/jackett_indexer_config.py Default indexer configuration helpers.
src/search/browser_strategy.py Browser-backed torrent scraping strategies.
src/search/btdigg.py          Direct fallback provider.
src/search/search_1337x.py     Direct fallback provider.
src/search/torrentgalaxy.py    Direct fallback provider.
src/search/nyaa.py             Direct fallback provider.
src/search/rss_monitor.py      Jackett RSS monitor.
```

Normal search policy:

```text
Healthy Jackett -> use Jackett as primary search path.
No healthy Jackett and direct_scraper_fallback=true -> use direct fallback providers.
No healthy provider -> report degraded search instead of pretending search is healthy.
```

### General web search

```text
src/search/web/base.py             WebSearchProvider abstraction.
src/search/web/service.py          WebSearchService provider selection/health.
src/search/web/brave.py            Brave Search API provider.
src/search/web/tavily.py           Tavily provider.
src/search/web/kagi.py             Kagi provider.
src/search/web/searxng.py          SearXNG provider.
src/search/web/duckduckgo_html.py  Last-resort degraded fallback parser/provider.
```

General web search should prefer configured providers such as Brave, Tavily, Kagi, or SearXNG. DuckDuckGo HTML should be understood as degraded fallback, not a reliable primary API.

## 9. Download, Queue, Scheduler, and Library Flow

### Download stack

```text
src/core/downloader.py                  DownloadManager and DownloadDependencies.
src/core/downloader_lifecycle.py        Lifecycle state handling.
src/core/downloader_start_coordinator.py Start/queue orchestration.
src/core/downloader_progress_cache.py   Progress snapshots.
src/core/downloader_monitor_registry.py Monitor callbacks.
src/core/torrent_engine.py              Torrent engine boundary.
src/core/queue_manager.py               Queue persistence and scheduling.
src/core/bandwidth_manager.py           Speed limits.
src/core/torrent_resolver.py            Torrent URL/magnet resolution.
src/core/torrent_racer.py               Candidate racing and cleanup.
```

The download stack now receives `StorageMonitor` so it can block or warn before starting work that would violate configured free-space reserves.

### Scheduler and category automation

```text
src/core/scheduler.py        MediaScheduler and SchedulerDependencies.
src/core/search_pipeline.py  Search orchestration helpers.
src/core/bundle_download.py  Category-neutral bundle/pack selective download handling.
src/core/air_date_monitor.py Air-date sync/monitoring.
src/core/suggestion_compiler.py Suggestions.
src/core/recommender.py     Recommendation engine.
src/core/taste_profiler.py  Taste profile construction.
```

The scheduler should be category-driven. It should ask categories what workflows they support rather than branching globally on TV/movie behavior.

### Library organization

```text
src/core/librarian.py              Organization/verification facade.
src/core/download_handler.py       Ready/complete download callback handler.
src/utils/library_scanner.py       Library scanning.
src/core/categories/path_planner.py Category target path computation.
src/core/categories/consolidator.py Library consolidation.
src/core/categories/verifier.py    Media verification.
```

Any operation that moves, links, deletes, or copies files must pass through security path validation.

## 10. Security Architecture

Security files:

```text
src/core/security/path_policy.py      SafePathResolver and SecurityPolicyError.
src/core/security/command_policy.py   CommandPolicy and CommandPolicyError.
src/core/security/confirmation.py     SecurityConfirmationService.
src/core/security/action_risk.py      ActionRiskPolicy.
src/core/security/audit.py            SecurityAuditLogger.
SECURITY.md                           Human-readable security model.
scripts/check_security_architecture.py Guard against unsafe patterns.
```

### Safe path model

The LLM, user requests, metadata, database paths, and downloaded filenames are all treated as untrusted. Code should resolve paths through `SafePathResolver` before writing, deleting, copying, linking, or cleaning up.

Important protections:

```text
Allowed roots only.
Resolved path containment checks.
Traversal rejection.
Symlink escape rejection.
Quarantine/trash-first deletion where appropriate.
No raw destructive file calls in high-level code.
```

### Command model

No generic shell tool should exist. Subprocess calls should use `CommandPolicy`, `shell=False`, and allowlisted executable/argument shapes.

Never introduce:

```text
os.system(...)
subprocess.run(string_command, shell=True)
raw rm/mv/cp shell construction
freeform shell tools for the LLM
```

### Destructive confirmations

Destructive actions should be two-phase:

```text
plan -> policy check -> dry-run ActionReceipt -> user confirmation -> execute -> final ActionReceipt
```

`SecurityConfirmationService` binds confirmation tokens to exact action details and affected paths.

## 11. Storage Architecture

Storage files:

```text
src/core/storage.py              StorageMonitor and StoragePathTarget.
src/ai/tools/storage.py          LLM storage tools.
src/web/routers/storage.py       `/api/storage/status` and capacity check API.
STORAGE.md                       Human-readable storage model.
```

### Storage model

Storage is category-aware and volume-aware:

```text
category roots -> resolved paths -> underlying filesystem/device -> grouped volume report
```

This matters because categories may share a disk or live on separate disks. The UI and assistant should show volume-level pressure intelligently instead of repeating the same disk three times.

### Storage integrations

```text
Dashboard UI       Displays Storage Watch.
Settings/Compass   Displays storage panel.
/api/health         Degrades when storage is critical.
AIAssistant         Receives prompt context summary when enabled.
LLM tools           get_storage_status and check_storage_capacity.
DownloadManager     Checks capacity before queueing downloads.
```

## 12. Web UI Architecture

### Templates and static files

```text
src/web/templates/base.html
src/web/templates/index.html
src/web/templates/library.html
src/web/templates/settings.html
src/web/templates/setup.html
src/web/static/css/style.css
src/web/static/js/app.js
src/web/static/js/pages/setup.js
src/web/static/js/pages/dashboard.js
src/web/static/js/components/
```

### Routers

```text
src/web/routers/categories.py       Category manifests/listing.
src/web/routers/category_items.py   Generic category item CRUD/actions.
src/web/routers/storage.py          Storage status and capacity checks.
src/web/routers/setup.py            Setup requirements/completion.
src/web/routers/health.py           Runtime health.
src/web/routers/providers.py        Provider status/configuration.
src/web/routers/system.py           System/Jackett endpoints and Trakt OAuth.
src/web/routers/downloads.py        Download UI/API.
src/web/routers/actions.py          Action gateway API.
src/web/routers/library.py          Library scanning/organization.
src/web/routers/settings.py         Settings.
src/web/routers/suggestions.py      Suggestions.
src/web/routers/upgrades.py         Upgrade approvals.
src/web/routers/pages.py            HTML page routes.
```

UI rule: prefer manifest-driven category rendering. Do not add hardcoded `/api/shows` or static TV/movie assumptions in global JavaScript.

Trakt is presented differently from API-key providers: LJS ships with a public bundled Trakt client ID for PKCE/OOB login, while user-specific access and refresh tokens are created only after account authorization. The setup and settings UI should explain this distinction and keep custom Trakt client IDs as an advanced option.

## 13. Communication Bridges

```text
src/web/discord_bridge.py
src/web/telegram_bridge.py
src/web/whatsapp_bridge.py
src/web/comms.py
```

Bridges should call the same `AIAssistant` and notification services used by the web UI. They should not get separate tool execution paths.

## 14. Configuration

### `src/core/config.py`

`SettingsManager` loads live global YAML from ignored `config/settings.local.yaml`, bootstraps it from tracked `config/settings.template.yaml`, migrates legacy `config/settings.yaml` when present, loads live category YAML from ignored `config/categories/<category_id>.yaml`, and bootstraps missing category files from tracked `config/category-templates/<category_id>.yaml`.

Important nested configs:

```text
LLMConfig
WebSearchConfig
StorageConfig
SecurityConfig
QualityProfile
```

### Environment and YAML

```text
.env.example
config/settings.template.yaml
config/settings.local.yaml        # ignored live file
config/category-templates/tv.yaml
config/category-templates/movie.yaml
config/categories/*.yaml          # ignored live files
```

Environment variables may be used for deployment overrides, but normal UI/setup edits write to ignored local files. `config/settings.template.yaml` and `config/category-templates/*.yaml` are public defaults and must not contain secrets. `config/settings.local.yaml` stores live global configuration such as setup completion, global download folder, LLM provider, web-search provider, storage thresholds, and global integrations. Category-specific live paths, naming templates, scheduler defaults, metadata-provider usage, and storage overrides live in ignored `config/categories/<category_id>.yaml`; services consume the merged effective map through `Settings.category_settings`.

## 15. Tests and Guard Scripts

### Tests

Tests mirror source areas. Important test groups include:

```text
tests/test_category_first_architecture_guards.py
tests/test_security_path_policy.py
tests/test_security_command_policy.py
tests/test_security_confirmation.py
tests/test_security_architecture_guards.py
tests/test_storage_monitor.py
```

### Scripts

```text
scripts/check_category_architecture.py
scripts/check_security_architecture.py
```

These scripts are architecture contracts. Run them before packaging changes.

Recommended local validation sequence:

```bash
python -m compileall -q src tests scripts
python scripts/check_category_architecture.py
python scripts/check_security_architecture.py
pytest
```

## 16. Common Flows

### User asks assistant to download an item

```text
AIAssistant.run()
  -> AgentRunPreparer builds context
  -> IntentRouter classifies intent
  -> CategoryResolver chooses category if needed
  -> AgentToolPolicy selects allowed tools
  -> CategoryToolFactory exposes category tools
  -> SearchAggregator queries JackettSearch first, then explicit direct fallback if enabled
  -> TorrentSelectionService normalizes/selects candidates
  -> DownloadManager checks storage and queues torrent
  -> DownloadCompletionHandler receives ready/complete callbacks
  -> Librarian/CategoryPathPlanner computes target paths
  -> SafePathResolver validates filesystem operations
  -> ActionReceipt / events / notifications / UI updates
```

### Setup wizard loads requirements

```text
SetupRouter
  -> CategoryRegistry.list_categories()
  -> each CategoryManifest.setup_requirements
  -> settings/web-search/jackett/storage status
  -> setup UI explains what is required and why
```

### UI executes a category action

```text
CategoryItemsRouter or ActionsRouter
  -> ActionGateway
  -> ToolRegistry / registered action handler
  -> ActionRiskPolicy if destructive
  -> SecurityConfirmationService if confirmation required
  -> category workflow/action
  -> ActionReceipt
  -> ActionEventStore / ShipEventBus
```

### Storage report is shown and injected into LLM context

```text
StorageMonitor.build_report()
  -> resolve category roots and download roots
  -> group by filesystem/device
  -> compute free/reserved/critical state
  -> StorageRouter returns API response
  -> dashboard/settings render status
  -> PromptBuilder/assistant context includes summary
  -> StorageToolProvider exposes detailed tools
```

## 17. Where To Add New Things

### New category

```text
src/core/categories/custom/<category_name>.py
src/core/categories/prompts/<category_name>.md
```

Use `CategorySpec` and `CategoryScaffoldService` first. Register/discover through `CategoryRegistry`.

### New category property

Add to the category manifest/spec. Store values in `properties_json` and index via `category_property_index` if needed. Do not add schema columns for one category's custom property.

### New metadata provider

```text
src/core/categories/metadata/<provider>.py
src/core/categories/metadata/enricher.py
```

Provider-specific code should stay behind category metadata abstractions. Generic core code may depend on category metadata interfaces, but TMDB/TVMaze/Trakt semantics must remain in category-owned providers or integration clients.

### New torrent provider

```text
src/search/<provider>.py
```

Inherit the search provider abstraction and return normalized `SearchResult` objects. Add fixtures/tests.

### New general web search provider

```text
src/search/web/<provider>.py
```

Inherit `WebSearchProvider`. Wire through `WebSearchService` and `WebSearchConfig`.

### New LLM tool

```text
src/ai/tools/<domain>.py
```

Add a provider class or extend the existing domain provider, then register through `AgentToolCatalog`. Do not register tools in random modules.

### New web API area

```text
src/web/routers/<domain>.py
src/web/action_handlers/<domain>.py
```

Add dependencies to `WebDependencies` if needed and wire the router in `create_app()`.

### New model

Usually add it to `src/core/models.py`. Keep model classes documented and typed.

## 18. Architecture Smells

Investigate immediately if you see:

```text
show_name as global identity outside TV category code.
/api/shows or ShowsRouter returning.
TV/movie branches in scheduler, assistant, repositories, or generic web routers.
LLM tools that directly expose shell commands.
os.system or shell=True.
Path.unlink, shutil.rmtree, os.remove, or os.link without safe path handling.
New category-specific database columns.
Direct scraper fallback silently hiding broken Jackett.
DuckDuckGo HTML treated as a reliable configured web-search provider.
UI JavaScript hardcoding TV/movie screens instead of using manifests.
```

## 19. Mental Model For Future Work

When changing LJS, ask three questions:

1. Is this behavior generic platform behavior or category-owned behavior?
2. Is this operation safe if the LLM/user/metadata/database value is malicious or wrong?
3. Does this need to be visible to both UI and assistant through the same API/action/tool path?

If the answer is unclear, prefer a smaller category-owned workflow, a typed model in `src/core/models.py`, and a policy-checked execution path.


## Category Artwork Cache

Provider artwork discovered by a category is cached under `data/categories/<category_id>/metadata/artwork/<item_id>/`. The web app exposes this read-only via `/category-data/...`, and UI view models prefer `local_poster_url` before remote TMDB image URLs. Generic UI code should not download or reinterpret artwork directly; category metadata hooks own the discovery and cache step.

## 12. Round 18 Documentation and OOP Guardrails

Public extension points must now be documented in both backend and UI code:

```text
Python: module docstrings, every class, every public method/function.
UI JS: every direct class, public class method, and top-level public function.
```

Run this audit before packaging architecture changes:

```bash
python scripts/check_public_docs.py
```

The chat-facing download control layer is intentionally decomposed:

```text
DownloadSnapshotPresenter      serialization, queue ordering, health labels
DownloadControlSchema          LLM tool schema only
DownloadFilterPredicates       individual target filter checks
DownloadFilterResolver         safe target resolution / selection / confirmation
DownloadItemActionService      pause/resume/cancel/priority/health-test calls
DownloadQueueMoveService       queue order mutations
DownloadControlActionService   small mutation facade
ManageDownloadsTool            thin LLM tool orchestration wrapper
```

When extending download control, add fields to the schema, predicates to the resolver, and mutations to a focused service. Do not put new business rules directly into `ManageDownloadsTool` unless they are specifically tool-bound orchestration rules such as confirmation or dry-run output.
