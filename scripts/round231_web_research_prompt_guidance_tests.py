#!/usr/bin/env python3
"""Round 231 checks for LLM web-research prompt/guidance coverage."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.prompt_builder import PromptBuilder
from src.ai.reasoning import ReasoningPlanner
from src.core.categories.tv_web_research import TvWebResearchMixin
from src.core.domain_models.web_search import CategoryWebResearchInput, CategoryWebResearchPlan, CategoryWebResearchSearch, WebResearchRequest, WebSearchHit
from src.core.models import Intent
from src.search.web.llm_category_research import LLMCategoryWebResearchPlanner
from src.search.web.research import WebResearchService
from src.search.web.research_guidance import WebResearchPromptGuidance


class _Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        if self.failures:
            print("Round 231 web research prompt guidance failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("Round 231 web research prompt guidance tests passed.")


class _CaptureLLM:
    def __init__(self) -> None:
        self.prompt = ""

    async def completion(self, *, task: str, messages: list[dict], **_: object) -> str:
        self.prompt = str(messages[-1]["content"])
        return """
        {
          "intent": "current production research",
          "max_searches": 2,
          "require_page_extraction_before_facts": true,
          "notes": ["uses freshness and source diversity"],
          "searches": [
            {"query":"For All Mankind season 6 production filming interview Apple TV 2026","intent":"production reporting","categories":["news"],"time_range":"year","max_results":10,"max_urls_to_fetch":4},
            {"query":"site:tv.apple.com For All Mankind episodes season 6","intent":"official source check","categories":["general"],"time_range":"","max_results":8,"max_urls_to_fetch":3}
          ]
        }
        """


async def _check_llm_planner_prompt(check: _Check) -> None:
    llm = _CaptureLLM()
    planner = LLMCategoryWebResearchPlanner(llm)
    mixin = TvWebResearchMixin()
    mixin.category_id = "tv"
    await planner.build_plan(
        category=mixin,
        research_input=CategoryWebResearchInput(
            category_id="tv",
            item_id="For All Mankind",
            item_name="For All Mankind",
            intent="whatever_the_llm_called_it",
            language="en",
            context={"user_query": "For All Mankind season 6 creators interview rumours"},
        ),
        fallback_plan=CategoryWebResearchPlan(
            category_id="tv",
            item_id="For All Mankind",
            searches=[CategoryWebResearchSearch(query="For All Mankind fallback")],
        ),
    )
    prompt = llm.prompt
    check.ok("CURRENT RUNTIME DATETIME" in prompt and "CURRENT YEAR" in prompt, "LLM category planner must receive current runtime/date context")
    check.ok("Treat caller intent labels as hints" in prompt, "LLM category planner must not depend on exact intent enums")
    check.ok("Use categories ['news']" in prompt, "LLM category planner must be told to use provider categories for current reporting")
    check.ok("time_range='day', 'month', or 'year'" in prompt, "LLM category planner must be told to use freshness controls")
    check.ok("Source Quality Policy" in prompt or "Strong TV sources" in prompt, "category-owned source-quality guidance must be injected into planner prompt")
    check.ok("For All Mankind season 6 creators interview rumours" in prompt, "planner prompt must preserve the user's exact search focus")


def main() -> None:
    check = _Check()

    generic = WebResearchPromptGuidance.general_rules()
    check.ok("Search like a researcher" in generic, "generic guidance should teach search strategy, not just list tools")
    check.ok("time_range" in generic and "categories" in generic, "generic guidance should explain provider freshness/category controls")
    check.ok("site:official-domain" in generic or "site:" in generic, "generic guidance should explain useful search operators")
    check.ok("snippets are leads" in generic, "generic guidance should demote search snippets")
    check.ok("Negative claims" in generic, "generic guidance should restrict no-news/no-official-word claims")

    prompt = PromptBuilder().build_system_prompt(Intent.SEARCH, active_category_id="tv")
    check.ok("CURRENT RUNTIME DATETIME" in prompt, "main agent prompt should include current runtime datetime")
    check.ok("PUBLIC WEB RESEARCH GUIDANCE" in prompt, "main SEARCH prompt should include general public-web guidance")
    check.ok("Evidence sufficiency checklist" in prompt, "main SEARCH prompt should include evidence sufficiency checklist")
    check.ok("TV-SPECIFIC RESEARCH RULES" not in prompt, "generic PromptBuilder must not hard-code TV-specific research rules")

    tv_prompt = (ROOT / "src/core/categories/prompts/tv.md").read_text()
    check.ok("category_web_research" in tv_prompt and "preserve the user's exact focus" in tv_prompt, "TV category prompt should provide category-specific web research guidance")
    check.ok("Deadline" in tv_prompt and "Variety" in tv_prompt, "TV category prompt should mention strong trade-source strategy")

    reasoning = (ROOT / "src/ai/reasoning.py").read_text()
    check.ok("metadata alone is insufficient" in reasoning, "advisory planner should know metadata-only plans are insufficient for current public questions")
    check.ok("WebResearchPromptGuidance.planner_rules" in reasoning, "advisory planner should reuse central web research planner rules")

    web_tools = (ROOT / "src/ai/tools/web.py").read_text()
    check.ok('"time_range"' in web_tools and '"categories"' in web_tools, "web_search tool should expose freshness/category controls to the LLM")
    check.ok("Free-form semantic research objective" in web_tools, "category_web_research intent should be described as semantic, not enum-bound")

    tv_contract = TvWebResearchMixin().web_research_contract()
    check.ok("llm_research_guidance" in tv_contract, "TV web contract should expose LLM research guidance")
    check.ok("source_quality_policy" in tv_contract, "TV web contract should expose source quality policy")
    check.ok("freshness_policy" in tv_contract, "TV web contract should expose freshness policy")

    hits = [
        WebSearchHit(title="Fan thread", url="https://reddit.com/r/example", snippet="rumors", source="test", rank=1),
        WebSearchHit(title="Official episodes", url="https://tv.apple.com/show/example", snippet="episodes", source="test", rank=5),
        WebSearchHit(title="Trade report", url="https://deadline.com/example", snippet="production interview", source="test", rank=3, published_at="2026-06-01"),
    ]
    ranked = WebResearchService()._ranked_hits(hits, WebResearchRequest(query="Example current news", categories=["news"], time_range="year"))
    check.ok("apple" in ranked[0].url or "deadline" in ranked[0].url, "fetch ordering should prefer official/trade evidence over social rank for current topics")
    check.ok("reddit" not in ranked[0].url, "social chatter must not consume first fetch budget when stronger sources exist")

    asyncio.run(_check_llm_planner_prompt(check))
    check.finish()


if __name__ == "__main__":
    main()
