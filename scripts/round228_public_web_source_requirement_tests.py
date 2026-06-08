#!/usr/bin/env python3
"""Round 228 regression tests for public-web source requirements.

These tests cover the Linux log failure where a SEARCH turn about rumours for a
tracked TV show exposed only metadata tools, then the planner produced a
metadata-only plan.  They avoid live network/LLM calls.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.plan_coordinator import PlanCoordinator
from src.ai.public_web_requirements import PublicWebEvidencePolicy
from src.ai.tool_policy import AgentToolPolicy
from src.ai.tools.research import MetadataLookupTool
from src.core.categories.registry import CategoryRegistry
from src.core.domain_models.media import ItemList, TvShowItem
from src.core.domain_models.settings import Settings
from src.core.models import AgentPlan, Intent, PlanStep, ToolExecutionContext


class _FakeToolExecutor:
    def get_definitions(self, allowed_tool_names: set[str]) -> list[dict[str, Any]]:
        return [{"function": {"name": name}} for name in sorted(allowed_tool_names)]


class _MetadataOnlyPlanner:
    async def generate_plan(self, user_prompt: str, intent: Intent, context: str | None = None, tool_schemas: Any = None) -> AgentPlan:
        return AgentPlan(
            intent=intent,
            user_goal="Search for any rumors or news about a next season of For All Mankind",
            steps=[
                PlanStep(
                    id="lookup_rumors",
                    tool_name="metadata_lookup",
                    arguments={
                        "query": "For All Mankind",
                        "media_type": "tv",
                        "question": "Are there any rumors about a next season of For All Mankind?",
                        "category_id": "tv",
                    },
                    depends_on=[],
                    success_condition="metadata says whether renewal/rumors exist",
                )
            ],
        )


class _CoordinatorWithFakePlanner(PlanCoordinator):
    def create_planner(self) -> _MetadataOnlyPlanner:
        return _MetadataOnlyPlanner()


class Round228PublicWebSourceRequirementTests:
    def run(self) -> None:
        self._test_tv_search_policy_keeps_public_web_tools()
        asyncio.run(self._test_metadata_only_plan_gets_category_web_research_step())
        asyncio.run(self._test_metadata_lookup_marks_current_public_questions_as_insufficient())
        self._test_architecture_doc_records_source_sufficiency_rule()
        print("Round 228 public web source requirement tests passed")

    def _settings(self) -> Settings:
        settings = Settings()
        settings.tracked_items = ItemList(items=[TvShowItem(key="For All Mankind", language="en", category_id="tv")])
        return settings

    def _test_tv_search_policy_keeps_public_web_tools(self) -> None:
        registry = CategoryRegistry.with_defaults()
        tv = registry.get("tv")
        assert tv is not None
        allowed = AgentToolPolicy(self._settings()).allowed_tool_names(Intent.SEARCH, category=tv)
        assert "metadata_lookup" in allowed
        assert "web_search" in allowed
        assert "web_research" in allowed
        assert "category_web_research" in allowed
        assert "create_web_information_watch" in allowed
        assert "track_category_item" in allowed

    async def _test_metadata_only_plan_gets_category_web_research_step(self) -> None:
        coordinator = _CoordinatorWithFakePlanner(_FakeToolExecutor(), llm_client=None, settings=self._settings())
        allowed = {
            "metadata_lookup",
            "web_search",
            "web_research",
            "category_web_research",
            "read_web_page",
        }
        plan, _, prompt = await coordinator.prepare_plan(
            user_prompt="Can you please search if there are rumours about next season of For All Mankind?",
            intent=Intent.SEARCH,
            system_prompt_content="system",
            allowed_tool_names=allowed,
            context='CATEGORY LIBRARY CONTEXT PACKET: {"category_id":"tv"}',
        )
        assert plan is not None
        tools = [step.tool_name for step in plan.steps]
        assert tools[0] == "metadata_lookup"
        assert "category_web_research" in tools
        research_step = next(step for step in plan.steps if step.tool_name == "category_web_research")
        assert research_step.arguments["category_id"] == "tv"
        assert research_step.arguments["item_id"] == "For All Mankind"
        assert research_step.arguments["intent"] == "llm_planned_public_research"
        assert "public_web_source_policy=metadata_plus_category_web_research" in prompt

    async def _test_metadata_lookup_marks_current_public_questions_as_insufficient(self) -> None:
        result = MetadataLookupTool._success_payload(
            request=type("Request", (), {
                "query": "For All Mankind",
                "media_type": "tv",
                "question": "Are there rumours about the next season?",
                "season": None,
                "episode": None,
            })(),
            services_tried=["library_snapshot"],
            results=[{"provider": "library_snapshot", "title": "For All Mankind", "type": "tv"}],
            best={"provider": "library_snapshot", "title": "For All Mankind", "type": "tv"},
        )
        assert result["ok"] is True
        assert result["requires_public_web_evidence"] is True
        assert any(action["tool"] == "category_web_research" for action in result["next_actions"])
        assert PublicWebEvidencePolicy.requires_public_web_evidence("rumours about next season")

    def _test_architecture_doc_records_source_sufficiency_rule(self) -> None:
        arch = (ROOT / "architecture.md").read_text(encoding="utf-8")
        assert "public-web source sufficiency" in arch
        assert "metadata-only" in arch
        assert "category_web_research" in arch


if __name__ == "__main__":
    Round228PublicWebSourceRequirementTests().run()
