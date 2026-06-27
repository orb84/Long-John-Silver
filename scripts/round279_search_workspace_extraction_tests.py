#!/usr/bin/env python3
"""Round 279 regression checks for search workspace extraction and cleanup."""
from __future__ import annotations

from pathlib import Path
import ast
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tools.search_workspace import (
    CandidateBundlePolicy,
    SearchQualityChoicePolicy,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_scheduling_no_longer_owns_workspace_private_helpers() -> None:
    source = (ROOT / "src/ai/tools/scheduling.py").read_text()
    tree = ast.parse(source)
    top_level_defs = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    require(top_level_defs == [], f"scheduling.py should not expose module-level workspace helpers, found {top_level_defs}")
    forbidden = [
        "def _quality_choice_policy",
        "def _annotate_selection_policy",
        "def _build_batch_recommendation",
        "def _candidate_picker_rows",
        "def _search_result_next_actions",
        "def _log_search_media_torrents_audit",
        "def _batch_candidate_score",
    ]
    for needle in forbidden:
        require(needle not in source, f"workspace helper still lives in scheduling.py: {needle}")
    require("SearchWorkspaceFormatter.format_size" in source, "scheduling tool should delegate workspace formatting")
    require("SearchBatchRecommendationBuilder.build" in source, "scheduling tool should delegate batch policy")


def test_cache_candidate_payload_has_no_duplicate_size_key() -> None:
    source = (ROOT / "src/ai/tools/scheduling.py").read_text()
    start = source.index("cache_candidates.append({")
    end = source.index("})", start)
    block = source[start:end]
    require(block.count('"size_bytes": c.get("size_bytes")') == 1, "cache candidate payload must not repeat size_bytes")


def test_generic_workspace_does_not_parse_tv_coordinates_for_quality_identity() -> None:
    source = (ROOT / "src/ai/tools/search_workspace.py").read_text()
    for needle in ('coords.get("season")', 'coords.get("episode")', 'candidate.get("season")', 'candidate.get("episode")'):
        require(needle not in source, f"generic workspace must not parse TV coordinates: {needle}")
    candidate = {"candidate_id": "c1", "title": "Some Result S01E01"}
    require(CandidateBundlePolicy.logical_unit_key(candidate) == "", "missing descriptor should not invent a category unit key")


def test_quality_choice_still_works_without_category_descriptor() -> None:
    candidates = [
        {
            "candidate_id": "large",
            "title": "Example 1080p high bitrate",
            "resolution": "1080p",
            "estimated_bitrate_kbps": 12000,
            "size_bytes": 4_700_000_000,
            "seeders": 80,
            "auto_queue_allowed": True,
        },
        {
            "candidate_id": "compact",
            "title": "Example 1080p compact bitrate",
            "resolution": "1080p",
            "estimated_bitrate_kbps": 8900,
            "size_bytes": 3_450_000_000,
            "seeders": 40,
            "auto_queue_allowed": True,
        },
    ]
    policy = SearchQualityChoicePolicy.evaluate(candidates, {})
    require(policy.get("requires_user_choice") is True, f"expected quality choice for same-result alternatives, got {policy}")
    require(set(policy.get("candidate_ids") or []) == {"large", "compact"}, f"both candidates should be exposed, got {policy}")


def test_generic_prompt_text_uses_bundle_unit_language() -> None:
    scheduling = (ROOT / "src/ai/tools/scheduling.py").read_text()
    workspace = (ROOT / "src/ai/tools/search_workspace.py").read_text()
    combined = scheduling + "\n" + workspace
    for phrase in (
        "season-pack or quality-choice",
        "missing episodes unless",
        "Do not queue only the first episode",
        "movie/TV language preferences",
        "English/Italian/etc.",
    ):
        require(phrase not in combined, f"generic scheduling/workspace text still has category-shaped wording: {phrase}")
    require("requested-bundle candidate" in combined, "generic replacement wording should mention requested bundles")


def main() -> None:
    test_scheduling_no_longer_owns_workspace_private_helpers()
    test_cache_candidate_payload_has_no_duplicate_size_key()
    test_generic_workspace_does_not_parse_tv_coordinates_for_quality_identity()
    test_quality_choice_still_works_without_category_descriptor()
    test_generic_prompt_text_uses_bundle_unit_language()
    print("ROUND279_SEARCH_WORKSPACE_EXTRACTION_TESTS_PASS")


if __name__ == "__main__":
    main()
