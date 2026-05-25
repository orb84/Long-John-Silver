"""Tests for configurable general web-search providers."""

import pytest

from src.core.models import WebSearchConfig
from src.search.web.duckduckgo_html import DuckDuckGoHtmlSearchProvider
from src.search.web.service import WebSearchService


@pytest.mark.asyncio
async def test_brave_without_key_fails_without_duckduckgo_fallback() -> None:
    """A missing primary API key reports degraded search instead of scraping silently."""
    service = WebSearchService(WebSearchConfig(provider="brave", api_key="", allow_duckduckgo_fallback=False))

    result = await service.search("ljs test query", max_results=3)

    assert result.ok is False
    assert result.provider == "brave"
    assert "API key" in (result.error or "")


@pytest.mark.asyncio
async def test_disabled_web_search_reports_health() -> None:
    """Disabled web search produces an explicit health response."""
    service = WebSearchService(WebSearchConfig(enabled=False))

    health = await service.health_check()

    assert health.provider == "disabled"
    assert health.configured is False
    assert health.ok is False


def test_duckduckgo_html_parser_normalizes_hits() -> None:
    """The last-resort parser converts result cards into shared models."""
    html = """
    <div class="result">
      <a class="result__a" href="https://example.test/page">Example Title</a>
      <a class="result__snippet">Example snippet text.</a>
    </div>
    """

    hits = DuckDuckGoHtmlSearchProvider._parse_html(html, max_results=5)

    assert len(hits) == 1
    assert hits[0].title == "Example Title"
    assert hits[0].url == "https://example.test/page"
    assert hits[0].source == "DuckDuckGo"


def test_duckduckgo_html_parser_decodes_redirect_urls() -> None:
    """DuckDuckGo redirect/protocol-relative URLs are normalized to targets."""
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.giocodelpontedipisa.it%2F&amp;rut=abc">Gioco del Ponte</a>
      <a class="result__snippet">Official event site.</a>
    </div>
    <div class="result">
      <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.org%2Fpage">Relative redirect</a>
    </div>
    """

    hits = DuckDuckGoHtmlSearchProvider._parse_html(html, max_results=5)

    assert [hit.url for hit in hits] == [
        "https://www.giocodelpontedipisa.it/",
        "https://example.org/page",
    ]
