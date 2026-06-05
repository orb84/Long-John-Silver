# SearXNG Managed Web Research Plan

**Status:** Implementation started in Round 218.  
**Scope:** Make SearXNG an optional, app-managed, cross-platform web-research sidecar for category/item public-source discovery.  
**Non-goal:** SearXNG must not become a media download source, torrent indexer, release selector, or metadata authority.

## 1. Product decision

LJS should default to **Automatic local SearXNG** for general web research, with a separate **Manual/existing endpoint** mode for advanced users. This order is deliberate: first-install testing must prove that LJS can download, configure, start, and health-check an LJS-owned SearXNG runtime on a machine that does not already have SearXNG configured. Manual mode is easier once managed mode is real, but the reverse can hide installer defects behind a pre-existing local service.

The first-run setup wizard and Compass/Settings UI must both expose:

- an automatic install/configure button;
- a managed/manual mode selector;
- the resulting SearXNG base URL;
- health/status feedback;
- manual endpoint/key fields for users who already run their own instance.

## 2. Architecture boundary

SearXNG belongs in the web-research path:

```text
SearXNG provider
  -> WebSearchService
  -> future WebResearchService / evidence extraction
  -> category-owned interpretation hooks
  -> CategoryItemCoordinator / category lifecycle writes when facts are accepted
```

It must not be wired into:

```text
SearchAggregator
TorrentSearchPipeline
queue_download
Jackett/Torznab acquisition selection
Soulseek/slskd transfer selection
```

Search snippets are leads, not facts. Durable category facts must eventually require page fetch/extraction, source scoring, and provenance records.

## 3. Managed installation principles

Managed mode owns an isolated runtime under LJS-controlled folders:

```text
data/searxng/
  src/searxng/
  venv/
  config/settings.yml
  logs/install.log
  logs/searxng.log

data/searxng_state/
  installed-version.json
```

The manager must not use `/etc/searxng`, `/usr/local/searxng`, a user-global Python venv, a system service, or an existing public/LAN instance for the default path.

If the preferred managed port is already in use, LJS chooses another localhost port instead of adopting the existing process. This avoids the Jackett-style failure where a stale process makes installer tests pass while LJS did not actually install/configure the service.

## 4. Cross-platform requirements

Supported operating systems:

- macOS x64/arm64;
- Linux x64/arm64 where Python can create a venv;
- Windows x64.

Path and launch differences:

| Concern | Linux/macOS | Windows |
|---|---|---|
| venv Python | `venv/bin/python` | `venv/Scripts/python.exe` |
| process owner | LJS child process by default | LJS child process by default |
| config path | app-owned `data/searxng/config/settings.yml` | app-owned `data/searxng/config/settings.yml` |
| bind address | `127.0.0.1` | `127.0.0.1` |
| default port | `18888`, with collision fallback | `18888`, with collision fallback |

The implementation prefers `uv` when it is already available because `uv venv --python 3.12 --seed` can provision a suitable Python runtime and seed `pip`, `wheel`, and `setuptools`. This is important on Linux: a non-seeded `uv venv` can produce a valid Python executable without `pip`, which is only a partial install. LJS therefore writes its own venv-ready marker only after SearXNG dependencies install successfully, removes partial venvs on retry/repair, and never treats `source + python` alone as installed. If seeded uv creation is unavailable, LJS falls back to non-seeded uv plus explicit `uv pip`/`ensurepip` bootstrap, then to the current app Python and standard-library `venv`. A later packaging pass can bundle/download `uv` or use `python-build-standalone` directly for frozen distributions. The public contract already calls this a managed runtime so that UI/settings/tests do not need to change when the Python bootstrap strategy is upgraded.

## 5. Managed settings.yml contract

Generated settings must include JSON output and must be private by default:

```yaml
use_default_settings: true

search:
  formats:
    - html
    - json
  safe_search: 1
  default_lang: "auto"
  max_page: 2

server:
  port: 18888
  bind_address: "127.0.0.1"
  limiter: false
  public_instance: false
  image_proxy: false
  method: "GET"

valkey:
  url: false
```

Important behavior:

- `format=json` health must fail clearly if JSON is disabled.
- `limiter` stays false for private localhost managed mode so Valkey is not required.
- `public_instance` stays false.
- LAN/public bind is not part of automatic setup.

## 6. Backend implementation sequence

### Phase A — Managed sidecar first

Deliverables:

- `SearXNGManager` with `ensure_installed`, `configure`, `start`, `stop`, `repair`, `health_check`, and `save_to_settings`.
- LJS-owned runtime folders.
- Source archive download into `data/searxng/src/searxng`.
- Private venv creation with seeded packaging tools where possible.
- Partial/broken venv detection and removal before retry/repair.
- Dependency install logging.
- Generated settings with JSON enabled.
- Start command using `SEARXNG_SETTINGS_PATH`.
- Port collision avoidance.
- No adoption of existing local/system SearXNG processes.
- System actions and API routes:
  - `system_install_searxng` / `POST /api/searxng/install`
  - `system_start_searxng` / `POST /api/searxng/start`
  - `system_repair_searxng` / `POST /api/searxng/repair`
  - `system_stop_searxng` / `POST /api/searxng/stop`
  - `GET /api/searxng/health`

### Phase B — Provider hardening

Deliverables:

- Active SearXNG health probe against `/search?q=ljs-health-check&format=json`.
- Support for categories, language, safe search, timeout, and result normalization.
- Typed errors:
  - `CONFIG_MISSING`
  - `PROVIDER_UNREACHABLE`
  - `JSON_FORMAT_DISABLED`
  - `INVALID_PROVIDER_RESPONSE`
  - `PROVIDER_TIMEOUT`
  - `RATE_LIMITED`
  - `NO_RESULTS`

### Phase C — UI wiring

Deliverables:

- Setup wizard defaults to SearXNG managed mode.
- Setup wizard has an explicit “Auto install SearXNG now” button.
- Compass/Settings has a Web Research subsection with provider, mode, endpoint, language/categories/safe-search, install, and test controls.
- Manual endpoint setup remains possible.

### Phase D — Evidence layer, not snippets-as-facts

Deliverables:

- `WebResearchService` above `WebSearchService`.
- URL normalization/dedupe.
- Page fetch/extract path.
- Source-kind scoring.
- Persistence tables:
  - `web_research_query_log`
  - `web_source_evidence`
  - `category_fact_provenance`
- Agent/API surfaces that clearly return evidence bundles, not category facts.
- Query/result cache remains a follow-up optimization once category hooks define real reuse windows.

### Phase E — Category hooks

Deliverables:

- Generic `CategoryWebResearchExtension` or category contract hooks.
- TV hook for official-page discovery, air-date corroboration, and delay/news checks.
- Later music/books/sports hooks.
- Durable category updates still route through `CategoryItemCoordinator`.

## 7. Test plan

### Deterministic managed installer tests

These must not require the real internet:

- fresh temp service directory creates `src`, `venv`, `config`, `logs`, `state` structure;
- generated `settings.yml` includes `search.formats` with `json`;
- generated config binds only to `127.0.0.1`;
- generated config sets `public_instance: false`;
- generated config sets `limiter: false` and `valkey.url: false`;
- Windows venv path resolves to `Scripts/python.exe`;
- Linux/macOS venv path resolves to `bin/python`;
- port collision causes managed port reassignment;
- install failure does not enable ready status;
- health result says not ready when no LJS-owned process is running.

### Provider tests

- SearXNG search always sends `format=json`;
- categories/language/safe-search/timeouts are mapped;
- 403 maps to `JSON_FORMAT_DISABLED`;
- 429 maps to `RATE_LIMITED`;
- HTML/invalid JSON maps to `INVALID_PROVIDER_RESPONSE`;
- empty JSON results are a warning for search, not proof that JSON health is impossible;
- duplicate URLs are deduped.

### Architecture boundary tests

- `src/search/aggregator.py` must not import or instantiate SearXNG.
- `queue_download` must not accept web-search hits as queueable candidates.
- TV/news/airdate interpretation must not be placed in base web-search code.
- UI setup/Compass actions must use the same `ActionGateway` system actions.

### Live smoke test

Optional network-dependent test:

1. Start from a clean temp or VM with no SearXNG listening on the default port.
2. Press setup “Auto install SearXNG now”.
3. Confirm install logs and generated settings path.
4. Confirm `/search?q=ljs-health-check&format=json` returns valid JSON.
5. Confirm Compass reports ready.
6. Stop LJS; restart; confirm managed state is still understandable.

## 8. Current implementation notes

Round 218 starts Phase A/B/C by adding:

- managed config fields to `WebSearchConfig`;
- richer `WebSearchHealth` and `WebSearchHit` fields;
- `src/search/web/searxng_manager.py`;
- active SearXNG JSON health probing and request parameter support;
- setup and Compass install/configure controls;
- API routes and system actions for managed SearXNG.

Round 219 starts Phase D by adding:

- `src/search/web/research.py` with bounded query planning, URL canonicalization, source classification, page fetch/extraction through `WebReader`, and conservative evidence scoring;
- `src/core/repositories/web_research.py`;
- migration `110_web_research_evidence.sql`;
- base-schema support for `web_research_query_log`, `web_source_evidence`, and `category_fact_provenance`;
- a new `web_research` assistant tool for fetched public-source evidence;
- `/api/web-research/test` and `/api/web-research/evidence` diagnostics;
- `research_release_info` now uses the configured web-research service instead of directly scraping DuckDuckGo HTML.

Important Round 219 boundary: the evidence bundle sets `facts_authoritative=false`. It stores page/source provenance only. Durable category facts still require a future category-owned interpretation hook and any mutations must go through `CategoryItemCoordinator`.

Round 220 implements Phase E groundwork by adding:

- category web-research hook models in `src/core/domain_models/web_search.py`;
- default no-op category hooks in `CategoryContractMixin`;
- `src/search/web/category_research.py`, a category-neutral orchestrator that runs category-authored plans through `WebResearchService` and persists interpreted provenance facts;
- `src/core/categories/tv_web_research.py`, a TV-owned implementation for official/primary source discovery, air-date references, and delay/postponement signals;
- a `category_web_research` assistant tool;
- `/api/category-web-research/test` diagnostics;
- deterministic tests in `scripts/round220_category_web_research_hooks_tests.py`.

Round 220 intentionally does not mutate tracked items from public web evidence.
TV interpretation produces non-authoritative signals and provenance rows only.
A later phase can add category workflows that decide when sufficiently strong
provenance should refresh metadata, annotate item state, or adjust lifecycle
checks through `CategoryItemCoordinator` / category watch paths.

### Round 221 hardening update

Round 221 adds the upgrade/rollback polish that the managed installer needs
before broader live testing:

- fixed the managed path root so SearXNG installs under project-level
  `data/searxng`, matching Jackett/slskd conventions, not `src/data/searxng`;
- added a configurable managed source ref (`web_search.managed_source_ref`,
  default `master`) so clean-machine tests can pin or override the source archive
  without changing UI/API contracts;
- added backup/restore support for the LJS-owned source checkout, venv,
  generated `settings.yml`, and state file;
- added manager methods and system actions/routes for upgrade, rollback, and
  uninstall;
- added Compass controls for managed upgrade and rollback;
- kept manual/external endpoints separate: rollback/uninstall never delete or
  adopt user-managed SearXNG instances.

The rollout sequence remains managed-install first.  Live validation should use
a clean macOS, Linux, and Windows machine/VM with no SearXNG process already
listening on the default port.  The deterministic test added for this phase is
`scripts/round221_searxng_upgrade_rollback_tests.py`.

## Round 222 hardening note — device logging, fallback tracing, and information-watch planning

Before device rollout, managed SearXNG and web research need visible diagnostics at every decision point:

- managed installer lifecycle events go to normal app logs and `data/searxng/logs/manager-events.jsonl`;
- `install.log` keeps subprocess output from venv/pip/source setup;
- `searxng.log` captures the managed SearXNG child process;
- provider logs show SearXNG endpoint/category/language/time-range choices and typed health failures;
- fallback logs explicitly distinguish primary-provider failure from degraded DuckDuckGo HTML success/failure;
- evidence logs show query logging, URL dedupe, fetch attempts, fetch failures, persisted evidence IDs, and category interpretation counts.

Compass/Settings and initial setup both expose automatic SearXNG installation. Compass saves visible web-search settings before calling the managed install endpoint so clean-machine source-ref and fallback settings are respected. DuckDuckGo HTML fallback is now its own web-research setting, separate from torrent direct-scraper fallback.

The proactive tracking proposal now lives in `docs/WEB_RESEARCH_PROACTIVE_TRACKING_PLAN.md`. It recommends reusing `PromptScheduler` for explicit user-created recurring checks and adding category-owned information-watch hooks later for tracked-item news/rumor watches.

## Round 226 Linux install note — SearXNG editable build dependency ordering

Linux live testing exposed a second clean-machine installer issue after the uv/pip bootstrap fix: current SearXNG source imports the `searx` package from `setup.py` while generating editable-install build metadata. That import path needs runtime requirements such as `msgspec`, so a clean `pip install -e <source>` can fail before pip has installed SearXNG's own dependencies.

Managed installation now performs dependency setup in this order:

1. create or repair the private venv;
2. ensure `pip`, `wheel`, and `setuptools` are present;
3. install SearXNG's pinned `requirements.txt` into the managed venv;
4. install the local SearXNG source with `--no-build-isolation -e` so build metadata can see those pinned runtime packages;
5. verify `import msgspec`, `import searx`, and `import searx.webapp` before writing the LJS venv-ready marker.

If pip fails on requirements or editable install and `uv` is available, LJS retries the same step through `uv pip`. The ready marker is still written only after import verification, so partial source/venv state is not treated as an installed managed runtime.

## Round 227 Linux install note — import verification must use generated settings

Linux live testing exposed a third clean-machine installer issue after dependency ordering was fixed: importing `searx.webapp` during venv verification loads SearXNG settings and aborts when the upstream default `server.secret_key` (`ultrasecretkey`) is still active. On first install, LJS has not yet written the final managed `settings.yml` because package installation runs before `configure()`.

Managed installation now writes a temporary LJS-owned `import-verification-settings.yml` with the same renderer used for final settings, including a generated random `server.secret_key`, and passes it to the verification subprocess through `SEARXNG_SETTINGS_PATH`. The final runtime settings are still written by `configure()` immediately before starting the sidecar. This keeps import verification realistic without accidentally treating upstream defaults as valid or overwriting the real managed settings.

## Round 229 — managed runtime and source-quality hardening

Linux live testing showed a non-installation failure mode after the managed runtime was working: SearXNG was installed and healthy during setup, but after a later LJS restart the sidecar process was not automatically started. Agent web-research calls therefore hit the configured localhost endpoint, received connection failures, and silently degraded to DuckDuckGo HTML fallback. Managed SearXNG must be treated like Jackett/slskd: if the user configured managed mode, LJS starts it after the web UI is reachable and web tools attempt one lazy managed start before falling back.

The same test also exposed a source-quality issue. Public web research must not treat stale snippets, unfetched pages, weak SEO schedule sites, social chatter, or degraded fallback results as authoritative. Current-news and next-season requests should search with the current year, the specific season/version when inferable, official/source-of-record terms, production/interview/renewal terms, and a recency window. Category hooks may prepare strong initial searches, but the LLM still owns research judgement and must continue searching or report uncertainty when the returned evidence is weak.

TV-specific rules added in this round:

- next-season/rumour searches preserve the user's concrete query and add current-year, next-season, renewal, production, interview, confirmed, and Apple/network terms;
- `rumor_check`/`rumour_check` are accepted aliases for the TV news-and-rumour category hook;
- next-episode metadata lookups request episode lists and must not treat local downloaded episode dictionaries as provider schedules;
- TV web interpretation rejects title-collision pages that mention the title but not TV/episode/series context;
- air-date signals require date mentions, not merely the words `air date` or `episode guide`;
- weak schedule pages are no longer classified as official/primary sources by the generic web layer.


## Round 231 LLM search-planning context

SearXNG quality depends on query planning as much as installation. LJS now provides reusable public-web research guidance to the LLM: current runtime date, source-quality hierarchy, freshness controls, search operator advice, and evidence sufficiency rules. The LLM should use SearXNG `categories` and `time_range` controls for current news/rumour/future-schedule questions rather than accepting generic first-page ranking as sufficient.

The managed provider still discovers candidate URLs only. `WebResearchService` fetches selected pages, stores provenance, and may order fetches within budget toward official/trade/reference/news sources for current topics. Category contracts supply domain-specific guidance; TV, for example, describes streamer/network/trade/interview and episode-guide source strategy without requiring generic code to enumerate every wording variant.
