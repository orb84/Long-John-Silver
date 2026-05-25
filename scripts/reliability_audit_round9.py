"""Round 9 reliability audit for multi-unit LLM download flow.

This script avoids network/DB services and tests the generic primitives that
prevent a request like "download remaining episodes" from degenerating into a
single queued episode.
"""
from __future__ import annotations

import asyncio
import json

from src.ai.streaming_agent_loop import StreamingAgentLoopExecutor
from src.ai.tools.downloads import QueueDownloadTool
from src.ai.tools.scheduling import _build_batch_recommendation
from src.core.models import (
    AgentPlan,
    Intent,
    PlanExecutionResult,
    PlanExecutionStep,
    PlanStep,
    ToolExecutionContext,
)
from src.utils.candidate_ids import attach_candidate_ids, store_result_set


class _SystemStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set_preference(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get_preference(self, key: str) -> str | None:
        return self.values.get(key)


class _Db:
    def __init__(self) -> None:
        self.system = _SystemStore()


class _Scheduler:
    def __init__(self) -> None:
        self.queued: list[dict] = []

    async def queue_download(self, **kwargs):
        self.queued.append(kwargs)
        return {"status": "queued", "download_id": f"dl{len(self.queued)}"}


class _PlanExecutor:
    def __init__(self, result_payload: dict) -> None:
        self.result_payload = result_payload
        self.queue_args: dict | None = None

    async def execute(self, plan: AgentPlan) -> PlanExecutionResult:
        assert plan.steps[0].tool_name == "queue_download"
        self.queue_args = plan.steps[0].arguments
        return PlanExecutionResult(
            plan=plan,
            all_successful=True,
            steps=[
                PlanExecutionStep(
                    step=plan.steps[0],
                    success=True,
                    result={
                        "role": "tool",
                        "name": "queue_download",
                        "content": json.dumps(self.result_payload),
                    },
                )
            ],
        )


def _sample_candidates() -> list[dict]:
    return attach_candidate_ids([
        {"index": 1, "title": "Show S05E03 ITA 1080p", "magnet": "magnet:?xt=urn:btih:aaaa", "season": 5, "episode": 3, "category_id": "tv"},
        {"index": 2, "title": "Show S05E04 ITA 1080p", "magnet": "magnet:?xt=urn:btih:bbbb", "season": 5, "episode": 4, "category_id": "tv"},
        {"index": 3, "title": "Show S05E05 ITA 1080p", "magnet": "magnet:?xt=urn:btih:cccc", "season": 5, "episode": 5, "category_id": "tv"},
    ])


async def audit_batch_recommendation_and_queue_tool() -> None:
    candidates = _sample_candidates()
    rec = _build_batch_recommendation(
        name="Show", category_id="tv", season=5, episode=None,
        result_set_id="rs1", candidates=candidates,
    )
    assert rec is not None
    assert rec["candidate_ids"] == [c["candidate_id"] for c in candidates]
    assert rec["auto_expand_single_selection"] is False

    db = _Db()
    await store_result_set(db, session_id="default", cache_data={
        "name": "Show",
        "category_id": "tv",
        "season": 5,
        "result_set_id": "rs1",
        "candidates": candidates,
        "batch_recommendation": rec,
    })

    scheduler = _Scheduler()
    tool = QueueDownloadTool(scheduler=scheduler, database=db)
    result = await tool.execute(
        {"candidate_ids": rec["candidate_ids"], "result_set_id": "rs1"},
        ToolExecutionContext(),
    )
    assert result["status"] == "queued"
    assert result["queued_count"] == 3
    assert [q["episode"] for q in scheduler.queued] == [3, 4, 5]

    # Follow-up selection of a single candidate stays single; only the explicit
    # batch recommendation path queues multiple units.
    single_scheduler = _Scheduler()
    single_tool = QueueDownloadTool(scheduler=single_scheduler, database=db)
    single_result = await single_tool.execute(
        {"candidate_id": rec["candidate_ids"][0], "result_set_id": "rs1"},
        ToolExecutionContext(),
    )
    assert single_result["queued_count"] == 1
    assert [q["episode"] for q in single_scheduler.queued] == [3]


async def audit_plan_auto_queues_batch_recommendation() -> None:
    rec = _build_batch_recommendation(
        name="Show", category_id="tv", season=5, episode=None,
        result_set_id="rs1", candidates=_sample_candidates(),
    )
    assert rec is not None
    search_payload = {
        "name": "Show",
        "batch_recommendation": {
            "queue_download_arguments": rec["queue_download_arguments"],
        },
    }
    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Grab the remaining episodes from season 5 of Show",
        steps=[],
    )
    plan_result = PlanExecutionResult(
        plan=plan,
        all_successful=True,
        steps=[
            PlanExecutionStep(
                step=PlanStep(id="search", tool_name="search_media_torrents"),
                success=True,
                result={"role": "tool", "name": "search_media_torrents", "content": json.dumps(search_payload)},
            )
        ],
    )
    payload = {
        "status": "queued",
        "download_id": "dl1",
        "download_ids": ["dl1", "dl2", "dl3"],
        "queued_count": 3,
        "queued": [{"season": 5, "episode": 3}, {"season": 5, "episode": 4}, {"season": 5, "episode": 5}],
    }
    executor = _PlanExecutor(payload)
    message = await StreamingAgentLoopExecutor._maybe_auto_queue_batch_recommendation(
        plan, executor, plan_result, messages=[],
    )
    assert message == "Queued 3 recommended download(s): S05E03, S05E04, S05E05."
    assert executor.queue_args == rec["queue_download_arguments"]


async def main() -> None:
    await audit_batch_recommendation_and_queue_tool()
    await audit_plan_auto_queues_batch_recommendation()
    print("round9 reliability audit passed")


if __name__ == "__main__":
    asyncio.run(main())
