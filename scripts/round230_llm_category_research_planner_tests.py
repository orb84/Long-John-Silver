#!/usr/bin/env python3
"""Round 230 regression checks for LLM-planned category web research."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.public_web_requirements import PublicWebEvidencePolicy
from src.core.categories.tv_web_research import TvWebResearchMixin
from src.core.domain_models.web_search import CategoryWebResearchInput, CategoryWebResearchPlan, CategoryWebResearchSearch
from src.search.web.llm_category_research import LLMCategoryWebResearchPlanner


class _Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        if self.failures:
            print("Round 230 LLM category research planner failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("Round 230 LLM category research planner tests passed.")


class _FakeLLM:
    def __init__(self) -> None:
        self.prompt = ""

    async def completion(self, *, task: str, messages: list[dict], **_: object) -> str:
        self.prompt = str(messages[-1]["content"])
        assert task == "research_web"
        return """
        {
          "intent": "news_and_rumor_watch",
          "max_searches": 3,
          "require_page_extraction_before_facts": true,
          "notes": ["semantic mapping from free-form rumor_check"],
          "searches": [
            {
              "query": "For All Mankind season 6 confirmed filming showrunner interview Apple TV 2026",
              "intent": "confirmed production reporting",
              "categories": ["news"],
              "language": "en",
              "time_range": "year",
              "max_results": 10,
              "max_urls_to_fetch": 5
            },
            {
              "query": "For All Mankind season 6 official renewal production press Apple TV 2026",
              "intent": "official or primary confirmation",
              "categories": ["general"],
              "language": "en",
              "time_range": "year",
              "max_results": 8,
              "max_urls_to_fetch": 4
            }
          ]
        }
        """


class _Category:
    category_id = "tv"

    def web_research_contract(self) -> dict:
        return {
            "enabled": True,
            "intents": ["news_and_rumor_watch", "airdate_corroboration"],
            "notes": ["Treat input intent labels semantically, not as enum values."],
        }


async def _run_llm_planner_check(check: _Check) -> None:
    llm = _FakeLLM()
    planner = LLMCategoryWebResearchPlanner(llm)
    research_input = CategoryWebResearchInput(
        category_id="tv",
        item_id="For All Mankind",
        item_name="For All Mankind",
        intent="rumor_check",
        language="en",
        context={"user_query": "For All Mankind season 6 rumors"},
    )
    fallback = CategoryWebResearchPlan(
        category_id="tv",
        item_id="For All Mankind",
        intent="airdate_corroboration",
        searches=[CategoryWebResearchSearch(query="For All Mankind air date official episode guide")],
    )
    plan = await planner.build_plan(category=_Category(), research_input=research_input, fallback_plan=fallback)
    check.ok(plan is not None, "LLM planner should return a validated plan")
    assert plan is not None
    joined_queries = "\n".join(search.query for search in plan.searches)
    check.ok("season 6 confirmed filming" in joined_queries, "LLM planner should preserve semantic current-news focus")
    check.ok(any(search.categories == ["news"] for search in plan.searches), "LLM planner should allow category/news source strategy")
    check.ok(any(search.time_range == "year" for search in plan.searches), "LLM planner should preserve recency windows")
    check.ok("Treat caller intent labels as hints" in llm.prompt, "planner prompt should tell LLM not to depend on exact enum names")
    check.ok("rumor_check" in llm.prompt, "planner prompt should pass through arbitrary caller labels for semantic mapping")


def main() -> None:
    check = _Check()
    asyncio.run(_run_llm_planner_check(check))

    tv_web = (ROOT / "src/core/categories/tv_web_research.py").read_text()
    tools_web = (ROOT / "src/ai/tools/web.py").read_text()
    category_research = (ROOT / "src/search/web/category_research.py").read_text()
    main_py = (ROOT / "main.py").read_text()

    check.ok('"rumor_check"' not in tv_web and '"rumour_check"' not in tv_web, "TV contract should not enumerate every synonym for rumor intent")
    check.ok("LLMCategoryWebResearchPlanner" in category_research, "category research service should use the LLM category planner")
    check.ok("plan_origin = \"llm\"" in category_research, "category research service should log LLM-vs-fallback origin")
    check.ok("llm_client=self._llm_client" in tools_web, "category/watch tools should pass the LLM client into research services")
    check.ok("llm_client=task_llm_client" in main_py, "main should inject task LLM into WebToolProvider")
    check.ok(
        PublicWebEvidencePolicy.category_research_intent("rumor", "leak") == "llm_planned_public_research",
        "source-sufficiency helper must not map free-form public questions to category enum labels",
    )
    check.ok("source_quality" in tools_web and "answer_policy" in tools_web, "category web research tool should expose source-quality answer policy")
    check.finish()


if __name__ == "__main__":
    main()
