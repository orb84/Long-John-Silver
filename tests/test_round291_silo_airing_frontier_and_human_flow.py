"""Round 291 regressions for currently-airing TV release truth and direct queue flow."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.ai.tool_outcome_guard import ToolOutcomeLedger
from src.ai.tools.scheduling import SearchMediaTorrentsTool
from src.ai.tools.search_workspace import (
    SearchBatchRecommendationBuilder,
    SearchWorkspaceCompletionContractBuilder,
)
from src.core.categories.base import CategoryWorkflowContext
from src.core.categories.registry import CategoryRegistry
from src.core.categories.tv import TvShowCategory
from src.core.categories.tv_agent_availability import TVAgentAvailabilityFactsBuilder
from src.core.models import ToolExecutionContext


class _MediaRows:
    async def get_category_metadata(self, category_id: str, title: str):
        return []

    async def list_category_units(self, category_id: str, title: str, status: str | None = None):
        return []

    async def get_item_progress(self, category_id: str, title: str):
        return {}


class _Database:
    media = _MediaRows()


class _Scheduler:
    def __init__(self) -> None:
        self.category_registry = CategoryRegistry.with_defaults()
        self._db = None

    async def search_media_torrents(self, **kwargs):
        return {
            "query": "Silo S03E01-E02; S03E01, S03E02 (pack unavailable; individual episodes)",
            "language": "Italian",
            "category_id": "tv",
            "name": "Silo",
            "display_name": "Silo",
            "season": 3,
            "episode": None,
            "search_scope": "bundle_preferred",
            "season_total_episode_count": 10,
            "aired_episode_count": 2,
            "aired_unit_labels": ["S03E01", "S03E02"],
            "release_frontier_episode": 2,
            "target_unit_count": 2,
            "target_unit_labels": ["S03E01", "S03E02"],
            "requested_unit_scope": "available_units",
            "season_release_state": "currently_airing",
            "expected_episode_count": 2,
            "candidates": [
                {
                    "title": "Silo.S03E01.1080p.WEB-DL.ITA.ENG",
                    "magnet": "magnet:?xt=urn:btih:silo-e1",
                    "size": "2576980480",
                    "size_bytes": 2576980480,
                    "seeders": 347,
                    "source": "fixture",
                    "languages": ["Italian", "English"],
                    "resolution": "1080p",
                    "codec": "h265",
                    "season": 3,
                    "episode": 1,
                    "unit_descriptor": {
                        "granularity": "episode",
                        "stable_key": "S03E01",
                        "label": "S03E01",
                        "sort_key": [3, 1],
                        "coordinates": {"season": 3, "episode": 1},
                    },
                },
                {
                    "title": "Silo.S03E02.1080p.WEB-DL.ITA.ENG",
                    "magnet": "magnet:?xt=urn:btih:silo-e2",
                    "size": "4509715456",
                    "size_bytes": 4509715456,
                    "seeders": 70,
                    "source": "fixture",
                    "languages": ["Italian", "English"],
                    "resolution": "1080p",
                    "codec": "h264",
                    "season": 3,
                    "episode": 2,
                    "unit_descriptor": {
                        "granularity": "episode",
                        "stable_key": "S03E02",
                        "label": "S03E02",
                        "sort_key": [3, 2],
                        "coordinates": {"season": 3, "episode": 2},
                    },
                },
            ],
        }


def test_availability_facts_separate_catalogue_total_from_aired_frontier() -> None:
    facts = TVAgentAvailabilityFactsBuilder.build(
        season=3,
        season_total_episode_count=10,
        aired_episode_numbers={1, 2},
        target_episode_numbers={1, 2},
        requested_unit_scope="available_units",
    )
    assert facts["season_total_episode_count"] == 10
    assert facts["aired_episode_count"] == 2
    assert facts["release_frontier_episode"] == 2
    assert facts["expected_episode_count"] == 2
    assert facts["target_unit_labels"] == ["S03E01", "S03E02"]
    assert facts["season_release_state"] == "currently_airing"


def test_unknown_release_dates_do_not_turn_catalogue_total_into_availability() -> None:
    facts = TVAgentAvailabilityFactsBuilder.build(
        season=3,
        season_total_episode_count=10,
        aired_episode_numbers=set(),
        target_episode_numbers=set(),
        requested_unit_scope="available_units",
    )
    assert facts["season_total_episode_count"] == 10
    assert facts["aired_episode_count"] == 0
    assert facts["release_frontier_episode"] is None
    assert facts["expected_episode_count"] is None
    assert facts["season_release_state"] == "released_count_unknown"


@pytest.mark.asyncio
async def test_tv_pack_queries_stop_at_aired_frontier_not_future_catalogue_order() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")
    context = SimpleNamespace(
        agent_search_facts={"season_number": 3, "release_frontier_episode": 2},
    )
    queries = await tv.agent_pack_search_queries(item, 3, language="Italian", context=context)
    assert any("S03E01-E02" in query for query in queries)
    assert all("E10" not in query and "-10" not in query for query in queries)


@pytest.mark.asyncio
async def test_tv_pack_queries_do_not_fall_back_to_catalogue_total_when_frontier_unknown() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")
    facts = TVAgentAvailabilityFactsBuilder.build(
        season=3,
        season_total_episode_count=10,
        aired_episode_numbers=set(),
        target_episode_numbers=set(),
        requested_unit_scope="available_units",
    )
    queries = await tv.agent_pack_search_queries(
        item,
        3,
        language="Italian",
        context=SimpleNamespace(agent_search_facts=facts),
    )
    assert all("E10" not in query and "-10" not in query for query in queries)


def test_tv_response_facts_preserve_both_total_and_current_release_truth() -> None:
    tv = TvShowCategory()
    facts = {
        "season_number": 3,
        "season_total_episode_count": 10,
        "aired_episode_count": 2,
        "release_frontier_episode": 2,
        "target_unit_count": 2,
        "target_unit_labels": ["S03E01", "S03E02"],
        "expected_episode_count": 2,
    }
    result = tv.agent_search_response_facts(
        item=tv.create_item("Silo"),
        season=3,
        query_summary="Silo S03E01-E10",
        context=SimpleNamespace(agent_search_facts=facts),
    )
    assert result == facts
    assert result["expected_episode_count"] != result["season_total_episode_count"]


@pytest.mark.asyncio
async def test_bundle_preferred_fallback_builds_complete_two_episode_batch_without_followup() -> None:
    tool = SearchMediaTorrentsTool(scheduler=_Scheduler(), llm_client=None)
    result = await tool.execute(
        {
            "name": "Silo",
            "category_id": "tv",
            "season": 3,
            "language": "Italian",
            "language_is_explicit": True,
            "search_scope": "bundle_preferred",
            "unit_scope": "available_units",
        },
        ToolExecutionContext(
            category_id="tv",
            session_id="round291",
            user_prompt="download the available episodes of the latest season of Silo in Italian",
        ),
    )
    batch = result["batch_recommendation"]
    assert [group["unit"] for group in batch["groups"]] == ["S03E01", "S03E02"]
    assert len(batch["candidate_ids"]) == 2
    assert result["completion_contract"]["follow_up_required"] is False
    assert result["completion_contract"]["action_required"] == "queue_download"
    assert result["completion_contract"]["target_unit_labels"] == ["S03E01", "S03E02"]
    assert result["llm_candidate_review_status"] == "skipped_complete_deterministic_batch"
    assert "ask for confirmation" in result["llm_next_action"]


def test_completion_contract_requires_exact_target_units_not_merely_matching_count() -> None:
    contract = SearchWorkspaceCompletionContractBuilder.build(
        response_facts={
            "target_unit_count": 2,
            "target_unit_labels": ["S03E01", "S03E02"],
        },
        batch_recommendation={
            "groups": [{"unit": "S03E01"}, {"unit": "S03E03"}],
            "queue_download_arguments": {"candidate_ids": ["one", "three"]},
        },
        quality_choice_policy={"requires_user_choice": False},
        language="Italian",
    )
    assert contract["follow_up_required"] is True
    assert contract["reason"] == "target_units_not_fully_covered"


def test_batch_filters_extra_units_and_ignores_blocked_bundle() -> None:
    tv = TvShowCategory()
    candidates = [
        {
            "candidate_id": "blocked-pack",
            "is_bundle": True,
            "bundle_scope": "episode_range",
            "auto_queue_allowed": False,
            "unit_descriptor": {"label": "S03E01-E10", "stable_key": "S03E01-E10"},
        },
        {
            "candidate_id": "one",
            "auto_queue_allowed": True,
            "seeders": 100,
            "unit_descriptor": {
                "label": "S03E01",
                "stable_key": "S03E01",
                "sort_key": [3, 1],
                "coordinates": {"season": 3, "episode": 1},
            },
        },
        {
            "candidate_id": "two",
            "auto_queue_allowed": True,
            "seeders": 80,
            "unit_descriptor": {
                "label": "S03E02",
                "stable_key": "S03E02",
                "sort_key": [3, 2],
                "coordinates": {"season": 3, "episode": 2},
            },
        },
        {
            "candidate_id": "future-three",
            "auto_queue_allowed": True,
            "seeders": 200,
            "unit_descriptor": {
                "label": "S03E03",
                "stable_key": "S03E03",
                "sort_key": [3, 3],
                "coordinates": {"season": 3, "episode": 3},
            },
        },
    ]
    batch = SearchBatchRecommendationBuilder.build(
        name="Silo",
        category_id="tv",
        season=3,
        episode=None,
        search_scope="bundle_preferred",
        result_set_id="set-1",
        candidates=candidates,
        category=tv,
        preferred_language="Italian",
        target_unit_labels=["S03E01", "S03E02"],
    )
    assert batch is not None
    assert batch["candidate_ids"] == ["one", "two"]
    assert [group["unit"] for group in batch["groups"]] == ["S03E01", "S03E02"]


def test_complete_search_result_reprompts_queue_instead_of_allowing_menu() -> None:
    ledger = ToolOutcomeLedger()
    ledger.record(
        "search_media_torrents",
        {
            "content": {
                "completion_contract": {
                    "follow_up_required": False,
                    "action_required": "queue_download",
                    "queue_download_arguments": {
                        "name": "Silo",
                        "result_set_id": "set-1",
                        "candidate_ids": ["episode-1", "episode-2"],
                    },
                }
            }
        },
    )
    reprompt = ledger.required_queue_followthrough()
    assert reprompt is not None
    assert "queue_download" in reprompt
    assert "episode-1" in reprompt and "episode-2" in reprompt
    assert "Do not present a menu" in reprompt


def test_successful_batch_receipt_is_not_mislabeled_partial_failure() -> None:
    ledger = ToolOutcomeLedger()
    ledger.record(
        "queue_download",
        {
            "content": {
                "status": "queued",
                "queued_count": 2,
                "download_ids": ["one", "two"],
                "error_count": 0,
                "errors": [],
                "partial_failure": False,
                "command_receipt": {"status": "queued", "ok": True, "receipt_persisted": True},
            }
        },
    )
    assert ledger.partial_queue_failure() is None
    assert ledger.unresolved_queue_failure() is None

@pytest.mark.asyncio
async def test_provider_channel_suffix_does_not_turn_valid_tool_into_not_allowed() -> None:
    from src.ai.tool_executor import ToolCallExecutor
    from src.ai.tool_registry import ToolRegistry

    registry = ToolRegistry()

    async def inspect_torrent_candidate(**kwargs):
        return {"ok": True, "candidate_id": kwargs.get("candidate_id")}

    registry.register(
        "inspect_torrent_candidate",
        "inspect",
        {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}},
            "required": ["candidate_id"],
        },
        inspect_torrent_candidate,
    )
    message, _ = await ToolCallExecutor(registry).execute_tool_call(
        name="inspect_torrent_candidate<|channel|>commentary",
        arguments_raw={"candidate_id": "episode-1"},
        tool_call_id="round291-tool",
        allowed_tool_names={"inspect_torrent_candidate"},
    )
    assert message["name"] == "inspect_torrent_candidate"
    assert '"ok":true' in message["content"]
