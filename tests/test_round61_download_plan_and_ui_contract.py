from types import SimpleNamespace

from src.ai.plan_coordinator import PlanCoordinator
from src.ai.plan_executor import PlanExecutor
from src.core.models import AgentPlan, Intent, PlanStep


def test_download_plan_strips_premature_placeholder_queue_step():
    coordinator = PlanCoordinator(tool_executor=None, llm_client=None, settings=SimpleNamespace(tracked_items=[]))
    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Download missing episodes from season 5",
        steps=[
            PlanStep(
                id="search_season",
                tool_name="search_media_torrents",
                arguments={"name": "For All Mankind", "season": 5},
                depends_on=[],
            ),
            PlanStep(
                id="queue_download",
                tool_name="queue_download",
                arguments={
                    "name": "For All Mankind",
                    "result_set_id": "${search_season.result_set_id}",
                    "candidate_ids": "${search_season.candidate_ids}",
                },
                depends_on=["search_season"],
            ),
        ],
    )

    normalized = coordinator._normalize_download_plan(
        plan,
        "grab the missing episodes from season 5 of For All Mankind",
        {"search_media_torrents", "queue_download"},
    )

    assert [step.tool_name for step in normalized.steps] == ["search_media_torrents"]


def test_plan_executor_resolves_candidate_ids_from_batch_recommendation_alias():
    executor = PlanExecutor(tool_executor=None, allowed_tool_names={"search_media_torrents", "queue_download"})
    payload = {
        "result_set_id": "rs_root",
        "batch_recommendation": {
            "result_set_id": "rs_batch",
            "candidate_ids": ["cand_s05e04", "cand_s05e05"],
            "queue_download_arguments": {
                "result_set_id": "rs_root",
                "candidate_ids": ["cand_s05e04", "cand_s05e05"],
            },
        },
    }

    assert executor._extract_placeholder_path(payload, "candidate_ids") == ["cand_s05e04", "cand_s05e05"]
    assert executor._extract_placeholder_path(payload, "queue_download_arguments") == {
        "result_set_id": "rs_root",
        "candidate_ids": ["cand_s05e04", "cand_s05e05"],
    }
