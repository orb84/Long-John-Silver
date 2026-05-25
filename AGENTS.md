# Coding Skill — LJS Project

---

## 0. Read the Architecture Contract First

Before changing code, read `architecture.md`. It is the source of truth for how the library, category, lifecycle, suggestion, taste, agent, and UI systems are intended to interact. Update it in the same change whenever you alter those interactions.

### 0.1 Canonical Library Object Rule

The core app must never contain category-specific library semantics. Core code stores generic envelopes and asks the owning category to build canonical library objects from category specifications.

Never add logic like:

```python
if category_id == "tv":
    # inspect seasons/episodes here
```

Instead, add or improve category hooks:

- `library_object_spec()`
- `library_item_from_scan()`
- `library_units_from_scan()`
- `library_progress_from_scan()`
- `build_library_object()`
- `create_suggestion_workflow()`

Suggestions, UI, lifecycle fingerprints, taste context, and agent tools must read canonical library objects. They must not reinterpret raw `category_item_units`, title aliases, cleaned names, or progress rows as independent sources of truth.

### 0.2 No Alias/Progress Hacks

If a feature thinks an item is missing even though the library contains it, do not patch the feature by searching aliases or falling back to progress rows. That creates multiple conflicting truths. Fix the category's canonical object builder so every consumer sees the same state.

### 0.3 Category Specifications Drive Structure

Each category defines its own object complexity. TV may define seasons/episodes. Games may define versions/DLC/platform builds. Books may define volumes/editions/formats/chapters. The library core must not know any of those structures.


### 0.4 Physical Files vs Logical Objects

Never assume one storage unit equals one logical content unit. A category may
have multiple local files for one logical object. For example, alternate
quality releases, split files, subtitle sidecars, platform builds, DLC, or
audiobook parts must not overwrite each other because they share a logical
coordinate. Store physical/local file evidence with a stable file-oriented
`unit_key`; let the category build logical nested views from those units.

### 0.5 Cross-System Review Required

When changing scanner output, naming templates, consolidation, download
completion, suggestions, lifecycle, UI detail payloads, or agent tools, inspect
the other systems in the same pass. These are all interfaces to the same
canonical library object. Do not code in a bubble.

### 0.5.1 Descriptor-First Download Rule

Download and queue systems must use the category-owned `unit_descriptor` as the
primary handoff for category units. Generic code may sort or filter by the
conventional descriptor fields `stable_key`, `label`, `granularity`, and
`sort_key`, but it must not interpret descriptor `coordinates` or legacy columns
as category semantics. Legacy `season`/`episode` fields exist only for old rows
and old assistant schemas; do not add new logic that depends on them.


### 0.5.2 LLM Tool Philosophy: Few Generic Tools, Category-Owned Context

Do not add category-specific LLM micro-tools for ordinary download decisions.
The agent-facing download flow is deliberately small:

```text
category context packet / enquire_about_media
        → search_media_torrents
        → queue_download
```

Categories may still declare actions/workflows for UI buttons, suggestions,
scheduled jobs, and internal implementations, but those names must not become
the default LLM tool surface. The LLM should receive category-owned context
(local units, provider/release state, configured language, existing audio
languages, quality profile, bundle rules) and decide what to search or queue
using the generic tools.

When handling prompts such as “download the episodes I am missing from the
latest season,” pass the model enough category context to reason from:

- which units are already local;
- which provider units have aired/released;
- which units are missing;
- configured item language;
- existing audio/subtitle languages on local files;
- category instructions for bundles/packs and fallbacks.

Do not solve that by exposing `tv.find_missing_episodes`,
`tv.download_missing_batch`, `books.download_volume`, `games.queue_patch`, or
similar category-specific tools to the ordinary DOWNLOAD intent. Improve the
category context/search hooks instead. Search results may include batch
recommendations, but the chat loop must not silently auto-queue them without
the LLM evaluating language, quality, ambiguity, and user intent.

Seeders are not decorative. For any torrent-selection prompt, batch
recommendation, or queue decision, seeder availability is a first-class metric.
After hard constraints are satisfied (requested unit/pack coverage, configured
or acceptable multi-audio language, usable resolution, queueable magnet),
functionally equivalent candidates must prefer the healthier seeder count. Do
not describe a lower-seeded duplicate as “top-ranked” when a materially
higher-seeded equivalent exists.

Do not hard-code natural-language follow-up phrases such as confirmations,
selections, or “continue/proceed” variants. Users may speak any language and may
refer back to candidates several turns later. Persist recent result sets as
structured pending-action context (`result_set_id`, `candidate_id`,
`queue_download_arguments`) and let the LLM route/plan from that context.
Generic code may validate stable IDs and queueability, but it must not parse
user intent from English keyword lists.

### 0.6 Suggestion Actions Are Declared by Suggestions

Approval handlers must execute the category workflow declared by the suggestion
endpoint/body. Do not hardcode a category/action pair in generic suggestion
management.

### 0.7 Documentation Is Part of the Code

Any architectural change touching library objects, category contracts, lifecycle, suggestions, taste, or agent context must update `architecture.md` and any relevant docs under `docs/`.

Round 87 added `scripts/check_ai_intent_architecture.py`; run it after any assistant/router/planner change. It fails if natural-language intent/follow-up keyword detectors reappear in `src/ai`.


### 0.8 Planner/Tool Regression Audits

Before handing back a build after touching `src/ai`, run the targeted scenario
trace for the round and a private-attribute audit. Missing private methods or
attributes in planner/tool classes are release-blocking regressions, not
acceptable runtime surprises.


You are operating on the **Long John Silver (LJS)** codebase. Follow these standards rigorously. They are not suggestions — they are rules.

---

## 1. Object-Oriented Programming Is Mandatory

### 1.1 Everything Lives in a Class

There are no standalone functions in `src/`. Every function is a method on a class. The only exceptions are:
- Pydantic models and enums in `models.py`
- Pure data transformations in `utils/` that are genuinely stateless and reusable — and even those should be grouped into a class if they share a domain.

### 1.2 Small, Focused Classes

- **One responsibility per class.** If a class does two unrelated things, split it.
- **Target: 50–150 lines per class.** If a class exceeds 200 lines, it's doing too much. Extract a collaborator.
- **Many small classes > few large classes.** A `DownloadManager` that also handles file organization should become `DownloadManager` + `Librarian`.
- **Shared behavior goes into base classes or composition.** If three search providers all parse HTML the same way, extract a `HTMLParserMixin` — don't copy-paste the parsing logic.

### 1.3 Core OOP Principles

| Principle | Rule |
|-----------|------|
| **Encapsulation** | Internal state is private (`_prefix`). Public interface is minimal and explicit. No reaching into another object's internals. |
| **Single Responsibility** | Each class owns one domain. `DownloadManager` manages downloads. `Librarian` organizes files. `SmartQualityInferrer` infers quality. Never conflate. |
| **Open/Closed** | New search providers are added by creating a new class inheriting `SearchProvider`. New tools by calling `registry.register()`. No modifying existing classes to add new providers. |
| **Liskov Substitution** | Any `SearchProvider` subclass must work anywhere the base class is expected. All must implement `search()` and `health_check()`. |
| **Dependency Inversion** | High-level modules (`AIAssistant`, `MediaScheduler`) depend on abstractions (`SearchProvider`, `ToolRegistry`), not concrete implementations (`BTDiggSearch`). Inject dependencies via constructor. |

### 1.4 Composition Over Inheritance

- Use inheritance **only** for IS-A relationships (`JackettSearch` IS-A `SearchProvider`).
- Use composition for HAS-A relationships (`AIAssistant` HAS-A `ToolRegistry`, `PreferenceManager`).
- Never go deeper than 2 levels of inheritance. If you need 3, redesign with composition.

### 1.5 No God Classes

- No class may directly import more than 5 other classes from `src/`. If it does, it's a coordinator — extract the sub-responsibilities.
- No method may exceed 30 lines. If it does, extract helper methods with descriptive names.

### 1.6 Dependency Injection

All service objects receive their dependencies via `__init__` parameters. No `from src.core.database import db` global singletons. The `main.py` entry point is the single composition root that wires everything together.

```python
# GOOD: Dependencies injected
class MediaScheduler:
    def __init__(self, downloader: DownloadManager, assistant: AIAssistant, ...):
        self._downloader = downloader
        self._assistant = assistant

# BAD: Reaching into globals or creating inside
class MediaScheduler:
    def __init__(self):
        self._downloader = DownloadManager(...)  # Where do settings come from?
```

---

## 2. Zero Duplication (DRY) — Absolute Rule

### 2.1 Before Writing Any Code

Search the entire codebase first. Use grep/glob to find:
- Methods with similar names (`def search`, `def find`, `def query`)
- Similar logic patterns (HTTP client creation, error handling, data parsing)
- Existing utilities in `src/utils/` or `src/core/` that already do what you need

### 2.2 If You Find 80% of What You Need

Refactor the existing function/class to be more generic — add parameters, extract the common core, and have both callers use it. Do NOT create a new function that duplicates 80% of an existing one.

### 2.3 Shared Utilities

If logic is used in 2+ places, it belongs in a shared location:
- **Data models** → `src/core/models.py`
- **Database operations** → `src/core/database.py`
- **Quality/logic helpers** → `src/utils/quality.py`, `src/utils/media_parser.py`
- **Base classes** → `src/search/base.py`, `src/ai/tool_registry.py`

### 2.4 No Copy-Paste Adaptation

If you find yourself copying a block of code and changing 2–3 lines to adapt it, extract a parameterized function instead.

---

## 3. Documentation — Comment Everything

### 3.1 Every File Has a Module Docstring

```python
"""
Module name for LJS.

One-line summary of what this module does and why it exists.
"""
```

### 3.2 Every Class Has a Docstring

```python
class ClassName:
    """One-line summary of the class's responsibility.

    Longer description if needed: what problem does this class solve,
    what are its key collaborators, and any non-obvious design decisions.
    """
```

### 3.3 Every Public Method Has a Docstring

```python
def method_name(self, param1: str, param2: int) -> bool:
    """One-line summary of what this method does.

    Args:
        param1: Description of param1.
        param2: Description of param2.

    Returns:
        Description of the return value.

    Raises:
        ValueError: When param1 is invalid.
    """
```

### 3.4 Inline Comments for Non-Obvious Logic

- **Always explain WHY, not WHAT.** The code shows what; the comment explains why.
- **Always comment:**
  - Workaround for external library bugs
  - Non-obvious algorithmic choices (e.g., "1.3x multiplier accounts for quality variance")
  - Business rules (e.g., "# CAM releases are filtered because they're recorded in theaters")
  - Magic numbers → extract to named constants
- **Never comment** what the code trivially does:
  ```python
  # BAD
  x = x + 1  # increment x

  # GOOD
  RETRY_BACKOFF_SECONDS = 2  # exponential backoff for provider failures
  ```

### 3.5 Update Comments When You Update Code

If you change what a function does, update its docstring. If you change a business rule, update the comment explaining it. Stale comments are worse than no comments — they mislead.

**Rule:** Every code change must be accompanied by a documentation update for that code. No exceptions.

### 3.6 Type Hints on Everything

Every function signature must have type hints for all parameters and return types.

```python
# GOOD
async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:

# BAD
async def search(self, query, max_results=10):
```

---

## 4. Change Tracking — PROGRESS.md

### 4.1 The PROGRESS.md File

Maintain durable work notes under `docs/project-history/` and keep `architecture.md` current for architecture changes. This is the single source of truth for what has changed, what problems exist, and what features are in flight.

### 4.2 Format

```markdown
# LJS Progress Log

## Active Issues
- [ISSUE-XXX] Short description of the problem. Status: investigating/in-progress/resolved.

## Changes

### YYYY-MM-DD — Brief Change Description
- **What:** What was changed (file, class, method).
- **Why:** Why it was changed (bug fix, new feature, refactor).
- **Files:** `path/to/file.py`, `path/to/other.py`

### YYYY-MM-DD — Previous Change
...
```

### 4.3 When to Update

- **Every commit** must have a corresponding PROGRESS.md entry.
- **Every bug found** gets logged in Active Issues before being fixed.
- **Every feature started** gets logged with its status.
- **When a bug is resolved**, move it from Active Issues to the Changes section with the fix description.

---

## 5. Code Style Rules

| Rule | Standard |
|------|----------|
| Python version | 3.10+ (use `X \| Y` unions, `match/case`, etc.) |
| Line length | 120 characters max |
| String quotes | Double quotes for docstrings, single quotes for code |
| Imports | Stdlib → third-party → local (`src.`), grouped with blank lines |
| Logging | `loguru.logger` everywhere. Never `logging` or `print()`. |
| Paths | Always `pathlib.Path`, never `os.path` |
| Data models | Pydantic `BaseModel` for anything crossing module boundaries |
| Async | All I/O-bound operations are `async`. Blocking calls use `asyncio.to_thread()`. |
| Error handling | Never bare `except:`. Always catch specific exceptions. Log and degrade gracefully. |
| Constants | Named constants at module level, ALL_CAPS. No magic numbers in logic. |

---

## 6. File Organization

```
src/
  ai/          — Agent logic: assistant, intent router, prompt builder, tool registry, reasoning
  core/        — Domain logic: config, database, scheduler, preferences, behavior tracker, conversation, vector store, downloader, recommender, smart quality, notifications
    categories/— Media categories subclasses (base.py, tv.py, movie.py, path_planner.py)
  integrations/— External APIs: tmdb, tvmaze, trakt
  llm_providers/— LLM abstraction: client, registry, catalog, key store, presets
  search/      — Search providers: base class, torznab, jackett_manager, fallback providers, aggregator, rss_monitor
  subtitles/   — Subtitle services: opensubtitles
  utils/       — Shared utilities: auth, blacklist, quality, media_parser, library_scanner, release_groups
  web/         — UI layer: FastAPI app, discord bridge, telegram bridge, templates, static
  core/models.py — All Pydantic models and enums (single source of truth)
config/
  personas/              — AI persona packages (`persona.md`, `persona.json`, `avatar.png`, `theme.json`)
  settings.template.yaml — Tracked fresh-install global template; no secrets
  settings.local.yaml    — Ignored live global settings created on first launch
  category-templates/    — Tracked category defaults (`tv.yaml`, `movie.yaml`, custom templates)
  categories/            — Ignored live category settings edited by setup/Compass
migrations/    — Database migration SQL files
tests/         — Mirror src/ structure
docs/project-history/ — Ignored local work notes and round logs
architecture.md — Living architecture contract
docs/          — Current architecture map and working-practices guide
```

### New File Rules

- A new media category subclass → `src/core/categories/category_name.py`, inherits `MediaCategory` and defines custom `CategoryProperty` descriptors.
- A new search provider → `src/search/provider_name.py`, inherits `SearchProvider`
- A new tool → add handler method to `AgentTools` or a new tool class, register in `tool_registry.py`
- A new integration → `src/integrations/provider.py`, standalone client class
- A new model → `src/core/models.py` (add to existing file, don't create separate model files)
- A new utility → `src/utils/utility_name.py`, only if used by 2+ modules

---

## 7. Testing Rules

- Every new class gets a corresponding test file in `tests/`
- Test file mirrors the source path: `src/core/scheduler.py` → `tests/test_scheduler.py`
- Use `pytest-asyncio` for async tests
- Mock all external services (LLM calls, HTTP requests, database)
- Test the public interface, not private methods
- Every bug fix must include a regression test
---

## 8. Project Source-of-Truth Documents

Before changing architecture, security, storage, setup, category behavior, or user-facing workflows, read the current project docs and keep them updated with the code change.

| Document | Must be updated when... |
|---|---|
| `docs/CODEBASE_ARCHITECTURE_MAP.md` | You add, move, rename, or substantially change packages, classes, routers, repositories, category workflows, tool providers, setup flows, or service wiring. |
| `docs/PROJECT_WORKING_PRACTICES.md` | You change development conventions, review checklists, category-first rules, test expectations, setup rules, or extension patterns. |
| `SECURITY.md` | You change safe-path policy, command policy, destructive confirmations, audit logging, filesystem operations, subprocess usage, or action risk handling. |
| `STORAGE.md` | You change storage monitoring, volume grouping, capacity checks, category-root storage mapping, download preflight rules, or LLM storage context. |
| `skills/category_creation_guide.md` | You change the category manifest contract, category scaffold format, custom-category validation, or generated category templates. |
| `README.md` | You change fresh-install setup, user-visible configuration, service requirements, or common operating instructions. |
| `PROGRESS.md` | Every meaningful code or documentation change. |

A change is incomplete if the code and the relevant source-of-truth document disagree.

---

## 9. Category-First Enforcement Rules

TV and Movie are built-in categories, not global application modes.

Allowed places for TV/Movie-specific behavior:

```text
src/core/categories/tv.py
src/core/categories/movie.py
src/core/categories/workflows/*
src/core/categories/metadata/*
src/core/categories/prompts/*
src/integrations/* clients that wrap external service APIs
tests that explicitly validate TV/Movie category behavior
```

Disallowed in generic layers unless the name is only explanatory documentation:

```text
src/ai/assistant.py
src/ai/tool_policy.py
src/ai/tool_catalog.py
src/ai/tools/* as global TV/Movie tools
src/core/database.py as category-specific schema
src/core/repositories/* as category-specific storage
src/core/scheduler.py as permanent TV/movie branching logic
src/core/state_coordinator.py
src/web/routers/* except generic category routers
src/web/static/js/* except manifest-driven rendering
```


Category-owned metadata and taste-profile rule:

- `src/core/taste_profiler.py` is generic aggregation only. It may resolve a category through `CategoryRegistry` and call `category.enrich_taste_metadata(item, context)`, but it must not branch on built-in category IDs or call feature/series/TMDB-specific provider methods directly.
- Built-in and custom categories own metadata-provider choices for taste profiling by overriding `MediaCategory.enrich_taste_metadata()`.
- If you change taste metadata flow, update `docs/CODEBASE_ARCHITECTURE_MAP.md`, `docs/PROJECT_WORKING_PRACTICES.md`, tests, and `scripts/check_category_architecture.py`.

Do not add identifiers such as `sync_tracked_shows`, `tracked_shows`, `show_progress`, `/api/shows`, `get_show_progress`, `delete_episode`, `delete_movie`, `get_tmdb_details`, or `get_tvmaze_show` outside the category boundary. Run:

```bash
python scripts/check_category_architecture.py
```

---

## 10. Fresh-Install Configuration And Secrets

The repository must never ship real user settings, tokens, API keys, Trakt tokens, Jackett keys, LLM keys, library history, or tracked items.

The repository tracks templates only:

- `config/settings.template.yaml` is the public fresh-install global template.
- `config/settings.local.yaml` is the ignored live settings file created on first launch.
- `config/settings.yaml` is the legacy live path and must stay ignored; startup migrates it to `config/settings.local.yaml` when present.
- `config/category-templates/<category_id>.yaml` stores public category defaults.
- `config/categories/<category_id>.yaml` stores ignored live category settings edited by setup/Compass.

Tracked templates may contain harmless defaults and public category policy, but they must not contain:

- `setup_complete: true`
- `tracked_items` with real library entries
- `trakt_access_token` or `trakt_refresh_token`
- TMDB, Jackett, Plex, OpenSubtitles, Discord, Telegram, WhatsApp, web-search, or LLM secrets
- password hashes or signed-session secrets
- absolute personal library paths

The Trakt client ID is different from an API secret. LJS can ship a public app client ID for PKCE/OOB login; user tokens are created only after authorization and must remain local.

When changing configuration ownership, update all affected docs in the same patch:

- `docs/CODEBASE_ARCHITECTURE_MAP.md` for where config lives and who consumes it.
- `docs/PROJECT_WORKING_PRACTICES.md` for how developers add/change config.
- `README.md` for user-facing setup examples.
- `STORAGE.md` if category paths/storage rules change.
- `PROGRESS.md` for the change log.

---

## 11. Database Initialization Rule

No service may insert, update, or query application tables before `await Database.initialize()` has completed.

Startup order must remain:

1. Load settings.
2. Create/connect database.
3. Create base schema.
4. Run any migrations above the base schema version.
5. Initialize repositories.
6. Only then run state synchronization, preference warmup, blacklist initialization, vector store initialization, auth/session operations, scheduler scans, or web app requests.

If you add a new startup service, verify it receives a fully initialized `Database` instance and does not access repositories before initialization.


## Category Artwork and Search Provider Rules

- Jackett is the primary torrent provider in the default application wiring. Use `src/search/jackett.py` for the managed Jackett JSON API path; keep `src/search/torznab.py` only as an optional advanced provider.
- Direct torrent scrapers are fallback providers. They must be explicit in settings/UI and should only be used after Jackett returns no usable candidates or when Jackett is unavailable.
- Category-discovered artwork must be stored under `data/categories/<category_id>/metadata/artwork/<item_id>/` and referenced through category metadata (`local_poster_path` / `local_poster_url`). Do not make generic UI code download artwork directly.
- When changing search or artwork behavior, update `docs/CODEBASE_ARCHITECTURE_MAP.md`, `docs/PROJECT_WORKING_PRACTICES.md`, `README.md`, and `PROGRESS.md`.

### 0.8 Round 73 Boundary Review Rules

- Category scan dataclasses and public scan models must both expose local payload evidence as `files`. Do not write new canonical object code that depends on the historical `detailed_episodes` name.
- Generic services must not choose a built-in category as a default. Missing category identity should become `media`, a failed validation, or a category-registry lookup, not an implicit TV/movie assumption.
- Download completion must call `category.download_target_for_item()` for normal path planning. The category extracts any structured unit fields it understands.
- Search preparation must go through `category.prepare_search_item()`. Do not add category-id branches to `SearchPipeline` for quality caps, provider quirks, or candidate shaping.
- Prefer `compute_target_path_from_fields()` for new path work. The legacy `compute_target_path(..., season, episode, ...)` wrapper exists only to avoid breaking older category code while it is migrated.

### 0.9 Round 74 Search and Cleanup Boundary Rules

- Treat the `episode_label` parameter in `SearchPipeline` as a historical name for an opaque category unit label. Do not parse it in generic code. Categories decide whether a label represents an episode, season pack, game version, book edition, album disc, or nothing.
- New search behavior must be added through category hooks: `build_search_query`, `build_alternative_search_queries`, `validate_search_result_for_request`, `quality_reference_for_search`, `discovery_already_satisfied`, and `download_coordinates_from_search_result`.
- Do not put file-size/bitrate heuristics in `SearchPipeline`. If a category can explain useful quality context from local canonical objects, it implements `quality_reference_for_search`.
- Do not map external integration media types to built-in categories in cleanup code. Categories must opt in through `matches_external_media_type(source, media_type)`.
- Do not read `detailed_episodes` from generic cleanup/listing code. Ask the category for `library_file_records_from_scan()` and use `file_record_matches_selector()` for any selector fields.
- Legacy download fields such as `season` and `episode` may still be passed through old APIs, but only category code may derive or interpret them. New APIs should prefer a category-owned unit descriptor.

### 0.10 Round 75 Download Unit Descriptor Rules

- Do not add new generic fields such as `season`, `episode`, `chapter`, `disc`, `track`, `version`, or `volume` to core download orchestration. If a category has structured units, expose them through a category-owned `unit_descriptor`.
- Search result caching, batch recommendations, queueing, duplicate checks, notifications, and UI receipts must preserve `unit_descriptor` end to end.
- Only categories may convert a descriptor into transitional legacy coordinates. Generic code may pass those coordinates through for compatibility but must not interpret them.
- Multi-unit recommendations must call `category.batch_group_for_candidate()`; never rebuild labels like `S01E03` in a generic tool.
- Queue fallback candidates must be matched through `category.candidates_represent_same_unit()` or descriptor stable keys before any legacy fallback.
- Seed-in-place/library-save-path planning must call `category.sharing_save_path_for_item()`. Generic downloader code must not create category subfolders such as seasons.

### 0.9 LLM Context Window Settings

When changing LLM routing or context assembly, keep endpoint maximum and user cap separate.

- The provider/model endpoint or catalog supplies the maximum context window when available.
- `llm.max_context_tokens` is a user cap and must be clamped to `min_selectable..endpoint_max`; `min_selectable` is normally 10,000 tokens unless the endpoint itself is smaller.
- `null` means “use the endpoint maximum for the selected model.”
- Values below the minimum selectable context window are raised to that minimum; do not reintroduce “0 disables context” because ordinary tool/candidate workflows need room to function.
- The Compass AI & LLM Gateway UI must expose this cap and show whether the maximum came from endpoint metadata or fallback.

Do not hardcode model context windows as the primary source of truth when the provider endpoint exposes model metadata. Static defaults are fallback only.

### 0.9.1 Compression-First Context Assembly

Do not implement normal long-context handling by deleting or blindly trimming old conversation turns. LJS context policy is **compression-first**.

- The provider/model endpoint maximum is the outer context limit when available.
- The user cap `llm.max_context_tokens` is applied separately and may be any value from the minimum selectable context window (normally 10,000 tokens) up to `endpoint_max`.
- The model context window includes output, so `reserved_output_tokens` must be subtracted before assembling prompt context.
- `llm.raw_recent_context_percent` defaults to `30`; keep that share of conversation-history budget as raw recent turns.
- Older conversation must be compressed into the remaining history budget. Stored summaries, deterministic compaction, or LLM summarization are valid; silent deletion is not.
- Final pre-provider budget enforcement may drop content only as a last-resort safety fallback after compression fails, and it must log that condition.

Any change to `ConversationManager`, `ConversationBinding`, `LLMTaskRuntime`, `TokenBudgetManager`, or AI Gateway context settings must keep this contract and update `scripts/round89_context_compression_tests.py` if the contract changes.


### 0.9.2 Round 90 Context Probing And Planner Budget Rules

Context-window probing must be provider-aware and endpoint-first. For LM Studio-compatible endpoints, probe the native root `/api/v1/models` in addition to the OpenAI-compatible `/models` route; prefer the loaded instance `config.context_length` when present because it reflects the runtime-loaded context. For generic OpenAI-compatible providers, inspect common metadata keys such as `context_length`, `max_context_length`, `context_window`, `max_context_tokens`, `n_ctx`, `num_ctx`, and `max_model_len` before falling back. The shared fallback is 16,384 tokens, not 8k.

Planner prompts must stay compact. Do not dump full JSON Schemas for every available tool into the planning prompt. Provide tool name, description, parameter names/types, short descriptions, and required fields. For matched item requests, active category context must be scoped to the matched item plus a small sample of other tracked keys; do not send the whole library state when the user is asking about one show/movie/game/book.

Plan placeholder resolution must be robust for common LLM aliases. `${lookup_metadata.seasons.length}` and similar `*.seasons.length` placeholders should resolve to the latest/known season count from metadata payloads when available instead of crashing the websocket chat flow.

### 0.9.3 Round 91 Context Loop and Ready-Target Rules

For ordinary DOWNLOAD intent, keep the model tool surface focused. Do not re-add broad browser/research/category-creation tools to DOWNLOAD just because they are read-only; the logs showed that their schemas inflated prompts and encouraged repeated searches. The default chain is still category context / `enquire_about_media`, `search_media_torrents`, then `queue_download`.

Large tool results must be compacted before becoming chat history. Preserve `result_set_id`, `candidate_id`, `candidate_ids`, queue arguments, seeders, language, resolution, codec, size/bitrate, and category `unit_descriptor`; drop raw tracker payloads and unneeded nested provider data. If a new tool returns large payloads, add it to `ToolResultCompactor` before exposing it in iterative LLM loops.

Completed-download import must never paper over path-safety failures by broadening allowed roots. If a category-planned target is blocked, retry once through the category-owned fallback path, then enforce that fallback with `SafePathResolver`. The fallback must stay inside the category root and preserve the original downloaded filename. Generic completion code may call category hooks; it must not invent TV/movie/book/game-specific folders.

## Round 92 operator notes

- When testing Discord, verify normal messages, `!search`, `!download`, `/ljs`, `/search`, and `/download` all acknowledge quickly and continue with periodic status pings during long tool turns.
- Do not reintroduce hard Discord request cancellation around assistant runs. Timeouts may protect individual external services, not the whole user turn.
- Keep the full torrent-quality reference out of routine DOWNLOAD prompts unless a dedicated explanation/test path explicitly needs it.
- For missing/latest/all unit requests, let category hooks expand units and packs; do not force a planner-guessed episode/chapter/track into the first search.

### 0.9.4 Round 93 Unified Chat Interface Rules

LJS has one chat architecture. Web, REST, Discord, Telegram, WhatsApp, and future bridges must adapt to `src/ai/chat_session_runner.py`; they must not maintain independent assistant-loop, context-window, memory, progress, or whole-turn timeout policies.

When changing any communication surface:

- Create a `ChatTurnRequest` and consume `ChatSessionRunner.run_events()` or `collect_response()`.
- Keep bridge code limited to transport adaptation: message splitting, platform send/reply/followup APIs, formatting restrictions, and notification delivery.
- Do not call `AIAssistant.run()` or `AIAssistant.run_stream()` directly from web/bridge handlers unless you are inside the shared runner itself.
- Do not add bridge-specific context trimming, bridge-specific recent-history rules, or bridge-specific tool filtering.
- Do not add whole-turn bridge timeouts. Timeouts belong around individual external services, not the full user conversation turn.
- Verify the shared contract with `scripts/round93_unified_chat_bridge_tests.py` after changing `src/web/app.py`, `src/web/*_bridge.py`, `src/web/comms.py`, or `src/ai/chat_session_runner.py`.

## Round 94 Agent Rule — Media Facts Need Episode-Level Grounding

When changing assistant planning, metadata tools, prompts, or bridge adapters, preserve the Round 94 grounding rule:

- Media fact questions use `metadata_lookup` before generic web search unless the answer is already present in the current conversation/tool context.
- A follow-up such as “episode 10” must be bound to the recent tracked item through the shared assistant pipeline, not inside Discord/Telegram/WhatsApp-specific code.
- Do not treat season-level metadata as sufficient for an episode-level air-date/title claim.
- If web fallback is needed, title-bind the query before sending it to web search.
- Future schedule dates must be described with future wording. Use the runtime date guidance and `compare_date_to_now` when needed.
- If no source returns the requested episode/date, say that plainly; never invent a title, date, or source attribution.

## Round 95 Agent Rule — Bridge status, reply language, and future-date evidence

When changing shared chat execution, prompts, planning, or communication bridges, preserve the Round 95 rules:

- `ChatSessionRunner` is allowed to preflight intent solely to decide whether status pings are warranted. Do not emit progress acknowledgements for ordinary CHAT turns such as thanks, greetings, or casual replies.
- For SEARCH, DOWNLOAD, and CONFIG turns, the first status line should be generated by the active LLM/persona when possible. Deterministic fallback text must be localized and varied; do not hard-code a single English Discord acknowledgement.
- The assistant replies in the language of the current user message. Configured item language, local file audio language, and preferred torrent language are media/search constraints only.
- Do not add configured item language as a generic SEARCH plan constraint. It belongs on torrent/download tools, not metadata/factual answers.
- Follow-up metadata repair may reuse a recent title and season, but it must not reuse a recent episode number unless the current prompt explicitly names one.
- For upcoming/future episode air-date questions, metadata lookup should be cross-checked with an official or high-confidence title-bound web source when available. If sources differ by one day, prefer official regional streamer evidence or report the disagreement honestly.
- Verify these rules with `scripts/round95_bridge_language_airdate_tests.py`.

## Round 96 Agent Rule — Metadata placeholders and compressed summaries

When changing metadata lookup, deterministic planning, or conversation compression, preserve the Round 96 rules:

- `metadata_lookup` is allowed to expose compact compatibility aliases for the requested episode, but the canonical compact field is `answer_hints.requested_episode`. Do not overload provider `results`, which remains a list of raw/provider results.
- `PlanExecutor` must resolve common local-planner aliases such as `${lookup.results.episode.air_date}` to the requested episode hint instead of surfacing raw placeholder errors after a successful metadata lookup.
- Future-oriented episode-date questions such as “When will episode 10 air?” must trigger official/high-confidence title-bound cross-check evidence when web search is available.
- Conversation summaries are trusted context. Summarizers must not introduce media dates, episode numbers, titles, or download statuses not explicitly present in the source turns; sanitize unsupported dates before storing compressed summaries.
- Verify these rules with `scripts/round96_airdate_placeholder_summary_tests.py`.

### 0.5.3 Item-Scoped Library Mutation Rule

When the application itself changes one known library item, do not trigger a full library scan. Completed downloads, item-level refresh buttons, and category-owned repair actions should pass the changed category id, item id, and path into an item-scoped reconciliation path. Full scans are reserved for manual whole-library rescan, startup repair, stale metadata repair, and unknown external filesystem changes.

Blocking file I/O such as copying, hardlinking, moving, and deleting large media files must not run on the FastAPI/event-loop thread. Use a worker thread/process boundary and then reconcile the affected item once the file operation completes.

## Round 98 Ready-Time Import Path Rule

Completed-download ready-time imports must not use configurable naming templates as the first path authority for TV payloads. Ready-time import is a seeding-safe exposure step: place the original source filename under the category-owned item folder and unit folder (`TV root / show / season / source filename`). Naming templates are reserved for explicit consolidation/preview/rename workflows. If a bridge, scheduler, or completion handler needs a ready import path, it must ask the owning category hook and must not construct `Season N/Title.ext` paths itself.

Warnings and errors must be visible from the UI without requiring the user to read a full raw log tail. Keep `/api/system/logs?level=warnings` working when adding or changing logging facilities.

## Round 99 Agent Rule — Pack-first does not mean pack-only

When changing download planning, torrent search, category hooks, or bridge status behavior, preserve these rules:

- A request for a whole/latest/last season, volume, collection, or equivalent multi-unit target should prefer a pack/bundle/full-unit search phase when that makes sense for the category.
- Pack preference is a phase preference, not a permanent constraint. If no acceptable pack exists, the owning category may fall back to individual units unless the user explicitly asked for pack-only behavior.
- Generic assistant/planner code may pass `search_scope`, but category code owns the meaning of packs, bundles, units, missing items, and fallback expansion.
- Do not mix pack candidates and many individual-unit candidates into one unexplained user reply. Report whether the shown results are pack results, fallback unit results, or a limited preview of a larger result set.
- Follow-up requests for “that pack”, “full season pack”, “better release”, or similar must remain in the DOWNLOAD/search-media flow and should reuse recent media context through the shared assistant pipeline.
- `ChatSessionRunner` owns progress cadence and caps. Bridges must not add their own repeated “still working” loop.
- Verify with `scripts/round99_pack_first_agent_flow_tests.py` after touching planner, search tool, category search hooks, bridge runners, or result compaction.

## Round 100 Agent Rule — Tool contracts and torrent candidate workspace

When touching agent tools, planners, prompts, or category search hooks, preserve these rules:

- Every tool name exposed by `AgentToolPolicy`, prompt guidance, or memory summaries must exist in the registered tool catalog. Missing tools are release-blocking regressions.
- Compatibility aliases may be accepted only at `ToolCallExecutor`, only when the canonical replacement is allowed for the current intent. Do not expose extra alias clutter in prompts.
- Optional browser/search dependencies must return structured unavailable errors, not crash the agent loop.
- Category-specific pack/bundle schemas live in the category. For TV, dynamic ranges such as `S05E01-E14` must derive `14` from that show season's metadata/cached state. Never hardcode terminal episode numbers.
- Pack-first search should use realistic release-name schemas (`S05`, `Season 5`, dynamic episode ranges, full/complete variants, and complete-series containers), then category-owned fallback to individual units if no acceptable pack exists and the user did not ask for pack-only.
- Search tools should cache full candidate records and expose a compact `candidate_picker` workspace to the LLM: ID, filename/title, size, seeders, and bundle hints. The LLM queues by candidate ID/result-set ID or asks for more details/file lists; it should not receive raw tracker dumps.
- Verify with `scripts/round100_tool_contract_pack_candidate_tests.py` after changing tool registration, tool policy, torrent search result shapes, or TV pack search.


## Round 101 Agent Rule — Download plans must not depend on invented result paths

Fresh DOWNLOAD discovery is not an arbitrary LLM-authored dependency graph. The planner may suggest a chain, but before execution the shared coordinator must canonicalize any fresh media download/search discovery into one `search_media_torrents` call using only literal arguments: item name, explicit numeric coordinates, language, and category-neutral `search_scope`.

- Do not execute `metadata_lookup -> search_media_torrents` chains where the search arguments contain placeholders such as `${lookup.results.latest_season}`. "Latest season" is a category-owned resolution problem; omit `season` and let the category hook resolve it from provider/cached metadata.
- Do not keep `check_storage_capacity` or queue steps with model-invented placeholders inside the deterministic pre-plan. Storage estimates and queueing must use the real `search_media_torrents` payload, cached `result_set_id`, and candidate IDs after discovery succeeds.
- Placeholder compatibility in `PlanExecutor` is a defensive fallback only, not the primary architecture. Prefer stable tool result fields and category hooks over adding one alias per crash.
- Internal plan constraints must not alter user intent detection. In particular, strings like `download_plan_contract=...only...` must not convert pack-preferred into pack-only.
- Verify with `scripts/round101_agent_contract_hardening_tests.py` whenever changing `PlanCoordinator`, `PlanExecutor`, `search_media_torrents`, category pack hooks, or planner prompts.

## Round 102 Agent Rule — LLM-led, contract-bound tool loop

When changing assistant execution, tool definitions, category search hooks, or bridge adapters, preserve the Round 102 boundary:

- Intent detection remains LLM-based and multilingual. Do not replace it with keyword routing. Use the structured intent result to decide whether progress updates are warranted.
- DOWNLOAD turns run through the natural tool-calling loop, not the deterministic structured pre-plan. The LLM may decide which registered tool to call next, but execution must validate tool names and argument schemas before running anything.
- Model-authored placeholders such as `${lookup.results.latest_season}`, `<URL from result>`, or guessed internal JSON paths are not valid tool arguments. The `ToolContractValidator` must return a typed recoverable error instead of allowing those calls to crash the turn.
- Tools should expose stable handles and affordances: `result_set_id`, `candidate_id`, `candidate_picker`, `result_handle`, and `next_actions`. The model chooses among these handles; it must not be asked to ingest raw tracker payloads or invent private result paths.
- `search_media_torrents` is the candidate-workspace entry point for media discovery. Full torrent records stay cached; the LLM receives compact ranked candidates and can call `inspect_torrent_candidate` for file-list/coverage detail before queueing ambiguous packs or full-series containers.
- Category code owns release schemas, latest/missing unit resolution, bundle semantics, fallback phases, and unit descriptors. Generic chat/planner code may pass category-neutral intent such as `season_pack_preferred`, but must not hardcode TV/movie/book/game behaviors.
- CHAT turns should not create or mutate active download/search goals. Active goal state is for SEARCH/DOWNLOAD/CONFIG task continuity and should remain compact.
- Verify with `scripts/round102_llm_led_contract_tests.py` after touching tool validation, search result shape, download execution, or candidate inspection.
