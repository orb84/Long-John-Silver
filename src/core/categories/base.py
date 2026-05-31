"""
Base classes for category packages in LJS.

Every library domain extends ``MediaCategory``. The concrete category owns
file identification, search semantics, organization, canonical object shape,
and LLM guidance. Generic callers must route through this contract instead of
adding domain branches to the scheduler, library core, UI, or assistant.
"""

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from dataclasses import dataclass, field
from loguru import logger

from src.core.categories.language import LanguageDetector, LanguageSearchTagger
from src.core.categories.verifier import MediaVerifier
from src.core.categories.path_planner import CategoryPathPlanner
from src.core.categories.consolidator import LibraryConsolidator
from src.core.categories.search_patterns import SearchPatterns
from src.core.categories.base_contract import CategoryContractMixin
from src.core.security.path_policy import SafePathResolver, SecurityPolicyError
from src.core.security.confirmation import SecurityConfirmationService
from src.core.categories.types import ScannedItem, ParsedMedia
from src.core.categories.identity import clean_display_title, basename_from_pathish
from src.core.models import (
    ActionReceipt,
    CategoryActionDeclaration,
    CategoryLlmProfile,
    CategoryManifest,
    CategoryPromptExample,
    CategoryProperty,
    CategorySetupRequirement,
    CategoryRouterBrief,
    CategoryUiSection,
    CategoryWorkflowDeclaration,
)

if TYPE_CHECKING:
    from src.core.models import Settings, QualityProfile, CategoryItem
    from src.core.database import Database
    from src.search.aggregator import SearchAggregator
    from src.core.search_pipeline import SearchPipeline



@dataclass
class CategoryUpdateContext:
    """Dependencies passed to category update operations."""
    db: 'Database'
    pipeline: 'SearchPipeline'
    aggregator: 'SearchAggregator'
    settings: 'Settings'


@dataclass
class CategoryWorkflowContext:
    """Runtime dependencies passed to category-owned workflows.

    Workflows use this context instead of importing global services. Keeping the
    scheduler, web API, and assistant dependent on this context allows each
    category to own metadata, search, download, and destructive actions without
    hardcoded global category branches.
    """

    db: 'Database'
    pipeline: 'SearchPipeline'
    aggregator: 'SearchAggregator'
    settings: 'Settings'
    downloader: object | None = None
    metadata_clients: dict[str, object] = field(default_factory=dict)
    metadata_enricher: object | None = None
    artwork_manager: object | None = None
    web_search: object | None = None
    user_id: str | None = None
    session_id: str | None = None
    category_registry: object | None = None
    search_constraints: dict[str, Any] = field(default_factory=dict)


class MediaCategory(CategoryContractMixin, ABC):
    """Abstract base for a media category.

    Subclass this to add support for a new type of media.
    Register the subclass with CategoryRegistry to make it
    available in the library, scheduler, and UI.
    """

    # ── Subclass must set these ───────────────────────────────────

    category_id: str = ""
    display_name: str = ""
    default_folder: str = ""
    icon: str | None = None
    media_kind: str = "media"
    capabilities: list[str] = []
    metadata_provider_names: list[str] = []
    supported_operations: list[str] = []
    category_tool_names: list[str] = []
    prompt_file: str | None = None
    router_priority: int = 0
    """Tie-breaker for deterministic routing; fallback categories should be lower."""

    # ── Collaborators ────────────────────────────────────────────

    _language_detector: LanguageDetector
    _language_tagger: type[LanguageSearchTagger] = LanguageSearchTagger
    _media_verifier: MediaVerifier
    _path_planner: CategoryPathPlanner
    _consolidator: LibraryConsolidator


    def __init__(self) -> None:
        """Initialize category collaborator instances."""
        self._language_detector = LanguageDetector()
        self._media_verifier = MediaVerifier()
        self._path_planner = CategoryPathPlanner()
        self._consolidator = LibraryConsolidator(self._path_planner)
        self._confirmation_service = SecurityConfirmationService()

    # ── Content type classifiers ──────────────────────────────────

    is_episodic: bool = False
    """Compatibility hint for older category packages.

    New code should prefer the declarative canonical object specification and
    category workflow declarations rather than branching on this flag.
    """

    # ── Content type constraints ───────────────────────────────────

    accepted_file_patterns: list[str] = ["*.mkv", "*.mp4", "*.avi", "*.webm"]
    """File extensions this category accepts. Used to guide the LLM
    torrent selection — candidates whose title suggests non-matching
    file types should be rejected."""

    # ── Search patterns ───────────────────────────────────────────

    @property
    def search(self) -> SearchPatterns:
        """Return search query patterns for this category."""
        return SearchPatterns()


    # ── Category Properties ────────────────────────────────────────

    @abstractmethod
    def get_properties(self, settings: "Settings") -> list[CategoryProperty]:
        """Return the custom configuration properties for this category."""
        ...

    def get_property_value(self, name: str, settings: "Settings") -> Any:
        """Get the configured value for a property from settings."""
        props = self.get_properties(settings)
        prop = next((p for p in props if p.name == name), None)
        if not prop:
            raise KeyError(f"Property '{name}' not found in category '{self.category_id}'")
        
        cat_configs = settings.category_settings.get(self.category_id, {})
        return cat_configs.get(name, prop.default_value)

    def set_property_value(self, settings: "Settings", name: str, value: Any) -> None:
        """Set and validate a property value in settings."""
        props = self.get_properties(settings)
        prop = next((p for p in props if p.name == name), None)
        if not prop:
            raise ValueError(f"Property '{name}' is not supported by category '{self.category_id}'")
        
        # Validate/coerce value
        try:
            if prop.value_type == "int":
                value = int(value)
            elif prop.value_type == "float":
                value = float(value)
            elif prop.value_type == "bool":
                if isinstance(value, str):
                    value = value.lower() in ("true", "1", "yes")
                else:
                    value = bool(value)
            elif prop.value_type == "string":
                value = str(value)
        except Exception as e:
            raise ValueError(f"Invalid value for property '{name}' (expected {prop.value_type}): {e}")
            
        if self.category_id not in settings.category_settings:
            settings.category_settings[self.category_id] = {}
        settings.category_settings[self.category_id][name] = value

    def create_item(self, key: str, **kwargs: Any) -> "CategoryItem":
        """Create the default tracked item model for this category.

        Category item creation is owned by the category so web/API code does
        not need hardcoded category/model branches. Concrete categories may
        override this to return richer item subclasses.
        """
        from src.core.models import GenericMediaItem

        clean_kwargs = {k: v for k, v in kwargs.items() if k not in {"key", "name", "category_id", "item_id"}}
        return GenericMediaItem(category_id=self.category_id, key=key, **clean_kwargs)

    def supports_capability(self, capability: str) -> bool:
        """Return whether this category advertises a manifest capability."""
        return capability in set(self.capabilities)

    # ── Path resolution ───────────────────────────────────────────

    def default_root_path(self, settings: "Settings") -> str:
        """Return this category's default path under the global library root.

        The global root is the normal user-facing setting.  Category-specific
        ``library_path`` values are optional overrides for users who want Movies,
        TV, Music, Books, or any custom category on different disks.
        """
        root = getattr(settings, "library_root", "./library") if settings is not None else "./library"
        return str(Path(root) / (self.default_folder or self.category_id))

    def get_root_path(self, settings: "Settings") -> str:
        """Return the effective root filesystem path for this category.

        Prefer an explicit category ``library_path`` override.  If absent or
        blank, fall back to ``settings.library_root/<default_folder>``.  The
        legacy ``settings.library_paths`` map is still honored as a migration
        fallback, but category config remains the save-time authority.
        """
        custom_path = ""
        if settings is not None:
            try:
                custom_path = self.get_property_value("library_path", settings)
            except KeyError:
                custom_path = ""
        if custom_path:
            return str(custom_path)
        legacy_paths = getattr(settings, "library_paths", {}) if settings is not None else {}
        if isinstance(legacy_paths, dict):
            legacy_path = str(legacy_paths.get(self.category_id) or "").strip()
            if legacy_path:
                return legacy_path
        return self.default_root_path(settings)

    def ensure_root_path(self, settings: "Settings") -> str:
        """Create and return the effective root path for write-time workflows."""
        root = Path(self.get_root_path(settings)).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        return str(root)

    # ── Name parsing ───────────────────────────────────────────────

    _RESOLUTION_RE = re.compile(r'(?P<resolution>2160p|1080p|720p|480p|4K)', re.IGNORECASE)
    _CODEC_RE = re.compile(r'(?P<codec>x264|x265|h264|h265|hevc|av1|xvid)', re.IGNORECASE)

    @abstractmethod
    def parse_name(self, name: str) -> 'ParsedMedia':
        """Parse a torrent/file name into structured media info.

        Subclasses implement category-specific parsing.
        The base class provides _extract_resolution, _extract_codec, and _extract_language
        as generic helpers; structured coordinates are interpreted only by the category that declares them.
        """
        ...

    def _extract_resolution(self, name: str) -> str | None:
        """Extract resolution from a name (2160p, 1080p, 720p, etc.)."""
        m = self._RESOLUTION_RE.search(name.replace('.', ' '))
        return m.group('resolution').lower() if m else None

    def _extract_codec(self, name: str) -> str | None:
        """Extract video codec from a name (h264, h265, hevc, etc.)."""
        m = self._CODEC_RE.search(name.replace('.', ' '))
        return m.group('codec').lower() if m else None

    def _extract_language(self, name: str) -> str | None:
        """Extract language tag from a release name.

        Delegates to LanguageDetector.from_name().
        """
        return self._language_detector.from_name(name)

    def validate_result(self, title: str, req_season: int | None = None,
                        req_episode: int | None = None) -> bool:
        """Regex-based validation of a search result against requested structured coordinates.

        Shared logic: parses the title and checks optional structured coordinate matches.
        Subclasses can override for custom validation rules.

        Args:
            title: The search result title to validate.
            req_season: Optional first structured coordinate retained for compatibility.
            req_episode: Optional second structured coordinate retained for compatibility.

        Returns:
            True if the result matches the request at the basic level.
        """
        parsed = self.parse_name(title)
        if req_season is not None and parsed.season is not None and parsed.season != req_season:
            return False
        if req_episode is not None and parsed.episode is None:
            return False
        if req_episode is not None and parsed.episode is not None and parsed.episode != req_episode:
            return False
        return True

    def build_llm_selection_prompt(self, candidates: list, item_name: str,
                                   episode_label: str, language: str) -> str:
        """Build the LLM prompt for selecting the best candidate from a list.

        The LLM receives this prompt along with normalized candidate data
        and must return the index of the best match. Subclasses can override
        to add category-specific guidance.

        Args:
            candidates: List of NormalizedTorrentCandidate dicts ready for the LLM.
            item_name: The media item name being searched for.
            episode_label: Optional category-owned unit/search label.
            language: Preferred language.

        Returns:
            A prompt string instructing the LLM how to select.
        """
        return (
            f"You are selecting the best torrent for '{item_name} {episode_label or ''}'.\n"
            f"Preferred language: {language}.\n"
            f"Choose the best candidate. Consider:\n"
            f"1. Language must match '{language}'\n"
            f"2. Higher seeders = more reliable\n"
            f"3. Prefer WEB-DL/BluRay over CAM/TS\n"
            f"4. Reasonable file size for the content\n"
            f"5. Higher resolution is better but must match the content\n"
            f"\nReturn ONLY the index number of the best candidate (e.g. '2'). "
            f"Return '-1' if none are acceptable."
        )

    # ── Naming templates ───────────────────────────────────────────

    _default_naming_template: str = '{title}/{filename_stem}'
    """Default naming template. Subclasses override for category-specific defaults."""

    def get_naming_template(self, settings: 'Settings' = None) -> str:
        """Return the naming template for this category.

        Reads from settings if user configured one, otherwise uses class default.
        ``settings`` may be omitted by preview/tests/path planners that already
        provide an explicit library root and only need the category default.
        """
        if settings is None:
            return self._default_naming_template
        return self.get_property_value("naming_template", settings)

    def format_template(self, template: str, data: dict) -> str:
        """Safely format a naming template with provided data."""
        return self._path_planner.format_template(template, data)

    def compute_target_path_from_fields(
        self,
        *,
        source_name: str,
        fields: dict[str, Any],
        settings: Optional['Settings'] = None,
        library_root: str | None = None,
    ) -> Path:
        """Compute a library path from category-owned template fields.

        This is the preferred path API for new code.  The category decides what
        fields exist, including any structured coordinates.  The shared planner
        only sanitizes and formats those fields; it does not interpret them.
        """
        return self._path_planner.compute_target_path_from_fields(
            source_name=source_name,
            template=self.get_naming_template(settings),
            library_root=library_root or './library',
            fields=fields,
        )

    def compute_target_path(self, source_name: str, item_name: str,
                            season: int, episode: int, **kwargs: Any) -> Path:
        """Compatibility wrapper for older category path callers.

        Keep this method on the category object, not in generic services. New
        consumers should call category hooks such as ``download_target_for_item``
        or ``consolidation_target_for_file`` so only the owning category decides
        which template variables are meaningful.
        """
        template = self.get_naming_template(kwargs.get('settings'))
        library_root = kwargs.get('library_root') or './library'
        safe_kwargs = {k: v for k, v in kwargs.items()
                       if k not in ('settings', 'library_root', 'item_name')}
        return self._path_planner.compute_target_path(
            source_name=source_name,
            item_name=item_name,
            season=season,
            episode=episode,
            template=template,
            library_root=library_root,
            **safe_kwargs,
        )


    def consolidation_target_for_file(
        self,
        file_path: Path,
        root_path: Path,
        parsed: ParsedMedia,
        settings: Optional['Settings'] = None,
    ) -> Path:
        """Return the desired consolidated path for one local file.

        The consolidator is only a file-walking/security executor.  Category
        packages own how parsed file facts map into template fields.  The base
        implementation supports simple categories and existing templates that
        use structured unit coordinates; richer categories should override this
        rather than adding branches to ``LibraryConsolidator``.
        """
        try:
            relative = file_path.relative_to(root_path)
            fallback_title = relative.parts[0] if relative.parts else file_path.stem
        except ValueError:
            fallback_title = file_path.stem
        fields = {
            "title": parsed.title or fallback_title,
            "year": parsed.year or "",
            "quality": parsed.resolution or "",
            "release_group": parsed.release_group or "",
        }
        return self.compute_target_path_from_fields(
            source_name=file_path.name,
            fields=fields,
            settings=settings,
            library_root=str(root_path),
        )

    def fallback_library_path(
        self,
        source: Path,
        item_name: str,
        settings: 'Settings',
        *,
        season: int | None = None,
        episode: int | None = None,
        source_name: str | None = None,
        year: int | None = None,
        episode_title: str | None = None,
    ) -> Path:
        """Return a safe category-owned fallback destination.

        Download completion calls this only when normal category target planning
        fails.  Keep it conservative and override inside concrete categories if
        they need a richer fallback layout.
        """
        root = Path(self.get_root_path(settings))
        title = clean_display_title(item_name or source.stem, fallback="Unknown")
        filename = basename_from_pathish(source_name or source.name, fallback=source.name or "file")
        return root / title / filename

    def download_target_for_item(
        self,
        source: Path,
        item: Any,
        settings: 'Settings',
        *,
        source_name: str | None = None,
        file_info: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Return the library target for a completed download.

        Download completion is a generic workflow, but library naming is
        category-owned.  The core passes the raw download item/file context and
        the category extracts only the fields its naming template supports.
        """
        data = dict(metadata or {})
        title = clean_display_title(data.get("title") or getattr(item, "item_name", "") or source.stem, fallback="Unknown")
        data.setdefault("title", title)
        data.setdefault("year", getattr(item, "year", None) or "")
        data.setdefault("quality", getattr(item, "quality", "") or "")
        return self.compute_target_path_from_fields(
            source_name=source_name or source.name,
            fields=data,
            settings=settings,
            library_root=self.get_root_path(settings),
        )

    def sharing_save_path_for_item(self, item: Any, settings: 'Settings', staging_root: Path) -> tuple[Path, bool]:
        """Return the torrent save path for seed-in-place sharing.

        The download manager must not know how a category nests units inside
        its library root. The base category uses a conservative item folder;
        richer categories can inspect ``item.import_context.unit_descriptor`` or
        legacy row fields to place payloads in a category-appropriate folder.
        Returning ``False`` disables sharing for this item.
        """
        try:
            root = Path(self.get_root_path(settings)).resolve()
        except Exception:
            return staging_root.resolve(), False
        context = getattr(item, "import_context", None)
        title = getattr(context, "planning_title", None) or getattr(item, "item_name", "") or getattr(item, "torrent_title", "") or getattr(item, "id", "")
        safe_title = clean_display_title(str(title or "Untitled"), fallback="Untitled")
        return (root / safe_title).resolve(), True

    # ── File organization ─────────────────────────────────────────

    def organize(self, source: Path, settings: 'Settings', metadata: dict) -> str | None:
        """Move/rename a downloaded file into the library.

        Default implementation computes target path via compute_target_path(),
        creates directories, and moves the file. Subclasses can override for
        completely custom organization logic.
        """
        item_name = metadata.get('item_name', metadata.get('title', 'Unknown'))
        season = metadata.get('season')
        episode = metadata.get('episode')
        if season is None:
            season = 0

        metadata['settings'] = settings
        metadata['library_root'] = self.get_root_path(settings)
        path_metadata = {
            k: v for k, v in metadata.items()
            if k not in {'item_name', 'season', 'episode'}
        }

        target = self.compute_target_path(
            source_name=source.name,
            item_name=item_name,
            season=season,
            episode=episode or 0,
            **path_metadata,
        )
        try:
            resolver = SafePathResolver.for_category(self, settings)
            safe_target = resolver.ensure_destination(target, purpose=f"{self.category_id}.organize", allow_overwrite=False)
            resolver.safe_mkdir(safe_target.parent, purpose=f"{self.category_id}.organize.mkdir")
            logger.info(f'Moving {source} -> {safe_target}')
            resolver.safe_move(source, safe_target, purpose=f"{self.category_id}.organize.move")
            return str(safe_target)
        except SecurityPolicyError as e:
            logger.error(f'Blocked unsafe organize path for {self.category_id}: {e}')
            return None
        except Exception as e:
            logger.error(f'Failed to move file: {e}')
            return None

    # ── Media verification ─────────────────────────────────────────

    async def verify_media(self, file_path: Path) -> bool:
        """Verify a file is valid media using async ffprobe.

        Delegates to MediaVerifier for async subprocess execution.
        """
        return await self._media_verifier.verify(file_path)

    # ── Library consolidation ──────────────────────────────────────

    def consolidate(self, root_path: str, dry_run: bool = True,
                    settings: Optional['Settings'] = None) -> list[dict]:
        """Walk the category's library directory and rename files to match the current template.

        Delegates to LibraryConsolidator for file-walking logic.

        Returns a list of result dicts with old_path, new_path, and status.
        """
        return self._consolidator.consolidate(
            self, root_path, dry_run=dry_run, settings=settings,
        )

    # ── Category-Agnostic Enquiry ───────────────────────────────────

    async def enquire(self, name: str, settings: "Settings", db: "Database") -> dict[str, Any]:
        """Enquire about a media item in this category using cached category metadata.

        Args:
            name: The title/name of the media item.
            settings: The active application settings.
            db: The async Database instance.

        Returns:
            A category-specific payload containing local status and cached metadata.
        """
        return {}

    # ── Language Detection ─────────────────────────────────────────

    async def detect_language(self, name: str, filepath: Optional[Path] = None,
                              default: str = "English") -> str:
        """Detect the likely language from a name and optionally its audio tracks.

        Delegates to LanguageDetector for name-based and audio-based detection.
        """
        return await self._language_detector.detect(name, filepath, default)

    # ── Background Updates ─────────────────────────────────────────

    async def update(self, item: 'CategoryItem', context: 'CategoryUpdateContext') -> None:
        """Periodic background update for this category item.
        
        Subclasses implement category-specific tracking logic (e.g., checking
        for new units, scanning for quality upgrades, syncing metadata).
        """
        pass

    # ── Library scanning ───────────────────────────────────────────

    @abstractmethod
    async def scan(self, root_path: str, existing_keys: set[str] | None = None) -> list[ScannedItem]:
        """Scan a directory and return structured items for this category."""
        ...

    def infer_quality(self, item: ScannedItem, profile: "QualityProfile") -> "QualityProfile":
        """Infer quality preferences from scanned content."""
        from src.core.models import QualityProfile
        return QualityProfile()

    # ── Cleanup ────────────────────────────────────────────────────

    def delete(self, name: str, settings: "Settings", season: int | None = None,
               episode: int | None = None, year: int | None = None) -> bool:
        """Delete a specific item from the library.

        Returns True if deleted, False if not found.
        """
        return False

    # ── Display ────────────────────────────────────────────────────

    def format_progress(self, progress: dict | None) -> str:
        """Format download progress for display in the UI."""
        return "—"

    def get_suggested_actions(self, name: str, settings: "Settings") -> list[dict]:
        """Return suggested actions for this item in the UI.

        Each dict has: action_type, title, description, endpoint, method.
        """
        return []


class CategoryMedia(MediaCategory):
    """Shared base for downloadable media categories.

    Downloadable library categories should inherit
    this class when they share media-library behavior but still declare their
    own metadata providers, UI sections, actions, and LLM profile.
    """

    capabilities = ["metadata", "downloadable", "file_organization"]
    supported_operations = ["search", "download", "scan", "organize", "delete"]
    category_tool_names = [
        "search_media_torrents",
        "queue_download",
        "list_media_items",
        "list_library_files",
    ]

    def llm_profile(self) -> CategoryLlmProfile:
        """Return generic media guidance extended by concrete subclasses."""
        profile = super().llm_profile()
        profile.domain_vocabulary.extend(["download", "quality", "release", "library"])
        profile.identifiers.extend(["quality", "language", "file_path"])
        profile.search_rules.append("Prefer releases that match the category, configured language, and quality profile.")
        profile.download_rules.append("Reject unrelated content types and archive/software/document releases unless the category explicitly allows them.")
        profile.organization_rules.append("Use the category naming template and library root when organizing files.")
        return profile

    def provider_setup_requirements(self, settings: "Settings") -> list[CategorySetupRequirement]:
        """Return setup requirements from declarative service definitions.

        Earlier rounds hardcoded TMDB/Trakt/Plex/OpenSubtitles here, which made
        every subclass of CategoryMedia look audiovisual. New categories such as
        Music, Audiobooks, and Ebooks prove that service requirements must come
        from the effective category YAML instead.
        """
        return super().provider_setup_requirements(settings)
