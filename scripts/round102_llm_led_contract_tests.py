#!/usr/bin/env python3
"""Round 102 contract-bound agent runtime regression tests.

These tests target the repeated production failure pattern from Discord logs:
LLM-authored plans/tool calls invented JSON placeholders or missing tools, then
runtime execution crashed.  The desired architecture is LLM-led but contract
bound: natural tool calls are allowed, while schemas, result handles, and
category-owned candidate workspaces keep execution safe.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tool_contracts import ToolContractValidator
from src.ai.tool_executor import ToolCallExecutor
from src.ai.tool_policy import AgentToolPolicy
from src.ai.tool_registry import ToolRegistry
from src.ai.tools.downloads import DownloadToolProvider
from src.ai.tools.scheduling import _search_result_next_actions
from src.core.models import Intent


async def _fake_search_media_torrents(**kwargs):
    return {"ok": True, "received": kwargs}


def assert_true(expr: bool, message: str) -> None:
    if not expr:
        raise AssertionError(message)


def test_contract_validator_blocks_model_placeholders() -> None:
    validator = ToolContractValidator()
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "season": {"type": "integer"},
            "search_scope": {"type": "string"},
        },
        "required": ["name"],
    }
    result = validator.validate(
        tool_name="search_media_torrents",
        arguments={"name": "Yellowstone", "season": "${lookup_show.result.seasons}"},
        schema=schema,
    )
    assert_true(not result.ok, "placeholder arguments must be rejected")
    assert_true(result.error_code == "UNRESOLVED_MODEL_PLACEHOLDER", "placeholder rejection must be typed")


def test_tool_executor_returns_typed_error_instead_of_crashing() -> None:
    async def run() -> None:
        registry = ToolRegistry()
        registry.register(
            "search_media_torrents",
            "fake media search",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "season": {"type": "integer"},
                },
                "required": ["name"],
            },
            _fake_search_media_torrents,
            intents={Intent.DOWNLOAD},
        )
        executor = ToolCallExecutor(registry)
        message, summary = await executor.execute_tool_call(
            "search_media_torrents",
            json.dumps({"name": "Yellowstone", "season": "${lookup.result.latest_season}"}),
            "tool_1",
            {"search_media_torrents"},
        )
        payload = json.loads(message["content"])
        assert_true(payload["ok"] is False, "executor must return a structured error")
        assert_true(payload["error_code"] == "UNRESOLVED_MODEL_PLACEHOLDER", "executor must preserve typed validation code")
        assert_true("crash" not in summary.lower(), "validation failure must not look like a runtime crash")

    asyncio.run(run())


def test_inspect_candidate_tool_is_registered_and_allowed() -> None:
    provider_names = {tool.name for tool in DownloadToolProvider().get_tools()}
    assert_true("inspect_torrent_candidate" in provider_names, "download provider must register candidate inspection")
    allowed = AgentToolPolicy().allowed_tool_names(Intent.DOWNLOAD, category=None)
    assert_true("inspect_torrent_candidate" in allowed, "DOWNLOAD policy must expose candidate inspection")


def test_download_turns_do_not_use_structured_preplan() -> None:
    source = Path("src/ai/assistant.py").read_text()
    assert_true("if ctx.intent == Intent.DOWNLOAD:" in source, "assistant must branch DOWNLOAD turns explicitly")
    assert_true("agent_plan, plan_exec = None, None" in source, "DOWNLOAD turns must skip placeholder-based structured preplans")
    contract = source[source.index("def _download_tool_loop_contract") :]
    assert_true("Do not write ${step.path}" in contract, "download contract must ban model-authored placeholders")
    assert_true("search_media_torrents" in contract and "queue_download" in contract, "download contract must preserve natural tool use")


def test_search_results_expose_affordances() -> None:
    actions = _search_result_next_actions(
        candidates=[{"candidate_id": "abc", "title": "Yellowstone.S05", "is_bundle": True, "seeders": 50}],
        search_scope="season_pack_preferred",
        result_set_id="rs_1",
        has_batch=False,
    )
    names = {a.get("action") for a in actions}
    assert_true("inspect_bundle_files" in names, "bundle result sets should invite candidate inspection")
    assert_true("queue_clear_candidate" in names, "clear candidates should expose queue affordance")


def test_policy_audit_detects_missing_registered_tool() -> None:
    class DummyPolicy:
        def allowed_tool_names(self, intent, category=None):
            return {"existing_tool", "missing_tool"}

    registry = ToolRegistry()
    registry.register("existing_tool", "exists", {"type": "object", "properties": {}}, _fake_search_media_torrents, intents={Intent.CHAT})
    findings = ToolContractValidator().audit_registry(registry, DummyPolicy(), [Intent.CHAT])
    assert_true(any("missing_tool" in finding for finding in findings), "registry audit must catch policy/registry drift")


def main() -> None:
    tests = [
        test_contract_validator_blocks_model_placeholders,
        test_tool_executor_returns_typed_error_instead_of_crashing,
        test_inspect_candidate_tool_is_registered_and_allowed,
        test_download_turns_do_not_use_structured_preplan,
        test_search_results_expose_affordances,
        test_policy_audit_detects_missing_registered_tool,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("Round 102 LLM-led contract-bound runtime tests passed.")


if __name__ == "__main__":
    main()
