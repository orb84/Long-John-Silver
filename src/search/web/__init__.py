"""Configurable general web search providers for LJS."""

from src.search.web.base import WebSearchProvider
from src.search.web.service import WebSearchService

__all__ = ["WebSearchProvider", "WebSearchService"]
