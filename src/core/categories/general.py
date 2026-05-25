"""General file category for exact user-requested torrent targets.

The General category is deliberately narrow: it gives the agent a safe place to
search for concrete files that do not belong to richer installed categories
without turning the rest of LJS into an untyped torrent grab bag.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.core.categories.base import CategoryMedia
from src.core.categories.identity import clean_display_title, clean_path_fragment, canonical_item_key
from src.core.categories.types import ParsedMedia, ScannedFileObservation, ScannedItem
from src.core.models import CategoryLlmProfile, CategoryPromptExample, CategoryProperty, CategorySetupRequirement

if TYPE_CHECKING:
    from src.core.models import Settings

_SAFE_GENERAL_SUFFIXES = {
    ".3gp", ".7z", ".aac", ".aiff", ".ape", ".ass", ".avi", ".azw3",
    ".cb7", ".cbr", ".cbz", ".csv", ".doc", ".docx", ".epub", ".flac",
    ".gif", ".gz", ".jpeg", ".jpg", ".json", ".m4a", ".m4v", ".mka",
    ".mkv", ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".ods", ".odt",
    ".ogg", ".opus", ".pdf", ".png", ".rar", ".srt", ".ssa", ".sub",
    ".tar", ".txt", ".wav", ".webm", ".webp", ".xls", ".xlsx", ".zip",
}
_DANGEROUS_SUFFIXES = {
    ".apk", ".app", ".bat", ".cmd", ".com", ".deb", ".dmg", ".exe", ".jar",
    ".msi", ".pkg", ".ps1", ".rpm", ".run", ".scr", ".sh", ".vbs",
}
_DANGEROUS_TITLE_RE = re.compile(
    r"\b(?:activator|crack|cracked|keygen|license\s*key|patcher|serial\s*number|warez)\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9]{3,}")
_QUALITY_MARKER_RE = re.compile(
    r"\b(?:2160p|1080p|720p|480p|4k|uhd|hdr|web\s*[- ]?dl|bluray|flac|mp3|pdf|epub|cbz|cbr|zip|rar|7z)\b",
    re.IGNORECASE,
)


class GeneralCategory(CategoryMedia):
    """Exact-file downloads that do not fit richer installed categories.

    This category exists so the assistant can search for a user-named torrent
    target such as a PDF, archive, audio file, dataset, manual, lecture, or
    other miscellaneous payload while still using category-owned paths,
    prompts, manifests, and candidate validation.
    """

    category_id = "general"
    display_name = "General Files"
    default_folder = "General"
    icon = "box-archive"
    media_kind = "general_file"
    capabilities = ["downloadable", "file_organization"]
    metadata_provider_names: list[str] = []
    supported_operations = ["search", "download", "scan", "organize"]
    category_tool_names = ["search_media_torrents", "queue_download", "list_media_items", "list_library_files"]
    prompt_file = "general.md"
    accepted_file_patterns = sorted(f"*{suffix}" for suffix in _SAFE_GENERAL_SUFFIXES)
    _default_naming_template = "{title}/{filename_stem}"

    def llm_profile(self) -> CategoryLlmProfile:
        """Return conservative LLM guidance for miscellaneous exact targets."""
        return CategoryLlmProfile(
            category_id=self.category_id,
            short_description="Exact-file torrent targets that do not belong to a richer installed category.",
            user_facing_description=(
                "General Files is a conservative catch-all for concrete user-named files, documents, "
                "archives, audio, datasets, lectures, manuals, and other payloads that are not TV shows, "
                "movies, or another installed category."
            ),
            router_description=(
                "General Files: use only for exact miscellaneous file/torrent targets when richer "
                "categories such as TV or Movies do not apply."
            ),
            domain_vocabulary=[
                "general file", "misc file", "miscellaneous", "document", "manual", "dataset",
                "archive", "pdf", "epub", "cbz", "cbr", "audio file", "flac", "mp3", "zip", "rar",
                "7z", "iso image", "exact torrent", "literal filename",
            ],
            item_types=["file", "document", "archive", "audio", "dataset", "manual", "lecture", "general_download"],
            identifiers=["exact_title", "literal_filename", "format", "extension", "library_path"],
            common_user_requests=[
                "Search for a torrent by exact filename or exact title.",
                "Download a PDF, archive, dataset, audio release, manual, or lecture that is not a movie/TV item.",
                "Store a one-off payload in the General library folder.",
            ],
            ambiguity_rules=[
                "Richer installed categories win. Do not use General for obvious movies, TV seasons/episodes, or any future category that clearly matches the request.",
                "Use General only when the user gives a concrete target name, filename, or format/extension. Ask one clarification when the target is vague.",
                "If the request could be a movie/TV item and a general file, prefer the richer category unless the user explicitly says file, PDF, archive, audio, dataset, manual, or General.",
                "Do not silently reinterpret a failed TV/movie search as General. The user must ask for a miscellaneous exact target or approve the category switch.",
            ],
            search_rules=[
                "Search literal terms. Preserve quoted names, version numbers, extensions, edition tags, and user-specified format words.",
                "Do not append the global media language automatically. If language matters, include it in the literal query name or constraints because the user said it.",
                "Prefer candidates whose title visibly contains the requested terms and file format. Weak fuzzy matches should be shown for confirmation, not queued.",
            ],
            download_rules=[
                "Queue only candidates that clearly match the exact user target and have a magnet link.",
                "Reject software installers, cracks, keygens, activators, scripts, app bundles, APKs, and executable payloads unless a future dedicated category with explicit policy owns that domain.",
                "For archives or bundles, inspect the file list when available before queueing if the useful payload is ambiguous.",
                "If several candidates look plausible, present candidate_id, title, size, and seeders and ask the user to choose.",
            ],
            organization_rules=[
                "Store completed payloads below the General library folder in a safe title folder, preserving the original payload filename.",
                "Do not rename miscellaneous payloads into TV/movie naming templates.",
            ],
            safety_rules=[
                "Never use General as a bypass around category validation or safety checks.",
                "Do not help obtain malware, cracked software, keygens, credential dumps, or other harmful payloads.",
            ],
            tool_usage_notes=[
                "When using search_media_torrents for this category, pass category_id='general'.",
                "Use the candidate workspace and result_set_id/candidate_id handles; do not invent file paths or JSON placeholders.",
            ],
            examples=[
                CategoryPromptExample(
                    user="Find the torrent for the Ubuntu 24.04 desktop ISO",
                    expected_intent="download",
                    expected_behavior=(
                        "Treat the query as an exact miscellaneous file target, search under category_id='general', "
                        "show matching ISO/archive candidates by stable candidate_id, and ask before queueing if ambiguous."
                    ),
                    tool_plan=["search_media_torrents", "queue_download"],
                ),
                CategoryPromptExample(
                    user="Download the PDF manual named ACME Router X1000 service manual",
                    expected_intent="download",
                    expected_behavior=(
                        "Use General only because the user explicitly requested a PDF/manual; preserve exact words, "
                        "reject unrelated software or video results, and queue only a clear PDF/manual match."
                    ),
                    tool_plan=["search_media_torrents", "queue_download"],
                ),
            ],
        )

    def get_properties(self, settings: "Settings") -> list[CategoryProperty]:
        """Return the minimal setup surface for General Files."""
        cat_configs = settings.category_settings.get(self.category_id, {}) if settings is not None else {}
        prop = CategoryProperty(
            name="library_path",
            value_type="string",
            description="Absolute path where one-off General Files downloads are stored.",
            default_value="",
        )
        prop.value = cat_configs.get(prop.name, prop.default_value)
        return [prop]

    def get_naming_template(self, settings: "Settings" = None) -> str:
        """Return a fixed conservative path template without exposing extra setup fields."""
        return self._default_naming_template

    def setup_requirements(self, settings: "Settings") -> list[CategorySetupRequirement]:
        """Return General setup requirements with category-specific wording."""
        requirements = super().setup_requirements(settings)
        for requirement in requirements:
            if requirement.id == "library_path":
                requirement.description = (
                    "Where completed one-off files, documents, archives, audio, datasets, manuals, "
                    "and other General Files payloads are stored."
                )
                requirement.why_it_matters = (
                    "General downloads do not inherit TV or movie folder rules, so they need their own safe root."
                )
        return requirements

    def parse_name(self, name: str) -> ParsedMedia:
        """Parse a miscellaneous release title without adding domain semantics."""
        original = str(name or "")
        stem = Path(original).stem if Path(original).suffix else original
        title = clean_display_title(stem.replace(".", " ").replace("_", " "), fallback=original or "Untitled")
        return ParsedMedia(
            original_title=original,
            title=title,
            resolution=self._extract_resolution(original),
            codec=self._extract_codec(original),
            language=self._extract_language(original),
        )

    def build_search_query(self, item: Any, unit_label: str | None, language: str | None) -> str:
        """Return a literal exact-target query; do not append global media language."""
        name = str(getattr(item, "key", "") or "").strip()
        if unit_label:
            name = f"{name} {unit_label}".strip()
        return name

    def validate_search_result_for_request(self, result: Any, item: Any, unit_label: str | None) -> bool:
        """Reject dangerous or obviously unrelated General candidates."""
        title = str(getattr(result, "title", "") or "")
        if not title.strip() or self._looks_dangerous(title):
            return False
        requested = f"{getattr(item, 'key', '')} {unit_label or ''}".strip()
        return self._has_term_overlap(requested, title)

    def quality_reference_for_search(self, item: Any, unit_label: str | None, context: Any | None = None) -> str:
        """Return concise ranking guidance for General candidate selection."""
        return (
            "General Files search is exact-target-first. Prefer visible title/filename and format matches, "
            "healthy seeders, and plausible size. Reject executable/software/crack/keygen payloads."
        )

    def unit_descriptor_from_search_result(self, result: Any, item: Any, unit_label: str | None) -> dict[str, Any]:
        """Describe the queued payload as one miscellaneous item."""
        title = str(getattr(item, "key", "") or getattr(result, "title", "") or "General file")
        stable = canonical_item_key(title) or "general-file"
        return {
            "granularity": "item",
            "label": clean_display_title(title, fallback="General file"),
            "stable_key": stable,
            "sort_key": [stable],
            "coordinates": {"title": title},
        }

    def torrent_bundle_candidate_context(self, result: Any, item: Any | None = None, unit_label: str | None = None) -> dict[str, Any] | None:
        """Mark likely archives/bundles so the agent can inspect them before queueing."""
        title = str(getattr(result, "title", "") or "")
        lower = title.lower()
        if any(marker in lower for marker in ("pack", "bundle", "collection")) or any(f"{ext}" in lower for ext in (".zip", ".rar", ".7z", ".tar")):
            return {"scope": "general_bundle", "pack_type": "archive_or_collection", "requires_file_list_check": True}
        return None

    def unit_descriptor_from_file(self, file_path: str, parsed: Any | None = None, item_descriptor: dict[str, Any] | None = None) -> dict[str, Any]:
        """Use torrent-relative filenames as file-level descriptors for selective downloads."""
        label = Path(str(file_path or "")).name or "file"
        return {
            "granularity": "file",
            "label": label,
            "stable_key": label,
            "sort_key": [label.lower()],
            "coordinates": {"filename": label},
        }

    def torrent_file_priority(
        self,
        *,
        file_path: str,
        parsed: Any | None,
        file_descriptor: dict[str, Any],
        selected: bool,
    ) -> int:
        """Prioritize safe selected payload files and ignore dangerous/sample files."""
        suffix = Path(str(file_path or "")).suffix.lower()
        lower = str(file_path or "").lower()
        if not selected or "sample" in lower or suffix in _DANGEROUS_SUFFIXES:
            return 0
        return 4 if not suffix or suffix in _SAFE_GENERAL_SUFFIXES else 1

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
        """Return the ready-time import path for a General download."""
        data = dict(metadata or {})
        title = data.get("title") or getattr(item, "item_name", "") or getattr(item, "torrent_title", "") or source.stem
        folder = clean_path_fragment(title, fallback="General File")
        filename = Path(source_name or source.name).name
        return Path(self.get_root_path(settings)) / folder / filename

    def sharing_save_path_for_item(self, item: Any, settings: "Settings", staging_root: Path) -> tuple[Path, bool]:
        """Seed-in-place General payloads directly under their safe item folder."""
        try:
            root = Path(self.get_root_path(settings)).resolve()
        except Exception:
            return staging_root.resolve(), False
        context = getattr(item, "import_context", None)
        title = getattr(context, "planning_title", None) or getattr(item, "item_name", "") or getattr(item, "torrent_title", "") or "General File"
        return (root / clean_path_fragment(title, fallback="General File")).resolve(), True

    async def scan(self, root_path: str, existing_keys: set[str] | None = None) -> list[ScannedItem]:
        """Scan the General library root as top-level folders or flat files."""
        root = Path(root_path)
        try:
            summaries = await asyncio.to_thread(self._collect_entries, root)
        except OSError as exc:
            logger.error(f"[GeneralCategory] Failed to access root path '{root_path}': {exc}")
            return []
        return [self._summary_to_scanned(summary) for summary in summaries if summary.get("files")]

    def _collect_entries(self, root: Path) -> list[dict[str, Any]]:
        """Collect top-level General file entries using blocking filesystem calls."""
        if not root.is_dir():
            return []
        entries: list[dict[str, Any]] = []
        for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_file() and self._is_safe_library_file(child):
                    entries.append({"name": child.stem, "files": [child]})
                elif child.is_dir():
                    files = [p for p in child.rglob("*") if p.is_file() and self._is_safe_library_file(p)]
                    if files:
                        entries.append({"name": child.name, "files": sorted(files, key=lambda path: str(path).lower())})
            except OSError as exc:
                logger.warning(f"[GeneralCategory] Skipping unreadable entry '{child}': {exc}")
        return entries

    def _summary_to_scanned(self, summary: dict[str, Any]) -> ScannedItem:
        """Convert collected filesystem facts into a neutral scanned item."""
        observations: list[ScannedFileObservation] = []
        total = 0
        resolutions: set[str] = set()
        codecs: set[str] = set()
        languages: set[str] = set()
        for file_path in summary.get("files") or []:
            size = int(file_path.stat().st_size)
            total += size
            parsed = self.parse_name(file_path.name)
            if parsed.resolution:
                resolutions.add(parsed.resolution)
            if parsed.codec:
                codecs.add(parsed.codec)
            if parsed.language:
                languages.add(parsed.language)
            observations.append(ScannedFileObservation(
                file_path=str(file_path),
                quality=parsed.resolution or file_path.suffix.lower().lstrip(".") or "unknown",
                size_bytes=size,
                detected_language=parsed.language or "",
            ))
        return ScannedItem(
            name=clean_display_title(summary.get("name"), fallback="General File"),
            category_id=self.category_id,
            resolutions=sorted(resolutions),
            codecs=sorted(codecs),
            file_count=len(observations),
            total_size_bytes=total,
            detailed_episodes=observations,
            detected_language=", ".join(sorted(languages)) if languages else "",
            detected_languages=sorted(languages),
        )

    @staticmethod
    def _is_safe_library_file(path: Path) -> bool:
        """Return true for files the General category should catalogue."""
        suffix = path.suffix.lower()
        if suffix in _DANGEROUS_SUFFIXES:
            return False
        return not suffix or suffix in _SAFE_GENERAL_SUFFIXES

    @classmethod
    def _looks_dangerous(cls, title: str) -> bool:
        """Return true when a result title advertises executable/crack payloads."""
        lower = title.lower()
        if _DANGEROUS_TITLE_RE.search(title):
            return True
        return any(f"{suffix}" in lower for suffix in _DANGEROUS_SUFFIXES)

    @staticmethod
    def _has_term_overlap(requested: str, candidate_title: str) -> bool:
        """Return true when candidate title contains meaningful request terms."""
        request_tokens = {token.lower() for token in _TOKEN_RE.findall(requested or "")}
        if not request_tokens:
            return False
        candidate = candidate_title.lower()
        matched = {token for token in request_tokens if token in candidate}
        if len(request_tokens) <= 2:
            return bool(matched)
        format_tokens = {token.lower() for token in _QUALITY_MARKER_RE.findall(requested or "")}
        required = 1 if format_tokens else 2
        return len(matched) >= min(required, len(request_tokens))
