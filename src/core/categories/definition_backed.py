"""Definition-backed runtime category for YAML-only category packages.

This module is intentionally generic: it lets a tracked category definition
become a real registered category without adding one Python subclass per media
domain.  Rich domains can still graduate to dedicated subclasses later, but the
baseline extension path should be data-driven enough for categories such as
music, ebooks, and audiobooks to appear in manifests, routing, setup, scanning,
and tool contracts.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.core.categories.base import CategoryMedia
from src.core.categories.audio_conversion import AudioConversionService
from src.core.categories.candidate_validation import DANGEROUS_FILE_SUFFIXES, DefinitionCandidateValidator
from src.core.categories.identity import clean_display_title, clean_path_fragment, canonical_item_key
from src.core.categories.local_object_reconstruction import (
    category_units_from_local_object,
    enrich_item_payload,
    scan_local_object,
)
from src.core.categories.types import ParsedMedia, ScannedFileObservation, ScannedItem
from src.core.models import (
    ActionReceipt,
    CategoryLlmProfile,
    CategoryPromptExample,
    CategoryProperty,
    CategoryWorkflowDeclaration,
    Intent,
    QualityProfile,
)
from src.integrations.category_metadata import CategoryMetadataResolver

if TYPE_CHECKING:
    from src.core.models import Settings


_YEAR_RE = re.compile(r"(?:^|\D)(?P<year>19\d{2}|20\d{2})(?:\D|$)")

class DefinitionBackedCategory(CategoryMedia):
    """Generic runtime implementation for a concrete YAML category definition.

    The class deliberately provides only safe, conservative behavior: manifest
    generation, router vocabulary, neutral file scanning, literal torrent query
    construction, basic candidate validation, and declared workflow preflight.
    Category-specific provider adapters and rich object models remain explicit
    future work instead of being faked by YAML.
    """

    def __init__(self, definition: dict[str, Any]) -> None:
        """Initialize a category from one effective definition payload."""
        super().__init__()
        self._definition = dict(definition or {})
        self.category_id = str(self._definition.get("category_id") or "").strip()
        self.display_name = str(self._definition.get("display_name") or self.category_id.replace("_", " ").title())
        self.default_folder = str(self._definition.get("default_folder") or self.display_name)
        self.icon = self._definition.get("icon") or "folder"
        self.media_kind = str(self._definition.get("media_kind") or self.category_id)
        self.capabilities = self._string_list(
            self._definition.get("capabilities"),
            default=["metadata", "downloadable", "file_organization"],
        )
        self.supported_operations = self._string_list(
            self._definition.get("supported_operations"),
            default=["search", "download", "scan", "organize"],
        )
        self.metadata_provider_names = self._metadata_provider_names()
        self.accepted_file_patterns = self._accepted_file_patterns()
        self.category_tool_names = self._configured_tool_names()
        try:
            self.router_priority = int(self._definition.get("router_priority", 0) or 0)
        except (TypeError, ValueError):
            self.router_priority = 0
        storage = self._section("storage")
        self._default_naming_template = str(storage.get("naming_template") or "{title}/{filename_stem}")
        self._audio_conversion = AudioConversionService(self, runtime_dependencies=self._section("runtime_dependencies"))

    @property
    def definition(self) -> dict[str, Any]:
        """Return the effective category definition backing this instance."""
        return dict(self._definition)

    def _section(self, name: str) -> dict[str, Any]:
        """Return one definition section as a mapping."""
        value = self._definition.get(name)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _string_list(value: Any, *, default: list[str] | None = None) -> list[str]:
        """Normalize a definition value into a list of non-empty strings."""
        if value is None:
            return list(default or [])
        raw = value if isinstance(value, list) else [value]
        result: list[str] = []
        for item in raw:
            token = str(item).strip()
            if token:
                result.append(token)
        return result

    def _metadata_provider_names(self) -> list[str]:
        """Return provider names declared by metadata or services sections."""
        providers = self._section("metadata").get("providers")
        names: list[str] = []
        if isinstance(providers, dict):
            names.extend(str(name) for name in providers.keys())
        services = self._section("services")
        names.extend(str(name) for name in services.keys())
        return sorted(set(name for name in names if name))

    def _accepted_file_patterns(self) -> list[str]:
        """Return accepted library file globs declared by the category."""
        formats = self._section("formats")
        patterns = self._string_list(formats.get("accepted_file_patterns"))
        return patterns or ["*.*"]

    def _configured_tool_names(self) -> list[str]:
        """Return generic and category workflow tool names from the definition."""
        tools = self._section("tools")
        names: list[str] = []
        for key in ("allowed_generic", "category_workflows", "tools"):
            names.extend(self._string_list(tools.get(key)))
        return sorted(set(names))

    def get_properties(self, settings: "Settings") -> list[CategoryProperty]:
        """Return setup properties for a definition-backed category."""
        cat_configs = settings.category_settings.get(self.category_id, {}) if settings is not None else {}
        prop = CategoryProperty(
            name="library_path",
            value_type="string",
            description=f"Absolute path where completed {self.display_name.lower()} files are organized.",
            default_value="",
        )
        prop.value = cat_configs.get(prop.name, prop.default_value)
        return [prop]

    def get_naming_template(self, settings: "Settings" = None) -> str:
        """Return the category's declarative naming template."""
        return self._default_naming_template

    def llm_profile(self) -> CategoryLlmProfile:
        """Build an LLM profile from the YAML definition."""
        profile_cfg = self._section("llm_profile")
        guidance = self._section("llm_guidance")
        formats = self._section("formats")
        tools = self._section("tools")
        examples = []
        for raw in profile_cfg.get("examples") or []:
            if not isinstance(raw, dict):
                continue
            examples.append(CategoryPromptExample(
                user=str(raw.get("user") or ""),
                expected_intent=str(raw.get("expected_intent") or "download"),
                expected_behavior=str(raw.get("expected_behavior") or "Use the category contract."),
                tool_plan=self._string_list(raw.get("tool_plan")),
            ))
        release_terms = self._string_list(formats.get("release_terms"))
        accepted = self._string_list(formats.get("accepted_file_patterns"))
        return CategoryLlmProfile(
            category_id=self.category_id,
            short_description=str(profile_cfg.get("short_description") or f"{self.display_name} managed through a definition-backed category."),
            user_facing_description=str(
                profile_cfg.get("user_facing_description")
                or f"{self.display_name} is a definition-backed LJS category with its own formats, metadata services, and download preferences."
            ),
            router_description=str(profile_cfg.get("router_description") or f"{self.display_name}: {self.media_kind} requests."),
            domain_vocabulary=self._string_list(profile_cfg.get("domain_vocabulary"), default=[self.display_name.lower(), self.category_id]) + release_terms[:16],
            item_types=self._string_list(profile_cfg.get("item_types"), default=[self.media_kind, self.category_id]),
            identifiers=self._string_list(profile_cfg.get("identifiers"), default=["title", "format", "library_path"]),
            common_user_requests=self._string_list(profile_cfg.get("common_user_requests")),
            ambiguity_rules=self._string_list(profile_cfg.get("ambiguity_rules")),
            search_rules=self._string_list(profile_cfg.get("search_rules")) + self._string_list(guidance.get("search_rules")),
            download_rules=self._string_list(profile_cfg.get("download_rules")) + self._string_list(guidance.get("download_rules")),
            organization_rules=self._string_list(profile_cfg.get("organization_rules")),
            safety_rules=self._string_list(profile_cfg.get("safety_rules")),
            tool_usage_notes=(
                self._string_list(profile_cfg.get("tool_usage_notes"))
                + self._string_list(tools.get("llm_usage_notes"))
                + self._string_list(guidance.get("behavior"))
                + (["Accepted file patterns: " + ", ".join(accepted)] if accepted else [])
            ),
            examples=examples,
        )

    def _search_policy(self) -> dict[str, Any]:
        """Return declarative torrent-search policy for this category."""
        policy = self._section("search_policy")
        if not policy:
            # Backward-compatible aliases for early definition drafts.
            policy = self._section("torrent_search")
        return policy if isinstance(policy, dict) else {}

    def language_is_search_relevant(self) -> bool:
        """Return whether language should constrain torrent search/ranking."""
        policy = self._search_policy()
        for key in ("language_relevant", "language_is_relevant"):
            if key in policy:
                return bool(policy.get(key))
        mode = str(policy.get("language_policy") or policy.get("language") or "").strip().lower()
        if mode in {"ignore", "none", "irrelevant", "not_relevant", "false"}:
            return False
        if mode in {"required", "relevant", "match", "true"}:
            return True
        # Definition-backed categories should opt into global language leakage.
        return False

    def normalize_search_language(self, language: str | None, *, explicit: bool = False) -> str | None:
        """Apply this category's language policy to a candidate search language."""
        value = str(language or "").strip()
        if not value:
            return None
        if not self.language_is_search_relevant():
            return None
        return value

    def uses_global_quality_profile(self) -> bool:
        """Return whether global video quality defaults apply to this category."""
        policy = self._search_policy()
        if "use_global_quality_profile" in policy:
            return bool(policy.get("use_global_quality_profile"))
        if "video_quality_relevant" in policy:
            return bool(policy.get("video_quality_relevant"))
        profile = self._section("download_profile")
        # Only categories that explicitly mention resolution should inherit the
        # video-oriented global QualityProfile.  Music/books/audio do not.
        return "preferred_resolution" in profile

    def create_item(self, key: str, **kwargs: Any):
        """Create a neutral item for this definition-backed category.

        GenericMediaItem historically carries English/1080p video defaults.
        Definition-backed categories clear those facets unless their own search
        policy says language or global video quality is meaningful.
        """
        from src.core.models import GenericMediaItem

        clean_kwargs = {k: v for k, v in kwargs.items() if k not in {"key", "name", "category_id", "item_id"}}
        if not self.language_is_search_relevant() and not str(clean_kwargs.get("language") or "").strip():
            clean_kwargs["language"] = ""
        if not self.uses_global_quality_profile() and "quality" not in clean_kwargs:
            clean_kwargs["quality"] = QualityProfile(preferred_resolution="", preferred_codecs=[])
        return GenericMediaItem(category_id=self.category_id, key=key, **clean_kwargs)

    def parse_name(self, name: str) -> ParsedMedia:
        """Parse a generic release/file name without inventing domain semantics."""
        original = str(name or "")
        stem = Path(original).stem if Path(original).suffix else original
        cleaned = stem.replace(".", " ").replace("_", " ").replace("-", " ")
        year = None
        match = _YEAR_RE.search(cleaned)
        if match:
            try:
                year = int(match.group("year"))
            except ValueError:
                year = None
        title = clean_display_title(cleaned, fallback=original or "Untitled")
        return ParsedMedia(
            original_title=original,
            title=title,
            year=year,
            # Resolution/codec extraction in the base class is video-oriented.
            # Definition-backed non-video categories opt out so scanners and
            # prompts do not reintroduce TV/movie quality vocabulary through a
            # generic parser path.
            resolution=self._extract_resolution(original) if self.uses_global_quality_profile() else None,
            codec=self._extract_codec(original) if self.uses_global_quality_profile() else None,
            language=self._extract_language(original) if self.language_is_search_relevant() else None,
        )

    def build_search_query(self, item: Any, unit_label: str | None, language: str | None) -> str:
        """Return a literal query with category-owned language/query policy."""
        parts = [str(getattr(item, "key", "") or getattr(item, "item_name", "") or "").strip()]
        if unit_label:
            parts.append(str(unit_label).strip())
        normalized_language = self.normalize_search_language(language)
        if normalized_language:
            parts.append(normalized_language)
        return " ".join(part for part in parts if part)

    def validate_search_result_for_request(self, result: Any, item: Any, unit_label: str | None) -> bool:
        """Reject dangerous/unrelated results using declarative search filters."""
        title = str(getattr(result, "title", "") or "")
        requested = f"{getattr(item, 'key', '')} {unit_label or ''}".strip()
        validator = DefinitionCandidateValidator(
            category_id=self.category_id,
            search_policy=self._search_policy(),
            string_list=lambda value: self._string_list(value),
        )
        return validator.validate(title=title, requested=requested).accepted

    def quality_reference_for_search(self, item: Any, unit_label: str | None, context: Any | None = None) -> str:
        """Return category-specific ranking guidance for torrent candidate selection."""
        profile = self._section("download_profile")
        formats = self._section("formats")
        return (
            f"{self.display_name} search is format- and edition-sensitive. Prefer visible title/author/artist/format "
            f"matches, healthy seeders, and plausible size. Declared accepted patterns: "
            f"{', '.join(self._string_list(formats.get('accepted_file_patterns'))[:12])}. "
            f"Download preferences: {profile}. Reject unrelated categories and dangerous executable payloads."
        )

    def build_torrent_selection_guidance(self) -> str:
        """Return category-owned torrent selection guidance without TV/movie leakage."""
        profile = self.llm_profile()
        formats = self._section("formats")
        accepted = ", ".join(self._string_list(formats.get("accepted_file_patterns"))[:16]) or "category-declared files"
        rules = []
        rules.extend(profile.search_rules[:6])
        rules.extend(profile.download_rules[:6])
        policy = self._search_policy()
        rejects = self._string_list(policy.get("reject_title_terms")) + self._string_list(policy.get("reject_terms"))
        if rejects:
            rules.append("Reject title terms for this category: " + ", ".join(rejects[:20]) + ".")
        rules.append("Reject candidates whose title strongly matches another category's declared release/file signatures instead of this category's own formats.")
        if not self.language_is_search_relevant():
            rules.append("Do not use global spoken-language preferences as torrent-search constraints for this category.")
        if not self.uses_global_quality_profile():
            rules.append("Ignore quality facets that are not declared by this category unless the user explicitly requested a companion item from another category.")
        body = " ".join(rule.strip() for rule in rules if str(rule).strip())
        return (
            f"This is a {self.display_name} download. Expected payload formats/patterns: {accepted}. "
            f"Select only candidates that match the requested {self.display_name.lower()} target. {body}"
        )

    def unit_descriptor_from_search_result(self, result: Any, item: Any, unit_label: str | None) -> dict[str, Any]:
        """Describe a queued definition-backed payload as a category item."""
        title = str(getattr(item, "key", "") or getattr(result, "title", "") or self.display_name)
        stable = canonical_item_key(title) or self.category_id
        return {
            "granularity": "item",
            "label": clean_display_title(title, fallback=self.display_name),
            "stable_key": stable,
            "sort_key": [stable],
            "coordinates": {"title": title, "category_id": self.category_id},
        }

    def unit_descriptor_from_file(self, file_path: str, parsed: Any | None = None, item_descriptor: dict[str, Any] | None = None) -> dict[str, Any]:
        """Use the torrent-relative filename as a stable file-level descriptor."""
        label = Path(str(file_path or "")).name or "file"
        return {
            "granularity": "file",
            "label": label,
            "stable_key": label,
            "sort_key": [label.lower()],
            "coordinates": {"filename": label, "category_id": self.category_id},
        }

    def torrent_file_priority(self, *, file_path: str, parsed: Any | None, file_descriptor: dict[str, Any], selected: bool) -> int:
        """Prioritize selected files matching the category's accepted patterns."""
        suffix = Path(str(file_path or "")).suffix.lower()
        lower = str(file_path or "").lower()
        if not selected or "sample" in lower or suffix in DANGEROUS_FILE_SUFFIXES:
            return 0
        if self._matches_accepted_pattern(file_path):
            return 4
        return 1 if not suffix else 0

    def download_target_for_item(
        self,
        source: Path,
        item: Any,
        settings: "Settings",
        *,
        source_name: str | None = None,
        file_info: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Return the ready-time import path under the category library root."""
        data = dict(metadata or {})
        title = data.get("title") or getattr(item, "item_name", "") or getattr(item, "torrent_title", "") or source.stem
        folder = clean_path_fragment(title, fallback=self.display_name)
        filename = Path(source_name or source.name).name
        return Path(self.get_root_path(settings)) / folder / filename

    async def scan(self, root_path: str, existing_keys: set[str] | None = None) -> list[ScannedItem]:
        """Scan a category library using declared file patterns."""
        root = Path(root_path)
        try:
            summaries = await asyncio.to_thread(self._collect_entries, root)
        except OSError as exc:
            logger.error(f"[{self.__class__.__name__}] Failed to access root path '{root_path}': {exc}")
            return []
        return [self._summary_to_scanned(summary) for summary in summaries if summary.get("files")]

    def _collect_entries(self, root: Path) -> list[dict[str, Any]]:
        """Collect category entries using the definition's local scan strategy."""
        if not root.is_dir():
            return []
        strategy = str(self._section("local_scan").get("grouping_strategy") or "top_level_catalog")
        if strategy == "file_or_edition_folder":
            return self._collect_file_or_edition_entries(root)
        if strategy == "leaf_folder_or_file":
            return self._collect_leaf_folder_entries(root)
        return self._collect_top_level_entries(root)

    def _collect_top_level_entries(self, root: Path) -> list[dict[str, Any]]:
        """Collect one item per top-level folder/file, useful for artist catalogs."""
        entries: list[dict[str, Any]] = []
        for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_file() and self._is_library_file(child):
                    entries.append({"name": child.stem, "files": [child], "root": root})
                elif child.is_dir():
                    files = [p for p in child.rglob("*") if p.is_file() and self._is_library_file(p)]
                    if files:
                        entries.append({"name": child.name, "files": sorted(files, key=lambda path: str(path).lower()), "root": child})
            except OSError as exc:
                logger.warning(f"[{self.category_id}] Skipping unreadable entry '{child}': {exc}")
        return entries

    def _collect_file_or_edition_entries(self, root: Path) -> list[dict[str, Any]]:
        """Collect ebook/comic entries as one edition-like item per file or edition folder."""
        entries: list[dict[str, Any]] = []
        seen: set[Path] = set()
        try:
            files = sorted((p for p in root.rglob("*") if p.is_file() and self._is_library_file(p)), key=lambda path: str(path).lower())
        except OSError as exc:
            logger.warning(f"[{self.category_id}] Failed to scan '{root}': {exc}")
            return entries
        by_parent: dict[Path, list[Path]] = {}
        for file_path in files:
            by_parent.setdefault(file_path.parent, []).append(file_path)
        for parent, parent_files in sorted(by_parent.items(), key=lambda item: str(item[0]).lower()):
            # If a folder contains several formats with the same base title, group them
            # as one local edition. Otherwise each file is its own ebook/comic item.
            stems: dict[str, list[Path]] = {}
            for file_path in parent_files:
                stems.setdefault(file_path.stem.lower(), []).append(file_path)
            for stem, stem_files in stems.items():
                if len(stem_files) > 1:
                    entries.append({"name": clean_display_title(stem_files[0].stem), "files": sorted(stem_files), "root": parent})
                    seen.update(stem_files)
            for file_path in parent_files:
                if file_path not in seen:
                    entries.append({"name": clean_display_title(file_path.stem), "files": [file_path], "root": file_path.parent})
                    seen.add(file_path)
        return entries

    def _collect_leaf_folder_entries(self, root: Path) -> list[dict[str, Any]]:
        """Collect audiobook entries by book folder or by single audio file."""
        entries: list[dict[str, Any]] = []
        try:
            files = sorted((p for p in root.rglob("*") if p.is_file() and self._is_library_file(p)), key=lambda path: str(path).lower())
        except OSError as exc:
            logger.warning(f"[{self.category_id}] Failed to scan '{root}': {exc}")
            return entries
        by_parent: dict[Path, list[Path]] = {}
        for file_path in files:
            by_parent.setdefault(file_path.parent, []).append(file_path)
        grouped: set[Path] = set()
        for parent, parent_files in sorted(by_parent.items(), key=lambda item: str(item[0]).lower()):
            child_audio_dirs = [child for child in parent.iterdir() if child.is_dir() and any(self._is_library_file(p) for p in child.rglob("*") if p.is_file())]
            if child_audio_dirs:
                continue
            if len(parent_files) == 1 and parent == root:
                file_path = parent_files[0]
                entries.append({"name": clean_display_title(file_path.stem), "files": [file_path], "root": parent})
            else:
                entries.append({"name": clean_display_title(parent.name), "files": sorted(parent_files), "root": parent})
            grouped.update(parent_files)
        for file_path in files:
            if file_path not in grouped:
                entries.append({"name": clean_display_title(file_path.stem), "files": [file_path], "root": file_path.parent})
        return entries

    def _summary_to_scanned(self, summary: dict[str, Any]) -> ScannedItem:
        """Convert collected filesystem facts into a neutral scanned item."""
        observations: list[ScannedFileObservation] = []
        total = 0
        codecs: set[str] = set()
        languages: set[str] = set()
        qualities: set[str] = set()
        entry_root = summary.get("root")
        for file_path in summary.get("files") or []:
            size = int(file_path.stat().st_size)
            total += size
            parsed = self.parse_name(file_path.name)
            suffix_quality = file_path.suffix.lower().lstrip(".") or "unknown"
            if parsed.codec:
                codecs.add(parsed.codec)
            if parsed.language:
                languages.add(parsed.language)
            qualities.add(parsed.resolution or suffix_quality)
            try:
                relative_path = file_path.relative_to(entry_root).as_posix() if entry_root else file_path.name
            except Exception:
                relative_path = file_path.name
            observations.append(ScannedFileObservation(
                file_path=str(file_path),
                quality=parsed.resolution or suffix_quality,
                size_bytes=size,
                detected_language=parsed.language or "",
                media_probe={"local_scan": {"relative_path": relative_path, "extension": suffix_quality}},
            ))
        scanned = ScannedItem(
            name=clean_display_title(summary.get("name"), fallback=self.display_name),
            category_id=self.category_id,
            resolutions=sorted(quality for quality in qualities if quality),
            codecs=sorted(codecs),
            detailed_episodes=observations,
            file_count=len(observations),
            total_size_bytes=total,
            detected_language=sorted(languages)[0] if languages else "",
            detected_languages=sorted(languages),
        )
        scanned.local_object_model = scan_local_object(self.category_id, scanned)
        return scanned

    def library_item_from_scan(self, scanned: Any) -> dict[str, Any]:
        """Attach definition-backed local object evidence to the generic item envelope."""
        payload = super().library_item_from_scan(scanned)
        return enrich_item_payload(self.category_id, payload, scanned)

    def library_units_from_scan(self, scanned: Any) -> list[dict[str, Any]]:
        """Build rich Music/Ebook/Audiobook units when local object evidence is available."""
        units = category_units_from_local_object(self.category_id, scanned)
        if units is not None:
            return units
        return super().library_units_from_scan(scanned)

    def library_progress_from_scan(self, scanned: Any, units: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Expose category-local progress counts without core category assumptions."""
        progress = super().library_progress_from_scan(scanned, units)
        if not progress:
            return None
        if self.category_id in {"music", "audiobooks", "ebooks"}:
            progress = dict(progress)
            progress["local_model_type"] = (getattr(scanned, "local_object_model", {}) or {}).get("model_type", "")
            progress["downloaded_unit_types"] = sorted({str(unit.get("unit_type") or "") for unit in units if unit.get("status") == "downloaded"})
        return progress

    def declare_workflows(self) -> list[CategoryWorkflowDeclaration]:
        """Declare workflows listed in the category definition."""
        tools = self._section("tools")
        workflows: list[CategoryWorkflowDeclaration] = []
        for tool_name in self._string_list(tools.get("category_workflows")):
            workflow_name = tool_name.split(".", 1)[1] if tool_name.startswith(f"{self.category_id}.") else tool_name
            workflows.append(CategoryWorkflowDeclaration(
                name=workflow_name,
                tool_name=tool_name,
                description=self._workflow_description(workflow_name),
                intent=self._workflow_intent(workflow_name),
                risk_level="write" if self._workflow_is_write(workflow_name) else "read",
                requires_confirmation=self._workflow_is_write(workflow_name),
                parameters=self._workflow_parameters(workflow_name),
            ))
        return workflows

    async def execute_workflow(self, workflow_name: str, arguments: dict[str, Any], context: Any) -> ActionReceipt:
        """Execute supported generic workflows or fail honestly for adapter gaps."""
        if workflow_name == "convert_audio_for_apple":
            return await self._audio_conversion.execute_convert_audio_for_apple(arguments, context)
        if workflow_name == "resolve_metadata":
            return await self._execute_resolve_metadata(arguments, context)
        declared = {workflow.name for workflow in self.declare_workflows()}
        if workflow_name in declared:
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="failed",
                user_message=(
                    f"{self.display_name} declares workflow '{workflow_name}', but this generic category "
                    "does not yet have a category-specific executor wired for that operation."
                ),
                technical_message="Declared workflow exists without a concrete executor implementation.",
                data={"missing_adapter": True, "workflow_name": workflow_name},
            )
        return await super().execute_workflow(workflow_name, arguments, context)

    def _workflow_description(self, workflow_name: str) -> str:
        """Return a human-readable description for a declared workflow."""
        if workflow_name == "convert_audio_for_apple":
            return "Convert an audio file into Apple-friendly .m4a output using FFmpeg, preserving the source file."
        return f"{self.display_name} category workflow: {workflow_name.replace('_', ' ')}."

    @staticmethod
    def _workflow_intent(workflow_name: str) -> Intent:
        """Infer a safe tool intent scope from a workflow name."""
        if any(token in workflow_name for token in ("download", "convert", "queue")):
            return Intent.DOWNLOAD
        return Intent.SEARCH

    @staticmethod
    def _workflow_is_write(workflow_name: str) -> bool:
        """Return whether a workflow mutates files/download state."""
        return any(token in workflow_name for token in ("download", "convert", "queue", "delete", "repair"))

    @staticmethod
    def _workflow_parameters(workflow_name: str) -> dict[str, Any]:
        """Return JSON schema for generic workflow arguments."""
        if workflow_name == "convert_audio_for_apple":
            return {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "Existing audio file inside the category library/download roots."},
                    "target_path": {"type": "string", "description": "Optional destination path inside allowed roots."},
                    "target_profile": {"type": "string", "enum": ["apple_lossless_m4a", "apple_aac_m4a"]},
                    "overwrite": {"type": "boolean"},
                    "confirmed": {"type": "boolean", "description": "Must be true to run FFmpeg; omitted/false returns a preview."},
                },
                "required": ["source_path"],
            }
        if workflow_name == "resolve_metadata":
            return {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Artist/album, author/book, or audiobook title to resolve."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            }
        return {"type": "object", "properties": {}, "required": []}


    async def _execute_resolve_metadata(self, arguments: dict[str, Any], context: Any) -> ActionReceipt:
        """Resolve category metadata through declared free/keyless provider adapters."""
        settings = getattr(context, "settings", None)
        if settings is None:
            return self._workflow_error("resolve_metadata", "Settings are unavailable; cannot read category service configuration.")
        query = str(arguments.get("query") or arguments.get("title") or "").strip()
        if not query:
            return self._workflow_error("resolve_metadata", "query is required.")
        try:
            limit = int(arguments.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        data = await CategoryMetadataResolver(self, settings, db=getattr(context, "db", None)).resolve(query, limit=limit)
        persisted = await self._persist_resolved_metadata(arguments, context, data)
        if persisted:
            data["persisted_library_metadata"] = persisted
        status = "success" if data.get("results") else "partial"
        message = f"Resolved {len(data.get('results') or [])} {self.display_name.lower()} metadata candidate(s) for '{query}'."
        return ActionReceipt(
            category_id=self.category_id,
            action_name="resolve_metadata",
            status=status,
            user_message=message,
            data=data,
        )

    async def _persist_resolved_metadata(self, arguments: dict[str, Any], context: Any, data: dict[str, Any]) -> dict[str, Any] | None:
        """Persist the selected/best metadata snapshot when resolving a library item.

        Manual metadata resolution can be a pure lookup, but scheduler/library refresh
        calls include an item_id. In that case the category should save a stable
        provider snapshot so the UI and future refresh scheduling have durable
        evidence instead of repeatedly re-querying providers.
        """
        db = getattr(context, "db", None)
        media_repo = getattr(db, "media", None) if db is not None else None
        if media_repo is None or not hasattr(media_repo, "upsert_category_metadata"):
            return None
        item_id = str(arguments.get("item_id") or "").strip()
        if not item_id:
            return None
        best = data.get("best") if isinstance(data.get("best"), dict) else None
        if not best:
            return None
        provider = str(best.get("provider") or "metadata")
        stable_id = str(best.get("stable_id") or "")
        identifiers = best.get("identifiers") if isinstance(best.get("identifiers"), dict) else {}
        external_id = stable_id or next((str(value) for value in identifiers.values() if value), "")
        snapshot = dict(best)
        snapshot.update({
            "provider": provider,
            "external_id": external_id,
            "stable_id": stable_id,
            "title": best.get("title") or item_id,
            "poster_url": best.get("cover_url") or best.get("poster_url") or "",
            "cover_url": best.get("cover_url") or "",
            "metadata_refresh_policy": self.metadata_refresh_policy(provider=provider),
        })
        await media_repo.upsert_category_metadata(self.category_id, item_id, provider, snapshot, external_id)
        return {"provider": provider, "external_id": external_id, "stable_id": stable_id}

    def metadata_refresh_policy(self, *, provider: str = "") -> dict[str, Any]:
        """Return category/provider refresh hints for persisted library metadata."""
        lifecycle = self._section("lifecycle_policy")
        try:
            default_days = int(lifecycle.get("default_check_interval_days") or 90)
        except (TypeError, ValueError):
            default_days = 90
        provider_ttls = {
            "musicbrainz": 14,
            "discogs": 14,
            "open_library": 7,
            "gutendex": 30,
            "internet_archive": 7,
            "google_books": 7,
            "apple_itunes_search": 1,
            "librivox": 14,
            "comic_vine": 14,
        }
        provider_days = provider_ttls.get(str(provider or ""), default_days)
        # Stable library items should not refresh as often as transient search
        # cache rows. Use the category lifecycle cadence as the floor and provider
        # TTL only as a lower-bound signal for volatile store/search providers.
        refresh_days = max(default_days, provider_days)
        return {
            "default_check_interval_days": default_days,
            "provider_ttl_days": provider_days,
            "refresh_after_days": refresh_days,
            "uses_stable_id": True,
        }

    async def after_library_file_imported(
        self,
        *,
        imported_path: Path,
        source_path: Path,
        item: Any,
        settings: "Settings",
        file_info: Any | None = None,
    ) -> list[Path]:
        """Delegate preference-driven audio sidecar creation to the audio workflow service."""
        return await self._audio_conversion.after_library_file_imported(
            imported_path=imported_path,
            source_path=source_path,
            item=item,
            settings=settings,
            file_info=file_info,
        )

    def _workflow_error(self, workflow_name: str, message: str, data: dict[str, Any] | None = None) -> ActionReceipt:
        """Return a failed workflow receipt."""
        return ActionReceipt(
            category_id=self.category_id,
            action_name=workflow_name,
            status="failed",
            user_message=message,
            technical_message=message,
            data=data or {},
        )

    def _is_library_file(self, path: Path) -> bool:
        """Return whether a path is safe and matches the category patterns."""
        suffix = path.suffix.lower()
        if suffix in DANGEROUS_FILE_SUFFIXES:
            return False
        return self._matches_accepted_pattern(path.name)

    def _matches_accepted_pattern(self, file_path: str | Path) -> bool:
        """Return whether the file path matches at least one declared glob."""
        name = Path(str(file_path)).name.lower()
        for pattern in self.accepted_file_patterns:
            if Path(name).match(str(pattern).lower()):
                return True
        return False
