#!/usr/bin/env python3
"""Round 140 tests: truthful Soulseek status and category-neutral search scope."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import Settings
from src.integrations.slskd_manager import SlskdManager
from src.ai.tools.scheduling import SearchMediaTorrentsTool
from src.core.scheduler_services import SchedulerTorrentSearchService


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_managed_startup_has_datetime_and_credentials_key() -> None:
    main_text = read("main.py")
    manager_text = read("src/integrations/slskd_manager.py")
    assert "from datetime import datetime, timezone" in main_text
    assert "def _credentials_key" in manager_text
    assert "hashlib.sha256" in manager_text


async def test_health_check_invalidates_stale_ready_after_start_error() -> None:
    settings = Settings()
    settings.soulseek.enabled = True
    settings.soulseek.managed = True
    settings.soulseek.api_key = "abc"
    settings.soulseek.account_status = "ready"
    manager = SlskdManager()
    manager._last_error = "simulated slskd startup failure"  # noqa: SLF001 - targeted regression
    result = await manager.health_check(settings)
    assert result["account_ready"] is False, result
    assert result["account_status"] == "error", result
    assert "simulated slskd startup failure" in result["account_status_message"]


def test_search_scope_schema_is_category_neutral() -> None:
    schema = SearchMediaTorrentsTool().parameters()
    enum = schema["properties"]["search_scope"]["enum"]
    assert enum == ["default", "bundle_preferred", "bundle_only", "individual_units_only"], enum
    text = str(schema["properties"]["search_scope"])
    assert "season_pack" not in text
    assert "broad" not in text


def test_search_scope_aliases_are_accepted_but_not_surfaced() -> None:
    assert SchedulerTorrentSearchService._normalize_search_scope("season_pack_preferred") == "bundle_preferred"
    assert SchedulerTorrentSearchService._normalize_search_scope("season_pack_only") == "bundle_only"
    assert SchedulerTorrentSearchService._normalize_search_scope("broad") == "default"
    assert SchedulerTorrentSearchService._normalize_search_scope("individual_units") == "individual_units_only"
    assert SchedulerTorrentSearchService._normalize_search_scope("album") == "default"


def test_llm_prompt_and_next_actions_do_not_teach_tv_scope_to_music() -> None:
    prompt = read("src/ai/prompt_builder.py")
    scheduling = read("src/ai/tools/scheduling.py")
    assert "search_scope=bundle_preferred" in prompt
    assert "search_scope=season_pack_preferred" not in prompt
    assert "args_hint" in scheduling
    assert "\"broad\"" not in scheduling
    assert "\"season_pack_preferred\" if" not in scheduling


def main() -> None:
    test_managed_startup_has_datetime_and_credentials_key()
    asyncio.run(test_health_check_invalidates_stale_ready_after_start_error())
    test_search_scope_schema_is_category_neutral()
    test_search_scope_aliases_are_accepted_but_not_surfaced()
    test_llm_prompt_and_next_actions_do_not_teach_tv_scope_to_music()
    print("Round 140 Soulseek truthful status/category-neutral scope tests passed")


if __name__ == "__main__":
    main()
