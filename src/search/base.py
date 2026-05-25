"""
Base class for search providers in LJS.

All search providers inherit from SearchProvider and implement the search() method.
Browser fallback is handled through injected TorrentBrowserStrategy instances.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional
from src.core.models import SearchResult

if TYPE_CHECKING:
    from src.utils.browser import Browser
    from src.search.browser_strategy import TorrentBrowserStrategy


class SearchProvider(ABC):
    """Abstract base class for torrent search providers.

    Providers that scrape websites may receive a Browser instance via
    set_browser() for health checks. Browser-based scraping is delegated
    to injected TorrentBrowserStrategy instances via set_browser_strategy().
    """

    def __init__(self):
        self._browser: "Browser | None" = None
        self._browser_strategy: Optional["object"] = None
        self._error_category: str | None = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the stable provider name used in diagnostics and UI labels.

        Keep this value short and unchanged unless migrating cached provider
        metadata and user-facing configuration at the same time.
        """
        ...

    @abstractmethod
    async def search(self, query: str) -> list[SearchResult]:
        """Search using the SearchProvider provider contract.

        Normalize inputs before calling external providers and return stable
        model objects.  Add new provider-specific behavior behind adapters,
        not in callers.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Run the public lifecycle operation health_check.

        This method is part of runtime orchestration.  Keep it idempotent
        where practical, clean up owned resources, and delegate specialized
        work to collaborators to preserve testability.
        """
        ...

    def set_browser(self, browser: "Browser") -> None:
        """Set the browser dependency or configuration.

        Use this mutator for explicit dependency injection only.  Preserve
        idempotency so tests and runtime setup can call it safely.
        """
        self._browser = browser

    def set_browser_strategy(self, strategy: "TorrentBrowserStrategy | None") -> None:
        """Set the browser strategy dependency or configuration.

        Use this mutator for explicit dependency injection only.  Preserve
        idempotency so tests and runtime setup can call it safely.
        """
        self._browser_strategy = strategy

    def record_error_category(self, category: str) -> None:
        """Execute the public SearchProvider.record_error_category behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        self._error_category = category

    @property
    def latest_error_category(self) -> str | None:
        """Execute the public SearchProvider.latest_error_category behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return self._error_category
