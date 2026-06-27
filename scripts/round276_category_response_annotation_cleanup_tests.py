#!/usr/bin/env python3
"""Round 276 regressions for category-owned search response annotations.

The scheduler may pass structured tool arguments around, but it must not parse
TV release range notation, construct TV labels, or decide season coverage by
itself.  Categories own those facts through explicit hooks.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.categories.tv import TvShowCategory
from src.core.scheduler_services import SchedulerServiceContext, SchedulerTorrentSearchService


def test_scheduler_no_longer_owns_tv_range_coverage_parsing() -> None:
    source = (ROOT / "src/core/scheduler_services.py").read_text()
    forbidden = [
        "_expected_episode_count_from_query_summary",
        "_annotate_requested_season_coverage",
        "full_requested_season",
        "partial_requested_season",
        "S0*{int(season)}E0*1",
    ]
    for token in forbidden:
        assert token not in source, f"scheduler still owns TV coverage token: {token}"


def test_base_category_exposes_response_annotation_hooks() -> None:
    source = (ROOT / "src/core/categories/base_contract.py").read_text()
    assert "def agent_search_response_facts" in source
    assert "def annotate_agent_search_candidate_payload" in source
    assert "def agent_unit_label_from_args" in source
    assert "Core search services should not parse category release names" in source


def test_tv_category_owns_expected_count_and_coverage_annotation() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Example Show")
    query = "Example Show S01E01-E08 | Example Show S01 | Example Show Season 1"
    facts = tv.agent_search_response_facts(item=item, season=1, query_summary=query)
    assert facts == {"expected_episode_count": 8}
    payload = {
        "bundle_context": {
            "scope": "episode_range",
            "season": 1,
            "start": 1,
            "end": 8,
            "unit_count": 8,
        }
    }
    annotated = tv.annotate_agent_search_candidate_payload(
        payload,
        SimpleNamespace(title="Example Show S01E01-E08"),
        item=item,
        season=1,
        response_facts=facts,
    )
    assert annotated["expected_episode_count"] == 8
    assert annotated["requested_season_coverage"] == "full_requested_season"
    assert annotated["coverage_note"].startswith("covers S01E01-E08")


def test_definition_backed_local_object_properties_are_builder_owned() -> None:
    source = (ROOT / "src/core/categories/local_object_reconstruction.py").read_text()
    assert "def enrich_properties" in source
    assert "_add_category_properties" not in source
    assert "if category_id ==" not in source
    assert "elif category_id ==" not in source
    definition_backed = (ROOT / "src/core/categories/definition_backed.py").read_text()
    assert 'self.category_id in {"music", "audiobooks", "ebooks"}' not in definition_backed
    assert "local_model_type" in definition_backed


def test_scheduler_unit_labels_are_category_owned() -> None:
    ctx = SchedulerServiceContext(
        settings_manager=None, db=None, downloader=None, pipeline=None, aggregator=None, categories=None
    )
    service = SchedulerTorrentSearchService(ctx)

    class VolumeCategory:
        def agent_unit_label_from_args(self, *, season=None, episode=None, search_scope=None):  # noqa: ANN001
            return f"Volume {season}" if season else None

    assert service._request_unit_label(VolumeCategory(), 2, None) == "Volume 2"
    assert service._request_unit_label(None, 1, 1) is None


def main() -> None:
    test_scheduler_no_longer_owns_tv_range_coverage_parsing()
    test_base_category_exposes_response_annotation_hooks()
    test_tv_category_owns_expected_count_and_coverage_annotation()
    test_definition_backed_local_object_properties_are_builder_owned()
    test_scheduler_unit_labels_are_category_owned()
    print("round276_category_response_annotation_cleanup_tests: OK")


if __name__ == "__main__":
    main()
