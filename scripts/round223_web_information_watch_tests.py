"""Round 223 checks for proactive web-information watches.

These tests avoid live internet, SearXNG startup, and LLM calls. They verify:
- durable watch schema/repository/service surfaces exist;
- the watch layer uses web/category research only and does not queue downloads;
- scheduled watch prompts can suppress no-change notifications;
- LLM-facing tools include tracking and watch creation paths;
- the next-season tracking/download scenario routes through category research,
  CategoryItemCoordinator, and later generic download tools rather than web snippets.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class Round223WebInformationWatchTests:
    """Small static/contract suite for proactive web-information watches."""

    def __init__(self) -> None:
        self._failures: list[str] = []

    def run(self) -> None:
        self._test_schema_and_repository_contract()
        self._test_watch_service_boundaries()
        self._test_scheduler_sentinel_and_tools()
        self._test_category_tracking_tool_uses_coordinator()
        self._test_next_season_scenario_guidance_and_tv_hooks()
        self._test_api_surfaces_exist()
        if self._failures:
            raise AssertionError("\n".join(self._failures))
        print("Round 223 web-information watch tests passed")

    def _test_schema_and_repository_contract(self) -> None:
        migration = (ROOT / "migrations/111_web_information_watches.sql").read_text(encoding="utf-8")
        models = (ROOT / "src/core/domain_models/web_search.py").read_text(encoding="utf-8")
        db = (ROOT / "src/core/database.py").read_text(encoding="utf-8")
        repo = (ROOT / "src/core/repositories/web_research.py").read_text(encoding="utf-8")
        for token in ("web_information_watch", "web_information_watch_event"):
            self._check(token in migration, f"migration should define {token}")
            self._check(token in db, f"base database schema should include {token}")
        self._check("class WebInformationWatch" in models, "WebInformationWatch model should exist")
        self._check("class WebInformationWatchEvent" in models, "WebInformationWatchEvent model should exist")
        for method in (
            "upsert_information_watch",
            "get_information_watch",
            "list_information_watches",
            "disable_information_watch",
            "add_information_watch_event",
            "update_information_watch_after_run",
        ):
            self._check(method in repo, f"repository should expose {method}")

    def _test_watch_service_boundaries(self) -> None:
        service = (ROOT / "src/search/web/information_watch.py").read_text(encoding="utf-8")
        self._check("class WebInformationWatchService" in service, "watch service should exist")
        self._check("CategoryWebResearchService" in service, "category watches should reuse category web research")
        self._check("WebResearchService" in service, "generic watches should reuse web research")
        self._check("queue_download" not in service and "search_media_torrents" not in service, "watch service must not queue or search downloads")
        self._check("CategoryItemCoordinator" not in service, "watch service must not mutate category items")
        forbidden_double = "category_id == " + chr(34) + "tv" + chr(34)
        forbidden_single = "category_id == " + chr(39) + "tv" + chr(39)
        self._check(forbidden_double not in service and forbidden_single not in service, "watch service must not branch on category-specific ids")
        self._check("LJS_NO_NOTIFICATION" in service, "scheduled prompts should support no-change notification suppression")
        self._check("allow_download_queueing" in service, "watch should remember explicit user allowance without acting on it")

    def _test_scheduler_sentinel_and_tools(self) -> None:
        scheduler = (ROOT / "src/core/prompt_scheduler.py").read_text(encoding="utf-8")
        web_tools = (ROOT / "src/ai/tools/web.py").read_text(encoding="utf-8")
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self._check("_should_suppress_condition_notification" in scheduler, "scheduler should suppress explicit no-notification sentinel")
        self._check("LJS_NO_NOTIFICATION" in scheduler, "scheduler should recognize no-notification sentinel")
        for tool in (
            "create_web_information_watch",
            "run_web_information_watch",
            "list_web_information_watches",
            "disable_web_information_watch",
        ):
            self._check(tool in web_tools, f"web tool provider should expose {tool}")
        self._check("WebInformationWatchPromptBuilder.scheduled_prompt" in web_tools, "create tool should schedule bounded watch prompt")
        self._check("prompt_scheduler=prompt_scheduler" in main, "main should inject PromptScheduler into WebToolProvider")

    def _test_category_tracking_tool_uses_coordinator(self) -> None:
        category_tools = (ROOT / "src/ai/tools/categories.py").read_text(encoding="utf-8")
        self._check("class TrackCategoryItemTool" in category_tools, "generic track_category_item tool should exist")
        self._check('name = "track_category_item"' in category_tools, "track_category_item should be LLM-visible")
        self._check("CategoryItemCoordinator" in category_tools, "tracking tool must route through CategoryItemCoordinator")
        self._check("settings.tracked_items.append" not in category_tools, "tracking tool must not write tracked_items directly")
        self._check("queue_download" not in category_tools, "tracking tool must not queue downloads")

    def _test_next_season_scenario_guidance_and_tv_hooks(self) -> None:
        prompt = (ROOT / "src/ai/prompt_builder.py").read_text(encoding="utf-8")
        tv = (ROOT / "src/core/categories/tv_web_research.py").read_text(encoding="utf-8")
        architecture = (ROOT / "architecture.md").read_text(encoding="utf-8")
        self._check("next season starts and start downloading/tracking" in prompt, "prompt should explicitly cover the user scenario")
        self._check("track_category_item" in prompt, "prompt should route untracked item mutations through track_category_item")
        self._check("allow_download_queueing=true" in prompt, "prompt should preserve explicit download-tracking allowance")
        self._check("public web evidence alone never authorizes a download" in prompt, "prompt should forbid web evidence from authorizing downloads")
        self._check("next_season_start_tracking" in tv, "TV hook should support next-season tracking intent")
        self._check("news_and_rumor_watch" in tv, "TV hook should support news/rumor watches")
        self._check("Web information watches" in architecture or "web-information watches" in architecture, "architecture should document watch layer")

    def _test_api_surfaces_exist(self) -> None:
        system_router = (ROOT / "src/web/routers/system.py").read_text(encoding="utf-8")
        for path in (
            "/api/web-information-watches",
            "/api/web-information-watches/{watch_id}/run",
            "/api/web-information-watches/{watch_id}/disable",
        ):
            self._check(path in system_router, f"system router should expose {path}")

    def _check(self, condition: Any, message: str) -> None:
        if not condition:
            self._failures.append(message)


if __name__ == "__main__":
    Round223WebInformationWatchTests().run()
