#!/usr/bin/env python3
"""Round 281 regressions for one-word TV title identity during torrent search."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.categories.title_authority import CategoryTitleAuthority
from src.core.categories.tv import TvShowCategory
from src.core.categories.tv_agent import TvAgentSearchMixin


@dataclass
class FakeResult:
    title: str
    magnet: str = "magnet:?xt=urn:btih:test"
    source: str = "test"
    seeders: int = 10
    size: str = "1 GB"
    size_bytes: int = 1_000_000_000
    quality_score: float = 0.6


def _episode_payload(candidate_id: str, title: str, *, matches_item: bool | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "candidate_id": candidate_id,
        "title": title,
        "seeders": 10,
        "languages": ["Italian"],
        "resolution": "1080p",
        "quality_score": 0.6,
        "unit_descriptor": {
            "granularity": "episode",
            "stable_key": "S01E06",
            "label": "S01E06",
            "sort_key": [1, 6],
            "coordinates": {"season": 1, "episode": 6},
        },
    }
    if matches_item is not None:
        payload["title_identity"] = {"matches_item": matches_item, "series_scope": title.split(" S01", 1)[0]}
    return payload


def test_one_word_authoritative_alias_is_not_substring_match() -> None:
    assert CategoryTitleAuthority.matches_any_alias("Beacon", ["Beacon"])
    assert CategoryTitleAuthority.matches_any_alias("Beacon 2026", ["Beacon"])
    assert not CategoryTitleAuthority.matches_any_alias("Beacon Runner", ["Beacon"])
    assert not CategoryTitleAuthority.matches_any_alias("Atomic Beacon", ["Beacon"])


def test_one_word_tv_title_fallback_rejects_longer_series_title() -> None:
    assert TvAgentSearchMixin._title_matches_requested_series("Beacon.S01E06.1080p.WEB-DL", "Beacon")
    assert TvAgentSearchMixin._title_matches_requested_series("Beacon.2026.1x06.1080p.WEB-DL", "Beacon")
    assert not TvAgentSearchMixin._title_matches_requested_series("Beacon.Runner.S01E06.1080p.WEB-DL", "Beacon")
    assert not TvAgentSearchMixin._title_matches_requested_series("Atomic.Beacon.S01E06.1080p.WEB-DL", "Beacon")


def test_exact_episode_decision_rejects_same_episode_from_different_series() -> None:
    tv = TvShowCategory()
    item = SimpleNamespace(key="Beacon", display_name="Beacon", metadata={})
    accepted, reason = tv._exact_label_decision_reason(FakeResult("Beacon.Runner.S01E06.1080p.WEB-DL"), item=item, label="S01E06")
    assert accepted is False
    assert reason == "reject_title_mismatch"
    accepted, reason = tv._exact_label_decision_reason(FakeResult("Beacon.S01E06.1080p.WEB-DL"), item=item, label="S01E06")
    assert accepted is True
    assert reason == "accept_exact_episode"


def test_payload_filter_rejects_title_identity_mismatch_even_with_matching_episode_descriptor() -> None:
    tv = TvShowCategory()
    accepted = _episode_payload("valid", "Beacon.S01E06.1080p.WEB-DL", matches_item=True)
    mismatch = _episode_payload("wrong-series", "Beacon.Runner.S01E06.1080p.WEB-DL", matches_item=False)
    filtered = tv.filter_agent_candidate_payloads_for_request(
        [mismatch, accepted],
        season=1,
        episode=6,
        search_scope="default",
        language="Italian",
    )
    assert [row["candidate_id"] for row in filtered] == ["valid"]


def test_candidate_annotation_publishes_title_identity_for_later_workspace_filter() -> None:
    tv = TvShowCategory()
    item = SimpleNamespace(key="Beacon", display_name="Beacon", metadata={})
    payload = _episode_payload("wrong-series", "Beacon.Runner.S01E06.1080p.WEB-DL")
    annotated = tv.annotate_agent_search_candidate_payload(
        payload,
        FakeResult("Beacon.Runner.S01E06.1080p.WEB-DL"),
        item=item,
        unit_label="S01E06",
        season=1,
        episode=6,
        search_scope="default",
        response_facts={},
        context=None,
    )
    assert annotated is payload
    assert payload["title_identity"]["matches_item"] is False
    assert payload["auto_queue_allowed"] is False
    filtered = tv.filter_agent_candidate_payloads_for_request([payload], season=1, episode=6, search_scope="default", language="Italian")
    assert filtered == []


def main() -> None:
    test_one_word_authoritative_alias_is_not_substring_match()
    test_one_word_tv_title_fallback_rejects_longer_series_title()
    test_exact_episode_decision_rejects_same_episode_from_different_series()
    test_payload_filter_rejects_title_identity_mismatch_even_with_matching_episode_descriptor()
    test_candidate_annotation_publishes_title_identity_for_later_workspace_filter()
    print("round281_tv_title_identity_guard_tests: OK")


if __name__ == "__main__":
    main()
