"""
Category custom properties configuration tools for LJS.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING
from urllib.parse import urlparse

from loguru import logger

from src.ai.tools.base import AgentTool
from src.ai.category_tool_factory import CategoryToolFactory
from src.core.models import ActionReceipt, ToolExecutionContext, Intent

if TYPE_CHECKING:
    from src.core.categories.registry import CategoryRegistry
    from src.core.config import SettingsManager


class GetCategoryDefinitionsTool:
    """Return registered categories and their properties."""

    name = "get_category_definitions"
    description = (
        "Retrieve the list of all registered media categories (e.g. TV shows, movies) "
        "and their custom properties (like library paths, naming templates, update timers) "
        "including their descriptions, types, and currently configured values."
    )
    intents = {Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["category_registry", "settings_manager"]

    def __init__(self, category_registry: Optional[CategoryRegistry] = None, settings_manager: Optional[SettingsManager] = None) -> None:
        self._registry = category_registry
        self._sm = settings_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Execute get_category_definitions tool."""
        logger.info("Tool: reading category definitions")
        if not self._registry:
            return {"error": "Category registry not available"}
        if not self._sm:
            return {"error": "Settings manager not available"}
            
        settings = self._sm.settings
        categories = []
        for cat in self._registry.list_all():
            manifest = cat.manifest(settings=settings)
            categories.append(manifest.model_dump())
            
        return {"categories": categories}


class ConfigureCategoryPropertyTool:
    """Configure a specific category property value."""

    name = "configure_category_property"
    description = (
        "Set or update a specific configuration property of a media category. "
        "For example, change the TV show library_path or customize ended_update_interval_days."
    )
    intents = {Intent.CONFIG}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["category_registry", "settings_manager"]

    def __init__(self, category_registry: Optional[CategoryRegistry] = None, settings_manager: Optional[SettingsManager] = None) -> None:
        self._registry = category_registry
        self._sm = settings_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "category_id": {
                    "type": "string",
                    "description": "The target category identifier (e.g. 'tv', 'movie')."
                },
                "property_name": {
                    "type": "string",
                    "description": "The name of the custom property to configure."
                },
                "value": {
                    "type": "string",
                    "description": "The new value for the property. Booleans can be passed as 'true'/'false'."
                }
            },
            "required": ["category_id", "property_name", "value"]
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Execute configure_category_property tool."""
        category_id = arguments.get("category_id")
        property_name = arguments.get("property_name")
        value = arguments.get("value")
        
        logger.info(f"Tool: configuring category property {category_id}.{property_name} = {value}")
        if not self._registry:
            return {"error": "Category registry not available"}
        if not self._sm:
            return {"error": "Settings manager not available"}
            
        cat = self._registry.get(category_id)
        if not cat:
            return {"error": f"Category '{category_id}' not found."}
            
        try:
            settings = self._sm.settings
            cat.set_property_value(settings, property_name, value)
            self._sm.save(settings)
            
            # Retrieve updated property to return current state
            props = cat.get_properties(settings)
            updated_prop = next((p for p in props if p.name == property_name), None)
            return {
                "status": "ok",
                "message": f"Successfully updated property '{property_name}' on category '{category_id}' to: {updated_prop.value if updated_prop else value}"
            }
        except Exception as e:
            return {"error": str(e)}


class GetCategoryManifestTool:
    """Return one category manifest including UI sections, actions, and LLM summary."""

    name = "get_category_manifest"
    description = (
        "Retrieve the read-only manifest for one media category, including category-owned settings fields, UI sections, "
        "category capabilities, declared actions, and LLM-oriented summary."
    )
    intents = {Intent.CONFIG, Intent.CHAT, Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["category_registry", "settings_manager"]

    def __init__(self, category_registry: Optional[CategoryRegistry] = None, settings_manager: Optional[SettingsManager] = None) -> None:
        self._registry = category_registry
        self._sm = settings_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "category_id": {"type": "string", "description": "The target category identifier."},
            },
            "required": ["category_id"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Execute get_category_manifest tool."""
        if not self._registry or not self._sm:
            return {"error": "Category registry or settings manager not available"}
        category_id = arguments.get("category_id")
        category = self._registry.get(category_id)
        if not category:
            return {"error": f"Category '{category_id}' not found"}
        return category.manifest(settings=self._sm.settings).model_dump()


class ExecuteCategoryActionTool:
    """Execute a category-declared action by category and action name."""

    name = "execute_category_action"
    description = (
        "Execute an action declared by a category manifest. Use this only for actions listed "
        "by get_category_manifest or get_category_definitions."
    )
    intents = {Intent.CONFIG, Intent.CHAT, Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["category_registry", "settings_manager"]

    def __init__(self, category_registry: Optional[CategoryRegistry] = None, settings_manager: Optional[SettingsManager] = None) -> None:
        self._registry = category_registry
        self._sm = settings_manager

    def parameters(self) -> dict:
        """Return the public tool parameter schema.

        The schema is consumed by the LLM runtime and should remain
        backward-compatible.  Add optional fields for extensions whenever
        possible, and keep validation rules mirrored in execute().
        """
        return {
            "type": "object",
            "properties": {
                "category_id": {"type": "string"},
                "action_name": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["category_id", "action_name"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Execute a category action and return an action receipt."""
        if not self._registry:
            return {"error": "Category registry not available"}
        category = self._registry.get(arguments.get("category_id"))
        if not category:
            return {"error": f"Category '{arguments.get('category_id')}' not found"}
        receipt = await category.execute_action(
            action_name=arguments.get("action_name"),
            arguments=arguments.get("arguments") or {},
            context=context,
        )
        if isinstance(receipt, ActionReceipt):
            return receipt.model_dump()
        return receipt




class CategoryDesignHelpers:
    """Public helper methods shared by category design tools."""

    @staticmethod
    def clean_list(value: Any) -> list[str]:
        """Normalize comma-separated or list-like tool arguments."""
        if not value:
            return []
        if isinstance(value, str):
            raw = [part.strip() for part in value.split(",")]
        else:
            raw = [str(part).strip() for part in value]
        seen = set()
        out = []
        for item in raw:
            if item and item.lower() not in seen:
                seen.add(item.lower())
                out.append(item)
        return out

    @staticmethod
    def snake_case(value: str) -> str:
        """Derive a safe category id from a display name."""
        import re
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
        return cleaned or "custom_category"

    @staticmethod
    def provider_queries(category_name: str, description: str, item_types: list[str], metadata_needs: list[str]) -> list[str]:
        """Return focused web-search queries for metadata/discovery provider research."""
        subject = category_name.strip() or description.strip() or "media category"
        item_fragment = " ".join(item_types[:3]) if item_types else subject
        need_fragment = " ".join(metadata_needs[:4]) if metadata_needs else "metadata database API"
        queries = [
            f"{subject} metadata API database",
            f"{subject} open data API",
            f"{item_fragment} metadata API",
            f"{subject} {need_fragment} API",
            f"best {subject} metadata provider API",
        ]
        return CategoryDesignHelpers.dedupe_queries(queries)[:5]

    @staticmethod
    def download_profile_queries(category_name: str, description: str, item_types: list[str]) -> list[str]:
        """Return focused searches for category-specific release/download conventions.

        These queries deliberately do not carry over the app's built-in movie/TV
        torrent vocabulary. They ask the web for the domain's own naming, file
        format, and indexer conventions so the LLM can build a download profile
        from evidence instead of from stale media defaults.
        """
        subject = category_name.strip() or description.strip() or "media category"
        item_fragment = " ".join(item_types[:3]) if item_types else subject
        queries = [
            f"{subject} torrent naming conventions release tags",
            f"{subject} release filename format conventions",
            f"{subject} file formats quality bitrate language tags",
            f"Jackett Torznab {subject} categories search parameters",
            f"{item_fragment} download naming best practices",
        ]
        return CategoryDesignHelpers.dedupe_queries(queries)[:5]

    @staticmethod
    def dedupe_queries(queries: list[str]) -> list[str]:
        """Return queries with case-insensitive de-duplication."""
        deduped: list[str] = []
        seen: set[str] = set()
        for query in queries:
            normalized = " ".join(str(query).split()).strip()
            key = normalized.lower()
            if normalized and key not in seen:
                seen.add(key)
                deduped.append(normalized)
        return deduped

    @staticmethod
    def hit_payload(hit: Any) -> dict[str, str]:
        """Normalize a search hit object/dict into the fields category research needs."""
        if hasattr(hit, "model_dump"):
            payload = hit.model_dump()
        elif isinstance(hit, dict):
            payload = hit
        elif all(hasattr(hit, attr) for attr in ("title", "url")):
            payload = {
                "title": getattr(hit, "title", ""),
                "url": getattr(hit, "url", ""),
                "snippet": getattr(hit, "snippet", ""),
                "source": getattr(hit, "source", ""),
            }
        else:
            payload = {"title": str(hit), "url": "", "snippet": ""}
        return {
            "title": str(payload.get("title") or ""),
            "url": str(payload.get("url") or ""),
            "snippet": str(payload.get("snippet") or ""),
            "source": str(payload.get("source") or ""),
        }

class PlanCategoryCreationTool:
    """Help the agent collect enough domain detail before scaffolding a category."""

    name = "plan_category_creation"
    description = (
        "Analyze a proposed new category and return targeted questions the agent should ask before scaffolding. "
        "Use this early when the user says they want to add a category, so the agent can understand item types, "
        "metadata, discovery providers, downloads, sub-units, naming, and taste dimensions instead of guessing."
    )
    intents = {Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies: list[str] = []

    def parameters(self) -> dict:
        """Return the public planning schema."""
        return {
            "type": "object",
            "properties": {
                "category_name": {"type": "string", "description": "User-facing category name, e.g. Video Games."},
                "category_description": {"type": "string", "description": "What the category should manage, if known."},
                "intended_use": {"type": "string", "description": "How the user expects to use the category."},
                "known_item_types": {"type": "array", "items": {"type": "string"}},
                "known_metadata_needs": {"type": "array", "items": {"type": "string"}},
                "known_discovery_sources": {"type": "array", "items": {"type": "string"}},
                "downloadable": {"type": "boolean", "description": "Whether the category should search/download items."},
                "episodic_or_unit_based": {"type": "boolean", "description": "Whether items have seasons/episodes/chapter/track-like units."},
                "user_answers": {"type": "object", "description": "Any answers already supplied by the user."},
            },
            "required": ["category_name"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Return missing design questions and provider-research guidance."""
        category_name = str(arguments.get("category_name") or "").strip()
        description = str(arguments.get("category_description") or "").strip()
        intended_use = str(arguments.get("intended_use") or "").strip()
        item_types = CategoryDesignHelpers.clean_list(arguments.get("known_item_types"))
        metadata_needs = CategoryDesignHelpers.clean_list(arguments.get("known_metadata_needs"))
        discovery_sources = CategoryDesignHelpers.clean_list(arguments.get("known_discovery_sources"))
        answers = arguments.get("user_answers") or {}
        downloadable = arguments.get("downloadable")
        unit_based = arguments.get("episodic_or_unit_based")

        if self._looks_like_builtin_general_request(category_name, description, intended_use, item_types):
            return {
                "category_name": category_name,
                "readiness": "builtin_category_available",
                "builtin_category_id": "general",
                "message": (
                    "General Files is already built in for exact miscellaneous file targets. "
                    "Do not scaffold a duplicate catch-all category; configure general.library_path instead. "
                    "Create a custom category only when the domain has stable metadata, units, lifecycle, "
                    "quality facets, or organization rules that General cannot safely express."
                ),
                "questions": [],
                "provider_research_queries": [],
                "download_profile_research_queries": [],
                "recommended_next_step": "Show the user the built-in General Files category and ask them to configure its library_path in Compass → Library Categories; Advanced Category Contracts is read-only diagnostics.",
                "minimum_spec_outline": {"category_id": "general", "display_name": "General Files"},
            }

        questions = []
        if not description and not intended_use:
            questions.append(self._question(
                "scope",
                f"What exactly should the {category_name} category manage, and what should be out of scope?",
                "The category description becomes the router/LLM contract and prevents the agent from building a vague generic bucket.",
                examples=["PC/console games only, not mods", "Audiobooks, but not ebooks", "Comics including series/issues"],
            ))
        if not item_types:
            questions.append(self._question(
                "item_types",
                "What are the main item types and optional subtypes?",
                "Item types drive identifiers, UI labels, parsing examples, and category-owned taste evidence.",
                examples=["game, dlc, expansion", "album, single, track", "book, series, volume"],
            ))
        if downloadable is None:
            questions.append(self._question(
                "downloadable",
                "Should this category be able to search/download items, or is it only for tracking/library metadata?",
                "Downloadable categories need category-specific release vocabulary, file-format conventions, and optional Jackett/Torznab hints. The runtime default should still search all configured Jackett indexers unless the user/category explicitly requests narrowing.",
                examples=["Yes, torrents/downloads matter", "No, just track metadata and recommendations"],
            ))
        if unit_based is None:
            questions.append(self._question(
                "units",
                "Do items have internal units such as seasons, episodes, chapters, issues, discs, tracks, or DLC?",
                "Unit-aware categories need dedicated structured arguments so the LLM does not hide localized phrases inside names.",
                examples=["games can have DLC", "comics have series/issues", "music albums have tracks"],
                required=False,
            ))
        if not metadata_needs:
            questions.append(self._question(
                "metadata",
                "Which metadata matters for recommendations, organization, and search?",
                "The category owns metadata semantics; this is where future taste dimensions come from.",
                examples=["platforms, developers, mechanics, genres", "authors, narrators, series, duration", "artists, labels, moods, release year"],
            ))
        if not discovery_sources:
            questions.append(self._question(
                "services",
                "Are there known metadata/discovery services you trust for this domain, or should I research options?",
                "A new category should not hard-code guesses. The agent should web-research services comparable to TMDB for that domain.",
                examples=["IGDB/RAWG/Steam for games", "MusicBrainz/Discogs for music", "OpenLibrary/Google Books for books"],
            ))
        if "taste" not in answers and not any("taste" in str(k).lower() for k in answers.keys()):
            questions.append(self._question(
                "taste_dimensions",
                "What kinds of likes/dislikes should the category learn from?",
                "Taste memory should score category-specific facets, not broad metadata blindly.",
                examples=["mechanics and mood more than platform", "narrator and pacing more than publisher", "director/tone more than country"],
                required=False,
            ))

        search_terms = CategoryDesignHelpers.provider_queries(category_name, description, item_types, metadata_needs)
        download_terms = CategoryDesignHelpers.download_profile_queries(category_name, description, item_types)
        readiness = "ready_for_provider_research" if len([q for q in questions if q["required"]]) <= 2 else "needs_user_answers"
        return {
            "category_name": category_name,
            "readiness": readiness,
            "questions": questions,
            "provider_research_queries": search_terms,
            "download_profile_research_queries": download_terms if downloadable else [],
            "recommended_next_step": (
                "Ask the required questions before previewing a scaffold. After the answers are known, call "
                "research_category_services for metadata providers. If the category is downloadable, also call "
                "research_category_download_profile so torrent/search conventions come from the category's own domain."
            ),
            "minimum_spec_outline": {
                "category_id": CategoryDesignHelpers.snake_case(category_name),
                "display_name": category_name,
                "description": description or f"{category_name} category; refine after user answers.",
                "item_types": item_types,
                "capabilities": ["metadata"] + (["downloadable"] if downloadable else []),
                "metadata_providers": discovery_sources,
                "taste_dimensions": {key: 0.35 for key in metadata_needs},
            },
        }

    @staticmethod
    def _looks_like_builtin_general_request(category_name: str, description: str, intended_use: str, item_types: list[str]) -> bool:
        """Return true when the user is asking for the existing General Files category."""
        text = " ".join([category_name, description, intended_use, " ".join(item_types)]).lower()
        if not text.strip():
            return False
        direct_names = {"general", "general files", "misc", "miscellaneous", "misc downloads", "random torrents"}
        if category_name.strip().lower() in direct_names:
            return True
        catch_all_markers = ("catch all", "catch-all", "miscellaneous", "random torrent", "one-off", "generic download", "general file")
        return any(marker in text for marker in catch_all_markers)

    @staticmethod
    def _question(qid: str, text: str, why: str, examples: list[str], required: bool = True) -> dict[str, Any]:
        return {"id": qid, "question": text, "why": why, "examples": examples, "required": required}




class ResearchCategoryServicesTool:
    """Search the web for metadata/discovery providers for a proposed category."""

    name = "research_category_services"
    description = (
        "Research web services, APIs, and databases comparable to TMDB for a proposed category. "
        "Use this before preview_category_scaffold so discovery_sources are based on current provider options, "
        "not guesses or hard-coded movie/TV assumptions. Results are leads for review, not automatic approval."
    )
    intents = {Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["settings_manager"]

    def __init__(self, settings_manager: Optional[SettingsManager] = None, search_service: Any = None) -> None:
        self._settings_manager = settings_manager
        self._search_service = search_service

    def parameters(self) -> dict:
        """Return provider research schema."""
        return {
            "type": "object",
            "properties": {
                "category_name": {"type": "string"},
                "category_description": {"type": "string"},
                "item_types": {"type": "array", "items": {"type": "string"}},
                "metadata_needs": {"type": "array", "items": {"type": "string"}},
                "max_results_per_query": {"type": "integer", "description": "Default 4; keep low to avoid noisy provider research."},
            },
            "required": ["category_name"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Run focused web searches and return provider leads plus scaffold hints."""
        category_name = str(arguments.get("category_name") or "").strip()
        description = str(arguments.get("category_description") or "").strip()
        item_types = CategoryDesignHelpers.clean_list(arguments.get("item_types"))
        metadata_needs = CategoryDesignHelpers.clean_list(arguments.get("metadata_needs"))
        max_results = int(arguments.get("max_results_per_query") or 4)
        queries = CategoryDesignHelpers.provider_queries(category_name, description, item_types, metadata_needs)
        service = self._search_service or self._make_search_service()
        if service is None:
            return {
                "ok": False,
                "error": "Web search service is not available. Configure web search before provider research.",
                "suggested_queries": queries,
            }

        grouped_results: list[dict[str, Any]] = []
        provider_leads: dict[str, dict[str, Any]] = {}
        for query in queries:
            try:
                result = await service.search(query, max_results=max(1, min(max_results, 8)))
            except Exception as exc:  # pragma: no cover - network/provider defensive guard
                grouped_results.append({"query": query, "ok": False, "error": str(exc), "results": []})
                continue
            hits = [CategoryDesignHelpers.hit_payload(hit) for hit in getattr(result, "hits", [])]
            grouped_results.append({
                "query": query,
                "provider": getattr(result, "provider", "unknown"),
                "ok": bool(getattr(result, "ok", False)),
                "error": getattr(result, "error", None),
                "results": hits,
            })
            for hit in hits:
                lead = self._lead_from_hit(hit, metadata_needs)
                if not lead:
                    continue
                existing = provider_leads.setdefault(lead["provider"], lead)
                existing.setdefault("source_urls", [])
                if hit["url"] not in existing["source_urls"]:
                    existing["source_urls"].append(hit["url"])
                existing.setdefault("evidence", [])
                existing["evidence"].append({"title": hit["title"], "snippet": hit["snippet"], "query": query})

        leads = sorted(provider_leads.values(), key=lambda item: (len(item.get("source_urls", [])), len(item.get("evidence", []))), reverse=True)
        return {
            "ok": any(group.get("ok") for group in grouped_results),
            "category_name": category_name,
            "queries": queries,
            "research_results": grouped_results,
            "candidate_discovery_sources": leads[:8],
            "usage_guidance": [
                "Treat these as provider leads, not proven integrations.",
                "Read official API/docs pages before requiring credentials or generating provider-specific adapters.",
                "Copy only reviewed providers into CategorySpec.discovery_sources; keep unknown providers optional.",
                "Ask the user which provider tradeoffs they prefer when multiple credible options exist.",
            ],
            "next_step": "Discuss credible providers with the user, then include selected ones in preview_category_scaffold.discovery_sources.",
        }

    def _make_search_service(self) -> Any:
        if not self._settings_manager:
            return None
        from src.search.web.service import WebSearchService
        from src.core.models import WebSearchConfig

        config = self._settings_manager.settings.web_search if self._settings_manager else WebSearchConfig()
        return WebSearchService(config)

    @staticmethod
    def _lead_from_hit(hit: dict[str, str], metadata_needs: list[str]) -> dict[str, Any] | None:
        url = hit.get("url") or ""
        if not url:
            return None
        domain = urlparse(url).netloc.lower().split(":")[0]
        if domain.startswith("www."):
            domain = domain[4:]
        if not domain or any(domain.endswith(noisy) for noisy in ("google.com", "bing.com", "reddit.com")):
            return None
        provider = domain.split(".")[0].replace("-", "_")
        title_snippet = f"{hit.get('title','')} {hit.get('snippet','')}".lower()
        api_like = any(token in title_snippet for token in ("api", "database", "developer", "docs", "metadata", "open data"))
        if not api_like:
            return None
        taste_keys = metadata_needs or ["display_name", "overview", "genres", "tags", "creators", "studios", "release_year"]
        setting_keys = []
        if any(token in title_snippet for token in ("api key", "oauth", "client id", "token")):
            setting_keys.append(f"{provider}_api_key")
        return {
            "provider": provider,
            "provider_domain": domain,
            "purpose": "metadata_enrichment",
            "required": False,
            "setting_keys": setting_keys,
            "taste_metadata_keys": taste_keys,
            "source_urls": [url],
            "requires_review": True,
        }

class ResearchCategoryDownloadProfileTool:
    """Search the web for category-specific torrent/release conventions."""

    name = "research_category_download_profile"
    description = (
        "Research download, torrent, release-name, file-format, and Jackett/Torznab conventions "
        "for a proposed downloadable category. Use this before preview_category_scaffold for "
        "downloadable categories so the download_profile is based on the category domain rather "
        "than inherited movie/TV assumptions."
    )
    intents = {Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["settings_manager"]

    def __init__(self, settings_manager: Optional[SettingsManager] = None, search_service: Any = None) -> None:
        self._settings_manager = settings_manager
        self._search_service = search_service

    def parameters(self) -> dict:
        """Return download-profile research schema."""
        return {
            "type": "object",
            "properties": {
                "category_name": {"type": "string"},
                "category_description": {"type": "string"},
                "item_types": {"type": "array", "items": {"type": "string"}},
                "max_results_per_query": {"type": "integer", "description": "Default 4; keep low to avoid noisy convention research."},
            },
            "required": ["category_name"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Run focused convention searches and return download-profile leads."""
        category_name = str(arguments.get("category_name") or "").strip()
        description = str(arguments.get("category_description") or "").strip()
        item_types = CategoryDesignHelpers.clean_list(arguments.get("item_types"))
        max_results = int(arguments.get("max_results_per_query") or 4)
        queries = CategoryDesignHelpers.download_profile_queries(category_name, description, item_types)
        service = self._search_service or self._make_search_service()
        if service is None:
            return {
                "ok": False,
                "error": "Web search service is not available. Configure web search before download-profile research.",
                "suggested_queries": queries,
            }

        grouped_results: list[dict[str, Any]] = []
        convention_leads: list[dict[str, Any]] = []
        for query in queries:
            try:
                result = await service.search(query, max_results=max(1, min(max_results, 8)))
            except Exception as exc:  # pragma: no cover - network/provider defensive guard
                grouped_results.append({"query": query, "ok": False, "error": str(exc), "results": []})
                continue
            hits = [CategoryDesignHelpers.hit_payload(hit) for hit in getattr(result, "hits", [])]
            grouped_results.append({
                "query": query,
                "provider": getattr(result, "provider", "unknown"),
                "ok": bool(getattr(result, "ok", False)),
                "error": getattr(result, "error", None),
                "results": hits,
            })
            for hit in hits:
                lead = self._convention_lead_from_hit(hit, query)
                if lead:
                    convention_leads.append(lead)

        return {
            "ok": any(group.get("ok") for group in grouped_results),
            "category_name": category_name,
            "queries": queries,
            "research_results": grouped_results,
            "download_profile_research_leads": convention_leads[:12],
            "download_profile_schema_hint": {
                "search_terms": "category-specific words the torrent/search query should include",
                "required_facets": "facets that must match, e.g. title/author/narrator/language/platform",
                "preferred_facets": "facets that improve selection, e.g. format/bitrate/edition/source",
                "acceptable_formats": "file/container/format names supported by this category",
                "quality_facets": "domain-specific quality concepts supported by research",
                "reject_terms": "category-specific red flags supported by research",
                "unit_handling": "how multi-file units should be interpreted, e.g. chapters/tracks/issues/DLC",
                "jackett_categories": "Torznab/Jackett category hints if researched",
            },
            "usage_guidance": [
                "Treat these results as convention leads, not a final policy.",
                "Ask the LLM to synthesize a download_profile only from user requirements plus these researched leads.",
                "Do not copy movie/TV release vocabulary unless the researched results show it is relevant for this category.",
                "Keep uncertain conventions in design_notes or requires_review rather than hard requirements.",
            ],
            "next_step": "Discuss category-specific release/search conventions with the user, then include reviewed rules in preview_category_scaffold.download_profile and download_profile_research.",
        }

    def _make_search_service(self) -> Any:
        if not self._settings_manager:
            return None
        from src.search.web.service import WebSearchService
        from src.core.models import WebSearchConfig

        config = self._settings_manager.settings.web_search if self._settings_manager else WebSearchConfig()
        return WebSearchService(config)

    @staticmethod
    def _convention_lead_from_hit(hit: dict[str, str], query: str) -> dict[str, Any] | None:
        """Return a reviewable convention lead from one search hit.

        This intentionally extracts only evidence-bearing text and generic signal
        buckets. The LLM/user decide the actual domain policy.
        """
        url = hit.get("url") or ""
        if not url:
            return None
        title = hit.get("title") or ""
        snippet = hit.get("snippet") or ""
        text = f"{title} {snippet}".lower()
        signal_terms = (
            "naming", "filename", "release", "torrent", "torznab", "jackett",
            "format", "bitrate", "codec", "language", "tags", "category",
            "quality", "chapter", "track", "issue", "edition", "source",
        )
        if not any(term in text for term in signal_terms):
            return None
        domain = urlparse(url).netloc.lower().split(":")[0]
        if domain.startswith("www."):
            domain = domain[4:]
        return {
            "source_title": title,
            "source_url": url,
            "source_domain": domain,
            "query": query,
            "evidence_snippet": snippet[:500],
            "requires_review": True,
        }


class GetCategoryCreationGuideTool:
    """Return the controlled skill guide used for creating categories."""

    name = "get_category_creation_guide"
    description = (
        "Read the official LJS category creation guide. Use this before proposing, "
        "previewing, or installing a new category so the generated CategorySpec follows "
        "the category-first architecture, taste-profile contract, and safety rules."
    )
    intents = {Intent.CONFIG, Intent.CHAT}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies: list[str] = []

    def parameters(self) -> dict:
        """Return the public tool parameter schema."""
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Read the skill guide from disk."""
        from pathlib import Path

        guide = Path("skills/category_creation_guide.md")
        if not guide.exists():
            return {"error": "Category creation guide not found."}
        return {"content": guide.read_text(encoding="utf-8")}


class PreviewCategoryScaffoldTool:
    """Preview safe scaffold files for a new category from a declarative spec."""

    name = "preview_category_scaffold"
    description = (
        "Generate reviewable files for a new media/category type from a declarative "
        "CategorySpec. This only previews files; it never writes to disk. Use it before "
        "apply_category_scaffold. Include category-owned taste_dimensions and discovery_sources "
        "when the new category needs richer recommendations or metadata enrichment."
    )
    intents = {Intent.CONFIG}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies: list[str] = []

    def parameters(self) -> dict:
        """Return the CategorySpec tool schema."""
        return {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "object",
                    "description": "Declarative CategorySpec. Do not include arbitrary code.",
                    "properties": {
                        "category_id": {"type": "string", "description": "lowercase snake_case id, e.g. video_games"},
                        "class_name": {"type": "string", "description": "Optional PascalCase class name ending in Category."},
                        "display_name": {"type": "string"},
                        "description": {"type": "string"},
                        "default_folder": {"type": "string"},
                        "media_kind": {"type": "string"},
                        "capabilities": {"type": "array", "items": {"type": "string"}},
                        "metadata_providers": {"type": "array", "items": {"type": "string"}},
                        "discovery_sources": {"type": "array", "items": {"type": "object"}},
                        "provider_research": {"type": "array", "items": {"type": "object"}},
                        "download_profile_research": {"type": "array", "items": {"type": "object"}},
                        "design_notes": {"type": "string"},
                        "download_profile": {
                            "type": "object",
                            "description": "Category-specific download/search rules synthesized from user requirements and research_category_download_profile results.",
                        },
                        "item_types": {"type": "array", "items": {"type": "string"}},
                        "identifiers": {"type": "array", "items": {"type": "string"}},
                        "properties": {"type": "array", "items": {"type": "object"}},
                        "units": {"type": "array", "items": {"type": "object"}},
                        "taste_dimensions": {
                            "type": "object",
                            "description": "Category-owned evidence facet weights from 0..1, e.g. mechanics:0.75 for games.",
                            "additionalProperties": {"type": "number"},
                        },
                        "examples": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["category_id", "display_name", "description"],
                }
            },
            "required": ["spec"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Render the preview and include validation issues."""
        from src.core.categories.scaffold import CategoryScaffoldService
        from src.core.models import CategorySpec

        try:
            spec = CategorySpec(**(arguments.get("spec") or {}))
            service = CategoryScaffoldService()
            preview = service.preview(spec)
            issues = service.validate_preview(preview)
            payload = preview.model_dump()
            payload["validation_issues"] = issues
            payload["next_step"] = (
                "Show the preview to the user. Only call apply_category_scaffold after explicit approval."
            )
            return payload
        except Exception as exc:
            return {"error": str(exc)}


class ApplyCategoryScaffoldTool:
    """Install a reviewed category scaffold after explicit user approval."""

    name = "apply_category_scaffold"
    description = (
        "Write a previously previewed category scaffold to the project after explicit user approval. "
        "This installs template-generated category code, prompt guidance, a default category YAML, "
        "and a smoke test. It does not execute arbitrary code."
    )
    intents = {Intent.CONFIG}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["category_registry"]

    def __init__(self, category_registry: Optional[CategoryRegistry] = None) -> None:
        self._registry = category_registry

    def parameters(self) -> dict:
        """Return the approved CategorySpec tool schema."""
        return {
            "type": "object",
            "properties": {
                "spec": {"type": "object", "description": "The exact CategorySpec that was previewed and approved."},
                "approved": {"type": "boolean", "description": "Must be true only after explicit user approval."},
                "overwrite_existing": {
                    "type": "boolean",
                    "description": "Set true only after separately confirming replacement of existing scaffold files.",
                },
            },
            "required": ["spec", "approved"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Apply the scaffold and refresh dynamic category discovery."""
        from src.core.categories.scaffold import CategoryScaffoldService
        from src.core.models import CategorySpec

        try:
            spec = CategorySpec(**(arguments.get("spec") or {}))
            receipt = CategoryScaffoldService().apply(
                spec,
                approved=bool(arguments.get("approved")),
                overwrite_existing=bool(arguments.get("overwrite_existing")),
            )
            if receipt.status == "success" and self._registry:
                self._registry.discover_categories()
            return receipt.model_dump()
        except Exception as exc:
            return {"error": str(exc)}




class CategoryToolProvider:
    """Provides dynamic category configuration tools to the agent tool registry."""

    def __init__(
        self,
        category_registry: Optional[CategoryRegistry] = None,
        settings_manager: Optional[SettingsManager] = None,
        database: Any = None,
        scheduler: Any = None,
        search_aggregator: Any = None,
        downloader: Any = None,
        metadata_enricher: Any = None,
        artwork_manager: Any = None,
    ) -> None:
        self._registry = category_registry
        self._sm = settings_manager
        self._db = database
        self._scheduler = scheduler
        self._search_aggregator = search_aggregator
        self._downloader = downloader
        self._metadata_enricher = metadata_enricher
        self._artwork_manager = artwork_manager

    def get_tools(self) -> list:
        """Return generic category tools plus dynamic category action/workflow tools."""
        tools = [
            GetCategoryDefinitionsTool(category_registry=self._registry, settings_manager=self._sm),
            GetCategoryManifestTool(category_registry=self._registry, settings_manager=self._sm),
            ConfigureCategoryPropertyTool(category_registry=self._registry, settings_manager=self._sm),
            ExecuteCategoryActionTool(category_registry=self._registry, settings_manager=self._sm),
            GetCategoryCreationGuideTool(),
            PlanCategoryCreationTool(),
            ResearchCategoryServicesTool(settings_manager=self._sm),
            ResearchCategoryDownloadProfileTool(settings_manager=self._sm),
            PreviewCategoryScaffoldTool(),
            ApplyCategoryScaffoldTool(category_registry=self._registry),
        ]
        tools.extend(CategoryToolFactory(self._registry, context_factory=self._build_context).build_tools())
        return tools

    def _build_context(self, context: ToolExecutionContext) -> Any:
        """Build a category workflow context for LLM-invoked category tools.

        Dynamic category tools need the same runtime collaborators that UI
        category endpoints receive: database, search pipeline, aggregator,
        downloader, settings, and metadata/artwork services.  Without this
        context, LLM calls to dynamic category workflow tools would reach
        the category with only an audit shell and fail before discovery.
        """
        from src.core.categories.base import CategoryWorkflowContext

        pipeline = self._scheduler.get_search_pipeline() if self._scheduler else None
        return CategoryWorkflowContext(
            db=self._db,
            pipeline=pipeline,
            aggregator=self._search_aggregator,
            settings=self._sm.settings if self._sm else None,
            downloader=self._downloader,
            metadata_enricher=self._metadata_enricher,
            artwork_manager=self._artwork_manager,
            user_id=context.user_id,
            session_id=context.session_id,
        )
