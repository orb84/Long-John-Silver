#!/usr/bin/env python3
"""Round 267 regression checks for TV background consent and search scope."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.categories.tv import TvShowCategory
from src.core.models import NormalizedTorrentCandidate


def test_nested_visible_off_wins() -> None:
    category = TvShowCategory()
    item = SimpleNamespace(key="Rooster", auto_download=True)
    changed = category.reconcile_settings_item_with_persisted_state(
        item,
        {"properties": {"auto_download": False}},
        None,
    )
    assert changed is True
    assert item.auto_download is False


def test_missing_persisted_confirmation_is_not_consent() -> None:
    category = TvShowCategory()
    item = SimpleNamespace(key="The Wire", auto_download=True)
    changed = category.reconcile_settings_item_with_persisted_state(item, {"key": "The Wire"}, None)
    assert changed is True
    assert item.auto_download is False


def test_manual_download_never_enables_tv_automation() -> None:
    category = TvShowCategory()
    settings = SimpleNamespace(tracked_items=SimpleNamespace(items=[SimpleNamespace(key="Star City", display_name="Star City", item_type="tv", auto_download=False)]))
    settings_manager = SimpleNamespace(settings=settings, save=lambda *_: (_ for _ in ()).throw(AssertionError("save should not be called")))
    changed = asyncio.run(category.maybe_enable_auto_download_after_user_download(
        item_id="Star City",
        import_context=SimpleNamespace(season=1, unit_descriptor={"coordinates": {"season": 1, "episode": 3}}),
        settings_manager=settings_manager,
        context=None,
    ))
    assert changed is False
    assert settings.tracked_items.items[0].auto_download is False


def test_exact_episode_candidate_filter_rejects_wrong_episode_and_wrong_show() -> None:
    category = TvShowCategory()
    candidates = [
        NormalizedTorrentCandidate(title="Widows Bay S01E06 1080p ITA", source="test", magnet="magnet:?xt=urn:btih:1", magnet_available=True, season=1, episode=6, quality_score=1.0, seeders=50),
        NormalizedTorrentCandidate(title="Other Show S01E01 Widows Bay 1080p ITA", source="test", magnet="magnet:?xt=urn:btih:2", magnet_available=True, season=1, episode=1, quality_score=1.0, seeders=50),
        NormalizedTorrentCandidate(title="Widows Bay S01E01 1080p ITA", source="test", magnet="magnet:?xt=urn:btih:3", magnet_available=True, season=1, episode=1, quality_score=1.0, seeders=8),
        NormalizedTorrentCandidate(
            title="Widows Bay S01 1080p ITA",
            source="test",
            magnet="magnet:?xt=urn:btih:4",
            magnet_available=True,
            is_bundle=True,
            bundle_scope="season",
            bundle_context={"pack_type": "single_season", "scope": "season", "season": 1},
            quality_score=1.0,
            seeders=250,
        ),
    ]
    filtered = category.filter_torrent_candidates_for_unit(
        candidates,
        item_id="Widows Bay",
        item_display_name="Widows Bay",
        unit_key="S01E01",
        unit_request={"label": "S01E01"},
        preferred_language="Italian",
    )
    titles = {candidate.title for candidate in filtered}
    assert "Widows Bay S01E06 1080p ITA" not in titles
    assert "Other Show S01E01 Widows Bay 1080p ITA" not in titles
    assert "Widows Bay S01E01 1080p ITA" in titles
    assert "Widows Bay S01 1080p ITA" in titles


def test_broad_tv_search_prefers_bundles_when_available() -> None:
    category = TvShowCategory()
    candidates = [
        NormalizedTorrentCandidate(title="Widows Bay S01E07 1080p ITA", source="test", magnet="magnet:?xt=urn:btih:a", magnet_available=True, season=1, episode=7, quality_score=1.0, seeders=100),
        NormalizedTorrentCandidate(
            title="Widows Bay S01 1080p ITA",
            source="test",
            magnet="magnet:?xt=urn:btih:b",
            magnet_available=True,
            is_bundle=True,
            bundle_scope="season",
            bundle_context={"pack_type": "single_season", "scope": "season", "season": 1},
            quality_score=1.0,
            seeders=300,
        ),
    ]
    filtered = category.filter_torrent_candidates_for_unit(
        candidates,
        item_id="Widows Bay",
        item_display_name="Widows Bay",
        unit_key="",
        unit_request={},
        preferred_language="Italian",
    )
    assert [candidate.title for candidate in filtered] == ["Widows Bay S01 1080p ITA"]


def test_source_contains_downloader_hard_creation_gate_and_rss_ignore() -> None:
    downloader = (ROOT / "src/core/downloader.py").read_text()
    assert "_can_create_background_download_row" in downloader
    assert "Background download blocked by category policy" in downloader
    assert "_reconcile_tracked_item_download_policy" in downloader
    workflows = (ROOT / "src/core/categories/tv_workflows.py").read_text()
    assert "background_search_disabled" in workflows
    scheduler = (ROOT / "src/core/scheduler.py").read_text()
    assert "stale feeds for this item will not be retained" in scheduler


def main() -> None:
    test_nested_visible_off_wins()
    test_missing_persisted_confirmation_is_not_consent()
    test_manual_download_never_enables_tv_automation()
    test_exact_episode_candidate_filter_rejects_wrong_episode_and_wrong_show()
    test_broad_tv_search_prefers_bundles_when_available()
    test_source_contains_downloader_hard_creation_gate_and_rss_ignore()
    print("ROUND267_TV_BACKGROUND_CONSENT_AND_SEARCH_SCOPE_TESTS_PASS")


if __name__ == "__main__":
    main()
