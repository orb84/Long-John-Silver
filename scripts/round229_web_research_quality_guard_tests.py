#!/usr/bin/env python3
"""Round 229 regression checks for managed web research quality guards."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.tools.metadata_lookup_support import LibraryMetadataSnapshotLookup, MetadataLookupRequest
from src.core.categories.tv_web_research import TvWebResearchInterpreter, TvWebResearchMixin
from src.core.domain_models.web_search import CategoryWebResearchInput
from src.search.web.research import WebSourceClassifier


class _Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        if self.failures:
            print("Round 229 web research quality guard failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("Round 229 web research quality guard tests passed.")


def main() -> None:
    check = _Check()
    main_py = (ROOT / "main.py").read_text()
    tools_web = (ROOT / "src/ai/tools/web.py").read_text()
    tv_web = (ROOT / "src/core/categories/tv_web_research.py").read_text()
    prompt = (ROOT / "src/ai/prompt_builder.py").read_text()

    check.ok("_start_managed_searxng_after_ui" in main_py, "main should auto-start managed SearXNG after UI readiness")
    check.ok("searxng_managed_startup" in main_py, "main should schedule managed SearXNG startup")
    check.ok("ManagedSearXNGToolStartup" in tools_web, "web tools should lazy-start managed SearXNG")
    check.ok("searxng_manager=searxng_manager" in main_py, "main should inject SearXNGManager into WebToolProvider")
    check.ok('"query": {"type": "string"' in tools_web, "category_web_research should accept the LLM/user research query")
    check.ok('"user_query"' in tools_web, "category_web_research should preserve the query in category context")

    request = MetadataLookupRequest.from_arguments({"query": "Star City", "media_type": "tv", "question": "When does the next episode air?"})
    check.ok(not isinstance(request, dict), "metadata request should parse")
    check.ok(getattr(request, "include_episodes", False), "next-episode metadata lookup should request episode lists")
    snapshot = {"episodes": {"1": [1, 2]}, "overview": "local files only"}
    check.ok(
        not LibraryMetadataSnapshotLookup.can_answer(snapshot, "When does the next episode of Star City air?"),
        "local downloaded episode dicts must not satisfy air-date metadata questions",
    )

    mixin = TvWebResearchMixin()
    mixin.category_id = "tv"
    plan = mixin.build_web_research_plan(CategoryWebResearchInput(
        category_id="tv",
        item_id="For All Mankind",
        item_name="For All Mankind",
        intent="rumor_check",
        language="en",
        context={"user_query": "For All Mankind season 6 rumors"},
    ))
    queries = "\n".join(search.query for search in plan.searches)
    check.ok("For All Mankind season 6 rumors" in queries, "TV rumor plan should preserve the LLM/user query")
    check.ok("season 6" in queries.casefold(), "TV rumor plan should search next-season-specific terms")
    check.ok(any(search.time_range == "year" for search in plan.searches), "TV rumor plan should use a recency window")
    check.ok(plan.searches[0].max_results >= 8, "TV rumor plan should allow enough result diversity")

    classifier = WebSourceClassifier()
    kind = classifier.classify("https://example.com/star-city-release-schedule", title="Star City release schedule", snippet="weekly schedule")
    check.ok(kind != "candidate_official_or_primary", "generic schedule pages must not be classified as official/primary")

    interpreter = TvWebResearchInterpreter()
    check.ok(
        not interpreter._is_title_bound_tv_source("star city amusement park schedule tickets", "Star City"),
        "TV interpreter should reject title collisions that are not TV sources",
    )
    check.ok(
        interpreter._is_title_bound_tv_source("star city apple tv series episode premiere", "Star City"),
        "TV interpreter should accept title-bound Apple/episode sources",
    )

    check.ok("Search like a researcher" in prompt, "prompt should include web-search best practices")
    check.ok("Never manufacture a weekly schedule" in prompt, "prompt should forbid extrapolated TV schedules")
    check.finish()


if __name__ == "__main__":
    main()
