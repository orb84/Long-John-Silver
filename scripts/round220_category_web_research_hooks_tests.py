"""Round 220 deterministic tests for category-owned web research hooks."""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.categories.registry import CategoryRegistry
from src.core.models import (
    CategoryResearchFact,
    CategoryWebResearchInput,
    WebEvidence,
    WebEvidenceBundle,
    WebResearchSource,
    WebSearchConfig,
)
from src.search.web.category_research import CategoryWebResearchService


class _FakeRepository:
    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] = []

    async def add_fact_provenance(self, **kwargs: Any) -> int:
        self.facts.append(kwargs)
        return len(self.facts)


class _FakeCollector:
    def __init__(self, config: Any, web_reader: Any = None, repository: Any = None) -> None:
        self.config = config
        self.web_reader = web_reader
        self.repository = repository

    async def collect_evidence(self, request: Any) -> WebEvidenceBundle:
        source = WebResearchSource(
            title="Example Show official episode guide",
            url="https://example.com/example-show/episodes",
            canonical_url="https://example.com/example-show/episodes",
            snippet="Official episode guide with air date January 8, 2026.",
            source_kind="candidate_official_or_primary",
            source_name="fake",
            rank=1,
            query=request.query,
            fetched=True,
            fetch_status="fetched",
            confidence=0.66,
            evidence_id=42,
        )
        return WebEvidenceBundle(
            topic=request.query,
            intent=request.intent,
            ok=True,
            provider="fake",
            query_log_ids=[7],
            sources=[source],
            evidence=[
                WebEvidence(
                    claim="Fetched public source for airdate_corroboration",
                    value="Example Show official episode guide",
                    source_name="fake",
                    url="https://example.com/example-show/episodes",
                    snippet="The official episode guide says S01E02 airs January 8, 2026 after a delay.",
                    confidence=0.66,
                )
            ],
            facts_authoritative=False,
        )


class Round220CategoryWebResearchHookTests:
    """Small deterministic test suite for the Round 220 category hook patch."""

    def run(self) -> None:
        self._test_tv_declares_web_research_contract()
        self._test_tv_plan_is_category_owned()
        self._test_tv_interpreter_returns_non_mutating_signals()
        asyncio.run(self._test_category_orchestrator_persists_interpreted_facts())
        self._test_no_search_aggregator_coupling()
        print("Round 220 category web research hook tests passed")

    def _registry(self) -> CategoryRegistry:
        registry = CategoryRegistry.with_defaults()
        assert registry.get("tv") is not None
        return registry

    def _test_tv_declares_web_research_contract(self) -> None:
        tv = self._registry().get("tv")
        contract = tv.web_research_contract()
        assert contract["enabled"] is True
        assert "airdate_corroboration" in contract["intents"]
        assert "download" not in " ".join(contract["intents"]).lower()

    def _test_tv_plan_is_category_owned(self) -> None:
        tv = self._registry().get("tv")
        plan = tv.build_web_research_plan(CategoryWebResearchInput(
            category_id="tv",
            item_id="Example Show",
            intent="airdate_corroboration",
            context={"season": 1, "episode": 2},
        ))
        expected_category = "tv"
        assert plan.category_id == expected_category
        assert plan.require_page_extraction_before_facts is True
        assert plan.searches
        queries = "\n".join(search.query for search in plan.searches)
        assert "Example Show" in queries
        assert "S01E02" in queries
        assert any("news" in search.categories for search in plan.searches)

    def _test_tv_interpreter_returns_non_mutating_signals(self) -> None:
        tv = self._registry().get("tv")
        source = WebResearchSource(
            title="Official guide",
            url="https://example.com/show/episodes",
            canonical_url="https://example.com/show/episodes",
            snippet="Official air date January 8, 2026",
            source_kind="candidate_official_or_primary",
            fetched=True,
            evidence_id=5,
        )
        bundle = WebEvidenceBundle(
            topic="Example Show S01E02 air date",
            intent="airdate_corroboration",
            ok=True,
            sources=[source],
            evidence=[
                WebEvidence(
                    claim="Fetched public source",
                    value="Official guide",
                    source_name="example",
                    url="https://example.com/show/episodes",
                    snippet="Official air date January 8, 2026; delayed from January 1, 2026.",
                    confidence=0.7,
                )
            ],
        )
        interpretation = asyncio.run(tv.interpret_web_evidence(bundle, CategoryWebResearchInput(
            category_id="tv", item_id="Example Show", item_name="Example Show", intent="airdate_corroboration",
        )))
        assert interpretation.can_mutate_item is False
        fact_types = {fact.fact_type for fact in interpretation.facts}
        assert "official_or_primary_source_candidate" in fact_types
        assert "air_date_reference" in fact_types
        assert "delay_or_cancellation_signal" in fact_types
        assert all(isinstance(fact, CategoryResearchFact) for fact in interpretation.facts)

    async def _test_category_orchestrator_persists_interpreted_facts(self) -> None:
        import src.search.web.category_research as module

        original = module.WebResearchService
        module.WebResearchService = _FakeCollector
        try:
            repository = _FakeRepository()
            result = await CategoryWebResearchService(
                category_registry=self._registry(),
                config=WebSearchConfig(provider="searxng", api_base="http://127.0.0.1:18888"),
                web_reader=object(),
                repository=repository,
            ).research(CategoryWebResearchInput(
                category_id="tv",
                item_id="Example Show",
                item_name="Example Show",
                intent="airdate_corroboration",
                context={"unit_key": "S01E02"},
            ))
        finally:
            module.WebResearchService = original
        assert result.ok is True
        assert result.interpretation.facts
        assert result.persisted_fact_ids
        assert repository.facts
        expected_category = "tv"
        assert all(row["category_id"] == expected_category for row in repository.facts)

    def _test_no_search_aggregator_coupling(self) -> None:
        category_research = Path("src/search/web/category_research.py").read_text()
        assert "SearchAggregator" not in category_research
        assert "search_aggregator" not in category_research
        assert "queue_download" not in category_research
        tv_source = Path("src/core/categories/tv_web_research.py").read_text()
        assert "queue_download" not in tv_source
        assert "add_magnet" not in tv_source
        assert inspect.iscoroutinefunction(self._registry().get("tv").interpret_web_evidence)


if __name__ == "__main__":
    Round220CategoryWebResearchHookTests().run()
