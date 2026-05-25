"""Regression tests for queue-time download identity snapshots."""

import pytest

from src.core.models import DownloadImportContext, DownloadItem, DownloadStatus


@pytest.mark.asyncio
async def test_download_import_context_round_trips_and_matches_provider_unit(db):
    """Provider identity must survive persistence and power duplicate checks."""
    context = DownloadImportContext.from_selection(
        category_id="tv",
        item_id="scrubs-2026",
        item_name="Scrubs",
        season=1,
        episode=1,
        language="Italian",
        release_title="Scrubs.2026.S01E01.1080p.WEB-DL-GROUP",
        metadata={
            "provider": "tvdb",
            "provider_id": "452452",
            "provider_media_type": "tv",
            "first_air_date": "2026-02-25",
            "status": "Continuing",
        },
    )
    item = DownloadItem(
        id="identity1",
        item_name="Scrubs",
        magnet="magnet:?xt=urn:btih:identity1",
        status=DownloadStatus.QUEUED,
        category_id="tv",
        item_id="scrubs-2026",
        season=1,
        episode=1,
        import_context=context,
    )

    await db.downloads.upsert_download(item)

    loaded = await db.downloads.get_download("identity1")
    assert loaded is not None
    assert loaded.import_context is not None
    assert loaded.import_context.stable_provider_key == "tvdb:tv:452452"
    assert loaded.import_context.stable_unit_key == "tvdb:tv:452452:official:S1:E1"
    assert loaded.import_context.planning_title == "Scrubs"
    assert loaded.import_context.planning_year == 2026

    matches = await db.downloads.find_existing_by_import_context(context)
    assert [match.id for match in matches] == ["identity1"]


def test_download_import_context_infers_provider_and_year_from_aliases():
    """TMDB/TVDB aliases should become stable identity without title guessing."""
    context = DownloadImportContext(
        category_id="tv",
        tmdb_id=87917,
        provider_media_type="tv",
        title="For All Mankind",
        first_air_date="2019-11-01",
        season=5,
        episode=6,
    )

    assert context.provider == "tmdb"
    assert context.provider_id == "87917"
    assert context.planning_year == 2019
    assert context.stable_unit_key == "tmdb:tv:87917:official:S5:E6"


@pytest.mark.asyncio
async def test_download_import_context_keeps_season_order_namespaces_separate(db):
    """Same provider episode coordinates in different order namespaces are not duplicates."""
    aired = DownloadImportContext(
        category_id="tv",
        provider="tmdb",
        provider_media_type="tv",
        provider_id="123",
        canonical_title="Example",
        season=1,
        episode=1,
        season_order_type="aired",
    )
    dvd = aired.model_copy(update={"season_order_type": "dvd"})
    await db.downloads.upsert_download(
        DownloadItem(
            id="aired1",
            item_name="Example",
            magnet="magnet:?xt=urn:btih:aired1",
            status=DownloadStatus.QUEUED,
            category_id="tv",
            item_id="example",
            season=1,
            episode=1,
            import_context=aired,
        )
    )

    assert await db.downloads.find_existing_by_import_context(dvd) == []


def test_download_import_context_does_not_use_candidate_id_as_provider_id():
    """Torrent candidate identifiers are release-option IDs, not media provider IDs."""
    context = DownloadImportContext.from_selection(
        category_id="tv",
        item_id="example",
        item_name="Example",
        season=1,
        episode=2,
        metadata={"provider": "tmdb", "provider_media_type": "tv", "title": "Example"},
        candidate={"id": "candidate-local-1", "candidate_id": "candidate-local-1", "title": "Example.S01E02"},
    )

    assert context.provider == "tmdb"
    assert context.provider_id == ""
    assert context.stable_provider_key == ""


@pytest.mark.asyncio
async def test_item_level_context_does_not_block_episode_download(db):
    """A show-level context is not enough to call every episode a duplicate."""
    show_context = DownloadImportContext(
        category_id="tv",
        provider="tmdb",
        provider_media_type="tv",
        provider_id="999",
        canonical_title="Example",
    )
    episode_context = show_context.model_copy(update={"season": 1, "episode": 2})
    await db.downloads.upsert_download(
        DownloadItem(
            id="show1",
            item_name="Example",
            magnet="magnet:?xt=urn:btih:show1",
            status=DownloadStatus.QUEUED,
            category_id="tv",
            item_id="example",
            import_context=show_context,
        )
    )

    assert await db.downloads.find_existing_by_import_context(episode_context) == []


@pytest.mark.asyncio
async def test_season_context_blocks_episode_download_for_same_season(db):
    """A season-pack context should still dedupe individual episodes it covers."""
    season_context = DownloadImportContext(
        category_id="tv",
        provider="tmdb",
        provider_media_type="tv",
        provider_id="999",
        canonical_title="Example",
        season=1,
    )
    episode_context = season_context.model_copy(update={"episode": 2})
    await db.downloads.upsert_download(
        DownloadItem(
            id="season1",
            item_name="Example",
            magnet="magnet:?xt=urn:btih:season1",
            status=DownloadStatus.QUEUED,
            category_id="tv",
            item_id="example",
            season=1,
            import_context=season_context,
        )
    )

    matches = await db.downloads.find_existing_by_import_context(episode_context)
    assert [match.id for match in matches] == ["season1"]
