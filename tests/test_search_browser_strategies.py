"""
Tests for Search1337xBrowserStrategy using a mock browser runtime.

Verifies search URL construction, result row parsing,
detail page magnet extraction, and failure/cooldown paths.
"""

import pytest
from src.core.models import BrowserFetchRequest, BrowserFetchResult, TorrentScrapeCandidate


def _read_fixture(name: str):
    from pathlib import Path
    return (Path(__file__).parent / "fixtures" / "search" / name).read_text(encoding="utf-8")


class MockBrowserRuntime:
    """Simulated BrowserRuntime that returns fixture HTML."""

    def __init__(self):
        self._pages = {}

    def set_fixture(self, url_fragment: str, fixture_name: str):
        self._pages[url_fragment] = _read_fixture(fixture_name)

    async def fetch(self, request: BrowserFetchRequest) -> BrowserFetchResult:
        html = ""
        for frag, fixture_html in self._pages.items():
            if frag in request.url:
                html = fixture_html
                break

        if not html:
            return BrowserFetchResult(
                ok=False,
                url=request.url,
                final_url=request.url,
                status=500,
                error="No fixture configured",
                blocked_reason="unknown",
            )

        return BrowserFetchResult(
            ok=True,
            url=request.url,
            final_url=request.url,
            status=200,
            title="Test Page",
            text="Test content",
            html=html,
            challenge_detected=False,
        )


class Test1337xBrowserStrategy:
    """Tests for the 1337x browser scraping strategy."""

    def test_import_and_create(self):
        from src.search.browser_strategy import Search1337xBrowserStrategy
        mock = MockBrowserRuntime()
        strategy = Search1337xBrowserStrategy(mock)
        assert strategy.provider_name == "1337x"

    def test_search_returns_candidates_with_magnets(self):
        from src.search.browser_strategy import Search1337xBrowserStrategy
        mock = MockBrowserRuntime()
        mock.set_fixture("/search/", "1337x_search.html")
        mock.set_fixture("/torrent/12345/", "1337x_detail.html")
        mock.set_fixture("/torrent/67890/", "1337x_detail.html")
        mock.set_fixture("/torrent/11111/", "1337x_detail.html")

        strategy = Search1337xBrowserStrategy(mock)
        strategy.MAX_DETAIL_FETCHES = 3

        import asyncio
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(strategy.search("Show Name"))
        loop.close()

        assert result.ok
        assert result.provider == "1337x"
        assert len(result.candidates) >= 1

        magnets = [c.magnet for c in result.candidates if c.magnet]
        assert len(magnets) >= 1

    def test_search_handles_blocked_domain(self):
        from src.search.browser_strategy import Search1337xBrowserStrategy

        class AlwaysBlockedRuntime:
            async def fetch(self, request):
                return BrowserFetchResult(
                    ok=False,
                    url=request.url,
                    final_url=request.url,
                    status=0,
                    blocked_reason="cooldown",
                    error=None,
                )

        strategy = Search1337xBrowserStrategy(AlwaysBlockedRuntime())
        import asyncio
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(strategy.search("test"))
        loop.close()
        assert not result.ok
        assert result.blocked_reason == "cooldown"

    def test_make_request_defaults(self):
        from src.search.browser_strategy import Search1337xBrowserStrategy
        request = Search1337xBrowserStrategy._make_request("https://example.com")
        assert request.url == "https://example.com"
        assert request.wait_seconds == 2.0
        assert request.screenshot_on_failure is True
        assert request.purpose == "torrent_search"

    def test_extract_magnet_from_html(self):
        from src.search.browser_strategy import Search1337xBrowserStrategy
        html = _read_fixture("1337x_detail.html")
        magnet = Search1337xBrowserStrategy._extract_magnet_from_html(html)
        assert magnet is not None
        assert magnet.startswith("magnet:")
        assert "abc123def456" in magnet

    def test_extract_magnet_returns_none_on_no_magnet(self):
        from src.search.browser_strategy import Search1337xBrowserStrategy
        magnet = Search1337xBrowserStrategy._extract_magnet_from_html("<html></html>")
        assert magnet is None
