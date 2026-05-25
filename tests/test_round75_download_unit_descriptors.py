"""Round 75 audit tests for download unit descriptor boundaries."""

from __future__ import annotations

from pathlib import Path

from src.core.categories.tv import TvShowCategory


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_tv_category_builds_unit_descriptor_and_legacy_coordinates() -> None:
    """TV may expose legacy coordinates, but the descriptor is the queue contract."""
    tv = TvShowCategory()

    class Item:
        key = "Pluribus"

    result = type("R", (), {"title": "Pluribus.S01E03.1080p"})()
    descriptor = tv.unit_descriptor_from_search_result(result, Item(), "S01E03")

    assert descriptor["granularity"] == "episode"
    assert descriptor["stable_key"] == "S01E03"
    assert descriptor["coordinates"] == {"season": 1, "episode": 3}
    assert tv.download_coordinates_from_search_result(result, Item(), "S01E03") == {"season": 1, "episode": 3}


def test_search_results_and_queue_cache_carry_unit_descriptors() -> None:
    """Search tool caching should preserve category-owned descriptors for queueing."""
    source = (project_root() / "src/ai/tools/scheduling.py").read_text(encoding="utf-8")
    assert '"unit_descriptor": c.get("unit_descriptor") or {}' in source
    assert "category.batch_group_for_candidate" in source
    assert "label_bits.append" not in source


def test_queue_download_uses_descriptors_for_ordering_fallbacks_and_import_context() -> None:
    """Queueing must not sort/fallback solely by season/episode coordinates."""
    source = (project_root() / "src/ai/tools/queue_download_support.py").read_text(encoding="utf-8")
    assert "sort_cached_download_candidates" in source
    assert "candidates_represent_same_unit" in source
    assert "unit_descriptor=unit_descriptor" in source
    assert "Return candidates sorted by season/episode" not in source


def test_seed_in_place_pathing_delegates_to_category() -> None:
    """The sharing mixin must not create TV season folders in generic code."""
    source = (project_root() / "src/core/downloader_sharing_mixin.py").read_text(encoding="utf-8")
    assert "sharing_save_path_for_item" in source
    assert "SeasonFolderLayout" not in source
    assert "Season {int" not in source


def test_import_context_duplicate_overlap_prefers_descriptor_stable_keys() -> None:
    """Repository duplicate checks should compare descriptor stable keys."""
    source = (project_root() / "src/core/repositories/download.py").read_text(encoding="utf-8")
    assert "wanted_has_descriptor" in source
    assert "other_has_descriptor" in source
    assert "return wanted.stable_unit_key == other.stable_unit_key" in source
