"""Round 234 regressions for fresh DOWNLOAD requests vs stale candidates."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.download_context_policy import DownloadContextPolicy
from src.ai.pending_actions import PendingActionContextBuilder
from src.ai.streaming_agent_loop import StreamingAgentLoopExecutor
from src.core.models import Intent


class _FakeSystemStore:
    async def get_preference(self, key: str):
        raise AssertionError(f"pending context should have been suppressed before DB load, got {key}")


class _FakeDB:
    system = _FakeSystemStore()


class _Delta:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = None


class _Choice:
    def __init__(self, content: str):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content: str):
        self.choices = [_Choice(content)]


class _FakeToolExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def execute_tool_call(self, **kwargs):
        self.calls.append(kwargs)
        return {"role": "tool", "tool_call_id": kwargs.get("tool_call_id"), "name": kwargs.get("name"), "content": "{\"candidate_count\": 1}"}, {"ok": True}


class _FakeStreamer:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, **kwargs):
        self.calls += 1
        text = "Captain, the old stale candidates say only episode 1 exists." if self.calls == 1 else "I searched with fresh tool evidence."
        async def _gen():
            yield _Chunk(text)
        return _gen()


async def test_pending_context_is_suppressed_for_fresh_download_request() -> None:
    builder = PendingActionContextBuilder(database=_FakeDB())
    context = await builder.build_for_session(
        "session-1",
        current_user_prompt="please grab me A Knight of the Seven Kingdoms in italian",
        intent=Intent.DOWNLOAD,
    )
    assert context == ""


async def test_streaming_download_turn_cannot_answer_before_tool_use() -> None:
    executor = _FakeToolExecutor()
    streamer = _FakeStreamer()
    loop = StreamingAgentLoopExecutor(
        tool_executor=executor,
        stream_completion=streamer,
    )
    chunks = []
    async for token in loop.execute(
        messages=[{"role": "system", "content": "test"}, {"role": "user", "content": "please grab me A Knight of the Seven Kingdoms in italian"}],
        tool_definitions=[{"type": "function", "function": {"name": "search_media_torrents", "parameters": {"type": "object", "properties": {}}}}],
        allowed_tool_names={"search_media_torrents", "queue_download"},
        max_iterations=3,
        task="download",
        user_prompt="please grab me A Knight of the Seven Kingdoms in italian",
        active_category_id="tv",
    ):
        chunks.append(token)
    output = "".join(chunks)
    assert "old stale candidates" not in output
    assert "fresh tool evidence" in output
    assert executor.calls, "fresh download prose should trigger recovery tool call"
    assert executor.calls[0]["name"] == "search_media_torrents"
    assert "A Knight of the Seven Kingdoms" in executor.calls[0]["arguments_raw"]


def test_policy_distinguishes_fresh_request_from_candidate_followup() -> None:
    assert DownloadContextPolicy.should_suppress_pending_candidates(
        "please grab me A Knight of the Seven Kingdoms in italian",
        Intent.DOWNLOAD,
    )
    assert not DownloadContextPolicy.should_suppress_pending_candidates(
        "queue the first one",
        Intent.DOWNLOAD,
    )
    assert not DownloadContextPolicy.should_suppress_pending_candidates(
        "candidate a84e9cc9bbf158cf looks good",
        Intent.DOWNLOAD,
    )


async def main() -> None:
    await test_pending_context_is_suppressed_for_fresh_download_request()
    await test_streaming_download_turn_cannot_answer_before_tool_use()
    test_policy_distinguishes_fresh_request_from_candidate_followup()
    print("round234 fresh download context tests passed")


if __name__ == "__main__":
    asyncio.run(main())
