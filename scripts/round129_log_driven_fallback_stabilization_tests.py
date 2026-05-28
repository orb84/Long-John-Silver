#!/usr/bin/env python3
"""Round 129 regression tests for log-driven fallback stabilization."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_movie_metadata_lookup_inherits_media_tmdb_key_during_hot_save() -> None:
    """Movie/TV lookups must use the abstract media key during setup hot-saves."""
    from src.ai.tools.metadata_lookup_support import MetadataClientResolver
    from src.core.domain_models.settings import Settings

    class Manager:
        def __init__(self) -> None:
            self.settings = Settings()
            # This is the shape produced while setup/Compass is saving private
            # category-owned config: the user-owned key lives on media, while
            # concrete child categories may not yet have a reloaded inherited copy.
            self.settings.category_settings = {
                "media": {"services": {"tmdb": {"api_key": "tmdb-test-key"}}},
                "movie": {},
                "tv": {},
            }

    resolver = MetadataClientResolver(settings_manager=Manager())
    require(resolver._current_tmdb_key("movie") == "tmdb-test-key", "movie metadata should inherit media TMDB key")
    require(resolver._current_tmdb_key("tv") == "tmdb-test-key", "tv metadata should inherit media TMDB key")
    require(resolver.tmdb_configured("movie"), "movie TMDB configured check should respect inherited media key")


async def test_metadata_lookup_soft_miss_does_not_crash_plan_without_web_tool_in_allowed_set() -> None:
    """A metadata miss is context for fallback, not a terminal planned-step error."""
    from src.ai.plan_executor import PlanExecutor
    from src.core.models import AgentPlan, Intent, PlanStep

    class FakeToolExecutor:
        async def execute_tool_call(self, *, name, arguments_raw, tool_call_id, allowed_tool_names):
            payload = {
                "ok": False,
                "error": "No metadata service result was available to the agent; fall back to web search if available.",
            }
            return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": json.dumps(payload)}, payload["error"]

    plan = AgentPlan(
        intent=Intent.SEARCH,
        user_goal="Identify a movie from a vague description",
        constraints={},
        steps=[PlanStep(id="lookup", tool_name="metadata_lookup", arguments={"query": "Super 8", "media_type": "movie"})],
    )
    executor = PlanExecutor(FakeToolExecutor(), allowed_tool_names={"metadata_lookup"})
    result = await executor.execute(plan)
    require(result.all_successful, "soft metadata miss should not become a user-visible crash")
    require(result.steps[0].success, "soft miss should be injected as successful context for fallback reasoning")
    require("soft miss" in (result.steps[0].summary or ""), "soft miss should be visible in trace summaries")


def test_jackett_login_redirect_is_degraded_not_warning_spam() -> None:
    """Jackett UI-login redirects should become actionable degraded diagnostics."""
    from src.search.jackett import JackettSearch
    from src.search.jackett_indexer_config import JackettIndexerConfigurer

    response = httpx.Response(302, headers={"Location": "http://127.0.0.1:9117/UI/Login?ReturnUrl=%2Fapi%2Fv2.0%2Findexers"})
    require(JackettIndexerConfigurer._is_login_redirect(response), "indexer config should detect Jackett UI login redirect")
    require(JackettSearch._is_login_redirect(response), "search provider should detect Jackett UI login redirect")

    config_source = (ROOT / "src/search/jackett_indexer_config.py").read_text(encoding="utf-8")
    require("status\": \"degraded\"" in config_source, "Jackett indexer auth failures should surface as degraded diagnostics")
    require("failed to fetch indexer catalogue" not in config_source, "redirect/catalogue auth should not log noisy warning spam")


def test_log_noise_reduced_for_expected_startup_and_scan_conditions() -> None:
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    smart_quality = (ROOT / "src/core/smart_quality.py").read_text(encoding="utf-8")
    require("logger.info(\n                \"Direct scraper fallback is enabled" in main, "configured direct fallback should be info, not warning")
    require("logger.debug(\n            f\"Inferred quality for" in smart_quality, "per-item quality inference should be debug, not info spam")


def main() -> None:
    test_movie_metadata_lookup_inherits_media_tmdb_key_during_hot_save()
    asyncio.run(test_metadata_lookup_soft_miss_does_not_crash_plan_without_web_tool_in_allowed_set())
    test_jackett_login_redirect_is_degraded_not_warning_spam()
    test_log_noise_reduced_for_expected_startup_and_scan_conditions()
    print("round129 log-driven fallback stabilization tests passed")


if __name__ == "__main__":
    main()
