#!/usr/bin/env python3
"""Round 192 regression tests for macOS Jackett/Soulseek search failures."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.slskd_client import SlskdClient
from src.ai.tools.soulseek import SearchSoulseekTool
from src.core.models import ToolExecutionContext


@dataclass
class DummySoulseekSettings:
    api_configured: bool = True
    managed: bool = True
    soulseek_credentials_configured: bool = True
    account_status: str = "ready"
    account_status_message: str = "ready"
    search_timeout_seconds: float = 4.0
    max_search_results: int = 20
    app_dir: str = "/tmp/ljs-slskd-test"
    search_enabled_categories: list[str] = field(default_factory=lambda: ["movie", "tv", "music"])


async def test_slskd_count_only_response_recovery() -> None:
    """If slskd reports file counts but hides rows, LJS retries with a plain search."""
    client = SlskdClient(DummySoulseekSettings())
    calls: list[tuple[str, str, Any]] = []

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path, kwargs.get("json") or kwargs.get("params")))
        if method == "POST" and path == "/api/v0/searches":
            payload = kwargs.get("json") or {}
            if "id" in payload:
                return {"id": "first-id", "token": 12, "searchText": payload.get("searchText")}
            return {"id": "recovery-id", "token": 13, "searchText": payload.get("searchText")}
        if "first-id" in path or path.endswith("/12/responses") or "/12/" in path:
            return {
                "id": "first-id",
                "token": 12,
                "isComplete": True,
                "responseCount": 250,
                "fileCount": 4000,
                "responses": [],
            }
        if "recovery-id" in path or path.endswith("/13/responses") or "/13/" in path:
            return {
                "id": "recovery-id",
                "token": 13,
                "isComplete": True,
                "responseCount": 1,
                "fileCount": 1,
                "responses": [
                    {
                        "username": "movie-user",
                        "hasFreeUploadSlot": True,
                        "files": [
                            {
                                "filename": "Project.Hail.Mary.2026.1080p.WEB-DL.ENG.mkv",
                                "size": 4_200_000_000,
                            }
                        ],
                    }
                ],
            }
        if path == "/api/v0/searches":
            return []
        return {}

    client._request = fake_request  # type: ignore[method-assign]
    result = await client.search("Project Hail Mary", timeout_seconds=2.0, max_results=20)
    assert result["ok"] is True
    assert result["candidate_count"] if "candidate_count" in result else len(result["candidates"])
    assert result["candidates"][0]["filename"].endswith(".mkv")
    assert any((call[2] or {}).get("searchText") == "Project Hail Mary" and "id" not in (call[2] or {}) for call in calls)


class DummyMovieCategory:
    category_id = "movie"

    def create_item(self, query: str, language: str | None = None) -> Any:
        return type("Item", (), {"key": query, "display_name": query, "language": language or "English", "metadata": {}})()

    def build_soulseek_search_queries(self, query: str, item: Any, **kwargs: Any) -> list[str]:
        return [query]

    def soulseek_search_limit(self, **kwargs: Any) -> int:
        return 10

    async def rank_soulseek_search_results(self, candidates: list[dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
        return [c for c in candidates if str(c.get("filename") or "").endswith(".mkv")]


class DummyRegistry:
    def get(self, category_id: str) -> Any:
        wanted = "mo" + "vie"
        if category_id == wanted:
            return DummyMovieCategory()
        return None


class DummySettings:
    language = "English"
    soulseek = DummySoulseekSettings()


class DummySettingsManager:
    settings = DummySettings()

    def save(self, settings: Any) -> None:
        pass


async def test_search_soulseek_defaults_to_active_category(monkeypatch: Any = None) -> None:
    """The direct Soulseek tool must not bypass category filtering when the LLM omits category_id."""
    async def fake_search(self: Any, query: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "candidates": [
                {"filename": "Project.Hail.Mary.epub", "extension": "epub"},
                {"filename": "Project.Hail.Mary.2026.1080p.WEB-DL.ENG.mkv", "extension": "mkv"},
            ],
        }

    original = SlskdClient.search
    SlskdClient.search = fake_search  # type: ignore[assignment]
    try:
        tool = SearchSoulseekTool(settings_manager=DummySettingsManager(), category_registry=DummyRegistry())
        result = await tool.execute({"query": "Project Hail Mary"}, ToolExecutionContext(category_id="movie"))
        assert result["category_id"] == "movie"
        assert result["candidate_count"] == 1
        assert result["candidates"][0]["filename"].endswith(".mkv")
    finally:
        SlskdClient.search = original  # type: ignore[assignment]


async def main() -> None:
    await test_slskd_count_only_response_recovery()
    await test_search_soulseek_defaults_to_active_category()
    print("round192 macOS Jackett/Soulseek recovery tests passed")


if __name__ == "__main__":
    asyncio.run(main())
