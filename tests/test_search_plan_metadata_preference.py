from src.ai.plan_coordinator import PlanCoordinator
from src.core.models import AgentPlan, PlanStep, Intent


def test_search_plan_prefers_metadata_lookup_before_web_for_media_facts():
    coordinator = PlanCoordinator(tool_executor=None, llm_client=None, settings=None)
    plan = AgentPlan(
        intent=Intent.SEARCH,
        user_goal="Find the name of the lead actor in the original Twin Peaks TV series.",
        constraints={},
        steps=[
            PlanStep(
                id="search_lead_actor",
                tool_name="web_search",
                arguments={"query": "Twin Peaks original series lead actor", "max_results": 5},
                depends_on=[],
            )
        ],
    )

    normalized = coordinator._normalize_search_plan(
        plan,
        user_prompt="who is the lead actor in the twin peaks tv series",
        allowed_tool_names={"metadata_lookup", "web_search"},
        context='ACTIVE CATEGORY LIBRARY CONTEXT PACKET:\n{"category_id": "tv"}',
    )

    assert normalized.steps[0].tool_name == "metadata_lookup"
    assert normalized.steps[0].arguments["media_type"] == "tv"
