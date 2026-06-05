"""Round 219 deterministic checks for web-research evidence boundaries.

These tests avoid live network and database dependencies.  They verify the
static contracts that keep SearXNG/web search in source-discovery/provenance
space instead of acquisition/download or category-fact space.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tool_catalog import AgentToolCatalog
from src.ai.tool_policy import AgentToolPolicy
from src.ai.tools.web import WebToolProvider
from src.core.models import Intent, WebResearchBudget, WebResearchRequest
from src.search.web.research import WebResearchUrlCanonicalizer, WebSourceClassifier


def test_web_research_models_are_bounded_and_non_authoritative() -> None:
    request = WebResearchRequest(
        query="For All Mankind season 5 release date",
        categories=[],
        time_range="century",
        max_results=100,
        budget=WebResearchBudget(max_searches=100, max_pages_per_search=99, max_urls_to_fetch=999),
    )
    assert request.categories == ["general"]
    assert request.time_range == ""
    assert request.max_results == 20
    assert request.budget.max_searches == 5
    assert request.budget.max_pages_per_search == 3
    assert request.budget.max_urls_to_fetch == 20
    assert request.budget.require_page_extraction_before_facts is True


def test_url_canonicalization_removes_tracking_and_fragments() -> None:
    canonicalizer = WebResearchUrlCanonicalizer()
    canonical = canonicalizer.canonicalize(
        "https://Example.com/show/?utm_source=x&keep=1&fbclid=abc#section"
    )
    assert canonical == "https://example.com/show?keep=1"


def test_source_classifier_is_generic_not_category_specific() -> None:
    classifier = WebSourceClassifier()
    assert classifier.classify("https://www.reuters.com/world/example") == "news"
    assert classifier.classify("https://en.wikipedia.org/wiki/Example") == "reference"
    assert classifier.classify("https://www.reddit.com/r/example/comments/1") == "social"
    assert classifier.classify("https://example.invalid/page") == "unknown"


def test_web_research_tool_is_registered_for_search_policy() -> None:
    registry = AgentToolCatalog([WebToolProvider()]).build_registry()
    names = set(registry.get_tool_names())
    assert "web_research" in names
    allowed = AgentToolPolicy().allowed_tool_names(Intent.SEARCH, category=None, confirmed=True)
    assert "web_research" in allowed


def test_schema_and_docs_preserve_boundaries() -> None:
    migration = (ROOT / "migrations/110_web_research_evidence.sql").read_text()
    assert "web_research_query_log" in migration
    assert "web_source_evidence" in migration
    assert "category_fact_provenance" in migration
    architecture = (ROOT / "architecture.md").read_text()
    assert "facts_authoritative=false" in architecture
    assert "web-research results are not queueable candidates" in architecture
    aggregator = (ROOT / "src/search/aggregator.py").read_text()
    assert "SearXNG" not in aggregator
    assert "WebResearchService" not in aggregator


def main() -> None:
    test_web_research_models_are_bounded_and_non_authoritative()
    test_url_canonicalization_removes_tracking_and_fragments()
    test_source_classifier_is_generic_not_category_specific()
    test_web_research_tool_is_registered_for_search_policy()
    test_schema_and_docs_preserve_boundaries()
    print("Round 219 web-research evidence tests passed")


if __name__ == "__main__":
    main()
