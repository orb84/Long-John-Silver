#!/usr/bin/env python3
"""Round 250 regressions for torrent candidate provenance, ranking, and file progress UI.

The failure covered here was a season-pack request that surfaced weak dual-audio
rows and wrong-season single episodes, then showed all expanded torrent files at
0% despite parent progress.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.categories.tv import TvShowCategory
from src.web.view_models.download_view_model import DownloadViewModelBuilder
from src.ai.tools.search_workspace import SearchQualityChoicePolicy
from src.ai.tools.search_workspace import SelectionPolicyAnnotator


@dataclass
class FakeResult:
    title: str
    size_bytes: int
    magnet: str = "magnet:?xt=urn:btih:deadbeef"
    source: str = "test"
    seeders: int = 1
    size: str = "1"
    quality_score: float = 0.0


class FakeDownloadItem:
    def __init__(self) -> None:
        self.id = "download-1"
        self.files = [
            {"file_index": 0, "file_path": "A.mkv", "size": 1000, "downloaded_bytes": 0, "progress": 0.0, "priority": 4, "status": "downloading"},
            {"file_index": 1, "file_path": "B.mkv", "size": 1000, "downloaded_bytes": 0, "progress": 0.0, "priority": 4, "status": "downloading"},
        ]

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": "downloading",
            "progress": 0.5,
            "downloaded_bytes": 1000,
            "total_size": 2000,
            "files": [dict(row) for row in self.files],
        }


class FakeDownloader:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def get_file_progress(self, _download_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]


def _season_candidate(candidate_id: str, stable_key: str, seeders: int, languages: list[str] | None, bitrate: int) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "title": f"Example {stable_key} pack",
        "seeders": seeders,
        "languages": languages or [],
        "resolution": "1080p",
        "estimated_bitrate_kbps": bitrate,
        "size_bytes": 10_000_000_000,
        "auto_queue_allowed": True,
        "is_bundle": stable_key == "S01",
        "bundle_scope": "season" if stable_key == "S01" else None,
        "unit_descriptor": {
            "granularity": "season" if stable_key.startswith("S") and "E" not in stable_key else "episode",
            "stable_key": stable_key,
            "label": stable_key,
            "sort_key": [int(stable_key[1:3]), 0],
            "coordinates": {"season": int(stable_key[1:3])},
        },
    }


def test_adjacent_range_size_estimation_is_per_episode_not_whole_pack() -> None:
    tv = TvShowCategory()
    result = FakeResult(
        "For All ManKind S01e01 10 720p Ita Eng Sub Ita Eng byMetalh",
        size_bytes=15_193_446_400,
    )
    facts = tv.search_candidate_quality_facts(result)
    assert 1_300 <= facts["per_episode_size_mb"] <= 1_600, facts
    assert 3_000 <= facts["estimated_bitrate_kbps"] <= 4_500, facts


def test_category_payload_filter_removes_wrong_season_rows_after_projection() -> None:
    tv = TvShowCategory()
    candidates = [
        _season_candidate("s01", "S01", 40, ["English"], 3500),
        _season_candidate("s04", "S04E01", 900, ["English"], 5000),
        _season_candidate("s01e01", "S01E01", 800, ["English"], 4500),
    ]
    filtered = tv.filter_agent_candidate_payloads_for_request(
        candidates,
        season=1,
        episode=None,
        search_scope="bundle_preferred",
        language="English",
    )
    ids = [row["candidate_id"] for row in filtered]
    assert "s04" not in ids
    assert ids[0] == "s01"
    assert "s01e01" in ids
    assert any("individual episode fallback" in " ".join(row.get("selection_warnings") or []) for row in filtered if row["candidate_id"] == "s01e01")


def test_quality_options_do_not_promote_low_seed_dual_audio_over_healthy_language_satisfying_rows() -> None:
    low_seed_dual = _season_candidate("dual-low-seed", "S01", 6, ["Italian", "English"], 13_000)
    high_seed_unknown = _season_candidate("unknown-high-seed", "S01", 900, [], 4_000)
    mid_seed_english = _season_candidate("english-mid-seed", "S01", 300, ["English"], 5_000)
    candidates = [low_seed_dual, high_seed_unknown, mid_seed_english]
    SelectionPolicyAnnotator.annotate(candidates, preferred_language="English")
    policy = SearchQualityChoicePolicy.evaluate(candidates, {})
    assert policy["requires_user_choice"] is True
    assert policy["candidate_ids"][0] in {"english-mid-seed", "unknown-high-seed"}
    assert policy["candidate_ids"].index("dual-low-seed") > 0


def test_multi_file_view_uses_parent_progress_when_file_rows_are_blank() -> None:
    view = DownloadViewModelBuilder(FakeDownloader()).build(FakeDownloadItem())
    progresses = [row["progress"] for row in view["files"]]
    assert progresses == [0.5, 0.5]
    assert [row["downloaded_bytes"] for row in view["files"]] == [500, 500]
    assert all(row.get("progress_estimated") is True for row in view["files"])


def test_exact_file_cache_progress_wins_over_parent_estimate() -> None:
    cache = [{"file_index": 0, "downloaded": 250, "progress": 0.25}, {"file_index": 1, "downloaded": 0, "progress": 0.0}]
    view = DownloadViewModelBuilder(FakeDownloader(cache)).build(FakeDownloadItem())
    assert view["files"][0]["progress"] == 0.25
    assert view["files"][0]["downloaded_bytes"] == 250
    assert not view["files"][0].get("progress_estimated")


def main() -> None:
    test_adjacent_range_size_estimation_is_per_episode_not_whole_pack()
    test_category_payload_filter_removes_wrong_season_rows_after_projection()
    test_quality_options_do_not_promote_low_seed_dual_audio_over_healthy_language_satisfying_rows()
    test_multi_file_view_uses_parent_progress_when_file_rows_are_blank()
    test_exact_file_cache_progress_wins_over_parent_estimate()
    print("round250_torrent_language_seed_scope_progress_tests: OK")


if __name__ == "__main__":
    main()
