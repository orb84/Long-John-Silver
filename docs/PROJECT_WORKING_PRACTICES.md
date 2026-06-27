# LJS Project Working Practices

_Last updated for the category-first, security-hardened, storage-aware package._

This guide explains how to work safely and productively in the current LJS structure. It complements `AGENTS.md`, `SECURITY.md`, `STORAGE.md`, and `docs/CODEBASE_ARCHITECTURE_MAP.md`.

## 1. Non-Negotiable Principles

### Category-first always

TV and Movie are categories, not global application modes. New behavior should either be:

```text
generic platform behavior, reusable by any category
```

or:

```text
category-owned behavior, declared in a manifest/action/workflow/provider
```

Do not introduce global TV/movie/show branches into assistant, scheduler, repositories, or generic UI.

### No backward-compatibility shims

This project is treated as a fresh-install category-first system. Do not add legacy tables, old route aliases, migration shims, or compatibility methods unless the owner explicitly changes this rule.

### Security is code, not prompting

Do not rely on LLM instructions for safety. Treat the user, the LLM, downloaded filenames, metadata, and database values as untrusted.

Hard rules:

```text
No generic shell tool.
No shell=True.
No raw destructive filesystem calls.
No file mutation outside configured safe roots.
No destructive action without an ActionReceipt and confirmation when required.
```

### Storage pressure is part of planning

Disk space is category-aware and volume-aware. Downloads, organization, cleanup, UI status, and assistant context should all use `StorageMonitor` rather than ad-hoc `shutil.disk_usage()` calls.

## 2. Before You Change Code

Run a quick orientation pass:

```bash
find src -maxdepth 3 -type f | sort
python -m compileall -q src tests scripts
python scripts/check_category_architecture.py
python scripts/check_security_architecture.py
```

Search for existing implementations before adding new ones:

```bash
grep -R "class .*Provider" -n src/search src/ai src/core/categories
grep -R "ActionReceipt" -n src
grep -R "StorageMonitor" -n src
grep -R "SafePathResolver" -n src
grep -R "CategoryManifest" -n src/core/categories src/core/models.py
```

Prefer extending existing abstractions over adding parallel systems.

## 3. Adding or Changing a Category

### Use the category scaffold path

Start with `CategorySpec` and `CategoryScaffoldService`:

```text
src/core/models.py                 CategorySpec, CategorySpecProperty, CategorySpecUnit
src/core/categories/scaffold.py    CategoryScaffoldService
skills/category_creation_guide.md  Category creation guidance
```

Generate/preview the structure, then review before applying.

### Category file layout

A category should usually live at:

```text
src/core/categories/custom/<category_id>.py
```

Built-ins live at:

```text
src/core/categories/tv.py
src/core/categories/movie.py
```

Category prompts live at:

```text
src/core/categories/prompts/<category_id>.md
```

Metadata providers live at:

```text
src/core/categories/metadata/<provider>.py
```

### What a category owns

A category owns:

```text
manifest
setup requirements
item display names
unit semantics
search query hints
metadata provider choices
actions/workflows
category-specific prompt examples
category-specific UI declarations
category-specific validation
```

### What a category must not own

A category should not own:

```text
database schema columns
web server composition
LLM provider management
generic filesystem safety policy
generic storage monitoring
generic torrent provider implementation
```

### Category checklist

Before calling a category complete:

```text
[ ] Manifest has stable category_id.
[ ] Manifest declares setup requirements.
[ ] Manifest declares UI sections and actions/workflows.
[ ] Category validates item properties.
[ ] Category emits useful search profiles/patterns.
[ ] Category actions return ActionReceipt.
[ ] Destructive category actions use confirmation.
[ ] Category paths are policy-checked before mutation.
[ ] Category has tests.
[ ] No global code special-cases the category.
```


## 4. Working With Taste Profiles And Metadata

`TasteProfiler` is generic aggregation code. It may count genres, people,
ratings, category composition, and index overviews, but it must not decide how a
category fetches external metadata.

Required pattern:

```text
TasteProfiler
  -> CategoryRegistry.get(category_id)
  -> category.enrich_taste_metadata(item, context)
  -> category-owned provider/workflow
  -> generic metadata envelope
```

When adding a category that should influence recommendations or prompt context:

```text
[ ] Store normalized metadata in category_item_metadata.
[ ] Override enrich_taste_metadata() only inside the category class/module.
[ ] Return provider, category_id, item_id, display_name, genres, people, rating, and overview when available.
[ ] Keep provider-specific terms out of src/core/taste_profiler.py.
[ ] Add a static regression test if the category introduces new provider hooks.
```

Do not add TV/movie/provider branches in `TasteProfiler`, scheduler, assistant,
repositories, or generic web/UI code. Use category hooks instead.

## 5. Working With Filesystem Operations

### Use SafePathResolver

Any operation that touches disk should go through `SafePathResolver` or a service that already uses it.

Security package:

```text
src/core/security/path_policy.py
src/core/security/action_risk.py
src/core/security/confirmation.py
src/core/security/audit.py
src/core/security/command_policy.py
```

Use safe operations for:

```text
copy
move
hardlink
unlink
delete tree
explicit quarantine when deliberately requested
cleanup temporary files
log cleanup
browser artifact cleanup
library organization
```

### Do not do this

```python
path.unlink()
shutil.rmtree(path)
os.remove(path)
os.system(command)
subprocess.run(command, shell=True)
subprocess.run(f"rm -rf {path}", shell=True)
```

### Preferred pattern

```python
resolver = SafePathResolver.for_category(
    category_id=category_id,
    category_roots=category_roots,
    app_roots=app_roots,
)

safe_path = resolver.resolve(requested_path, purpose="category.delete_unit")
resolver.safe_unlink(safe_path.path, purpose="category.delete_unit")
```

Use the actual current API signatures in `path_policy.py`; the example above is the intended shape, not a replacement for reading the class.

### Destructive action pattern

Use a two-phase flow:

```text
1. Build affected path list.
2. Resolve and validate every path.
3. Return dry-run ActionReceipt with risk and affected paths.
4. Ask for confirmation.
5. Validate the confirmation token.
6. Execute exact same action payload.
7. Return final ActionReceipt.
8. Write audit event.
```

Do not accept a confirmation for a changed path list or changed action payload.

## 6. Working With Shell/Subprocess Calls

### Use CommandPolicy

Subprocess calls belong behind typed wrappers and `CommandPolicy`.

Examples of acceptable external tools:

```text
ffprobe
Playwright installer
Jackett startup command
communication bridge package installer, if explicitly allowed
```

Rules:

```text
Use shell=False.
Pass argv as a list.
Allowlist executable names.
Allowlist dangerous argument shapes.
Validate all path arguments first.
Capture output when practical.
Log failures with enough context.
```

Never expose subprocess execution as an LLM tool.

## 7. Working With Storage Awareness

### Use StorageMonitor

Storage package:

```text
src/core/storage.py
src/ai/tools/storage.py
src/web/routers/storage.py
STORAGE.md
```

Use `StorageMonitor` when you need to know:

```text
How much space is free on category library disks.
Whether multiple categories share a disk.
Whether a planned download fits.
Whether health should be degraded.
What storage summary should be shown to the LLM.
```

### Do not duplicate disk checks

Avoid ad-hoc code like:

```python
free = shutil.disk_usage(path).free
```

unless you are inside `StorageMonitor` or a low-level helper used by it.

### Capacity checks before downloads

Before queueing a download or racing candidates, pass the estimated size and category target to the storage layer. If the estimate is unknown, prefer a conservative warning or category-specific reserve check rather than silently assuming it is safe.

### UI expectations

Storage should be visible in:

```text
Dashboard Storage Watch
Settings/Compass storage panel
/api/storage/status
/api/health degraded status
assistant prompt context
LLM get_storage_status/check_storage_capacity tools
```

If you add a new download path or category root, update storage target discovery.

## 8. Working With the Assistant and Tools

### Tool provider layout

LLM tools belong in domain providers:

```text
src/ai/tools/categories.py
src/ai/tools/downloads.py
src/ai/tools/library.py
src/ai/tools/preferences.py
src/ai/tools/research.py
src/ai/tools/scheduling.py
src/ai/tools/storage.py
src/ai/tools/web.py
```

Register providers through:

```text
src/ai/tool_catalog.py  AgentToolCatalog
```

### Tool design rules

A good tool is:

```text
typed
narrow
bounded
side-effect aware
policy checked
well documented
returns structured data
```

Avoid tools that are:

```text
freeform shell commands
freeform Python execution
raw SQL execution
raw filesystem mutation
unbounded browser control
category-specific global shortcuts
```

### Always preserve the shared action path

UI and LLM should not have separate behavior for the same operation. Route shared behavior through:

```text
ActionGateway
ToolRegistry
ActionReceipt
ActionEventStore
ShipEventBus
```

This keeps audit, confirmations, and UI updates consistent.

### Assistant prompt context

Always-on context should stay compact. The assistant can receive summaries for:

```text
category manifests
user preferences
storage pressure
provider health
recent conversation state
```

Do not dump large raw reports into the prompt. Give summaries and provide tools for detail.

## 8. Working With Search

### Torrent search

Primary path should be Jackett:

```text
SearchAggregator -> JackettSearch native JSON provider; TorznabSearch is optional only for non-Jackett-compatible deployments
```

Direct scrapers should be explicit fallback only:

```text
direct_scraper_fallback=true and no healthy Jackett provider
```

Never silently hide broken Jackett by falling back to unstable scrapers while reporting healthy status.

### General web search

Use configured providers through:

```text
src/search/web/service.py       WebSearchService
src/search/web/base.py          WebSearchProvider
```

Provider preference:

```text
Brave / Tavily / Kagi / SearXNG -> primary configured options
DuckDuckGo HTML -> last-resort degraded fallback
```

### Search result quality

For torrent selection, prefer normalized candidate data:

```text
magnet availability
size bytes
seeders
resolution
codec
release type
season/episode/unit info
language
red flags
quality score
extraction confidence
```

Small LLMs should receive compact candidate summaries, not raw provider rows.

## 9. Working With the Web UI

### Router pattern

Routers live in:

```text
src/web/routers/<domain>.py
```

They should receive `WebDependencies` and expose a `get_router()` method.

### Page and static assets

```text
src/web/templates/*.html
src/web/static/js/*.js
src/web/static/css/style.css
```

Use generic category APIs and manifests where possible. Do not build static TV/Movie-only panels into global UI code.

### API conventions

Prefer category-first routes:

```text
GET    /api/categories
GET    /api/categories/{category_id}/manifest
GET    /api/categories/{category_id}/items
POST   /api/categories/{category_id}/items
GET    /api/categories/{category_id}/items/{item_id}
PATCH  /api/categories/{category_id}/items/{item_id}
DELETE /api/categories/{category_id}/items/{item_id}
POST   /api/categories/{category_id}/items/{item_id}/actions/{action_name}
POST   /api/categories/{category_id}/actions/{action_name}
```

Avoid adding old show/movie-specific API routes.

## 10. Working With Settings

### Settings location

```text
src/core/config.py          SettingsManager
src/core/category_config.py CategoryConfigStore
src/core/models.py          Settings and nested config models
config/settings.template.yaml       tracked global fresh-install template
config/settings.local.yaml          ignored live global config
config/category-definitions/*.yaml and config/category-config-templates/*.yaml    tracked category defaults
config/categories/*.yaml            ignored live category config
.env.example                env var reference
```

### Adding a setting

Checklist:

```text
[ ] Add typed field to the appropriate model in src/core/models.py for global settings, or a category property/manifest declaration for category settings.
[ ] For global settings, update SettingsManager YAML loading if nested.
[ ] For category settings, update the category manifest/property list and the default `config/category-definitions/<category_id>.yaml plus config/category-config-templates/<category_id>.yaml`; never commit live `config/categories/<category_id>.yaml` edits.
[ ] Update .env.example if environment-backed.
[ ] Update setup/settings UI if user-facing.
[ ] Update CODEBASE_ARCHITECTURE_MAP.md when configuration ownership changes.
[ ] Update docs.
[ ] Add tests for defaults and loading behavior.
```

### Secrets

Prefer environment variables for API keys and tokens. Avoid logging secrets. If a setting can contain a secret, mask it in UI/status reports.

## 11. Working With Database and Repositories

### Database rule

Do not add category-specific columns for category properties. Use generic storage:

```text
category_items.properties_json
category_items.metadata_json
category_items.state_json
category_item_units.properties_json
category_property_index
```

### Repository rule

Repositories should expose generic operations using:

```text
category_id
item_id
unit_key
unit_type
status
properties/metadata/state payloads
```

Avoid:

```text
show_name
movie_title as a repository identity
get_show_progress as a generic method
add_downloaded_episode as a generic method
```

If a category needs a convenience method, put it in the category/workflow layer, not in a generic repository.

## 12. Working With Tests

### Minimum checks before handing off

```bash
python -m compileall -q src tests scripts
python scripts/check_category_architecture.py
python scripts/check_security_architecture.py
pytest
```

If pytest cannot run because dependencies are missing, record the exact blocker in `PROGRESS.md` and run targeted import/smoke checks where possible.

### Add tests with changes

Every new class or non-trivial behavior should have tests. Mirror source areas where practical:

```text
src/core/storage.py                -> tests/test_storage_monitor.py
src/core/security/path_policy.py   -> tests/test_security_path_policy.py
src/search/web/service.py          -> tests/test_web_search_service.py
src/core/categories/scaffold.py    -> tests/test_category_scaffold.py
```

### Guard tests are contracts

Do not weaken architecture/security guard tests to make implementation easier. If a guard blocks needed work, update the design and document why.

## 13. Documentation Practices

Update docs when behavior changes:

```text
PROGRESS.md     Always update for meaningful changes.
SECURITY.md     Update when safety model changes.
STORAGE.md      Update when storage behavior changes.
README.md       Update for setup/user-facing behavior.
docs/CODEBASE_ARCHITECTURE_MAP.md Update for structure changes.
```

Use `PROGRESS.md` to record:

```text
what changed
why it changed
files touched
known issues
blocked tests
```

## 14. Code Style Reminders

Follow `AGENTS.md`. The most important daily rules:

```text
Use classes for domain behavior.
Keep classes small and focused.
Inject dependencies via constructors.
Use Pydantic models for cross-boundary data.
Use pathlib.Path for paths.
Use loguru.logger, not print/logging.
Use async for I/O.
Avoid bare except.
Use type hints everywhere.
Keep methods short.
Write docstrings for public classes/methods.
```

## 15. Review Checklist

Before submitting a change, ask:

```text
[ ] Did I keep TV/Movie logic inside category-owned boundaries?
[ ] Did I avoid legacy compatibility shims?
[ ] Did I avoid new category-specific schema columns?
[ ] Did all filesystem writes/deletes pass safe path policy?
[ ] Did subprocess calls pass command policy?
[ ] Did destructive behavior produce ActionReceipt and confirmation if needed?
[ ] Did storage-sensitive behavior use StorageMonitor?
[ ] Did UI and LLM share the same action/tool path?
[ ] Did I update PROGRESS.md and relevant docs?
[ ] Did compileall and guard scripts pass?
[ ] Did I add or update tests?
```

## 16. Troubleshooting Guide

### Assistant has the wrong tools

Check:

```text
main.py provider list passed to AgentToolCatalog
src/ai/tool_catalog.py
src/ai/tool_policy.py
src/ai/tools/<domain>.py
assistant.set_tool_registry(tool_registry)
```

### Category does not appear

Check:

```text
CategoryRegistry.register_defaults()
custom category discovery
category_id in manifest
import errors in custom category module
/api/categories response
```

### Download is blocked unexpectedly

Check:

```text
StorageMonitor report
category root mapping
estimated torrent size
storage reserve thresholds
DownloadManager capacity check result
logs around check_storage_capacity
```

### File operation is blocked

Check:

```text
allowed roots in SafePathResolver
resolved real path
symlink path components
purpose string
category root configuration
security audit log
```

### Web search returns degraded results

Check:

```text
settings.web_search.enabled
settings.web_search.provider
API key/base URL
WebSearchService.health_check()
DuckDuckGo fallback setting
provider logs
```

### Jackett search not working

Check:

```text
/api/jackett/health
/api/search/health
Jackett URL/API key
configured indexers
Jackett JSON or optional Torznab endpoint response
settings.direct_scraper_fallback
SearchAggregator provider diagnostics
```

## 17. Preferred Implementation Pattern

When adding a feature, use this order:

```text
1. Define or reuse models in src/core/models.py.
2. Add domain service with injected dependencies.
3. Add category-owned logic if category-specific.
4. Add policy checks for safety/storage/action risk.
5. Expose through shared ActionGateway/tool/API path.
6. Add UI using generic manifests/status APIs where possible.
7. Add tests.
8. Run compileall and guard scripts.
9. Update PROGRESS.md and docs.
```

This order keeps the system understandable and prevents accidental global special cases.


## Category Artwork Cache

Provider artwork discovered by a category is cached under `data/categories/<category_id>/metadata/artwork/<item_id>/`. The web app exposes this read-only via `/category-data/...`, and UI view models prefer `local_poster_url` before remote TMDB image URLs. Generic UI code should not download or reinterpret artwork directly; category metadata hooks own the discovery and cache step.

## Definition-Backed Category Cleanup Rules

When a definition-backed category needs local scan shaping, keep the behavior in
`LocalObjectReconstructor` and focused builders under `src/core/categories/`.
Do not add scanner/scheduler branches for albums, chapters, ebook formats,
volumes, or other category-local units.

When a definition-backed category needs a provider/source preference, declare it
in the category YAML, preferably under `source_strategy`. The generic
`DefinitionBackedCategory` may read the declaration, but it must not branch on a
concrete category id such as Music or Audiobooks to change runtime behavior.

Planner cleanup rule: generic planner repair may consume structured fields that
the LLM or tool schema already emitted, but it must not parse user-language
phrases to infer category unit scope. Improve category prompt skills and
structured tool arguments instead.
