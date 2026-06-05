"""LLM-assisted category web research planning.

The generic web-research layer owns budgets, provider execution, fetching, and
provenance.  The category owns domain context.  This planner lets the LLM map a
user's free-form research objective to a bounded category research plan instead
of forcing deterministic code to enumerate every wording variant.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.core.models import (
    CategoryWebResearchInput,
    CategoryWebResearchPlan,
    CategoryWebResearchSearch,
)
from src.utils.json_parser import LLMResponseParser
from src.search.web.research_guidance import WebResearchPromptGuidance


class LLMCategoryWebResearchPlanner:
    """Build a bounded category web-research plan with an LLM.

    The planner is deliberately not an executor and not an authority.  It may
    choose the research objective, source strategy, and search queries from the
    user's wording plus the category contract.  Deterministic code validates the
    output, caps budgets, and then sends the searches through the ordinary
    evidence pipeline.
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        self._llm_client = llm_client

    async def build_plan(
        self,
        *,
        category: Any,
        research_input: CategoryWebResearchInput,
        fallback_plan: CategoryWebResearchPlan | None = None,
    ) -> CategoryWebResearchPlan | None:
        """Return an LLM-authored plan, or ``None`` when unavailable/invalid."""
        if not self._llm_client:
            return None
        try:
            prompt = self._build_prompt(category=category, research_input=research_input, fallback_plan=fallback_plan)
            response = await self._llm_client.completion(
                task="research_web",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=900,
                temperature=0.0,
            )
            raw = LLMResponseParser.safe_extract_content(response)
            payload = LLMResponseParser.extract_json_resilient(raw)
            plan = self._plan_from_payload(payload, research_input=research_input)
            if not plan.searches:
                logger.info("LLMCategoryWebResearchPlanner: planner returned no searches")
                return None
            logger.info(
                "LLMCategoryWebResearchPlanner: plan accepted category={} item={} input_intent={} output_intent={} searches={}",
                research_input.category_id,
                research_input.item_id or research_input.item_name,
                research_input.intent,
                plan.intent,
                len(plan.searches),
            )
            return plan
        except Exception as exc:
            logger.warning("LLMCategoryWebResearchPlanner: failed to build plan: {}", exc)
            return None

    def _build_prompt(
        self,
        *,
        category: Any,
        research_input: CategoryWebResearchInput,
        fallback_plan: CategoryWebResearchPlan | None,
    ) -> str:
        contract = self._category_contract(category)
        fallback = fallback_plan.model_dump(mode="json") if fallback_plan is not None else {}
        context = dict(research_input.context or {})
        user_query = str(context.get("user_query") or context.get("query") or "").strip()
        objective = user_query or research_input.intent or "general research"
        current_date = datetime.now(timezone.utc).date().isoformat()
        category_prompt_guidance = WebResearchPromptGuidance.category_contract_text(contract)
        schema = {
            "intent": "short semantic label for this research objective",
            "max_searches": 1,
            "require_page_extraction_before_facts": True,
            "notes": ["why these searches were chosen"],
            "searches": [
                {
                    "query": "exact public search query",
                    "intent": "semantic purpose of this query",
                    "categories": ["general"],
                    "language": research_input.language or "auto",
                    "time_range": "day|month|year|",
                    "max_results": 8,
                    "max_urls_to_fetch": 4,
                }
            ],
        }
        return (
            "You are the category-aware research planner for Long John Silver. Return ONLY valid JSON.\n"
            "Your job is to transform a free-form user research objective into a small set of high-quality web searches.\n"
            "Do NOT answer the user. Do NOT invent facts. Do NOT decide downloads. Plan source discovery only.\n\n"
            f"{WebResearchPromptGuidance.runtime_context()}\n"
            f"Current UTC date: {current_date}\n"
            f"Category id: {research_input.category_id}\n"
            f"Item title/id: {research_input.item_name or research_input.item_id}\n"
            f"Input intent label from caller: {research_input.intent}\n"
            f"User/search objective: {objective}\n"
            f"Language preference: {research_input.language}\n"
            f"Category contract JSON: {json.dumps(contract, ensure_ascii=False, sort_keys=True)[:4500]}\n"
            f"Category-owned LLM research guidance: {category_prompt_guidance[:3500]}\n"
            f"Fallback deterministic plan JSON, if any: {json.dumps(fallback, ensure_ascii=False, sort_keys=True)[:3500]}\n\n"
            f"{WebResearchPromptGuidance.planner_rules()}\n"
            "- Use max_results 6-12 and max_urls_to_fetch 3-5 per search unless the category contract says otherwise.\n"
            "- Keep every query bound to the item title/id or known aliases from context; avoid ambiguous generic searches.\n"
            f"{WebResearchPromptGuidance.sufficiency_checklist()}\n\n"
            f"Output schema example: {json.dumps(schema, ensure_ascii=False)}\n"
            "JSON:"
        )

    @staticmethod
    def _category_contract(category: Any) -> dict[str, Any]:
        method = getattr(category, "web_research_contract", None)
        if not callable(method):
            return {}
        try:
            value = method()
            return value if isinstance(value, dict) else {}
        except Exception as exc:
            logger.debug("LLMCategoryWebResearchPlanner: category contract unavailable: {}", exc)
            return {}

    def _plan_from_payload(self, payload: dict[str, Any], *, research_input: CategoryWebResearchInput) -> CategoryWebResearchPlan:
        searches = []
        for raw in payload.get("searches") or []:
            if not isinstance(raw, dict):
                continue
            query = str(raw.get("query") or "").strip()
            if not query:
                continue
            searches.append(CategoryWebResearchSearch(
                query=query,
                intent=str(raw.get("intent") or payload.get("intent") or research_input.intent or "general_research"),
                categories=self._safe_categories(raw.get("categories")),
                language=str(raw.get("language") or research_input.language or "auto"),
                time_range=str(raw.get("time_range") or ""),
                max_results=int(raw.get("max_results") or 8),
                max_urls_to_fetch=int(raw.get("max_urls_to_fetch") or 4),
            ))
        max_searches = int(payload.get("max_searches") or min(len(searches) or 1, 4))
        notes = [str(note) for note in (payload.get("notes") or []) if str(note).strip()]
        notes.append("llm_planned_category_web_research")
        return CategoryWebResearchPlan(
            category_id=research_input.category_id,
            item_id=research_input.item_id or research_input.item_name,
            intent=str(payload.get("intent") or research_input.intent or "general_research"),
            searches=searches,
            max_searches=max_searches,
            require_page_extraction_before_facts=bool(payload.get("require_page_extraction_before_facts", True)),
            notes=notes,
        )

    @staticmethod
    def _safe_categories(value: Any) -> list[str]:
        if not isinstance(value, list):
            return ["general"]
        allowed = {"general", "news", "videos", "music", "science", "it", "social media"}
        result = []
        for item in value:
            text = str(item or "").strip().lower()
            if text in allowed:
                result.append(text)
        return result or ["general"]
