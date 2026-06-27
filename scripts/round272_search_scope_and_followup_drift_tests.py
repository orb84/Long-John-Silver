#!/usr/bin/env python3
"""Round 272 regressions for search-scope and follow-up parser drift."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.download_context_policy import DownloadContextPolicy
from src.ai.plan_coordinator import PlanCoordinator
from src.core.categories.search_scope import SearchScopePolicy
from src.core.models import AgentPlan, Intent, PlanStep


class _NoopToolExecutor:
    pass


def test_search_scope_policy_centralizes_legacy_aliases() -> None:
    assert SearchScopePolicy.normalize("season_pack_preferred") == SearchScopePolicy.BUNDLE_PREFERRED
    assert SearchScopePolicy.normalize("pack_only") == SearchScopePolicy.BUNDLE_ONLY
    assert SearchScopePolicy.normalize("individual_units_only") == SearchScopePolicy.INDIVIDUAL_UNITS_ONLY
    assert SearchScopePolicy.normalize("latest season") == SearchScopePolicy.DEFAULT


def test_plan_coordinator_no_longer_parses_pack_scope_from_phrases() -> None:
    coord = PlanCoordinator(_NoopToolExecutor(), llm_client=None, settings=None)
    unstructured = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="download the latest season as a complete pack",
        steps=[PlanStep(id="search", tool_name="search_media_torrents", arguments={"name": "Example"})],
    )
    assert coord._requested_pack_scope("download the latest season as a complete pack", unstructured) is None

    structured = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="download a bundle",
        steps=[PlanStep(id="search", tool_name="search_media_torrents", arguments={"name": "Example", "search_scope": "season_pack_preferred"})],
    )
    assert coord._requested_pack_scope("download a bundle", structured) == SearchScopePolicy.BUNDLE_PREFERRED


def test_download_context_policy_uses_stable_handles_not_phrase_lists() -> None:
    assert DownloadContextPolicy.should_suppress_pending_candidates(
        "please grab me A Knight of the Seven Kingdoms in italian",
        Intent.DOWNLOAD,
    )
    assert DownloadContextPolicy.should_suppress_pending_candidates(
        "queue the first one",
        Intent.DOWNLOAD,
    )
    assert not DownloadContextPolicy.should_suppress_pending_candidates(
        "candidate a84e9cc9bbf158cf looks good",
        Intent.DOWNLOAD,
    )
    assert not DownloadContextPolicy.should_suppress_pending_candidates(
        "#1 720p please",
        Intent.DOWNLOAD,
    )


def test_legacy_scope_literals_do_not_leak_outside_policy() -> None:
    allowed = {Path("src/core/categories/search_scope.py")}
    banned = {"season_pack_preferred", "season_pack_only", "pack_preferred", "pack_only"}
    findings: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        if rel in allowed:
            continue
        for token in banned:
            if token in text and "reject_episode_for_pack_only" not in text:
                findings.append(f"{rel}: {token}")
    assert not findings, "legacy search-scope aliases must stay centralized: " + "; ".join(findings)


def main() -> None:
    test_search_scope_policy_centralizes_legacy_aliases()
    test_plan_coordinator_no_longer_parses_pack_scope_from_phrases()
    test_download_context_policy_uses_stable_handles_not_phrase_lists()
    test_legacy_scope_literals_do_not_leak_outside_policy()
    print("ROUND272_SEARCH_SCOPE_AND_FOLLOWUP_DRIFT_TESTS_PASS")


if __name__ == "__main__":
    main()
