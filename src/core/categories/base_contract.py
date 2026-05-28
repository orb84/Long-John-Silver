"""Manifest, LLM-context, and workflow contract mixins for media categories.

This module keeps category-facing UI/LLM policy separate from file and
organization mechanics.  New categories should override these hooks in their
own category classes only when their domain needs richer behavior; otherwise
inherit the safe generic defaults from CategoryContractMixin.
"""

from __future__ import annotations

from pathlib import Path
import importlib.util
import shutil
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from src.core.categories.base_context import CategoryContextMixin
from src.core.categories.identity import basename_from_pathish
from src.core.models import (
    ActionReceipt,
    CategoryActionDeclaration,
    CategoryLlmProfile,
    CategoryManifest,
    CategoryRuntimeDependency,
    CategorySetupRequirement,
    CategoryUiSection,
    CategoryWorkflowDeclaration,
)

if TYPE_CHECKING:
    from src.core.models import Settings


class CategoryContractMixin(CategoryContextMixin):
    """Provide UI manifest, assistant context, and workflow defaults.

    MediaCategory intentionally composes this mixin so that concrete media
    categories can focus on domain parsing and organization.  Override these
    methods in a subclass when a category has a stronger contract, and prefer
    delegating substantial logic to category services instead of growing a
    monolithic category class.
    """

    # ── Category manifest, UI, and LLM contract ────────────────────


    def category_runtime_config(self, settings: Optional['Settings'] = None) -> dict[str, Any]:
        """Return this category's effective flattened runtime configuration.

        The config originates from ignored ``config/categories/<category_id>.yaml`` and is
        hot-loaded into ``Settings.category_settings``.  Category code should use
        this helper instead of reaching into the settings mapping with ad-hoc
        paths, so nested sections such as metadata providers, scheduler flags,
        storage policy, and lifecycle policy stay category-owned.
        """
        if settings is None:
            return {}
        try:
            data = getattr(settings, "category_settings", {}) or {}
            value = data.get(self.category_id, {})
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def category_config_section(self, settings: Optional['Settings'], section: str) -> dict[str, Any]:
        """Return one nested category config section as a mapping."""
        value = self.category_runtime_config(settings).get(section)
        return value if isinstance(value, dict) else {}

    def category_service_config(self, settings: Optional['Settings'], service_id: str) -> dict[str, Any]:
        """Return this category's local configuration for one external service.

        Live values are read from ignored ``config/categories/<category_id>.yaml``
        under ``services.<service_id>``.  ``metadata.providers.<service_id>`` can contribute enable/disable flags, while credentials and service behavior live under ``services``. Category code should call this method instead of reading global service fields directly.
        """
        services = self.category_config_section(settings, "services")
        service_cfg = services.get(service_id) if isinstance(services, dict) else None
        result = dict(service_cfg) if isinstance(service_cfg, dict) else {}

        metadata = self.category_config_section(settings, "metadata")
        providers = metadata.get("providers") if isinstance(metadata, dict) else {}
        provider_cfg = providers.get(service_id) if isinstance(providers, dict) else None
        if isinstance(provider_cfg, dict):
            result = {**provider_cfg, **result}
        elif isinstance(provider_cfg, bool) and "enabled" not in result:
            result["enabled"] = provider_cfg
        return result

    def category_service_enabled(
        self,
        settings: Optional['Settings'],
        service_id: str,
        default: bool = True,
    ) -> bool:
        """Return whether one category-declared external service is enabled."""
        cfg = self.category_service_config(settings, service_id)
        if "enabled" in cfg:
            return bool(cfg.get("enabled"))
        return bool(default)

    def category_service_secret(
        self,
        settings: Optional['Settings'],
        service_id: str,
        key: str,
    ) -> str | None:
        """Return one service value from this category's effective config.

        Effective config may be inherited from an abstract base such as
        ``media``. There is deliberately no global fallback: category YAML is the
        authority for category-owned services.
        """
        cfg = self.category_service_config(settings, service_id)
        value = cfg.get(key)
        if value not in (None, ""):
            return str(value)
        return None

    def category_download_profile(self, settings: Optional['Settings']) -> dict[str, Any]:
        """Return category-owned download preferences such as quality/language."""
        return self.category_config_section(settings, "download_profile")

    def language_is_search_relevant(self) -> bool:
        """Return whether torrent search should use language as a hard facet.

        TV, movies, audiobooks, and ebooks often need language constraints;
        music albums usually do not.  The default preserves legacy media
        behavior, while definition-backed categories can opt out declaratively
        instead of leaking global TV/movie language preferences into every
        category.
        """
        return True

    def normalize_search_language(self, language: str | None, *, explicit: bool = False) -> str | None:
        """Return the category-approved language constraint for torrent search.

        Args:
            language: Candidate language constraint from the user, tracked item,
                or global settings.
            explicit: Whether the value came from an explicit tool argument.

        Returns:
            A normalized language string, or ``None`` when language should not
            affect this category's torrent query/ranking.
        """
        value = str(language or "").strip()
        if not value:
            return None
        return value if self.language_is_search_relevant() else None

    def uses_global_quality_profile(self) -> bool:
        """Return whether global video-oriented quality defaults apply here.

        Legacy media categories use the shared ``QualityProfile`` resolution and
        codec defaults.  New definition-backed domains can opt out so values
        like 1080p/720p do not leak into music, ebook, audiobook, or other
        non-video searches.
        """
        return True

    def category_tool_policy(self, settings: Optional['Settings']) -> dict[str, Any]:
        """Return category-owned LLM tool exposure preferences."""
        return self.category_config_section(settings, "tools")

    def category_llm_guidance(self, settings: Optional['Settings']) -> dict[str, Any]:
        """Return natural-language guidance loaded from the category config.

        This lets a tracked category definition teach the assistant domain behavior,
        release-name examples, ambiguity rules, and tool-use preferences without
        editing the global prompt builder. Ignored live YAML stores only
        private enable flags and preferences for that definition.
        """
        return self.category_config_section(settings, "llm_guidance")

    def category_configured_tool_names(self, settings: Optional['Settings']) -> list[str]:
        """Return tool names listed in ``tools.allowed_generic/category_workflows``.

        The names are declarative policy. The ToolRegistry still decides whether
        a named tool actually exists, and AgentToolPolicy still applies intent
        and risk gates. This prevents YAML from inventing executable plumbing
        while allowing category packages to document the tools they want the LLM
        to consider.
        """
        policy = self.category_tool_policy(settings)
        names: list[str] = []
        for key in ("allowed_generic", "category_workflows", "tools"):
            values = policy.get(key) if isinstance(policy, dict) else None
            if isinstance(values, list):
                names.extend(str(value) for value in values if value)
        return sorted(set(names))

    def category_runtime_dependencies(self, settings: Optional['Settings']) -> dict[str, Any]:
        """Return dependency declarations loaded from category YAML.

        Dependencies are declarative preflight facts, not executable plugin code.
        The category/workflow implementation decides how to use them.
        """
        return self.category_config_section(settings, "runtime_dependencies")

    def resolved_runtime_dependencies(self, settings: Optional['Settings']) -> list[CategoryRuntimeDependency]:
        """Return category runtime dependencies with local availability status."""
        raw = self.category_runtime_dependencies(settings)
        result: list[CategoryRuntimeDependency] = []
        for dep_id, dep_cfg in sorted((raw or {}).items()):
            if not isinstance(dep_cfg, dict):
                continue
            kind = str(dep_cfg.get("kind") or "binary")
            command = str(dep_cfg.get("command") or dep_id)
            configured = self._dependency_available(kind, command)
            severity = dep_cfg.get("severity") or ("required" if dep_cfg.get("required") else "info")
            try:
                result.append(CategoryRuntimeDependency(
                    id=str(dep_id),
                    label=str(dep_cfg.get("label") or dep_id),
                    kind=kind,
                    command=command,
                    required=bool(dep_cfg.get("required", False)),
                    configured=configured,
                    purpose=str(dep_cfg.get("purpose") or ""),
                    install_hint=str(dep_cfg.get("install_hint") or ""),
                    validation_action=dep_cfg.get("validation_action"),
                    severity=severity,
                ))
            except Exception as exc:
                logger.warning(f"Invalid runtime dependency declaration for {self.category_id}.{dep_id}: {exc}")
        return result

    @staticmethod
    def _dependency_available(kind: str, command: str) -> bool:
        """Check whether a declared dependency appears locally available."""
        if not command:
            return False
        if kind == "binary":
            return shutil.which(command) is not None
        if kind == "python_package":
            return importlib.util.find_spec(command) is not None
        # Services and other declarative dependencies need explicit category checks.
        return False

    def sanitized_service_settings(self, settings: Optional['Settings']) -> list[dict[str, Any]]:
        """Return category service settings without leaking secret values.

        Services are category-owned contracts: a category may declare TMDB,
        Trakt, Plex, OpenSubtitles, or future services with labels, purposes,
        enable flags, credentials, and LLM usage notes. The manifest exposes the
        contract and configured/unconfigured status, never raw secrets.
        """
        services = self.category_config_section(settings, "services")
        result: list[dict[str, Any]] = []
        for service_id, raw_cfg in sorted((services or {}).items()):
            if not isinstance(raw_cfg, dict):
                continue
            safe: dict[str, Any] = {"id": service_id}
            for key, value in raw_cfg.items():
                lower = key.lower()
                is_secret = any(token in lower for token in ("key", "token", "secret", "password"))
                if is_secret:
                    safe[f"{key}_configured"] = bool(value)
                    safe[f"{key}_secret"] = True
                else:
                    safe[key] = value
            result.append(safe)
        return result

    def metadata_provider_enabled(self, settings: Optional['Settings'], provider: str, default: bool = True) -> bool:
        """Return whether a category metadata/discovery provider is enabled.

        This reads ``metadata.providers.<provider>.enabled`` from the owning
        category config.  Missing values inherit ``default`` so category YAML can explicitly disable a provider without generic code learning provider semantics.
        """
        return self.category_service_enabled(settings, provider, default=default)

    def lifecycle_policy_from_settings(self, settings: Optional['Settings'] = None) -> dict[str, Any]:
        """Return lifecycle policy with category YAML overrides applied."""
        policy = dict(self.lifecycle_policy())
        configured = self.category_config_section(settings, "lifecycle_policy")
        if configured:
            policy.update(configured)
        return policy

    def llm_profile_for_settings(self, settings: Optional['Settings'] = None) -> CategoryLlmProfile:
        """Return the category LLM profile with YAML guidance merged in."""
        profile = self.llm_profile()
        guidance = self.category_llm_guidance(settings)
        if not guidance:
            return profile
        behavior = guidance.get("behavior")
        if isinstance(behavior, list):
            profile.tool_usage_notes.extend(str(item) for item in behavior if item)
        search_examples = guidance.get("search_examples")
        if isinstance(search_examples, list):
            profile.search_rules.append("Category release/query examples: " + "; ".join(str(item) for item in search_examples[:8]))
        extra_search = guidance.get("search_rules")
        if isinstance(extra_search, list):
            profile.search_rules.extend(str(item) for item in extra_search if item)
        extra_download = guidance.get("download_rules")
        if isinstance(extra_download, list):
            profile.download_rules.extend(str(item) for item in extra_download if item)
        return profile

    def router_brief(self) -> CategoryRouterBrief:
        """Return the compact category-router description."""
        return self.llm_profile().router_brief(self.display_name)

    def llm_profile(self) -> CategoryLlmProfile:
        """Return the LLM-oriented category profile.

        Subclasses should override this to add domain vocabulary,
        ambiguity rules, examples, and category-specific search/download
        rules. The default profile is intentionally generic so custom
        categories remain usable before they add richer guidance.
        """
        return CategoryLlmProfile(
            category_id=self.category_id,
            short_description=f"{self.display_name} media category.",
            user_facing_description=(
                f"{self.display_name} is a media category. I can use the category's "
                "registered tools, settings, and actions to help manage it."
            ),
            router_description=f"{self.display_name}: media handled by the {self.category_id} category.",
            domain_vocabulary=[self.display_name.lower(), self.category_id],
            item_types=[self.category_id],
            identifiers=["title", "library_path"],
            tool_usage_notes=[
                "Use only tools/actions declared by this category plus safe generic app tools.",
            ],
        )

    def taste_profile_schema(self) -> dict[str, Any]:
        """Return category-owned metadata keys useful for taste profiling.

        The core taste profiler stores and aggregates a normalized common subset,
        but each category may document richer domain fields here so the agent can
        research and record meaningful evidence for items outside the library.
        """
        return {
            "common_keys": [
                "display_name", "overview", "genres", "rating", "external_id",
                "provider", "release_year", "creators", "studios", "tags",
            ],
            "signal_types": ["mention", "curious", "like", "favorite", "dislike", "reject"],
        }

    def taste_profile_llm_instructions(self) -> list[str]:
        """Return guidance for recording category-scoped taste evidence."""
        return [
            "When the user discusses an item in this category, research or enrich it through category/provider tools when possible.",
            "Record weak signals for casual mentions and stronger signed signals for explicit likes/dislikes.",
            "Keep evidence scoped to this category; do not mix unrelated category taste into a global blob.",
            "Do not turn one liked/disliked item into a broad genre conclusion unless the user says that or repeated evidence supports it.",
        ]

    def taste_dimension_weights(self) -> dict[str, float]:
        """Return cautious metadata multipliers for derived taste facets.

        Categories can raise weights for dimensions that are highly diagnostic
        in their domain (for example game mechanics) and lower weak dimensions
        (for example platforms or broad languages).  These are multipliers over
        user-specific evidence, not standalone preference scores.
        """
        return {
            "genres": 0.22,
            "creators": 0.35,
            "studios": 0.32,
            "tags": 0.28,
        }

    def discovery_contract(self) -> list[dict[str, Any]]:
        """Return declarative discovery/enrichment services owned by this category.

        This static contract names provider families; live service details and
        secrets come from ``config/categories/<category_id>.yaml``.
        """
        return [
            {
                "provider": provider,
                "purpose": "metadata_enrichment",
                "required": False,
                "setting_keys": [f"category_config.{self.category_id}.services.{provider}"],
                "taste_metadata_keys": self.taste_profile_schema().get("common_keys", []),
            }
            for provider in self.metadata_provider_names
        ]

    async def prepare_search_item(self, item: Any, *, settings: Any, scan_result: Any | None = None) -> Any:
        """Return a category-adjusted copy of an item before torrent search.

        The generic search pipeline calls this hook instead of branching on
        category IDs for quality limits, language defaults, or other domain
        preparation. The default applies safe category ``download_profile``
        values when the item supports those fields.
        """
        profile = self.category_download_profile(settings)
        if not profile or not hasattr(item, "model_copy"):
            return item
        item_copy = item.model_copy(deep=True)
        language = profile.get("language")
        if language and hasattr(item_copy, "language") and not getattr(item_copy, "language", None):
            item_copy.language = language
        quality = getattr(item_copy, "quality", None)
        preferred_resolution = profile.get("preferred_resolution")
        if quality is not None and preferred_resolution and hasattr(quality, "preferred_resolution"):
            quality.preferred_resolution = str(preferred_resolution)
        size_mode = profile.get("size_limit_mode")
        if quality is not None and size_mode and hasattr(quality, "size_limit_mode"):
            quality.size_limit_mode = str(size_mode)
        return item_copy


    def ui_sections(self) -> list[CategoryUiSection]:
        """Return UI sections the frontend can render for this category."""
        return [
            CategoryUiSection(
                id="overview",
                title="Overview",
                component="metadata_summary",
                description="General metadata and library status for this category.",
            ),
            CategoryUiSection(
                id="files",
                title="Files",
                component="file_list",
                description="Files currently known for this category.",
            ),
            CategoryUiSection(
                id="downloads",
                title="Downloads",
                component="download_list",
                description="Downloads associated with this category.",
            ),
        ]

    def declare_actions(self) -> list[CategoryActionDeclaration]:
        """Declare category actions shared by UI and LLM tool policy."""
        return [
            CategoryActionDeclaration(
                name="scan_library",
                label="Scan Library",
                description="Scan the configured library path for this category.",
                parameters={
                    "type": "object",
                    "properties": {
                        "dry_run": {"type": "boolean", "description": "Preview changes without mutating files."},
                    },
                    "required": [],
                },
                requires_confirmation=False,
                destructive=False,
                risk_level="read",
                tool_name=f"{self.category_id}.scan_library",
            ),
            CategoryActionDeclaration(
                name="consolidate_library",
                label="Consolidate Library",
                description="Preview or apply category naming and folder organization rules.",
                parameters={
                    "type": "object",
                    "properties": {
                        "dry_run": {"type": "boolean", "description": "Preview changes before moving or renaming files."},
                    },
                    "required": [],
                },
                requires_confirmation=True,
                destructive=False,
                risk_level="write",
                tool_name=f"{self.category_id}.consolidate_library",
            ),
        ]

    async def execute_action(self, action_name: str, arguments: dict[str, Any], context: Any) -> ActionReceipt:
        """Execute a category-owned action and return an action receipt.

        Base categories intentionally support only safe generic receipts.
        Concrete categories or category workflow services should override this
        for real work such as metadata refresh, category-unit search, or
        category-specific repair operations.
        """
        declared = {action.name: action for action in self.declare_actions()}
        if action_name not in declared:
            return ActionReceipt(
                category_id=self.category_id,
                action_name=action_name,
                status="failed",
                user_message=f"{self.display_name} does not support action '{action_name}'.",
                technical_message="Unsupported category action.",
            )

        action = declared[action_name]
        if action.requires_confirmation and not arguments.get("confirmed"):
            return ActionReceipt(
                category_id=self.category_id,
                action_name=action_name,
                status="needs_confirmation",
                user_message=action.confirmation_prompt or f"Confirm '{action.label}' for {self.display_name}.",
                data={"requires_confirmation": True},
            )

        # Category actions and category workflows share the same architecture:
        # actions are the UI/permission contract, workflows are the concrete
        # domain implementation.  Route declared actions with an operation to
        # the category-owned workflow executor instead of forcing every
        # subclass to duplicate the dispatch boilerplate.
        workflow_name = action.operation or action.name
        workflows = {workflow.name for workflow in self.declare_workflows()}
        if workflow_name in workflows:
            return await self.execute_workflow(workflow_name, arguments, context)

        return ActionReceipt(
            category_id=self.category_id,
            action_name=action_name,
            status="failed",
            user_message=(
                f"Action '{action_name}' is declared by {self.display_name}, but no concrete "
                "executor is wired yet."
            ),
            technical_message="Category action declaration exists without implementation.",
        )

    def manifest(self, settings: Optional['Settings'] = None, include_private_profile: bool = False) -> CategoryManifest:
        """Return the complete category manifest for UI and assistant runtime."""
        properties: list[dict[str, Any]] = []
        setup_requirements: list[CategorySetupRequirement] = []
        if settings is not None:
            properties = [prop.model_dump() for prop in self.get_properties(settings)]
            setup_requirements = self.setup_requirements(settings)
        profile = self.llm_profile_for_settings(settings)
        configured_tool_names = self.category_configured_tool_names(settings)
        return CategoryManifest(
            category_id=self.category_id,
            display_name=self.display_name,
            description=profile.user_facing_description,
            default_folder=self.default_folder,
            default_library_path=self.default_root_path(settings) if settings is not None and hasattr(self, "default_root_path") else "",
            effective_library_path=self.get_root_path(settings) if settings is not None else "",
            icon=self.icon,
            media_kind=self.media_kind,
            capabilities=list(self.capabilities),
            metadata_providers=list(self.metadata_provider_names),
            discovery_sources=self.discovery_contract(),
            service_settings=self.sanitized_service_settings(settings),
            download_profile=self.category_download_profile(settings),
            tool_policy=self.category_tool_policy(settings),
            llm_guidance=self.category_llm_guidance(settings),
            properties=properties,
            ui_sections=self.ui_sections(),
            actions=self.declare_actions(),
            workflows=self.declare_workflows(),
            setup_requirements=setup_requirements,
            runtime_dependencies=self.resolved_runtime_dependencies(settings),
            tool_names=sorted(set(self.declare_tool_names() + configured_tool_names)),
            supported_operations=list(self.supported_operations),
            router_brief=self.router_brief(),
            llm_summary=profile.short_description,
            examples=[example.user for example in profile.examples],
        )

    def declare_tool_names(self) -> list[str]:
        """Return LLM tool names owned or explicitly approved by this category."""
        action_tools = [action.exposed_tool_name for action in self.declare_actions() if action.llm_visible]
        workflow_tools = [workflow.tool_name for workflow in self.declare_workflows() if workflow.tool_name]
        return sorted(set(self.category_tool_names + action_tools + workflow_tools))

    def provider_setup_requirements(self, settings: 'Settings') -> list[CategorySetupRequirement]:
        """Return setup requirements derived from category service declarations.

        Category YAML describes services under ``services`` with labels, purposes,
        optional fields, and enable flags. The base implementation turns that
        declarative contract into setup guidance so new categories do not require
        global setup-code branches for every provider.
        """
        services = self.category_config_section(settings, "services")
        requirements: list[CategorySetupRequirement] = []
        for service_id, service_cfg in sorted((services or {}).items()):
            if not isinstance(service_cfg, dict):
                continue
            requirements.extend(self._service_setup_requirements(str(service_id), service_cfg))
        return requirements

    def _service_setup_requirements(self, service_id: str, service_cfg: dict[str, Any]) -> list[CategorySetupRequirement]:
        """Build setup rows for one declarative service config."""
        label = str(service_cfg.get("label") or service_id.replace("_", " ").title())
        purpose = str(service_cfg.get("purpose") or service_cfg.get("llm_usage") or "Category service.")
        fields = service_cfg.get("fields") if isinstance(service_cfg.get("fields"), dict) else {}
        enabled = service_cfg.get("enabled", True)
        requirements: list[CategorySetupRequirement] = []

        credential_fields = [
            (str(field), str(kind))
            for field, kind in fields.items()
            if str(field) != "enabled" and str(kind) in {"secret", "string", "token", "url"}
        ]
        if not credential_fields:
            requirements.append(CategorySetupRequirement(
                id=f"{service_id}_service",
                label=label,
                description=f"{purpose} No credential is required; this can be toggled per category.",
                required=bool(service_cfg.get("required", False)),
                configured=bool(enabled),
                setting_key=f"category_config.{self.category_id}.services.{service_id}.enabled",
                help_url=service_cfg.get("help_url"),
                severity=str(service_cfg.get("severity") or "info"),
                why_it_matters=str(service_cfg.get("why_it_matters") or ""),
            ))
            return requirements

        for field, kind in credential_fields:
            value = service_cfg.get(field)
            field_required = bool(service_cfg.get("required", False) or service_cfg.get("required_fields", []) and field in service_cfg.get("required_fields", []))
            is_secret = kind in {"secret", "token"} or any(token in field.lower() for token in ("key", "token", "secret", "password"))
            requirements.append(CategorySetupRequirement(
                id=f"{service_id}_{field}",
                label=f"{label} {field.replace('_', ' ')}",
                description=purpose,
                required=field_required,
                configured=bool(value),
                setting_key=f"category_config.{self.category_id}.services.{service_id}.{field}",
                help_url=service_cfg.get("help_url"),
                severity=str(service_cfg.get("severity") or ("required" if field_required else "recommended")),
                why_it_matters=str(service_cfg.get("why_it_matters") or ""),
                secret=is_secret,
            ))
        return requirements


    def setup_requirements(self, settings: 'Settings') -> list[CategorySetupRequirement]:
        """Return educational setup requirements for this category.

        Base requirements are derived from category capabilities and metadata
        provider declarations so custom categories get useful setup guidance
        without modifying the global wizard. Concrete categories can override
        this method for more specialized requirements.
        """
        category_settings = settings.category_settings.get(self.category_id, {})
        library_path = str(category_settings.get("library_path") or "").strip()
        default_path = self.default_root_path(settings) if hasattr(self, "default_root_path") else str(Path(settings.library_root) / self.default_folder)
        requirements = [
            CategorySetupRequirement(
                id="library_path",
                label=f"{self.display_name} library folder",
                description=(
                    f"Optional override for completed {self.display_name.lower()} files. "
                    f"Leave blank to use the library root default: {default_path}"
                ),
                required=False,
                configured=bool(getattr(settings, "library_root", "")),
                setting_key=f"category_config.{self.category_id}.paths.library_path",
                severity="info",
                why_it_matters=(
                    "Use a category override only when this category belongs on a different disk, share, "
                    "or existing server library. Otherwise LJS creates the default folder under the global library root."
                ),
            )
        ]

        if "downloadable" in self.capabilities:
            requirements.append(
                CategorySetupRequirement(
                    id="jackett",
                    label="Jackett torrent search",
                    description=(
                        "Reliable torrent search should go through Jackett. Direct scrapers can be enabled "
                        "as a slower degraded fallback, but Jackett remains the recommended primary source."
                    ),
                    required=not bool(getattr(settings, "direct_scraper_fallback", False)),
                    configured=bool(settings.jackett_url and settings.jackett_api_key) or bool(getattr(settings, "direct_scraper_fallback", False)),
                    setting_key="jackett_url",
                    action="install_jackett",
                    help_url="https://github.com/Jackett/Jackett",
                    severity="required",
                )
            )

        requirements.extend(self.provider_setup_requirements(settings))
        for dependency in self.resolved_runtime_dependencies(settings):
            requirements.append(CategorySetupRequirement(
                id=f"runtime_{dependency.id}",
                label=dependency.label,
                description=dependency.purpose or f"Runtime dependency for {self.display_name}.",
                required=dependency.required,
                configured=dependency.configured,
                setting_key=f"runtime_dependencies.{self.category_id}.{dependency.id}",
                action=dependency.validation_action,
                severity=dependency.severity,
                why_it_matters=dependency.install_hint,
            ))

        requirements.append(
            CategorySetupRequirement(
                id="web_search",
                label="General web search provider",
                description=(
                    "Assistant research works best with a configured provider such as "
                    "Brave, Tavily, Kagi, or SearXNG. DuckDuckGo HTML is only a last-resort fallback."
                ),
                required=False,
                configured=bool(settings.web_search.enabled and settings.web_search.provider != "duckduckgo_html"),
                setting_key="web_search",
                severity="recommended",
            )
        )
        return requirements

    def declare_workflows(self) -> list[CategoryWorkflowDeclaration]:
        """Declare category workflows exposed to the UI and LLM tool policy.

        Workflows are higher-level category operations such as resolving
        metadata, finding missing units, or queueing a category-specific
        download. The base category returns no workflows so custom categories
        can opt in incrementally.
        """
        return []

    async def execute_workflow(
        self,
        workflow_name: str,
        arguments: dict[str, Any],
        context: Any,
    ) -> ActionReceipt:
        """Execute a category workflow and return an action receipt.

        Concrete categories should override this when they have runtime
        collaborators available. The safe default fails explicitly instead of
        pretending that a declared workflow completed.
        """
        declared = {workflow.name: workflow for workflow in self.declare_workflows()}
        if workflow_name not in declared:
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="failed",
                user_message=f"{self.display_name} does not support workflow '{workflow_name}'.",
                technical_message="Unsupported category workflow.",
            )
        return ActionReceipt(
            category_id=self.category_id,
            action_name=workflow_name,
            status="failed",
            user_message=(
                f"Workflow '{workflow_name}' is declared by {self.display_name}, but no concrete "
                "executor is wired yet."
            ),
            technical_message="Category workflow declaration exists without implementation.",
        )


    async def after_library_file_imported(
        self,
        *,
        imported_path: Path,
        source_path: Path,
        item: Any,
        settings: Optional['Settings'],
        file_info: Any | None = None,
    ) -> list[Path]:
        """Run optional category-owned post-import work.

        Download orchestration calls this after a completed payload has been
        hardlinked/copied into the category library.  The default does nothing.
        Categories may return extra sidecar paths they created (for example an
        Apple-friendly M4A/M4B audio sidecar) so the library reconciler can
        refresh those files as part of the same managed mutation.
        """
        return []

    def related_sidecar_imports_for_file(
        self,
        *,
        source_path: Path,
        imported_path: Path,
        item: Any,
        settings: Optional['Settings'],
        file_info: Any | None = None,
    ) -> list[dict[str, str]]:
        """Return category-owned sidecar source/target plans for an imported file.

        Generic download code must not decide that every ``.srt`` or ``.nfo``
        near a payload belongs to a media file.  Categories that understand
        sidecar naming conventions can return explicit ``source``/``target``
        pairs.  The download handler then performs the safe copy/hardlink/move
        according to the current lifecycle phase.
        """
        return []




    # ── Category-owned cleanup / file listing contract ────────────

    def matches_external_media_type(self, source: str, media_type: str) -> bool:
        """Return whether an external library type maps to this category.

        Integrations such as Plex use their own terms for content types. The
        cleanup core must not map those terms to built-in categories; each
        category declares which external media types it accepts.
        """
        return media_type == self.category_id

    def library_file_records_from_scan(self, scanned: Any) -> list[dict[str, Any]]:
        """Return category-owned file records for cleanup/agent inspection.

        The records are intentionally opaque except for common file facts.
        Categories may add selector fields understood by their own
        ``file_record_matches_selector`` implementation.
        """
        records: list[dict[str, Any]] = []
        for scanned_file in list(getattr(scanned, 'files', []) or []):
            size = int(getattr(scanned_file, 'size_bytes', 0) or 0)
            records.append({
                'name': getattr(scanned, 'name', ''),
                'category_id': self.category_id,
                'path': getattr(scanned_file, 'file_path', ''),
                'size_mb': round(size / (1024 * 1024), 1),
                'quality': getattr(scanned_file, 'quality', ''),
            })
        return records

    def file_record_matches_selector(
        self,
        file_info: dict[str, Any],
        *,
        season: int | None = None,
        episode: int | None = None,
        year: int | None = None,
    ) -> bool:
        """Return whether a cleanup/listing record matches user selectors."""
        return True


    # ── Canonical library object contract ──────────────────────────

    def library_object_spec(self) -> dict[str, Any]:
        """Describe this category's canonical library object shape.

        Core storage and UI code must not invent category-specific structures.
        They ask this method for the category-owned schema, then call
        ``library_item_from_scan``, ``library_units_from_scan``,
        ``library_progress_from_scan``, and ``build_library_object`` to normalize
        raw filesystem/provider/download observations into one object.
        """
        return {
            "schema_version": 1,
            "item_identity_fields": ["category_id", "item_id", "display_name"],
            "unit_types": {
                "file": {
                    "description": "Generic local payload file.",
                    "required_fields": ["unit_key", "file_path"],
                    "optional_fields": [
                        "display_name", "quality", "resolution", "codec", "language",
                        "audio_languages", "audio_tracks", "subtitle_languages", "subtitle_tracks",
                        "media_probe", "size_bytes", "estimated_bitrate_kbps", "subtitle_files",
                    ],
                }
            },
            "computed_fields": [
                "unit_count", "downloaded_unit_count", "total_size_bytes", "has_local_files",
            ],
            "source_of_truth_rule": (
                "The canonical library object is the only supported read model for library state. "
                "Consumers must not reinterpret raw category_item_units directly."
            ),
        }

    def library_item_from_scan(self, scanned: Any) -> dict[str, Any]:
        """Normalize one scanned item into the category item envelope.

        The default keeps only generic fields.  Categories with nested domain
        state override this method and still store their fields inside the same
        category-owned JSON envelopes.
        """
        return {
            "category_id": self.category_id,
            "item_id": getattr(scanned, "name", ""),
            "key": getattr(scanned, "name", ""),
            "display_name": getattr(scanned, "name", ""),
            "item_type": self.category_id,
            "status": "present",
            "properties": {
                "file_count": int(getattr(scanned, "file_count", 0) or 0),
                "total_size_bytes": int(getattr(scanned, "total_size_bytes", 0) or 0),
            },
            "metadata": {
                "resolutions": list(getattr(scanned, "resolutions", []) or []),
                "codecs": list(getattr(scanned, "codecs", []) or []),
                "detected_language": getattr(scanned, "detected_language", ""),
                "detected_languages": list(getattr(scanned, "detected_languages", []) or []),
                "subtitle_languages": list(getattr(scanned, "subtitle_languages", []) or []),
                "year": getattr(scanned, "year", None),
            },
            "state": {"library_present": True},
        }

    def library_units_from_scan(self, scanned: Any) -> list[dict[str, Any]]:
        """Normalize scanned files into category unit envelopes.

        Base categories expose each scanned file as a generic ``file`` unit.
        Rich categories override this to create their declared unit envelopes without
        leaking those meanings into core code.
        """
        units: list[dict[str, Any]] = []
        for index, scanned_file in enumerate(list(getattr(scanned, "files", []) or []), start=1):
            file_path = str(getattr(scanned_file, "file_path", "") or "")
            unit_key = f"file:{index:04d}"
            units.append({
                "unit_key": unit_key,
                "unit_type": "file",
                "display_name": file_path.rsplit("/", 1)[-1] or unit_key,
                "status": "downloaded",
                "file_path": file_path,
                "quality": getattr(scanned_file, "quality", "") or "",
                "size_bytes": int(getattr(scanned_file, "size_bytes", 0) or 0),
                "language": getattr(scanned_file, "detected_language", "") or getattr(scanned, "detected_language", "") or "",
                "audio_languages": list(getattr(scanned_file, "audio_languages", []) or []),
                "audio_tracks": list(getattr(scanned_file, "audio_tracks", []) or []),
                "subtitle_languages": list(getattr(scanned_file, "subtitle_languages", []) or []),
                "subtitle_tracks": list(getattr(scanned_file, "subtitle_tracks", []) or []),
                "media_probe": dict(getattr(scanned_file, "media_probe", {}) or {}),
                "sort_index": index,
            })
        return units

    def scan_average_bitrate_kbps(self, scanned: Any) -> int | None:
        """Return an optional category-owned scan-time bitrate estimate.

        The generic scanner may summarize file size and count, but it must not
        assume a runtime model such as episode length, movie length, track
        length, or game package semantics. Categories that can make a useful
        lightweight estimate override this hook and name the result as an
        estimate until a real media-probe layer supplies extracted bitrates.
        """
        return None

    def rss_unit_label_from_parsed(self, parsed: Any) -> str | None:
        """Return an optional category-owned unit label for RSS matches.

        RSS monitoring is a generic feed watcher. If a category can derive a
        compact unit label from its parsed title, such as an episode/version/
        volume marker, it exposes that label here instead of making the monitor
        inspect category-specific coordinates.
        """
        return None

    def library_progress_from_scan(self, scanned: Any, units: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Return optional category progress derived from the canonical units."""
        if not units:
            return None
        return {
            "unit_type": "progress",
            "display_name": "Library progress",
            "downloaded_unit_count": len([unit for unit in units if unit.get("status") == "downloaded"]),
            "total_size_bytes": sum(int(unit.get("size_bytes") or 0) for unit in units),
        }

    def build_library_object(self, context: Any) -> dict[str, Any]:
        """Build the category's canonical library object from raw envelopes.

        This default is intentionally generic and mirrors
        ``CanonicalLibraryObjectBuilder._generic_object``.  Category subclasses
        override this when their schema declares nested structures.
        """
        item = context.item or {}
        units = list(context.units or [])
        downloaded = [unit for unit in units if unit.get("status") == "downloaded"]
        total_size = sum(int(unit.get("size_bytes") or 0) for unit in downloaded)
        return {
            "schema_version": self.library_object_spec().get("schema_version", 1),
            "category_id": self.category_id,
            "item_id": context.item_id,
            "display_name": item.get("display_name") or context.item_id,
            "item_type": item.get("item_type") or self.category_id,
            "status": item.get("status") or "",
            "properties": item.get("properties") or {},
            "metadata": item.get("metadata") or {},
            "state": item.get("state") or {},
            "units": units,
            "groups": {"default": downloaded},
            "computed": {
                "unit_count": len(units),
                "downloaded_unit_count": len(downloaded),
                "total_size_bytes": total_size,
                "has_local_files": bool(downloaded),
            },
            "provider_metadata": [row.get("metadata") or {} for row in context.metadata_rows],
        }


    def provider_media_type(self) -> str:
        """Return this category's provider-media type token.

        External metadata providers sometimes need a compact type string.  The
        scheduler and downloader should ask the category for that token instead
        of branching on built-in category IDs.
        """
        return self.category_id

    def create_suggestion_workflow(self, context: Any) -> Any | None:
        """Return the category-owned suggestion workflow, if this category has one.

        The generic suggestion compiler calls this hook instead of importing
        category-specific workflows. Returning ``None`` means the category has
        no automated suggestions yet.
        """
        return None


    # ── Lifecycle, suggestion, and taste policy ───────────────────


    async def next_scheduled_unit(self, item: Any, context: dict[str, Any]) -> dict[str, Any] | None:
        """Return category-owned upcoming-unit state, if the category has schedules.

        The scheduler may call this generic hook for every tracked item.  The
        category decides whether provider clients in ``context`` are relevant
        and which state keys should be updated.
        """
        return None

    def lifecycle_policy(self) -> dict[str, Any]:
        """Return this category's item lifecycle and suggestion policy.

        The core lifecycle engine persists fingerprints and due times, but the
        category owns what counts as meaningful change and how often an item
        should be revisited.  Custom categories should override this method
        before adding scheduler special cases elsewhere.
        """
        return {
            "policy_version": 1,
            "identity_fields": ["category_id", "item_id", "provider", "external_id"],
            "lifecycle_fields": ["status", "metadata", "library_units", "taste_snapshot"],
            "suggestion_types": ["metadata_repair", "better_release", "manual_review"],
            "invalidation_triggers": [
                "metadata_changed",
                "library_changed",
                "taste_changed",
                "download_completed",
                "download_failed",
                "manual_refresh",
                "policy_version_changed",
            ],
            "default_check_interval_days": 90,
            "llm_policy_description": (
                f"{self.display_name} uses the generic lifecycle policy until the category declares domain-specific rules."
            ),
        }

    def lifecycle_decision(self, item: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Decide when one item should next be checked.

        The default is intentionally conservative and provider-free. It gives
        generated categories useful behavior without teaching the scheduler
        about their domain. Concrete categories can inspect item state, metadata
        envelopes, and policy settings through ``context`` and return
        ``next_check_at``, ``valid_until``, ``reason``, and ``confidence``.
        """
        from datetime import datetime, timedelta, timezone

        policy = context.get("policy") or self.lifecycle_policy()
        days = int(policy.get("default_check_interval_days") or 90)
        next_check_at = (datetime.now(timezone.utc) + timedelta(days=max(days, 1))).isoformat()
        return {
            "next_check_at": next_check_at,
            "valid_until": next_check_at,
            "reason": f"Generic {self.category_id} lifecycle policy; next check in {days} day(s).",
            "confidence": 0.6,
        }

    def suggestion_policy(self) -> dict[str, Any]:
        """Return suggestion-specific policy metadata for UI, docs, and scaffolds."""
        policy = dict(self.lifecycle_policy())
        return {
            "suggestion_types": policy.get("suggestion_types", []),
            "invalidation_triggers": policy.get("invalidation_triggers", []),
            "policy_version": policy.get("policy_version", 1),
            "llm_policy_description": policy.get("llm_policy_description", ""),
        }


    async def enrich_taste_metadata(self, item: Any, context: Any) -> dict[str, Any] | None:
        """Return normalized taste-profile metadata for one category item.

        Generic services such as ``TasteProfiler`` call this hook instead of
        branching on category identifiers. Categories that know how to use external domain sources should override this
        method and return a category-owned metadata envelope.
        The base implementation intentionally returns ``None`` so custom
        categories remain safe until they opt in.
        """
        return None

    def taste_metadata_provider_name(self, metadata: dict[str, Any]) -> str:
        """Return the provider key used when persisting taste metadata.

        Categories may override this when they need a stable external-provider
        identifier. The generic fallback is category-scoped, avoiding provider
        assumptions in core services.
        """
        return str(metadata.get("provider") or f"{self.category_id}_taste")

    def normalize_taste_metadata_payload(
        self,
        item: Any,
        metadata: Any,
        provider: str,
    ) -> dict[str, Any] | None:
        """Normalize a category-owned metadata record for taste profiling.

        Args:
            item: Category item being enriched.
            metadata: Pydantic model or mapping returned by a provider.
            provider: Stable provider identifier owned by the category.

        Returns:
            A metadata envelope suitable for ``category_item_metadata`` or
            ``None`` when the provider returned no useful record.
        """
        if not metadata:
            return None
        if hasattr(metadata, "model_dump"):
            payload = metadata.model_dump()
        elif isinstance(metadata, dict):
            payload = dict(metadata)
        else:
            return None
        payload.setdefault("provider", provider)
        payload.setdefault("category_id", self.category_id)
        payload.setdefault("item_id", getattr(item, "key", ""))
        payload.setdefault("display_name", getattr(item, "display_name", None) or getattr(item, "key", ""))
        return payload


    async def cache_metadata_artwork(
        self,
        item: Any,
        metadata: dict[str, Any],
        context: Any,
        provider: str = "metadata",
    ) -> dict[str, Any]:
        """Let the category cache artwork referenced by provider metadata.

        Generic code supplies an opaque context; the category decides whether an
        artwork manager is available and stores downloaded assets under its own
        ``data/categories/<category_id>/metadata/artwork`` folder.
        """
        manager = getattr(context, "artwork_manager", None)
        if not manager or not metadata:
            return metadata
        item_id = str(metadata.get("item_id") or getattr(item, "key", ""))
        try:
            return await manager.cache_poster_from_metadata(
                self.category_id, item_id, metadata, provider=provider,
            )
        except Exception as exc:
            logger.debug(f"{self.category_id} artwork cache skipped for {item_id}: {exc}")
            return metadata

    def metadata_providers(self, context: Any) -> list[Any]:
        """Return metadata provider instances owned by this category.

        Category subclasses may use dependencies from the supplied context to
        construct their provider clients. Returning providers from the category
        keeps external metadata behavior out of the global assistant tool pile.
        """
        return []

    def load_prompt_file(self) -> str:
        """Load optional category-owned prompt guidance from disk.

        Prompt files refine category behavior but never override global safety,
        privacy, confirmation, or tool-policy rules. Missing prompt files are
        treated as an empty extension so custom categories can omit them.
        """
        if not self.prompt_file:
            return ""
        prompt_path = Path(__file__).parent / "prompts" / self.prompt_file
        try:
            return prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            logger.warning(f"Category prompt file not found: {prompt_path}")
            return ""
        except OSError as exc:
            logger.warning(f"Failed to read category prompt file {prompt_path}: {exc}")
            return ""


    # ── Category-owned search contract ────────────────────────────

    @staticmethod
    def _append_search_language(query: str, language: str | None) -> str:
        """Append language to a search query without provider/domain assumptions."""
        from src.core.categories.language import LanguageSearchTagger

        return LanguageSearchTagger.append_to_query(query, language)

    def build_search_query(self, item: Any, unit_label: str | None, language: str | None) -> str:
        """Return the primary torrent-search query for an item/unit request.

        The shared search pipeline passes only the tracked item, the opaque
        category unit label, and the preferred language.  The category decides
        whether the label means an episode, version, volume, edition, disc, or
        nothing at all.  Core search code must not parse category labels.
        """
        name = str(getattr(item, "key", "") or "").strip()
        if unit_label:
            name = f"{name} {unit_label}".strip()
        return self._append_search_language(name, language)

    def build_alternative_search_queries(self, item: Any, unit_label: str | None, language: str | None) -> list[str]:
        """Return category-owned fallback torrent queries.

        The default has no alternatives. Categories with multiple release-name
        conventions override this hook; generic services must not synthesize
        structured alternatives such as episode tags.
        """
        return []

    def validate_search_result_for_request(self, result: Any, item: Any, unit_label: str | None) -> bool:
        """Return whether a candidate result matches the category request.

        Default validation only calls the category's parser/validator without
        interpreting the opaque unit label. Categories with structured units
        override this hook to compare parsed coordinates or other domain fields.
        """
        title = str(getattr(result, "title", "") or "")
        try:
            return self.validate_result(title)
        except Exception:
            return True

    def quality_reference_for_search(self, item: Any, unit_label: str | None, context: Any | None = None) -> str:
        """Return concise category-owned quality context for LLM ranking.

        Some categories can estimate useful targets from local canonical library
        objects: average file size, bitrate, resolution distribution, language,
        subtitle availability, and so on. The base category is silent because
        the core search pipeline does not know what a good size/bitrate means.
        """
        return ""

    async def discovery_already_satisfied(self, item: Any, unit_label: str | None, context: Any | None = None) -> bool:
        """Return whether auto-discovery should skip this request.

        Categories use this to prevent duplicate downloads from canonical
        library state.  The search pipeline must not construct category unit
        keys or inspect raw unit rows itself.
        """
        return bool(getattr(item, "discovered", False) and not unit_label)

    def download_coordinates_from_search_result(self, result: Any, item: Any, unit_label: str | None) -> dict[str, Any]:
        """Return legacy download coordinates derived by the category.

        Download rows still carry transitional fields such as ``season`` and
        ``episode``.  Only the category may populate them from a search result,
        and generic code must treat them as compatibility fields.
        """
        return {}

    def unit_descriptor_from_search_result(self, result: Any, item: Any, unit_label: str | None) -> dict[str, Any]:
        """Return the category-owned unit descriptor for a candidate result.

        The descriptor is the canonical queue/download handoff for structured
        units. Categories may use coordinates, versions, editions, chapters,
        discs, tracks, DLC names, or any other shape. Shared services may only
        read the conventional ``stable_key``, ``label``, ``granularity``,
        ``sort_key``, and ``coordinates`` fields; they must not infer category
        semantics from the descriptor body.
        """
        label = str(unit_label or "").strip()
        descriptor: dict[str, Any] = {"granularity": "item", "label": label, "coordinates": {}}
        if label:
            descriptor.update({"granularity": "unit", "stable_key": label, "sort_key": [label]})
        return {key: value for key, value in descriptor.items() if value not in (None, "", [], {})}

    def unit_descriptor_from_agent_args(self, *, season: int | None = None, episode: int | None = None, **_: Any) -> dict[str, Any]:
        """Return a descriptor for transitional assistant unit arguments.

        The base category intentionally does not interpret ``season`` or
        ``episode``. Categories that opt into those legacy arguments override
        this hook and expose a descriptor matching their own object spec.
        """
        return {"granularity": "item", "label": "", "coordinates": {}}

    def sort_cached_download_candidates(self, entries: list[dict[str, Any]], request_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return category-owned ordering for queued cached candidates.

        Batch queueing must not sort by TV-like fields in generic code. The
        default keeps cache ranking order. Categories with ordered units override
        this to use descriptor sort keys.
        """
        return entries

    def candidates_represent_same_unit(self, first: dict[str, Any], second: dict[str, Any], request_context: dict[str, Any] | None = None) -> bool:
        """Return whether two cached candidates are alternatives for one unit."""
        first_desc = first.get("unit_descriptor") or {}
        second_desc = second.get("unit_descriptor") or {}
        first_key = str(first_desc.get("stable_key") or "")
        second_key = str(second_desc.get("stable_key") or "")
        return bool(first_key and first_key == second_key)

    def batch_group_for_candidate(self, candidate: dict[str, Any], request_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Return a batch grouping descriptor for a cached candidate.

        ``None`` means the candidate is not a concrete queueable sub-unit for a
        multi-unit recommendation. Categories decide which descriptors are safe
        to auto-group.
        """
        descriptor = candidate.get("unit_descriptor") or {}
        stable_key = str(descriptor.get("stable_key") or "").strip()
        if not stable_key or descriptor.get("granularity") == "item":
            return None
        return {
            "key": stable_key,
            "label": descriptor.get("label") or stable_key,
            "sort_key": descriptor.get("sort_key") or [stable_key],
            "descriptor": descriptor,
        }


    # ── Torrent bundle / multi-payload handling ────────────────────

    def torrent_bundle_candidate_context(self, result: Any, item: Any | None = None, unit_label: str | None = None) -> dict[str, Any] | None:
        """Return category-owned bundle hints for one torrent candidate.

        A *bundle* is any torrent whose payload may contain more than the exact
        requested item/unit: TV season packs, movie collections, game bundles,
        book anthologies, soundtrack/discography packs, and similar releases.
        The core never decides what those words mean.  Categories can annotate
        candidates so the LLM sees that total torrent size should be evaluated
        per useful unit/file and that selective download may be possible after
        metadata arrives.
        """
        return None

    def estimate_bundle_unit_size_mb(
        self,
        *,
        total_size_bytes: int,
        title: str,
        bundle_context: dict[str, Any] | None = None,
        target_descriptor: dict[str, Any] | None = None,
    ) -> float:
        """Estimate the useful per-unit/file size for a bundle candidate.

        The default only divides by an explicit category-provided ``unit_count``.
        Categories with richer semantics can use provider metadata, parsed
        ranges, file counts, or LLM-provided context before torrent metadata is
        available.
        """
        if total_size_bytes <= 0:
            return 0.0
        unit_count = None
        context = bundle_context or {}
        try:
            unit_count = int(context.get("unit_count") or 0)
        except (TypeError, ValueError):
            unit_count = None
        if not unit_count or unit_count <= 0:
            return total_size_bytes / (1024 * 1024)
        return (total_size_bytes / (1024 * 1024)) / unit_count

    def unit_descriptor_from_file(self, file_path: str, parsed: Any | None = None, item_descriptor: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the category-owned descriptor for a file inside a torrent.

        Generic download code passes the torrent-relative path and the parsed
        category facts.  The default descriptor is file-scoped and intentionally
        avoids interpreting coordinates.
        """
        label = basename_from_pathish(file_path, fallback="file")
        return {
            "granularity": "file",
            "label": label,
            "stable_key": label,
            "sort_key": [label],
            "coordinates": {},
        }

    def torrent_file_matches_target(
        self,
        *,
        file_path: str,
        parsed: Any | None,
        file_descriptor: dict[str, Any],
        target_descriptors: list[dict[str, Any]],
    ) -> bool:
        """Return whether a torrent file should be downloaded for a target.

        This hook is the generic selective-download seam.  The core supplies
        opaque descriptors; the category decides whether a file is useful.
        The default supports exact descriptor-key matches only.
        """
        if not target_descriptors:
            return True
        file_key = str((file_descriptor or {}).get("stable_key") or "").strip()
        if not file_key:
            return False
        wanted = {str((desc or {}).get("stable_key") or "").strip() for desc in target_descriptors}
        return file_key in wanted

    def torrent_file_priority(
        self,
        *,
        file_path: str,
        parsed: Any | None,
        file_descriptor: dict[str, Any],
        selected: bool,
    ) -> int:
        """Return libtorrent priority for one torrent file.

        Categories may prioritize ordered units, subtitles, or companion files.
        The default downloads selected payload files at normal priority and
        ignores unselected/sample files.
        """
        lower = str(file_path or "").lower()
        if "sample" in lower:
            return 0
        return 4 if selected else 0


    def accepts_agent_unit_args(self, *, season: int | None = None, episode: int | None = None, **_: Any) -> bool:
        """Whether this category understands generic agent unit arguments.

        The current compatibility tool schema exposes two optional structured
        coordinates. Categories opt in here when those coordinates are meaningful
        for their own object specification; core orchestration must not branch
        on category ids.
        """
        return False

    async def build_agent_search_labels(
        self,
        item: "CategoryItem",
        *,
        season: int | None = None,
        episode: int | None = None,
        language: str | None = None,
        search_scope: str | None = None,
        context: CategoryWorkflowContext | None = None,
    ) -> list[str | None]:
        """Return category-owned labels for interactive torrent search.

        The assistant and scheduler should not contain category-specific unit
        expansion rules. Categories that understand structured sub-units override this hook and return
        the labels the shared search pipeline should execute.
        """
        return [None]

    async def rank_agent_search_results(
        self,
        results: list[Any],
        *,
        item: "CategoryItem",
        language: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        context: CategoryWorkflowContext | None = None,
    ) -> list[Any]:
        """Category-owned ranking/filtering hook for agent candidate lists."""
        return results

    async def search_agent_candidates(
        self,
        item: "CategoryItem",
        *,
        season: int | None = None,
        episode: int | None = None,
        language: str | None = None,
        search_scope: str | None = None,
        context: CategoryWorkflowContext,
    ) -> tuple[list[Any], str]:
        """Run an interactive candidate search using category-owned labels.

        Core orchestration calls this one hook for every category. Subclasses
        decide how to fan out a user request; the shared pipeline performs the
        actual provider search.
        """
        labels = await self.build_agent_search_labels(
            item,
            season=season,
            episode=episode,
            language=language,
            search_scope=search_scope,
            context=context,
        )
        merged: list[Any] = []
        seen: set[str] = set()
        for label in labels or [None]:
            results = await context.pipeline.run_search(item, label, mode="llm", language=language)
            for result in results or []:
                magnet = getattr(result, "magnet", None) or ""
                identity = magnet or f"{getattr(result, 'source', '')}|{getattr(result, 'title', '')}"
                identity = str(identity).lower()
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append(result)
        try:
            ranked = await self.rank_agent_search_results(
                merged,
                item=item,
                language=language,
                season=season,
                episode=episode,
                context=context,
            )
        except RecursionError as exc:
            logger.error(f"{self.category_id} agent ranking hit recursion guard; returning unranked candidates: {exc}")
            ranked = merged
        except Exception as exc:
            logger.warning(f"{self.category_id} agent ranking failed; returning unranked candidates: {exc}")
            ranked = merged
        label_summary = ", ".join(str(label) for label in labels if label) or item.key
        return ranked, label_summary
