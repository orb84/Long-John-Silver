"""
Category registry for LJS.

Manages installed media categories. The registry is the single
source of truth for which media types are available. Adding a
new category is a one-line register() call — no changes needed
anywhere else in the codebase.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Optional
from loguru import logger

from src.core.categories.base import MediaCategory
from src.core.categories.tv import TvShowCategory
from src.core.categories.movie import MovieCategory
from src.core.categories.general import GeneralCategory
from src.core.categories.types import ParsedMedia
from src.core.models import CategoryManifest, CategoryRouterBrief


class CategoryRegistry:
    """Registry of all installed media categories.

    Usage:
        registry = CategoryRegistry()
        registry.register_defaults()
        tv = registry.get("tv")
    """

    def __init__(self):
        self._categories: dict[str, MediaCategory] = {}

    def register(self, category: MediaCategory) -> None:
        """Register a media category instance."""
        self._categories[category.category_id] = category

    def discover_categories(self) -> None:
        """Scan built-in extension and custom directories for category subclasses."""
        categories_dir = Path(__file__).parent.resolve()
        self._discover_from_path(categories_dir, "src.core.categories")
        self._discover_from_path(categories_dir / "custom", "src.core.categories.custom")

    def _discover_from_path(self, directory: Path, package: str) -> None:
        """Import category modules from one package directory safely."""
        if not directory.exists():
            return
        for filepath in directory.glob("*.py"):
            module_name = filepath.stem
            if module_name in (
                "__init__", "base", "registry", "types", "tv", "movie", "language",
                "verifier", "path_planner", "consolidator", "search_patterns", "scaffold",
                "general",
            ):
                continue
            full_module_name = f"{package}.{module_name}"
            try:
                module = importlib.reload(sys.modules[full_module_name]) if full_module_name in sys.modules else importlib.import_module(full_module_name)
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, MediaCategory) and obj is not MediaCategory and obj.__module__ == module.__name__:
                        category_instance = obj()
                        self.register(category_instance)
                        logger.info(f"Dynamically registered category: '{category_instance.category_id}' from {filepath}")
            except Exception as e:
                logger.error(f"Failed to dynamically load category from {filepath}: {e}")

    def register_defaults(self) -> None:
        """Register built-in categories, then dynamically discover extension categories."""
        self.register(TvShowCategory())
        self.register(MovieCategory())
        self.register(GeneralCategory())
        self.discover_categories()

    @classmethod
    def with_defaults(cls) -> "CategoryRegistry":
        """Create a registry populated with the built-in and discovered categories."""
        registry = cls()
        registry.register_defaults()
        return registry

    def get(self, category_id: str) -> Optional[MediaCategory]:
        """Get a category by ID. Returns None if not found."""
        return self._categories.get(category_id)

    def list_all(self) -> list[MediaCategory]:
        """Return all registered categories."""
        return list(self._categories.values())

    def list_ids(self) -> list[str]:
        """Return all registered category IDs."""
        return list(self._categories.keys())

    def parse(self, name: str, category_id: str = "") -> ParsedMedia:
        """Parse a media name using a registered category.

        Args:
            name: Torrent or file name to parse.
            category_id: Optional category ID. If omitted or unknown, all
                registered categories are tried and the best match is returned.

        Returns:
            ParsedMedia extracted by the requested category or best classifier.
        """
        if category_id:
            category = self.get(category_id)
            if category:
                return category.parse_name(name)

        classified = self.classify(name)
        if classified:
            return classified[1]
        return ParsedMedia(original_title=name, title=name)

    def classify(self, name: str) -> tuple[Optional[MediaCategory], ParsedMedia] | None:
        """Try all registered parsers and return the best match.

        Used when the category is truly unknown (e.g. scanning unknown files).
        Returns (category, parsed_media) for the first category whose parser
        finds a season/episode/year match, or None if nothing matches.
        """
        # Parse using all registered categories, return the first match with
        # meaningful metadata (season/episode for TV, year for movies)
        for cat in self._categories.values():
            parsed = cat.parse_name(name)
            if cat.is_episodic and parsed.season is not None:
                return cat, parsed
            if not cat.is_episodic and parsed.year is not None:
                return cat, parsed
        # Fallback: return first match with any title extraction
        for cat in self._categories.values():
            parsed = cat.parse_name(name)
            if parsed.title != name:
                return cat, parsed
        return None


    def manifests(self, settings: object | None = None, include_private_profile: bool = False) -> list[CategoryManifest]:
        """Return manifests for all registered categories."""
        return [
            category.manifest(settings=settings, include_private_profile=include_private_profile)
            for category in self._categories.values()
        ]

    def router_briefs(self) -> list[CategoryRouterBrief]:
        """Return compact router briefs for all registered categories."""
        return [category.router_brief() for category in self._categories.values()]

    def resolve_from_text(self, text: str, tracked_items: object | None = None) -> Optional[MediaCategory]:
        """Resolve the most likely category for a user prompt.

        Uses tracked item keys first, then category vocabulary and router
        brief keywords. This is intentionally deterministic and cheap;
        an LLM category resolver can be added later as a fallback.
        """
        normalized = text.lower()
        if tracked_items:
            for item in tracked_items:
                key = getattr(item, "key", "")
                if key and key.lower() in normalized:
                    category = self.get(getattr(item, "item_type", ""))
                    if category:
                        return category

        scored: list[tuple[int, MediaCategory]] = []
        for category in self._categories.values():
            brief = category.router_brief()
            score = 0
            for token in [category.category_id, category.display_name.lower(), *brief.keywords, *brief.item_types]:
                if token and str(token).lower() in normalized:
                    score += 1
            # Category vocabulary, not registry-owned media assumptions, drives
            # routing boosts.  A custom category can add words like chapters,
            # versions, discs, seasons, or films to its router brief without
            # modifying this registry.
            for token in brief.keywords + brief.item_types:
                if token and str(token).lower() in normalized:
                    score += 1
            if score:
                scored.append((score, category))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def __contains__(self, category_id: str) -> bool:
        return category_id in self._categories

    def __iter__(self):
        return iter(self._categories.values())

    def __len__(self) -> int:
        return len(self._categories)
