"""
Tests for BrowserSession and BrowserToolProvider.

Verifies session budget enforcement, loop prevention, link navigation,
and evidence collection through the bounded browser session abstraction.
"""

import pytest
from src.core.models import BrowserFetchRequest, BrowserFetchResult, PageLink


class FakeBrowserRuntime:
    """Simulated BrowserRuntime that returns predictable responses."""

    def __init__(self):
        self.fetch_count = 0

    async def fetch(self, request: BrowserFetchRequest) -> BrowserFetchResult:
        self.fetch_count += 1
        return BrowserFetchResult(
            ok=True,
            url=request.url,
            final_url=request.url,
            status=200,
            title=f"Page {self.fetch_count}",
            text=f"Content of {request.url}",
            html=f"<html>{request.url}</html>",
            links=[
                PageLink(text="Link 1", url="https://example.com/1"),
                PageLink(text="Link 2", url="https://example.com/2"),
            ],
            challenge_detected=False,
        )


class TestBrowserSession:
    """Tests for BrowserSession budgeting and state management."""

    def test_creates_and_stores_current_page(self):
        from src.ai.browser_session import BrowserSession
        runtime = FakeBrowserRuntime()
        session = BrowserSession(runtime=runtime, session_id="test-1")

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(session.open("https://example.com"))
        loop.close()

        assert session._page_count == 1
        assert "https://example.com" in session._visited_urls

    def test_prevents_revisiting_same_url(self):
        from src.ai.browser_session import BrowserSession
        runtime = FakeBrowserRuntime()
        session = BrowserSession(runtime=runtime, session_id="test-2")

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(session.open("https://example.com/page"))
        result = loop.run_until_complete(session.open("https://example.com/page"))
        loop.close()

        assert not result.ok
        assert result.blocked_reason == "already_visited"

    def test_exhausts_after_max_pages(self):
        from src.ai.browser_session import BrowserSession
        runtime = FakeBrowserRuntime()
        session = BrowserSession(runtime=runtime, session_id="test-3", max_pages=2)

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(session.open("https://example.com/a"))
        loop.run_until_complete(session.open("https://example.com/b"))
        result = loop.run_until_complete(session.open("https://example.com/c"))
        loop.close()

        assert session.is_exhausted
        assert not result.ok
        assert result.blocked_reason == "session_exhausted"

    def test_get_link_retrieves_by_index(self):
        from src.ai.browser_session import BrowserSession
        runtime = FakeBrowserRuntime()
        session = BrowserSession(runtime=runtime, session_id="test-4")

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(session.open("https://example.com"))
        loop.close()

        link = session.get_link(0)
        assert link is not None
        assert link.url == "https://example.com/1"

        link = session.get_link(99)
        assert link is None

    def test_evidence_collection_and_report(self):
        from src.ai.browser_session import BrowserSession
        runtime = FakeBrowserRuntime()
        session = BrowserSession(runtime=runtime, session_id="test-5")

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(session.open("https://example.com"))
        loop.close()

        session.add_evidence(
            claim="Score is 97%",
            source="Rotten Tomatoes",
            url="https://rottentomatoes.com/xyz",
        )
        report = session.build_report()
        assert len(report["evidence"]) == 1
        assert report["evidence"][0]["claim"] == "Score is 97%"
        assert "https://example.com" in report["visited_urls"]

    def test_remaining_pages_decreases(self):
        from src.ai.browser_session import BrowserSession
        runtime = FakeBrowserRuntime()
        session = BrowserSession(runtime=runtime, session_id="test-6", max_pages=5)
        assert session.remaining_pages == 5

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(session.open("https://example.com/a"))
        loop.close()
        assert session.remaining_pages == 4


class TestBrowserToolProvider:
    """Tests for BrowserToolProvider tool handlers."""

    def test_creates_tool_handlers(self):
        from src.ai.browser_tools import BrowserToolProvider
        from src.ai.browser_session import BrowserSession
        runtime = FakeBrowserRuntime()

        provider = BrowserToolProvider(runtime)
        session = provider.new_session("test-session")
        assert isinstance(session, BrowserSession)
        assert session._session_id == "test-session"

        open_handler = provider.make_browser_open_handler(session)
        assert open_handler is not None

        read_handler = provider.make_browser_read_selected_handler(session)
        assert read_handler is not None

        report_handler = provider.make_browser_evidence_report_handler(session)
        assert report_handler is not None

    def test_browser_open_handler_returns_structured_result(self):
        from src.ai.browser_tools import BrowserToolProvider
        runtime = FakeBrowserRuntime()
        provider = BrowserToolProvider(runtime)
        session = provider.new_session("test-7")
        handler = provider.make_browser_open_handler(session)

        import asyncio
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(handler("https://example.com", purpose="test"))
        loop.close()

        assert result["ok"] is True
        assert result["status"] == 200
        assert len(result["links"]) == 2

    def test_browser_read_selected_navigates_link(self):
        from src.ai.browser_tools import BrowserToolProvider
        runtime = FakeBrowserRuntime()
        provider = BrowserToolProvider(runtime)
        session = provider.new_session("test-8")
        open_handler = provider.make_browser_open_handler(session)
        read_handler = provider.make_browser_read_selected_handler(session)

        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(open_handler("https://example.com"))
        result = loop.run_until_complete(read_handler(0))
        loop.close()

        assert result["ok"] is True
        assert "text_preview" in result
