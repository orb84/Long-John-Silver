"""Executable incident checks for Round 291 release-frontier and queue truth."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.tool_executor import ToolCallExecutor
from src.ai.tool_outcome_guard import ToolOutcomeLedger
from src.ai.tool_registry import ToolRegistry
from src.ai.tools.search_workspace import (
    SearchBatchRecommendationBuilder,
    SearchWorkspaceCompletionContractBuilder,
)
from src.core.categories.registry import CategoryRegistry
from src.core.categories.tv_agent_availability import TVAgentAvailabilityFactsBuilder


class Round291Checks:
    """Reproduce the Silo count/menu/partial-receipt failures deterministically."""

    @classmethod
    async def run(cls) -> None:
        facts = cls._check_release_frontier_truth()
        cls._check_unknown_dates_do_not_become_catalogue_availability()
        batch = cls._check_bundle_preferred_episode_batch(facts)
        cls._check_completion_contract(facts, batch)
        cls._check_success_receipt_truth()
        await cls._check_provider_tool_suffix()
        cls._check_architecture_contract()

    @staticmethod
    def _check_release_frontier_truth() -> dict:
        facts = TVAgentAvailabilityFactsBuilder.build(
            season=3,
            season_total_episode_count=10,
            aired_episode_numbers=[1, 2],
            target_episode_numbers=[1, 2],
            requested_unit_scope="available_units",
        )
        assert facts["season_total_episode_count"] == 10
        assert facts["aired_episode_count"] == 2
        assert facts["expected_episode_count"] == 2
        assert facts["target_unit_labels"] == ["S03E01", "S03E02"]
        return facts

    @staticmethod
    def _check_unknown_dates_do_not_become_catalogue_availability() -> None:
        facts = TVAgentAvailabilityFactsBuilder.build(
            season=3,
            season_total_episode_count=10,
            aired_episode_numbers=[],
            target_episode_numbers=[],
            requested_unit_scope="available_units",
        )
        assert facts["season_total_episode_count"] == 10
        assert facts["release_frontier_episode"] is None
        assert facts["expected_episode_count"] is None

    @staticmethod
    def _check_bundle_preferred_episode_batch(facts: dict) -> dict:
        category = CategoryRegistry.with_defaults().get("tv")
        candidates = [
            {
                "candidate_id": "silo-e1",
                "title": "Silo S03E01 1080p ITA",
                "seeders": 347,
                "languages": ["Italian"],
                "auto_queue_allowed": True,
                "unit_descriptor": {
                    "granularity": "episode",
                    "stable_key": "S03E01",
                    "label": "S03E01",
                    "sort_key": [3, 1],
                    "coordinates": {"season": 3, "episode": 1},
                },
            },
            {
                "candidate_id": "silo-e2",
                "title": "Silo S03E02 1080p ITA",
                "seeders": 70,
                "languages": ["Italian"],
                "auto_queue_allowed": True,
                "unit_descriptor": {
                    "granularity": "episode",
                    "stable_key": "S03E02",
                    "label": "S03E02",
                    "sort_key": [3, 2],
                    "coordinates": {"season": 3, "episode": 2},
                },
            },
        ]
        batch = SearchBatchRecommendationBuilder.build(
            name="Silo",
            category_id="tv",
            season=3,
            episode=None,
            search_scope="bundle_preferred",
            result_set_id="round291",
            candidates=candidates,
            category=category,
            preferred_language="Italian",
            target_unit_labels=facts["target_unit_labels"],
        )
        assert batch is not None
        assert batch["candidate_ids"] == ["silo-e1", "silo-e2"]
        return batch

    @staticmethod
    def _check_completion_contract(facts: dict, batch: dict) -> None:
        contract = SearchWorkspaceCompletionContractBuilder.build(
            response_facts=facts,
            batch_recommendation=batch,
            quality_choice_policy={"requires_user_choice": False},
            language="Italian",
        )
        assert contract["follow_up_required"] is False
        assert contract["action_required"] == "queue_download"
        assert contract["target_unit_labels"] == ["S03E01", "S03E02"]

    @staticmethod
    def _check_success_receipt_truth() -> None:
        ledger = ToolOutcomeLedger()
        ledger.record("queue_download", {"content": {
            "status": "queued",
            "queued_count": 2,
            "download_ids": ["one", "two"],
            "errors": [],
            "partial_failure": False,
            "command_receipt": {"status": "queued", "ok": True, "receipt_persisted": True},
        }})
        assert ledger.partial_queue_failure() is None
        assert ledger.unresolved_queue_failure() is None

    @staticmethod
    async def _check_provider_tool_suffix() -> None:
        registry = ToolRegistry()

        async def inspect_torrent_candidate(**kwargs):
            return {"ok": True, "candidate_id": kwargs.get("candidate_id")}

        registry.register(
            "inspect_torrent_candidate",
            "inspect",
            {"type": "object", "properties": {"candidate_id": {"type": "string"}}, "required": ["candidate_id"]},
            inspect_torrent_candidate,
        )
        message, _ = await ToolCallExecutor(registry).execute_tool_call(
            "inspect_torrent_candidate<|channel|>commentary",
            {"candidate_id": "silo-e1"},
            "round291-tool",
            {"inspect_torrent_candidate"},
        )
        assert message["name"] == "inspect_torrent_candidate"
        assert '"ok":true' in message["content"]

    @staticmethod
    def _check_architecture_contract() -> None:
        architecture = Path("architecture.md").read_text(encoding="utf-8")
        prompt = Path("src/ai/task_prompt_guidance.py").read_text(encoding="utf-8")
        assert "Round 291 — TV release-frontier truth" in architecture
        assert "season_total_episode_count is the catalogue order" in prompt
        assert "without another confirmation" in Path("src/ai/tool_outcome_guard.py").read_text(encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(Round291Checks.run())
    print("ROUND291_SILO_AIRING_FRONTIER_DIRECT_QUEUE_PASS")
