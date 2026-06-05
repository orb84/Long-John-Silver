"""Round 222 checks for SearXNG logging, UI install seams, and fallback tracing.

These tests avoid live internet and do not start SearXNG. They verify that:
- managed install paths expose trace logs without secrets;
- Compass and first-run setup both expose automatic SearXNG installation;
- web-research fallback to DuckDuckGo is explicit/off by default and traced;
- category/general research scenarios flow through web research, not download search.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import WebSearchConfig, WebSearchHealth, WebSearchHit, WebSearchResult
from src.search.web.base import WebSearchProvider
from src.search.web.searxng_manager import SearXNGManager
from src.search.web.service import WebSearchService


class _FailingPrimaryProvider(WebSearchProvider):
    provider_name = "searxng"

    async def search(self, query: str, max_results: int = 5) -> WebSearchResult:
        return WebSearchResult(
            query=query,
            provider=self.provider_name,
            ok=False,
            error="SearXNG is not reachable: connection refused",
            error_code="PROVIDER_UNREACHABLE",
        )

    async def health_check(self) -> WebSearchHealth:
        return WebSearchHealth(provider=self.provider_name, configured=True, ok=False, error_code="PROVIDER_UNREACHABLE")


class Round222LoggingFallbackTests:
    """Small deterministic suite for logging/fallback and scenario traces."""

    def __init__(self) -> None:
        self._failures: list[str] = []

    def run(self) -> None:
        self._test_manager_trace_log_file_and_redaction()
        asyncio.run(self._test_duckduckgo_fallback_is_explicit_and_traced())
        self._test_ui_install_paths_and_separate_fallback_controls()
        self._test_scenario_paths_are_web_research_not_download_acquisition()
        self._test_proactive_tracking_plan_integrates_with_existing_scheduler()
        if self._failures:
            raise AssertionError("\n".join(self._failures))
        print("Round 222 web research logging/fallback tests passed")

    def _test_manager_trace_log_file_and_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manager = SearXNGManager(root / "service", root / "state")
            manager._trace_event("test.event", api_key="secret", message="hello")
            trace_path = manager.logs_dir() / "manager-events.jsonl"
            self._check(trace_path.exists(), "SearXNG manager should write manager-events.jsonl")
            events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self._check(any(event.get("event") == "test.event" for event in events), "trace log should include emitted event")
            test_event = next(event for event in events if event.get("event") == "test.event")
            self._check(test_event.get("api_key") == "<redacted>", "trace log should redact secret-looking fields")
            self._check(test_event.get("message") == "hello", "trace log should keep useful non-secret fields")

    async def _test_duckduckgo_fallback_is_explicit_and_traced(self) -> None:
        import src.search.web.service as service_module

        original_builder = service_module.WebSearchService._build_provider
        original_fallback_search = service_module.DuckDuckGoHtmlSearchProvider.search

        async def fake_fallback_search(self: Any, query: str, max_results: int = 5) -> WebSearchResult:
            return WebSearchResult(
                query=query,
                provider="duckduckgo_html",
                ok=True,
                hits=[WebSearchHit(title="Fallback result", url="https://example.com/news", rank=1, source="DuckDuckGo")],
            )

        try:
            service_module.WebSearchService._build_provider = staticmethod(lambda config, time_range="": _FailingPrimaryProvider())
            service_module.DuckDuckGoHtmlSearchProvider.search = fake_fallback_search

            disabled = await WebSearchService(WebSearchConfig(provider="searxng", api_base="http://127.0.0.1:18888", allow_duckduckgo_fallback=False)).search("show rumor")
            self._check(disabled.provider == "searxng", "fallback disabled should return primary provider result")
            self._check(disabled.ok is False, "fallback disabled should not hide primary provider failure")
            self._check(disabled.fallback_used is False, "fallback disabled should not mark fallback_used")

            enabled = await WebSearchService(WebSearchConfig(provider="searxng", api_base="http://127.0.0.1:18888", allow_duckduckgo_fallback=True)).search("show rumor")
            self._check(enabled.ok is True, "explicit fallback should recover when fallback provider returns hits")
            self._check(enabled.provider == "duckduckgo_html", "fallback result should report DuckDuckGo provider")
            self._check(enabled.fallback_used is True, "fallback result should mark fallback_used")
            self._check(enabled.primary_provider == "searxng", "fallback result should preserve primary provider")
            self._check("connection refused" in enabled.primary_error, "fallback result should preserve primary error")
        finally:
            service_module.WebSearchService._build_provider = original_builder
            service_module.DuckDuckGoHtmlSearchProvider.search = original_fallback_search

    def _test_ui_install_paths_and_separate_fallback_controls(self) -> None:
        settings_js = (ROOT / "src/web/static/js/components/settingsPanel.js").read_text(encoding="utf-8")
        setup_js = (ROOT / "src/web/static/js/pages/setup.js").read_text(encoding="utf-8")
        setup_html = (ROOT / "src/web/templates/setup.html").read_text(encoding="utf-8")
        system_router = (ROOT / "src/web/routers/system.py").read_text(encoding="utf-8")

        self._check("installSearxng()" in settings_js, "Compass should expose installSearxng()")
        self._check("/api/settings/search" in settings_js and "/api/searxng/install" in settings_js, "Compass install should save settings then call managed install endpoint")
        self._check("pref-web-search-duckduckgo-fallback" in settings_js, "Compass should have a dedicated DuckDuckGo fallback control")
        self._check("allow_duckduckgo_fallback: !!(document.getElementById('pref-web-search-duckduckgo-fallback')" in settings_js, "web fallback must not reuse torrent direct-scraper fallback")
        self._check("installSetupSearxng" in setup_js and "/api/searxng/install" in setup_js, "initial setup should expose managed SearXNG install")
        self._check("setup-web-search-managed" in setup_html and "Auto install SearXNG now" in setup_html, "initial setup template should show automatic managed install")
        for path in ("/api/searxng/install", "/api/searxng/health", "/api/searxng/repair"):
            self._check(path in system_router, f"system router should expose {path}")

    def _test_scenario_paths_are_web_research_not_download_acquisition(self) -> None:
        tools = (ROOT / "src/ai/tools/web.py").read_text(encoding="utf-8")
        category_service = (ROOT / "src/search/web/category_research.py").read_text(encoding="utf-8")
        tv_research = (ROOT / "src/core/categories/tv_web_research.py").read_text(encoding="utf-8")
        search_aggregator = (ROOT / "src/search/aggregator.py").read_text(encoding="utf-8")

        self._check("current news, rumors" in tools, "web_research tool should describe rumors/current news use")
        self._check("category_web_research" in tools, "category_web_research tool should be registered for category-owned research")
        self._check("queue_download" not in category_service, "category research orchestrator must not queue downloads")
        self._check("queue_download" not in tv_research and "add_magnet" not in tv_research, "TV web research must not authorize acquisition")
        self._check("SearXNGSearchProvider" not in search_aggregator and "WebResearchService" not in search_aggregator, "torrent aggregation must not depend on web research")
        self._check("delay_news_check" in tv_research and "official_page_discovery" in tv_research, "TV hooks should cover rumors/news and official-source discovery")

    def _test_proactive_tracking_plan_integrates_with_existing_scheduler(self) -> None:
        plan = (ROOT / "docs/WEB_RESEARCH_PROACTIVE_TRACKING_PLAN.md").read_text(encoding="utf-8")
        scheduler = (ROOT / "src/core/prompt_scheduler.py").read_text(encoding="utf-8")
        scheduling_tool = (ROOT / "src/ai/tools/scheduling.py").read_text(encoding="utf-8")
        self._check("PromptScheduler" in plan and "create_scheduled_task" in plan, "plan should reuse existing scheduled assistant task system")
        self._check("condition_check" in plan and "weekly" in plan.lower(), "plan should support recurring weekly information checks")
        self._check("create_task" in scheduler and "condition_check" in scheduler, "existing scheduler should support condition checks")
        self._check("interval_minutes" in scheduling_tool and "max_runs" in scheduling_tool, "existing scheduling tool should expose recurrence controls")
        self._check("not media acquisition" in plan.lower() or "not acquisition" in plan.lower(), "plan should preserve web-research/download boundary")

    def _check(self, condition: Any, message: str) -> None:
        if not condition:
            self._failures.append(message)


if __name__ == "__main__":
    Round222LoggingFallbackTests().run()
