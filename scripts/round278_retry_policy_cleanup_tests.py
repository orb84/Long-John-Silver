#!/usr/bin/env python3
"""Round 278 unmatched-search retry policy cleanup tests."""

from __future__ import annotations

import asyncio
import ast
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tools.scheduling import SearchMediaTorrentsTool
from src.ai.tools.search_retry import UnmatchedSearchRetryScheduler
from src.core.models import Settings, ToolExecutionContext


class _DummyPromptScheduler:
    """Record created prompt tasks for retry-policy assertions."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def list_tasks(self, user_id: str | None = None) -> list[Any]:
        """Return no existing tasks so tests exercise creation."""
        return []

    async def create_task(self, **kwargs: Any) -> Any:
        """Record one created task and return a tiny task object."""
        self.created.append(kwargs)
        return type("Task", (), {
            "id": "retry123",
            "interval_minutes": kwargs["interval_minutes"],
            "max_runs": kwargs["max_runs"],
        })()


class _DummySettingsManager:
    """Settings holder with unmatched-search retry enabled."""

    def __init__(self) -> None:
        self.settings = Settings()
        self.settings.soulseek.enabled = True
        self.settings.soulseek.auto_retry_unmatched_searches = True
        self.settings.soulseek.retry_search_interval_minutes = 360
        self.settings.soulseek.retry_search_max_runs = 12


def test_retry_helper_is_not_copied_into_unrelated_tools() -> None:
    """Only the torrent-search tool keeps the compatibility retry wrapper."""
    tree = ast.parse(Path("src/ai/tools/scheduling.py").read_text(encoding="utf-8"))
    owners: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for child in node.body:
            if isinstance(child, ast.AsyncFunctionDef) and child.name == "_maybe_schedule_unmatched_retry":
                owners.append(node.name)
    assert owners == ["SearchMediaTorrentsTool"], owners


def test_retry_prompt_is_notification_only() -> None:
    """A recurring missed-search retry must not become background auto-queue consent."""
    prompt = UnmatchedSearchRetryScheduler._retry_prompt(
        marker="ljs:auto-retry-search:test",
        name="Example Album",
        category_id="music",
        search_scope="default",
    )
    folded = prompt.casefold()
    assert "never queue" in folded
    assert "auto-download" in folded
    assert "notify me with the best candidates" in folded
    assert "if a clear safe match appears" not in folded
    assert "auto-download is enabled" not in folded
    assert "album/track" not in folded


async def test_unmatched_search_creates_safe_retry_task() -> None:
    """The compatibility wrapper delegates to the shared notification-only policy."""
    scheduler = type("DummyScheduler", (), {})()
    scheduler._prompt_scheduler = _DummyPromptScheduler()
    scheduler._settings_manager = _DummySettingsManager()
    tool = SearchMediaTorrentsTool(scheduler=scheduler)  # type: ignore[arg-type]
    res = {
        "candidate_count": 0,
        "companion_soulseek": {"enabled": True, "status": "ready", "candidate_count": 0},
    }
    await tool._maybe_schedule_unmatched_retry(
        res=res,
        name="Example Album",
        category_id="music",
        search_scope="default",
        context=ToolExecutionContext(user_id="u", session_id="s", source="web"),
    )
    assert res["deferred_search_retry"]["scheduled"] is True
    assert "notification-only" in res["deferred_search_retry"]["reason"]
    prompt = scheduler._prompt_scheduler.created[0]["prompt"]
    assert "Never queue" in prompt
    assert "stable candidate IDs" in prompt


def main() -> None:
    """Run the Round 278 regression checks."""
    test_retry_helper_is_not_copied_into_unrelated_tools()
    test_retry_prompt_is_notification_only()
    asyncio.run(test_unmatched_search_creates_safe_retry_task())
    print("Round 278 retry policy cleanup tests passed")


if __name__ == "__main__":
    main()
