#!/usr/bin/env python3
"""Round 280 architecture/prompt drift checks.

These tests protect the cleanup seam around generic research metadata tools and
active category prompt examples. They are intentionally static/smoke checks so
future cleanup rounds can run them without external services.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _top_level_function_names(relative: str) -> list[str]:
    tree = ast.parse(_read(relative), filename=relative)
    return [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_metadata_tool_support_has_class_owned_argument_helpers() -> None:
    assert _top_level_function_names("src/ai/tools/metadata_lookup_support.py") == []
    text = _read("src/ai/tools/metadata_lookup_support.py")
    assert "class MetadataLookupArgumentNormalizer" in text
    assert "query = MetadataLookupArgumentNormalizer.resolve_title(arguments)" in text
    assert "MetadataLookupArgumentNormalizer.safe_int(arguments.get(\"season\"))" in text


def test_research_tool_does_not_duplicate_metadata_argument_helpers() -> None:
    assert _top_level_function_names("src/ai/tools/research.py") == []
    text = _read("src/ai/tools/research.py")
    assert "def _resolve_title" not in text
    assert "def _safe_int" not in text
    assert "MetadataLookupArgumentNormalizer.resolve_title(arguments)" in text
    assert "MetadataLookupArgumentNormalizer.safe_int(" in text


def test_metadata_lookup_request_smoke_keeps_coordinate_behavior() -> None:
    from src.ai.tools.metadata_lookup_support import MetadataLookupArgumentNormalizer, MetadataLookupRequest

    request = MetadataLookupRequest.from_arguments({
        "query": "Series Title episode 3",
        "media_type": "tv",
        "season": "2",
    })
    assert not isinstance(request, dict)
    assert request.query == "Series Title episode 3"
    assert request.season == 2
    assert request.episode == 3
    assert request.include_episodes is True
    assert MetadataLookupArgumentNormalizer.resolve_title({"title": " Series Title "}) == "Series Title"


def test_tv_definition_examples_are_not_log_specific_titles() -> None:
    text = _read("config/category-definitions/tv.yaml")
    assert "search_examples:" in text
    assert "Series Title S05 ITA 1080p" in text
    assert "Series Title S01E01-06 ITA" in text
    for title in ("Widows Bay", "Yellowstone", "Silicon Valley", "Gomorra", "Star City"):
        assert title not in text


def test_media_probe_policy_lives_on_named_collaborators() -> None:
    text = _read("src/core/categories/media_probe.py")
    for class_name in (
        "MediaProbeValueParser",
        "MediaProbeResolution",
        "MediaProbeLanguageNormalizer",
        "MediaProbeService",
    ):
        assert f"class {class_name}" in text
    module_preamble = text.split("class MediaProbeService", 1)[0]
    assert "_probe_semaphore:" not in module_preamble
    assert "_cache_loaded = False" not in module_preamble
    assert "return _DEFAULT_MEDIA_PROBE_SERVICE.parse_probe_payload" in text
    assert "return MediaProbeLanguageNormalizer.from_tags(tags)" in text


def test_generic_intent_router_uses_bundle_wording_not_tv_pack_wording() -> None:
    text = _read("src/ai/intent_router.py")
    assert "season pack" not in text
    assert "bundle/range" in text


def main() -> None:
    tests = [
        test_metadata_tool_support_has_class_owned_argument_helpers,
        test_research_tool_does_not_duplicate_metadata_argument_helpers,
        test_metadata_lookup_request_smoke_keeps_coordinate_behavior,
        test_tv_definition_examples_are_not_log_specific_titles,
        test_media_probe_policy_lives_on_named_collaborators,
        test_generic_intent_router_uses_bundle_wording_not_tv_pack_wording,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ROUND280_METADATA_PROMPT_DRIFT_TESTS_PASS")


if __name__ == "__main__":
    main()
