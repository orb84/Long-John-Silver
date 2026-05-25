"""Round 58 regressions for chat progress, metadata fallback, and voyage logs."""

import json

import pytest

from src.ai.plan_executor import PlanExecutor
from src.ai.tool_executor import ToolCallExecutor
from src.ai.tools.research import MetadataLookupTool
from src.core.models import AgentPlan, Intent, PlanStep, ToolExecutionContext


class FakeToolRegistry:
    def __init__(self):
        self.calls = []
        self._handlers = {}

    def register(self, name, handler):
        self._handlers[name] = handler

    def get_definitions(self, allowed_tool_names):
        return [{"name": name} for name in allowed_tool_names]

    async def execute(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return await self._handlers[name](arguments)


@pytest.mark.asyncio
async def test_metadata_plan_miss_is_soft_when_web_fallback_is_available():
    registry = FakeToolRegistry()

    async def metadata_lookup(args):
        return {
            "ok": False,
            "query": args.get("query"),
            "error": "No metadata service is configured or available for this lookup.",
        }

    registry.register("metadata_lookup", metadata_lookup)
    executor = PlanExecutor(
        tool_executor=ToolCallExecutor(registry),
        allowed_tool_names={"metadata_lookup", "web_search", "read_web_page"},
    )
    plan = AgentPlan(
        intent=Intent.SEARCH,
        user_goal="Find cast information.",
        steps=[
            PlanStep(
                id="lookup_metadata",
                tool_name="metadata_lookup",
                arguments={"query": "For All Mankind", "media_type": "tv"},
            )
        ],
    )

    result = await executor.execute(plan)

    assert result.all_successful is True
    assert result.steps[0].success is True
    assert "soft miss" in (result.steps[0].summary or "")


class FakeMediaRepo:
    async def list_category_items(self, category_id):
        if category_id == "tv":
            return [{
                "item_id": "For All Mankind",
                "key": "For All Mankind",
                "display_name": "For All Mankind",
                "metadata": {
                    "display_name": "For All Mankind",
                    "tmdb_id": 87917,
                    "cast_names": ["Joel Kinnaman", "Wrenn Schmidt"],
                    "number_of_seasons": 5,
                },
            }]
        return []

    async def get_category_metadata(self, category_id, item_id, provider=None):
        assert category_id == "tv"
        assert item_id == "For All Mankind"
        return [{
            "provider": "tmdb",
            "external_id": "87917",
            "metadata": {
                "display_name": "For All Mankind",
                "cast_names": ["Joel Kinnaman", "Wrenn Schmidt", "Krys Marshall"],
                "number_of_seasons": 5,
            },
        }]


class FakeDatabase:
    media = FakeMediaRepo()


@pytest.mark.asyncio
async def test_metadata_lookup_uses_persisted_library_snapshot_without_external_service():
    tool = MetadataLookupTool(tmdb_client=None, tvmaze_client=None, database=FakeDatabase())

    result = await tool.execute(
        {"query": "For All Mankind", "media_type": "tv", "season": 5},
        ToolExecutionContext(),
    )

    assert result["ok"] is True
    assert result["best"]["provider"] == "tmdb"
    assert result["answer_hints"]["top_billed_actor"] == "Joel Kinnaman"
    assert result["answer_hints"]["season"] == 5


def test_appdeck_owns_voyage_log_refresh_after_dashboard_split():
    app_js = open("src/web/static/js/app.js", encoding="utf-8").read()
    assert "window.refreshLogs = () => this._refreshVoyageLogs();" in app_js
    assert "/api/system/logs?lines=160" in app_js
    assert "voyage-log-line" in app_js


def test_websocket_chat_sends_immediate_status_before_wait_loop():
    app_py = open("src/web/app.py", encoding="utf-8").read()
    initial_send = app_py.index('await websocket.send_json({"type": "status", "content": initial_content})')
    wait_loop = app_py.index('while True:')
    assert initial_send < wait_loop
