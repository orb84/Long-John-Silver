"""Configurable general web search and research providers for LJS."""

from src.search.web.base import WebSearchProvider
from src.search.web.research import WebResearchService
from src.search.web.service import WebSearchService

__all__ = ["WebSearchProvider", "WebSearchService", "WebResearchService"]
