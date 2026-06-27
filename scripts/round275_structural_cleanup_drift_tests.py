#!/usr/bin/env python3
"""Round 275 structural cleanup regression checks.

The checks keep category semantics out of generic planner/reconstruction seams:
- PlanCoordinator must not strip unit coordinates by parsing user prose.
- Definition-backed local object shaping lives behind LocalObjectReconstructor.
- Definition-backed Soulseek source preference is declarative YAML, not a
  hard-coded concrete category id branch.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.plan_coordinator import PlanCoordinator
from src.core.categories.definition_backed import DefinitionBackedCategory
from src.core.models import AgentPlan, Intent, PlanStep


def read(rel: str) -> str:
    """Read a project file as UTF-8 text."""
    return (ROOT / rel).read_text(encoding="utf-8")


def test_plan_coordinator_uses_structured_multi_unit_signal_only() -> None:
    """Natural language alone must not trigger generic unit-coordinate stripping."""
    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Grab the missing episodes from the latest season",
        constraints={},
        steps=[
            PlanStep(
                id="search_latest_guess",
                tool_name="search_media_torrents",
                arguments={"name": "Synthetic Show", "season": 5, "episode": 10, "language": "Italian"},
                depends_on=[],
                success_condition="Candidates returned.",
            )
        ],
    )
    normalized = PlanCoordinator(tool_executor=None, llm_client=None)._normalize_download_plan(
        plan,
        "Please grab the episodes I am missing from the latest season",
        allowed_tool_names={"search_media_torrents"},
    )
    assert normalized.steps[0].arguments["episode"] == 10
    assert "multi_unit_scope" not in normalized.constraints

    structured = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="Grab the requested set of units",
        constraints={"requested_unit_scope": "missing"},
        steps=[
            PlanStep(
                id="search_structured_multi",
                tool_name="search_media_torrents",
                arguments={"name": "Synthetic Show", "season": 5, "episode": 10, "language": "Italian"},
                depends_on=[],
                success_condition="Candidates returned.",
            )
        ],
    )
    normalized_structured = PlanCoordinator(tool_executor=None, llm_client=None)._normalize_download_plan(
        structured,
        "Download the requested units",
        allowed_tool_names={"search_media_torrents"},
    )
    assert normalized_structured.steps[0].arguments["season"] == 5
    assert "episode" not in normalized_structured.steps[0].arguments
    assert normalized_structured.constraints["multi_unit_scope"] == "category_owned_fanout_without_single_unit_guess"


def test_local_object_reconstruction_is_class_owned() -> None:
    """The definition-backed local-model helper should not expose old module-level API functions."""
    source = read("src/core/categories/local_object_reconstruction.py")
    tree = ast.parse(source)
    top_level_functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert "scan_local_object" not in top_level_functions
    assert "enrich_item_payload" not in top_level_functions
    assert "category_units_from_local_object" not in top_level_functions
    assert "class LocalObjectReconstructor" in source
    assert '"total_size_bytes": sum(f.size_bytes for f in facts),' in source
    assert source.count('"total_size_bytes": sum(f.size_bytes for f in facts),') == 1


def test_definition_backed_soulseek_strategy_is_declarative() -> None:
    """Music source preference should come from YAML fields instead of concrete id branches."""
    source = read("src/core/categories/definition_backed.py")
    strategy_block = source[source.index("    def soulseek_source_strategy"):source.index("    def _clean_soulseek_query")]
    assert 'category_id == "music"' not in strategy_block
    assert "source_strategy" in strategy_block

    definition = yaml.safe_load(read("config/category-definitions/music.yaml"))
    category = DefinitionBackedCategory(definition)
    default_strategy = category.soulseek_source_strategy(item_name="Example Album", search_scope="default")
    bundle_strategy = category.soulseek_source_strategy(item_name="Example Artist Complete Discography", search_scope="default")
    assert default_strategy["download_preference"] == "soulseek_first"
    assert bundle_strategy["download_preference"] == "torrent_first"


def test_local_object_reconstructor_shapes_definition_units() -> None:
    """Smoke-check the new class API used by DefinitionBackedCategory."""
    from src.core.categories.local_object_reconstruction import LocalObjectReconstructor

    scanned = SimpleNamespace(
        name="Artist",
        files=[
            SimpleNamespace(
                file_path="/library/Artist/Album/01 - Track One.flac",
                size_bytes=123,
                quality="flac",
                media_probe={"local_scan": {"relative_path": "Album/01 - Track One.flac"}},
            )
        ],
    )
    model = LocalObjectReconstructor.scan("music", scanned)
    units = LocalObjectReconstructor.category_units("music", scanned)
    assert model["model_type"] == "local_music_catalog"
    assert model["album_count"] == 1
    assert units and units[0]["unit_type"] == "track"


def main() -> None:
    """Run the Round 275 regression checks without requiring pytest."""
    tests = [
        test_plan_coordinator_uses_structured_multi_unit_signal_only,
        test_local_object_reconstruction_is_class_owned,
        test_definition_backed_soulseek_strategy_is_declarative,
        test_local_object_reconstructor_shapes_definition_units,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
