"""Regression tests for malformed bare JSON tool-call output."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.ai.agent_loop import AgentLoopExecutor
from src.ai.agent_loop_state import AgentLoopState
from src.ai.bare_tool_call import BareToolCallDetector
from src.ai.streaming_agent_loop import StreamingAgentLoopExecutor
from src.ai.tool_executor import ToolCallExecutor
from src.ai.tool_registry import ToolRegistry


def _response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[]))]
    )


def _chunk(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


async def _stream(*parts: str):
    for part in parts:
        yield _chunk(part)


def test_detector_recovers_web_search_args_only_when_allowed() -> None:
    recovered = BareToolCallDetector.from_text(
        '{"query":"All Quiet on the Western Front 2022 war film","max_results":5}',
        {"web_search"},
    )

    assert recovered is not None
    assert recovered.name == "web_search"
    assert recovered.arguments["query"].startswith("All Quiet")

    assert BareToolCallDetector.from_text('{"query":"x","max_results":5}', set()) is None
    assert BareToolCallDetector.from_text('Here is the answer: {"query":"x"}', {"web_search"}) is None


@pytest.mark.asyncio
async def test_agent_loop_executes_bare_json_search_instead_of_returning_it() -> None:
    calls: list[dict] = []
    registry = ToolRegistry()

    async def web_search(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "results": [{"title": "War film result"}]}

    registry.register(
        "web_search",
        "search",
        {"type": "object", "properties": {"query": {"type": "string"}}},
        web_search,
    )
    executor = AgentLoopExecutor(
        ToolCallExecutor(registry),
        llm_completion=_CompletionSequence([
            _response('{"query":"All Quiet on the Western Front 2022 war film","max_results":5}'),
            _response("Here are a few strong recent war-film picks."),
        ]),
    )

    messages = [{"role": "user", "content": "can you suggest some cool war movie from the last 5 years ?"}]
    result = await executor.execute(
        messages=messages,
        tool_definitions=[{"type": "function", "function": {"name": "web_search"}}],
        allowed_tool_names={"web_search"},
        max_iterations=3,
        task="search",
    )

    assert result.response == "Here are a few strong recent war-film picks."
    assert calls == [{"query": "All Quiet on the Western Front 2022 war film", "max_results": 5}]
    assert not any(msg.get("content", "").startswith('{"query"') for msg in messages if isinstance(msg, dict))
    assert any(msg.get("tool_calls") for msg in messages if isinstance(msg, dict))


@pytest.mark.asyncio
async def test_streaming_loop_buffers_and_recovers_bare_json_before_display() -> None:
    calls: list[dict] = []
    registry = ToolRegistry()

    async def web_search(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "results": [{"title": "War film result"}]}

    registry.register(
        "web_search",
        "search",
        {"type": "object", "properties": {"query": {"type": "string"}}},
        web_search,
    )
    stream_completion = _StreamCompletionSequence([
        ["{\"query\":\"All Quiet on the Western Front 2022 war film\",", "\"max_results\":5}"],
        ["Here are ", "better picks."],
    ])
    executor = StreamingAgentLoopExecutor(
        ToolCallExecutor(registry),
        stream_completion=stream_completion,
    )

    messages = [{"role": "user", "content": "war movie suggestions"}]
    chunks = []
    async for chunk in executor.execute(
        messages=messages,
        tool_definitions=[{"type": "function", "function": {"name": "web_search"}}],
        allowed_tool_names={"web_search"},
        max_iterations=3,
        task="search",
    ):
        chunks.append(chunk)

    assert "".join(chunks) == "Here are better picks."
    assert calls == [{"query": "All Quiet on the Western Front 2022 war film", "max_results": 5}]
    assert executor.last_content == "Here are better picks."


class _CompletionSequence:
    def __init__(self, responses):
        self._responses = list(responses)

    async def __call__(self, **_kwargs):
        if not self._responses:
            raise AssertionError("Unexpected completion call")
        return self._responses.pop(0)


class _StreamCompletionSequence:
    def __init__(self, streams):
        self._streams = list(streams)

    async def __call__(self, **_kwargs):
        if not self._streams:
            raise AssertionError("Unexpected stream completion call")
        return _stream(*self._streams.pop(0))
