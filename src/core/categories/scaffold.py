"""
Category scaffold generation for LJS.

This module turns a validated CategorySpec into reviewable files for a new
category. It deliberately uses a narrow declarative spec and template-rendered
Python so LLM-assisted category creation cannot silently inject arbitrary runtime
code. Installation is still a confirmation-gated step.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import indent
from typing import Any

from src.core.models import ActionReceipt, CategoryScaffoldPreview, CategorySpec, ChangedEntity


class CategoryScaffoldService:
    """Render safe, template-based category files from a validated spec."""

    def preview(self, spec: CategorySpec) -> CategoryScaffoldPreview:
        """Build the complete reviewable file preview for a category spec.

        Args:
            spec: Validated declarative category specification.

        Returns:
            Preview containing category module, prompt, config, and regression
            test file contents without writing anything to disk.
        """
        warnings = self._warnings_for(spec)
        return CategoryScaffoldPreview(
            category_id=spec.category_id,
            files={
                f"src/core/categories/custom/{spec.category_id}.py": self.render_module(spec),
                f"src/core/categories/prompts/{spec.category_id}.md": self.render_prompt(spec),
                f"config/category-templates/{spec.category_id}.yaml": self.render_config(spec),
                f"tests/test_category_{spec.category_id}.py": self.render_test(spec),
            },
            warnings=warnings,
        )

    def validate_preview(self, preview: CategoryScaffoldPreview) -> list[str]:
        """Return safety issues detected in rendered scaffold files."""
        blocked = [
            "subprocess", "os.system", "eval(", "exec(", "socket",
            "requests", "httpx", "aiohttp", "urllib", "shutil.rmtree",
        ]
        issues: list[str] = []
        for path, content in preview.files.items():
            for token in blocked:
                if token in content:
                    issues.append(f"{path}: blocked token {token!r}")
        return issues

    def apply(
        self,
        spec: CategorySpec,
        approved: bool = False,
        root: Path | None = None,
        overwrite_existing: bool = False,
    ) -> ActionReceipt:
        """Write scaffold files after explicit approval and validation.

        Args:
            spec: Validated category specification.
            approved: Must be true after the user reviewed the preview.
            root: Optional repository root for tests. Runtime callers omit it.
            overwrite_existing: Whether existing category files may be replaced.
        """
        preview = self.preview(spec)
        issues = self.validate_preview(preview)
        if issues:
            return ActionReceipt(
                category_id=spec.category_id,
                action_name="category_scaffold_apply",
                status="failed",
                user_message="Generated category scaffold failed safety validation.",
                data={"issues": issues},
            )
        if not approved:
            return ActionReceipt(
                category_id=spec.category_id,
                action_name="category_scaffold_apply",
                status="needs_confirmation",
                user_message="Review the generated files and approve before writing them.",
                data={"preview": preview.model_dump(), "requires_confirmation": True},
            )
        base = root or Path.cwd()
        existing = [rel_path for rel_path in preview.files if (base / rel_path).exists()]
        if existing and not overwrite_existing:
            return ActionReceipt(
                category_id=spec.category_id,
                action_name="category_scaffold_apply",
                status="needs_confirmation",
                user_message=(
                    "Some category scaffold files already exist. Review them carefully and "
                    "confirm overwrite_existing=true if you intend to replace them."
                ),
                data={"existing_files": existing, "requires_confirmation": True},
            )
        for rel_path, content in preview.files.items():
            target = base / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return ActionReceipt(
            category_id=spec.category_id,
            action_name="category_scaffold_apply",
            status="success",
            user_message=f"Installed category scaffold for {spec.display_name}.",
            changed_entities=[ChangedEntity(entity_type="category", entity_id=spec.category_id, display_name=spec.display_name, change="scaffold_written")],
            data={"files": list(preview.files)},
        )

    def render_module(self, spec: CategorySpec) -> str:
        """Render a CategoryMedia subclass from the category spec."""
        properties = self._render_property_list(spec)
        capabilities = repr(spec.capabilities)
        metadata_providers = repr(spec.metadata_providers)
        supported_operations = repr(self._supported_operations(spec))
        item_types = repr(spec.item_types or [spec.category_id])
        identifiers = repr(spec.identifiers)
        taste_schema = repr(self._taste_schema_for(spec))
        taste_weights = repr(self._taste_weights_for(spec))
        discovery_contract = repr(self._discovery_contract_for(spec))
        download_profile = repr(spec.download_profile or {})
        lifecycle_policy = repr(self._lifecycle_policy_for(spec))
        return f'''"""
{spec.display_name} category for LJS.

Generated from a declarative CategorySpec. Review this file before enabling it
and add real parsing, metadata, and workflow logic as the category matures.
"""

from __future__ import annotations

from typing import Any

from src.core.categories.base import CategoryMedia
from src.core.categories.types import ParsedMedia, ScannedItem
from src.core.models import CategoryLlmProfile, CategoryProperty, Settings


class {spec.class_name}(CategoryMedia):
    """{spec.description}"""

    category_id = "{spec.category_id}"
    display_name = "{spec.display_name}"
    default_folder = "{spec.default_folder}"
    media_kind = "{spec.media_kind}"
    capabilities = {capabilities}
    metadata_provider_names = {metadata_providers}
    supported_operations = {supported_operations}
    prompt_file = "{spec.category_id}.md"

    def llm_profile(self) -> CategoryLlmProfile:
        """Return category-specific LLM routing and behavior guidance."""
        return CategoryLlmProfile(
            category_id=self.category_id,
            short_description="{self._escape(spec.description)}",
            user_facing_description="{self._escape(spec.description)}",
            router_description="{self._escape(spec.display_name)}: {self._escape(spec.description)}",
            domain_vocabulary={item_types},
            item_types={item_types},
            identifiers={identifiers},
            tool_usage_notes=[
                "Use generic category item APIs until category-owned workflows are implemented.",
                "Use category taste signals for user likes/dislikes; do not turn metadata existence into preference evidence.",
                "Torrent/download selection is category-specific; use this category's download_profile and researched release conventions before applying quality rules.",
            ],
        )

    def get_properties(self, settings: Settings) -> list[CategoryProperty]:
        """Return manifest-driven category settings."""
        category_settings = settings.category_settings.get(self.category_id, {{}})
        return [
{indent(properties, ' ' * 12)}
        ]

    def discovery_contract(self) -> list[dict[str, Any]]:
        """Return declared discovery/enrichment providers for this category."""
        return {discovery_contract}

    def download_profile(self) -> dict[str, Any]:
        """Return category-specific torrent/download constraints."""
        return {download_profile}

    def lifecycle_policy(self) -> dict[str, Any]:
        """Return category-owned lifecycle, suggestion, and invalidation policy."""
        return {lifecycle_policy}

    def lifecycle_decision(self, item: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Choose the next provider-free check using this category's policy.

        Generated categories start with a conservative interval. Tighten this
        after the category defines domain lifecycle states such as versions,
        volumes, episodes, editions, events, or ongoing series.
        """
        from datetime import datetime, timedelta, timezone

        policy = context.get("policy") or self.lifecycle_policy()
        days = int(policy.get("default_check_interval_days") or {int(spec.default_check_interval_days or 90)})
        check_at = datetime.now(timezone.utc) + timedelta(days=max(days, 1))
        return {{
            "next_check_at": check_at.isoformat(),
            "valid_until": check_at.isoformat(),
            "reason": f"Generated {{self.category_id}} lifecycle policy; refine with domain-specific rules before heavy automation.",
            "confidence": 0.55,
        }}

    def suggestion_policy(self) -> dict[str, Any]:
        """Expose suggestion policy metadata for UI and assistant context."""
        policy = self.lifecycle_policy()
        return {{
            "policy_version": policy.get("policy_version", 1),
            "suggestion_types": policy.get("suggestion_types", []),
            "invalidation_triggers": policy.get("invalidation_triggers", []),
            "llm_policy_description": policy.get("llm_policy_description", ""),
        }}

    def taste_profile_schema(self) -> dict[str, Any]:
        """Return category-owned taste metadata fields."""
        return {taste_schema}

    def taste_dimension_weights(self) -> dict[str, float]:
        """Return cautious metadata multipliers for derived taste facets."""
        return {taste_weights}

    def parse_name(self, name: str) -> ParsedMedia:
        """Parse a file or release name into generic category metadata."""
        return ParsedMedia(original_title=name, title=name)

    async def scan(self, root_path: str, existing_keys: set[str] | None = None) -> list[ScannedItem]:
        """Return scanned items for this category.

        The scaffold intentionally starts conservative. Add domain-specific
        file parsing only after fixtures and tests describe expected names.
        """
        return []
'''

    def render_prompt(self, spec: CategorySpec) -> str:
        """Render a category prompt stub from the spec."""
        example_lines = []
        for example in spec.examples:
            example_lines.append(f"- User: {example.user}\n  Expected: {example.expected_behavior}")
        examples = "\n".join(example_lines) if example_lines else "- Add category-specific examples before enabling automation."
        provider_notes = self._provider_research_notes(spec)
        download_research_notes = self._download_research_notes(spec)
        design_notes = spec.design_notes.strip() or "No extra design notes recorded yet."
        return f"""# {spec.display_name} Category Prompt

{spec.description}

## Routing
- Category ID: `{spec.category_id}`
- Item types: {', '.join(spec.item_types or [spec.category_id])}
- Identifiers: {', '.join(spec.identifiers)}

## Behavior Rules
- Use generic category item storage unless this category defines a specific workflow.
- Ask for missing identifiers before destructive or irreversible actions.
- Return ActionReceipt-compatible results for every category action.
- For recommendations and memory, record raw taste evidence first; derive profile summaries from evidence.
- Negative feedback should remain item/facet-scoped unless the user explicitly generalizes it.
- Keep the requested scope intact. Do not broaden this category into adjacent domains unless the user explicitly asks.
- Torrent/download selection is category-specific. Use this category's researched release/download vocabulary; do not inherit unrelated quality rules from other categories.
- Lifecycle, suggestion validity, and refresh cadence are category-owned. Do not ask the core app to add global special cases for this category.

## Lifecycle and suggestions
{self._lifecycle_policy_notes(spec)}

## Discovery and enrichment
- Declared metadata providers: {', '.join(spec.metadata_providers) or 'none yet'}
- Keep provider-specific knowledge inside this category or a category-owned provider adapter.
- Treat researched services as leads until official API/docs pages are reviewed.

## Download/search profile
{self._download_profile_notes(spec)}

## Design notes
{design_notes}

## Provider research leads
{provider_notes}

## Download-profile research leads
{download_research_notes}

## Examples
{examples}
"""

    def render_config(self, spec: CategorySpec) -> str:
        """Render default per-category YAML config."""
        property_lines: list[str] = []
        for prop in spec.properties:
            if prop.name == "library_path":
                continue
            property_lines.append(f"  {prop.name}: {repr(prop.default_value)}")
        properties = "\nproperties:\n" + "\n".join(property_lines) + "\n" if property_lines else ""
        discovery_yaml = self._render_discovery_yaml(spec)
        download_yaml = self._render_download_profile_yaml(spec)
        lifecycle_yaml = self._render_lifecycle_policy_yaml(spec)
        return (
            f"category_id: {spec.category_id}\n"
            "enabled: true\n"
            "paths:\n"
            f"  library_path: ./library/{spec.default_folder}\n"
            f"{properties}"
            f"{download_yaml}"
            f"{lifecycle_yaml}"
            f"{discovery_yaml}"
        )

    def render_test(self, spec: CategorySpec) -> str:
        """Render a smoke test for the generated category scaffold."""
        return f'''"""Smoke tests for the generated {spec.display_name} category."""

import pytest

from src.core.categories.custom.{spec.category_id} import {spec.class_name}
from src.core.models import Settings


@pytest.mark.asyncio
async def test_{spec.category_id}_category_manifest_and_scan() -> None:
    """Generated category exposes a manifest and a safe scan default."""
    category = {spec.class_name}()
    manifest = category.manifest(settings=Settings())
    scanned = await category.scan("/tmp/nonexistent")

    assert manifest.category_id == "{spec.category_id}"
    assert manifest.display_name == "{spec.display_name}"
    assert manifest.discovery_sources == category.discovery_contract()
    assert category.taste_profile_schema()["common_keys"]
    lifecycle_policy = category.lifecycle_policy()
    assert lifecycle_policy["policy_version"] >= 1
    assert lifecycle_policy["suggestion_types"]
    assert category.suggestion_policy()["invalidation_triggers"]
    assert scanned == []
'''

    def _lifecycle_policy_for(self, spec: CategorySpec) -> dict[str, Any]:
        """Build a generic but category-owned lifecycle/suggestion policy."""
        lifecycle_fields = list(spec.lifecycle_fields or [])
        for field in ["status", "metadata", "library_units", "taste_snapshot"]:
            if field not in lifecycle_fields:
                lifecycle_fields.append(field)
        suggestion_types = list(spec.suggestion_types or [])
        fallback_suggestions = ["metadata_repair", "better_release", "manual_review"]
        if "downloadable" in spec.capabilities:
            fallback_suggestions.insert(1, "missing_item")
        for suggestion in fallback_suggestions:
            if suggestion not in suggestion_types:
                suggestion_types.append(suggestion)
        invalidation_triggers = list(spec.invalidation_triggers or [])
        for trigger in [
            "metadata_changed",
            "library_changed",
            "taste_changed",
            "download_completed",
            "download_failed",
            "manual_refresh",
            "policy_version_changed",
        ]:
            if trigger not in invalidation_triggers:
                invalidation_triggers.append(trigger)
        return {
            "policy_version": 1,
            "identity_fields": list(spec.identifiers or ["title", "library_path"]),
            "lifecycle_fields": lifecycle_fields,
            "suggestion_types": suggestion_types,
            "invalidation_triggers": invalidation_triggers,
            "default_check_interval_days": int(spec.default_check_interval_days or 90),
            "llm_policy_description": (
                f"For {spec.display_name}, keep lifecycle, suggestion cadence, invalidation, and taste-sensitive "
                "refresh decisions inside the category. Refine this policy with domain-specific states before enabling "
                "expensive provider or search automation."
            ),
        }

    def _lifecycle_policy_notes(self, spec: CategorySpec) -> str:
        """Render human-readable lifecycle policy notes for the category prompt."""
        policy = self._lifecycle_policy_for(spec)
        return "\n".join([
            f"- Identity fields: {', '.join(policy['identity_fields'])}",
            f"- Lifecycle fields: {', '.join(policy['lifecycle_fields'])}",
            f"- Suggestion types: {', '.join(policy['suggestion_types'])}",
            f"- Invalidation triggers: {', '.join(policy['invalidation_triggers'])}",
            f"- Default check interval: {policy['default_check_interval_days']} day(s). Tighten this only with category-specific evidence.",
        ])

    def _render_lifecycle_policy_yaml(self, spec: CategorySpec) -> str:
        """Render lifecycle/suggestion policy into generated YAML config."""
        policy = self._lifecycle_policy_for(spec)
        lines = ["lifecycle_policy:"]
        lines.append(f"  policy_version: {int(policy['policy_version'])}")
        lines.append(f"  identity_fields: {self._yaml_list(policy['identity_fields'])}")
        lines.append(f"  lifecycle_fields: {self._yaml_list(policy['lifecycle_fields'])}")
        lines.append(f"  suggestion_types: {self._yaml_list(policy['suggestion_types'])}")
        lines.append(f"  invalidation_triggers: {self._yaml_list(policy['invalidation_triggers'])}")
        lines.append(f"  default_check_interval_days: {int(policy['default_check_interval_days'])}")
        lines.append(f"  llm_policy_description: {self._yaml_scalar(policy['llm_policy_description'])}")
        return "\n" + "\n".join(lines) + "\n"

    def _download_profile_notes(self, spec: CategorySpec) -> str:
        """Render category-specific download/search guidance for prompts."""
        if not spec.download_profile:
            if "downloadable" not in spec.capabilities:
                return "- This category is not marked downloadable yet."
            return (
                "- Downloadable, but no category-specific torrent/search profile has been reviewed yet.\n"
                "- Before production use, run download-profile research and define acceptable formats, required facets, category-specific reject terms, and relevant quality facets."
            )
        lines = []
        for key, value in spec.download_profile.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def _render_download_profile_yaml(self, spec: CategorySpec) -> str:
        """Render download/search profile into a reviewable YAML-like section."""
        if not spec.download_profile:
            return ""
        lines = ["download_profile:"]
        for key, value in spec.download_profile.items():
            if isinstance(value, (list, tuple)):
                lines.append(f"  {key}: {self._yaml_list(value)}")
            elif isinstance(value, dict):
                lines.append(f"  {key}: {repr(value)}")
            elif isinstance(value, bool):
                lines.append(f"  {key}: {str(value).lower()}")
            else:
                lines.append(f"  {key}: {self._yaml_scalar(value)}")
        return "\n" + "\n".join(lines) + "\n"

    def _provider_research_notes(self, spec: CategorySpec) -> str:
        """Render provider research leads for the category prompt."""
        if not spec.provider_research:
            return "- No web-researched provider leads recorded yet."
        lines: list[str] = []
        for lead in spec.provider_research[:8]:
            provider = str(lead.get("provider") or lead.get("provider_domain") or "unknown")
            purpose = str(lead.get("purpose") or "metadata_enrichment")
            domain = str(lead.get("provider_domain") or "")
            review = "requires review" if lead.get("requires_review", True) else "reviewed"
            suffix = f" ({domain})" if domain else ""
            lines.append(f"- {provider}{suffix}: {purpose}; {review} before provider-specific code is added.")
        return "\n".join(lines)

    def _download_research_notes(self, spec: CategorySpec) -> str:
        """Render researched download/release convention leads."""
        leads = getattr(spec, "download_profile_research", []) or []
        if not leads:
            return "- No web-researched download/release convention leads recorded yet."
        lines: list[str] = []
        for lead in leads[:10]:
            title = str(lead.get("source_title") or lead.get("title") or lead.get("source_domain") or "unknown source")
            domain = str(lead.get("source_domain") or "")
            snippet = str(lead.get("evidence_snippet") or lead.get("snippet") or "").strip()
            review = "requires review" if lead.get("requires_review", True) else "reviewed"
            suffix = f" ({domain})" if domain else ""
            if snippet:
                lines.append(f"- {title}{suffix}: {snippet[:180]}; {review}.")
            else:
                lines.append(f"- {title}{suffix}: {review} before category-specific download rules are hardened.")
        return "\n".join(lines)

    def _render_discovery_yaml(self, spec: CategorySpec) -> str:
        """Render discovery provider declarations into YAML-like config."""
        sources = self._discovery_contract_for(spec)
        if not sources:
            return ""
        lines = ["discovery_sources:"]
        for source in sources:
            lines.append(f"  - provider: {self._yaml_scalar(source.get('provider', 'unknown'))}")
            lines.append(f"    purpose: {self._yaml_scalar(source.get('purpose', 'metadata_enrichment'))}")
            lines.append(f"    required: {str(bool(source.get('required', False))).lower()}")
            setting_keys = source.get("setting_keys") or []
            taste_keys = source.get("taste_metadata_keys") or []
            lines.append(f"    setting_keys: {self._yaml_list(setting_keys)}")
            lines.append(f"    taste_metadata_keys: {self._yaml_list(taste_keys)}")
        return "\n" + "\n".join(lines) + "\n"

    @staticmethod
    def _yaml_scalar(value: Any) -> str:
        """Render a safe scalar for generated YAML snippets."""
        text = str(value).replace("'", "''")
        return f"'{text}'"

    def _yaml_list(self, values: Any) -> str:
        """Render a compact YAML list."""
        if not values:
            return "[]"
        return "[" + ", ".join(self._yaml_scalar(value) for value in values) + "]"

    def _supported_operations(self, spec: CategorySpec) -> list[str]:
        """Derive safe starting operations from capabilities."""
        operations = ["scan"]
        if "downloadable" in spec.capabilities:
            operations.extend(["search", "download"])
        if "metadata" in spec.capabilities or spec.metadata_providers:
            operations.append("refresh_metadata")
        return operations

    def _warnings_for(self, spec: CategorySpec) -> list[str]:
        """Return review warnings for incomplete but valid category specs."""
        warnings: list[str] = []
        if not any(prop.name == "library_path" for prop in spec.properties):
            warnings.append("No library_path property was provided; the scaffold will add one.")
        has_providers = bool(spec.metadata_providers or spec.discovery_sources)
        if "downloadable" in spec.capabilities and not has_providers:
            warnings.append("Downloadable categories usually need a metadata/discovery provider or clear identifiers.")
        if ("metadata" in spec.capabilities or "downloadable" in spec.capabilities) and not spec.provider_research and not has_providers:
            warnings.append("No provider research/discovery sources were supplied; run research_category_services before installing if metadata enrichment matters.")
        if "downloadable" in spec.capabilities and spec.download_profile and not getattr(spec, "download_profile_research", None):
            warnings.append("Download profile was supplied without recorded download-profile research leads; keep it provisional unless user requirements fully define it.")
        if "downloadable" in spec.capabilities and not spec.download_profile:
            warnings.append("Downloadable category has no download_profile; run research_category_download_profile before enabling automated searches.")
        if not spec.taste_dimensions:
            warnings.append("No category-specific taste dimensions were provided; generic cautious defaults will be used.")
        if not spec.lifecycle_fields:
            warnings.append("No lifecycle_fields were provided; generated lifecycle policy will use generic state/library/metadata defaults.")
        if not spec.suggestion_types:
            warnings.append("No suggestion_types were provided; generated suggestions will be limited to generic review/repair types until refined.")
        return warnings

    def _render_property_list(self, spec: CategorySpec) -> str:
        """Render CategoryProperty constructors for the generated class."""
        properties = list(spec.properties)
        if not any(prop.name == "library_path" for prop in properties):
            properties.insert(0, self._default_library_property(spec))
        rendered = []
        for prop in properties:
            default_repr = repr(prop.default_value)
            rendered.append(
                "CategoryProperty(\n"
                f"    name=\"{prop.name}\",\n"
                f"    value_type=\"{prop.value_type}\",\n"
                f"    description=\"{self._escape(prop.description)}\",\n"
                f"    default_value={default_repr},\n"
                f"    value=category_settings.get(\"{prop.name}\"),\n"
                "),"
            )
        return "\n".join(rendered)

    def _default_library_property(self, spec: CategorySpec) -> object:
        """Create the default library path property for a scaffold."""
        from src.core.models import CategoryProperty

        return CategoryProperty(
            name="library_path",
            value_type="string",
            description=f"Target folder for {spec.display_name} files.",
            default_value=f"./library/{spec.default_folder}",
        )

    def _taste_schema_for(self, spec: CategorySpec) -> dict[str, Any]:
        """Build a category-owned taste schema from the spec."""
        common_keys = [
            "display_name", "overview", "genres", "rating", "external_id",
            "provider", "release_year", "creators", "studios", "tags",
        ]
        for key in spec.taste_dimensions.keys():
            if key not in common_keys:
                common_keys.append(key)
        return {
            "common_keys": common_keys,
            "signal_types": ["mention", "curious", "like", "favorite", "dislike", "reject", "downloaded", "watched"],
        }

    def _taste_weights_for(self, spec: CategorySpec) -> dict[str, float]:
        """Return explicit taste dimensions or generic cautious defaults."""
        if spec.taste_dimensions:
            return {str(key): float(value) for key, value in spec.taste_dimensions.items()}
        return {
            "genres": 0.22,
            "creators": 0.35,
            "studios": 0.32,
            "tags": 0.28,
        }

    def _discovery_contract_for(self, spec: CategorySpec) -> list[dict[str, Any]]:
        """Return explicit discovery contract or provider-derived defaults."""
        if spec.discovery_sources:
            return [self._normalize_discovery_source(source, spec) for source in spec.discovery_sources]
        schema = self._taste_schema_for(spec)
        return [
            {
                "provider": provider,
                "purpose": "metadata_enrichment",
                "required": False,
                "setting_keys": [],
                "taste_metadata_keys": schema["common_keys"],
            }
            for provider in spec.metadata_providers
        ]

    def _normalize_discovery_source(self, source: dict[str, Any], spec: CategorySpec) -> dict[str, Any]:
        """Keep discovery contracts declarative and free of raw search-result noise."""
        schema = self._taste_schema_for(spec)
        return {
            "provider": str(source.get("provider") or "unknown"),
            "purpose": str(source.get("purpose") or "metadata_enrichment"),
            "required": bool(source.get("required", False)),
            "setting_keys": [str(key) for key in source.get("setting_keys", [])],
            "taste_metadata_keys": [str(key) for key in source.get("taste_metadata_keys", schema["common_keys"])],
        }

    @staticmethod
    def _escape(value: str) -> str:
        """Escape double quotes and newlines for generated Python strings."""
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
