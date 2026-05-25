# LJS Category Creation Guide

This guide defines the safe workflow for creating new LJS categories through the
agent or through the scaffold API. A category is the owner of its domain meaning:
parsing, metadata, naming, setup requirements, prompts, actions, workflows,
discovery/enrichment providers, and taste-profile dimensions. Global assistant,
storage, router, scheduler, and UI code must stay generic.

## Agent-Safe Creation Flow

1. Read this guide with `get_category_creation_guide`.
2. Call `plan_category_creation` with the category idea and any constraints the user already gave.
3. Ask concise, targeted follow-up questions for missing scope, item types, units, downloadability, metadata, taste dimensions, and provider preferences. Do not scaffold from a vague category name unless the user explicitly asks for a rough placeholder.
4. Call `research_category_services` for metadata/discovery APIs/databases comparable to TMDB in the new domain. Use web results as leads, not as automatic truth.
5. If the category is downloadable, call `research_category_download_profile` for domain-specific torrent/release/file-format/indexer conventions. This is the source for download vocabulary; do not reuse another category's release rules.
6. Discuss credible service/provider and download-profile tradeoffs with the user, including API keys, licensing, coverage, region/language constraints, indexer constraints, file formats, quality facets, and whether providers are optional or required.
7. Draft a declarative `CategorySpec`. Do **not** draft arbitrary Python code.
8. Include category-owned taste dimensions, lifecycle fields, suggestion types, invalidation triggers, `discovery_sources`, `provider_research`, `download_profile`, and `download_profile_research` leads when relevant.
9. Call `preview_category_scaffold(spec)` and show the rendered file list, warnings, and important snippets to the user.
10. Wait for explicit user approval.
11. Call `apply_category_scaffold(spec, approved=true)` only after approval.
12. After installation, the registry auto-discovers the category from `src/core/categories/custom/`.
13. Run or ask the user/agent runner to run the generated smoke test plus category architecture guard before relying on the category in production.

The preview/apply split is intentional. LLM-generated categories must be reviewable before anything is written to disk.

## Required CategorySpec Fields

Minimum useful spec:

```json
{
  "category_id": "video_games",
  "display_name": "Video Games",
  "description": "Interactive games tracked as category items with platform, studio, mechanics, and completion-state metadata.",
  "default_folder": "Video Games",
  "media_kind": "interactive_media",
  "capabilities": ["metadata", "downloadable"],
  "metadata_providers": ["igdb", "steam"],
  "item_types": ["game", "dlc", "expansion"],
  "identifiers": ["title", "platform", "storefront", "external_id"],
  "lifecycle_fields": ["release_status", "current_version", "latest_known_version", "platform", "dlc_state"],
  "suggestion_types": ["missing_item", "version_upgrade", "dlc_completion", "better_release", "metadata_repair"],
  "invalidation_triggers": ["metadata_changed", "library_changed", "taste_changed", "download_completed", "new_version_detected", "manual_refresh", "policy_version_changed"],
  "default_check_interval_days": 30,
  "taste_dimensions": {
    "mechanics": 0.75,
    "themes": 0.60,
    "genres": 0.35,
    "developers": 0.55,
    "platforms": 0.20,
    "difficulty": 0.45,
    "session_length": 0.35
  }
}
```

`category_id` must be lowercase snake_case. `class_name` is optional; the scaffold derives `VideoGamesCategory` from `video_games`.

## Generated Files

The scaffold currently generates:

```text
src/core/categories/custom/<category_id>.py
src/core/categories/prompts/<category_id>.md
config/category-templates/<category_id>.yaml
tests/test_category_<category_id>.py
```

Custom categories live under `src/core/categories/custom/` so built-in modules remain stable and generated files are easy to review. `CategoryRegistry.discover_categories()` imports this package at startup and after scaffold installation.


## Canonical Library Object Specification

Every category must define how library state becomes a canonical object. The core library stores generic item/unit/metadata envelopes only; it does not know the category's nested structure. New categories must implement or consciously inherit these hooks:

- `library_object_spec()` — declares item identity fields, unit types, unit fields, computed fields, and the source-of-truth rule.
- `library_item_from_scan(scanned)` — turns a scanned top-level item into the category item envelope.
- `library_units_from_scan(scanned)` — turns scanned files into category units such as files, chapters, tracks, issues, versions, DLC, or any other category-defined sub-object.
- `library_progress_from_scan(scanned, units)` — derives optional progress/status from the canonical units.
- `build_library_object(context)` — combines item, units, provider metadata, settings, and downloads into the one read model consumed by UI, suggestions, lifecycle, taste, and agent tools.

Required local-file evidence whenever available:

```json
{
  "file_path": "...",
  "size_bytes": 123456789,
  "estimated_bitrate_kbps": 4200,
  "resolution": "1080p",
  "codec": "h265",
  "language": "English",
  "subtitle_files": ["..."]
}
```

Rules:

- Do not make suggestions, UI, agent tools, or lifecycle code reconstruct state from raw `category_item_units`.
- Do not fix identity problems with alias searches in consumers. Fix canonical object construction.
- Do not use legacy progress rows as source of truth for missing-unit decisions. Progress is derived status, not library truth.
- If a category has complex nested structures, define them in `library_object_spec()` and build them in `build_library_object()`.

## Category-Owned Taste Memory

Every new category should define taste dimensions that make sense for that domain. These are cautious multipliers over evidence, not a dumb scoreboard.

Good examples:

```json
"taste_dimensions": {
  "mechanics": 0.75,
  "themes": 0.60,
  "developers": 0.55,
  "genres": 0.35,
  "platforms": 0.20
}
```

Guidance:

- Store raw taste evidence first: likes, dislikes, curiosity, downloads, watched/played/completed signals.
- Do not treat metadata existence as preference evidence.
- Do not infer broad genre dislike from one disliked item unless the user says so.
- Downloads/library presence are weak interest signals, not proof of liking.
- Negative feedback should stay item/facet-scoped unless repeated evidence or explicit user wording supports generalization.
- The category decides which facets are meaningful. Core memory must not learn hard-coded movie/game/book semantics.

## Category-Owned Lifecycle and Suggestions

Every new category must describe when item work is useful. This prevents startup refresh storms and keeps suggestions stable until a meaningful category-owned input changes. The core lifecycle engine persists state and fingerprints; it does not know what a season, version, edition, issue, track, or DLC means.

A useful spec should include:

```json
{
  "lifecycle_fields": ["release_status", "current_version", "latest_known_version", "library_units", "taste_snapshot"],
  "suggestion_types": ["missing_item", "version_upgrade", "better_release", "metadata_repair"],
  "invalidation_triggers": ["metadata_changed", "library_changed", "taste_changed", "download_completed", "download_failed", "manual_refresh", "policy_version_changed"],
  "default_check_interval_days": 30
}
```

Guidance:

- Lifecycle fields are the category-specific facts that decide whether an item should wake up again.
- Suggestion types must be meaningful in the category domain; do not reuse TV/movie names unless they truly fit.
- Invalidation triggers should include library/provider/preference/download changes that make saved suggestions stale.
- Stable items should keep saved suggestions until `next_check_at` or invalidation. Do not ask the scheduler to recompute everything at startup.
- Bump `policy_version` in the category implementation when policy changes should invalidate old ledgers.
- Use `lifecycle_decision(item, context)` for domain cadence: upcoming episode dates, new game versions, ongoing sagas, album releases, comic issue schedules, etc.

## Guided Design Questions

The agent should be inquisitive before scaffolding. Good category creation is a design conversation, not a one-shot file generator. Ask about:

- Scope: what belongs in the category and what does not.
- Item types: the nouns the category manages.
- Units: seasons, episodes, issues, tracks, chapters, DLC, expansions, editions, or other sub-items.
- Downloadability: whether torrent/search/queue flows matter and which domain-specific torrent constraints apply.
- Metadata: what facts help search, organization, and recommendations.
- Taste: which facets should positive/negative evidence affect cautiously.
- Lifecycle: what item states, future dates, versions, editions, or sub-units should control the next useful check.
- Suggestions: which saved suggestions can exist and what invalidates them.
- Discovery services: what external APIs/databases are authoritative enough for enrichment.
- Setup: which provider keys, local paths, or external apps the user is willing to configure.

Use `plan_category_creation` to get a structured checklist of missing questions. Ask only the questions that matter for the user's goal; do not bury the user in a questionnaire.

## Provider Research Workflow

For categories with metadata or discovery needs, the agent should call `research_category_services` before generating a scaffold. The tool runs focused web searches such as "video games metadata API database" and returns candidate provider leads.

Provider research should answer:

- What service is the domain equivalent of TMDB?
- Does it expose an API or just a website?
- Does it require API keys, OAuth, or paid tiers?
- What identifiers does it provide?
- What taste metadata can it contribute?
- How complete is the dataset for the user's language/region/platform?
- Is the provider suitable for discovery, enrichment, reviews, schedules, release dates, or storefront/pricing?

Do not automatically trust search results. The agent must present provider tradeoffs and ask the user which services to use before applying a scaffold. Use `provider_research` in the spec to preserve leads for later implementation, but only put reviewed/selected providers into `discovery_sources`.

## Category-Specific Download Profiles

Downloadability does not mean "use the torrent rules from another category." Torrent search constraints must be researched and owned by the category domain. The agent should call `research_category_download_profile` before scaffolding a downloadable category and use the returned web leads to synthesize a provisional profile.

The profile should answer domain questions such as:

- Which identifiers must match? Examples: title, author, narrator, platform, album artist, issue number, language.
- Which file/container formats are acceptable? Examples should come from user requirements or research, not from another category.
- Which quality facets matter in this domain? Examples: bitrate/duration for spoken audio, lossless/bitrate for music, platform/version for games, scan group/volume for comics, resolution/source/codec for video.
- Which unit conventions exist? Examples: chapters, tracks, issues, volumes, DLC, episodes, seasons.
- Which Jackett/Torznab categories or indexers are relevant? Preserve them as optional hints. The runtime default is to search all configured Jackett indexers first, because useful results can appear in unexpected trackers; category hints should improve ranking/querying, not hide valid candidates by default.
- Which reject terms are category-specific red flags? Keep uncertain ones provisional.

When a user says "I want it all," interpret that as "all relevant features inside the stated scope." It is not a license to broaden Audio Books into Books+Ebooks or to import unrelated release vocabulary. If the user wants maximum torrent coverage, prefer the Jackett all-configured-indexers strategy and keep category-specific indexers/categories as soft hints or an optional advanced narrowing mode.

`CategorySpec.download_profile` can preserve reviewed domain-specific rules, for example:

```json
{
  "torrent_categories": ["Audio/Audiobook", "Books/Audio"],
  "acceptable_formats": ["m4b", "mp3", "flac", "mka"],
  "quality_facets": ["narrator", "unabridged", "language", "bitrate", "duration", "edition"],
  "reject_terms": ["sample", "abridged unless requested"],
  "requires_review": true
}
```

`CategorySpec.download_profile_research` should preserve the web-search leads used to derive the profile so a human or future agent can audit why those conventions were chosen.

## Discovery and Enrichment Contracts

Use `metadata_providers` for simple provider names. Use `discovery_sources` when the category needs richer provider declarations:

```json
"discovery_sources": [
  {
    "provider": "igdb",
    "purpose": "metadata_enrichment",
    "required": false,
    "setting_keys": ["igdb_client_id", "igdb_client_secret"],
    "taste_metadata_keys": ["genres", "mechanics", "developers", "themes", "platforms"]
  },
  {
    "provider": "steam",
    "purpose": "storefront_metadata",
    "required": false,
    "setting_keys": [],
    "taste_metadata_keys": ["tags", "developers", "publishers", "platforms"]
  }
]
```

Provider-specific API clients and data normalization belong inside category-owned adapters or workflows, not in generic assistant code.

## Setup Requirements

Prefer manifest-driven setup requirements over hard-coded wizard logic. The base category automatically advertises:

- a category library folder;
- Jackett/Torznab when the category is downloadable;
- TMDB when the category declares `tmdb` metadata;
- TVMaze when the category declares `tvmaze` metadata;
- general web search as a recommended assistant research provider.

Override `setup_requirements(settings)` only when the category has truly unique requirements. Future provider-specific setup should ideally be derived from `discovery_sources`.

Compass renders per-category paths, provider toggles, and service credentials from the category manifest and ignored live `config/categories/<category_id>.yaml`, bootstrapped from tracked `config/category-templates/<category_id>.yaml`. Do **not** add category-specific service fields to a global settings panel. Put public default category-owned values in the category template using sections such as `metadata.providers`, `scheduler`, `storage`, and `lifecycle_policy`; declare external credentials through `provider_setup_requirements()` / `setup_requirements()` so the UI can show them inside the owning category. The Advanced Category Contracts panel is read-only diagnostics for this manifest contract, not an editable config surface.

## Required Class Contract

Each generated category subclasses `CategoryMedia` and should eventually define or override:

- `category_id`
- `display_name`
- `default_folder`
- `capabilities`
- `metadata_provider_names`
- `supported_operations`
- `llm_profile()`
- `get_properties(settings)`
- `discovery_contract()`
- `taste_profile_schema()`
- `taste_dimension_weights()`
- `lifecycle_policy()`
- `lifecycle_decision(item, context)`
- `suggestion_policy()`
- `parse_name(name)`
- `scan(root_path, existing_keys=None)`

A scaffold may start conservative with `scan()` returning `[]`; real file discovery must be added only with fixtures and tests.

## Guardrails

Do not add category-specific branches to generic layers. Category-specific knowledge belongs in:

```text
src/core/categories/custom/<category_id>.py
src/core/categories/prompts/<category_id>.md
src/core/categories/metadata/*
src/core/categories/workflows/*
tests for that category
```

Do not add global tools for category-specific metadata or destructive actions. Expose them through category actions, category workflows, generic category item APIs, or category-owned provider adapters.

Never let the agent install or execute arbitrary code as part of category creation. The scaffold service renders a restricted template from a validated declarative spec.

## Minimum Test Checklist

- Scaffold preview contains category module, prompt, category YAML, and smoke test.
- Manifest includes properties, setup requirements, discovery sources, actions, workflows, and prompt metadata.
- Parser covers representative filenames and release titles once parsing is implemented.
- Scan handles empty folders and malformed files safely.
- Destructive actions return `ActionReceipt` and require confirmation.
- Category taste dimensions appear in `taste_dimension_weights()` and profile schema.
- Category lifecycle fields, suggestion types, and invalidation triggers appear in `lifecycle_policy()`.
- Saved suggestions are not recomputed unless due, new, forced, or invalidated.
- Architecture guard tests pass.

## Category-Owned Assistant Context and Detail Payloads

Each category owns the information packet shown to the LLM after the active category has been resolved. Do **not** add category-specific `if category_id == ...` logic to assistant, scheduler, or UI layers. Override these hooks in the category subclass when the generic defaults are not expressive enough:

- `build_llm_context_packet(user_message, intent, settings, db, max_items, max_units)`
- `summarize_item_for_llm(item)`
- `summarize_unit_for_llm(unit)`
- `build_item_detail_payload(item_id, item, settings, db, artwork_manager)`
- `accepts_agent_unit_args(**kwargs)`, `build_agent_search_labels(...)`, `search_agent_candidates(...)`, and `rank_agent_search_results(...)`

The frontend detail modal renders generic components. The category owns the shape and meaning of the payload; the UI should not infer domain semantics that belong in the category.

## Multilingual Planning Rule

Do not rely on deterministic parsing of localized natural-language phrases such as “fifth season”, “quinta stagione”, or equivalents in other languages. The LLM must interpret the user's language and emit structured tool arguments according to the active category's schema. Regex/parser normalization is only a safety fallback for release-title formats such as `S05E03`.

## Generic Metadata Lookup for LLM Media Questions

Do **not** add bespoke tools or workflows for every factual media question. Categories expose semantics and local-library workflows, while open-ended factual metadata questions should use `metadata_lookup` when it applies, or a category-owned metadata adapter when the domain is not covered.

## Search, Download, and Cleanup Hooks

Generated categories must not rely on global TV/movie search behavior. Define these hooks when the category has any non-trivial unit model or quality logic:

- `build_search_query(item, unit_label, language)` for the primary query.
- `build_alternative_search_queries(item, unit_label, language)` for release-name variants.
- `validate_search_result_for_request(result, item, unit_label)` for category-owned candidate filtering.
- `quality_reference_for_search(item, unit_label, context)` when local canonical objects can explain useful size/bitrate/resolution context.
- `discovery_already_satisfied(item, unit_label, context)` to prevent duplicate downloads from canonical state.
- `download_coordinates_from_search_result(result, item, unit_label)` only while old download rows still need compatibility coordinates.

For cleanup and library-file listing, define:

- `matches_external_media_type(source, media_type)` for integrations such as Plex.
- `library_file_records_from_scan(scanned)` for category-owned file rows shown to the agent/UI.
- `file_record_matches_selector(file_info, **selectors)` for category-owned deletion/listing selectors.

Do not add new parsing or selector logic to `SearchPipeline`, `ContentCleanup`, scheduler, repositories, or global assistant tools. The core passes context; the category decides what its unit labels and file selectors mean.

## Built-in General Files Category

`General Files` (`category_id: general`) is now a built-in conservative catch-all for exact miscellaneous file targets. Do **not** scaffold another generic catch-all category, and do **not** use a custom category as a bypass around richer domain categories. If the user's idea is simply "misc downloads", "random torrents", or "general files", point them to the built-in General Files category and its single required setup field: `library_path`.

Rules for the category creation assistant:

- Richer installed categories win over General. A new category should be proposed only when the domain has stable nouns, metadata, quality facets, lifecycle rules, or organization needs that General cannot safely express.
- General is exact-target-first: a concrete filename/title/format is required. Vague domains such as "games", "books", "music", or "courses" should become real categories when the user wants ongoing library behavior.
- Never scaffold executable/software/crack/keygen/activator-oriented categories without a separate safety and policy review.
- Setup UI is manifest-driven. Newly installed categories should expose setup requirements through `CategoryManifest` / `setup_requirements()` so the frontend can detect them and prompt the user to review required paths or keys.
- Custom categories still belong under `src/core/categories/custom/`; built-in modules such as General Files remain under `src/core/categories/` and should be edited deliberately, not generated by the scaffold.
