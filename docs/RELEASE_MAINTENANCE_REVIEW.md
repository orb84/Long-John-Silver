# Release Maintenance Review

This document records the release-readiness review expectations for the current LJS tree. It is intentionally practical: it explains what the main runtime paths own, what should not be duplicated, and which risk areas need deliberate future refactors rather than emergency patches.

## Configuration ownership

The repository tracks public templates and shareable category definitions only.

- `config/settings.template.yaml` contains global blank defaults.
- `config/settings.local.yaml` is created on first launch and ignored by git.
- `config/category-definitions/*.yaml` contains shareable category behavior: services, tools, workflows, LLM guidance, filename examples, accepted formats, and lifecycle policy.
- `config/category-config-templates/*.yaml` contains blank per-category local defaults.
- `config/categories/*.yaml` is created on first launch, ignored by git, and stores private user values such as library paths, API keys, tokens, provider enable flags, category preferences, and scheduler/storage overrides.

External service clients must be created from category-owned service config. TMDB, Trakt, Plex, and OpenSubtitles belong to the abstract `media` category; TV and Movie inherit them. TV-specific services such as TVMaze belong to the TV category. Do not add new global `tmdb_api_key`, `trakt_*`, `plex_*`, or OpenSubtitles fields.


## First-run setup flow

The setup wizard must use the same ownership boundaries as Compass:

- global settings such as the active LLM provider, web-search provider, bridge tokens, and the active download directory go to `config/settings.local.yaml`;
- category library paths go to `config/categories/<category_id>.yaml` as `paths.library_path`;
- shared Media services such as TMDB and Trakt go to `config/categories/media.yaml` under `services`;
- shared TV/Movie search defaults such as language, preferred resolution, bitrate, file-size ceiling, and size-limit mode go to the abstract Media `download_profile`;
- tracked category definitions in `config/category-definitions/*.yaml` must never receive user paths or credentials.

Setup endpoints should write category config through `setup_category_config` / `SettingsManager.save`, not by calling authenticated Compass endpoints as a shortcut. The Compass **Content Selection** panel follows the same rule by saving shared media candidate preferences to `category_settings.media.download_profile`, while bandwidth caps remain global download settings. Setup mutation endpoints still pass during first-run because `verify_auth` allows incomplete setup, but after setup completion they must require the normal web authentication path instead of remaining unauthenticated back doors.

## Main startup flow

`main.py` should remain a readable application bootstrap, not a place where domain logic accumulates. The intended sequence is:

1. load global and category config through `SettingsManager`;
2. initialize storage/database/logging/security primitives;
3. initialize LLM provider routing and semantic memory;
4. register categories and load category-owned service config;
5. build torrent search providers and category-owned metadata clients;
6. initialize download, library, scheduler, assistant, bridge, and notification services;
7. start background loops under `TaskSupervisor` so shutdown remains controlled.

If a new feature needs domain decisions, put those decisions behind category hooks, tools, or workflow declarations. The startup file may wire the dependency, but it should not learn TV/movie/general semantics.

## Category inheritance flow

The built-in class shape is:

```text
media        abstract shared media definition
├── tv       extends media
└── movie    extends media
general      independent conservative file category
```

The effective runtime config is a deep merge of the shareable definition and ignored local config, with inheritance applied before categories read it. Save operations write only private/user-editable data back to `config/categories/*.yaml`; definition-only content should not leak into live config.

## Current high-risk areas

The architecture checker currently reports no hard findings, but several large classes remain review risks:

- `AIAssistant`
- `PlanCoordinator`
- `PlanExecutor`
- `SearchMediaTorrentsTool`
- `MediaScheduler`
- `TasteProfiler`
- large repository classes

Do not grow these classes casually. When changing one of them, prefer extracting a collaborator with a narrow responsibility and update the architecture docs/tests in the same patch.

## Maintenance rules before release

Run the structural checks after meaningful changes:

```bash
python3 -m compileall -q src scripts main.py
python3 scripts/check_public_docs.py
python3 scripts/check_category_architecture.py
python3 scripts/check_ai_intent_architecture.py
python3 scripts/check_ai_context_architecture.py
python3 scripts/check_security_architecture.py
python3 scripts/check_model_facade_imports.py
python3 scripts/check_compatibility_shims.py
python3 scripts/check_architecture.py --summary
```

Full `pytest` is still expected in a complete development environment. If a sandbox lacks runtime dependencies such as `aiosqlite`, say so explicitly instead of claiming the full suite passed.
## Round 119 migration startup fix

The migration runner now strips SQL comments and splits statements only on semicolons outside quoted strings. This prevents comment prose from being executed as SQL when migration comments contain semicolons. Migration 107 was also cleaned up and covered by `scripts/round119_migration_startup_fix_tests.py`.
