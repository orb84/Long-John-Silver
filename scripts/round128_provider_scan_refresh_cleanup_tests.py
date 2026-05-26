#!/usr/bin/env python3
"""Round 128 regression tests for provider split, local object scans, and refresh policy."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_provider_adapters_are_split_from_resolver() -> None:
    resolver = (ROOT / "src/integrations/category_metadata.py").read_text(encoding="utf-8")
    registry = (ROOT / "src/integrations/metadata_providers/registry.py").read_text(encoding="utf-8")
    music = (ROOT / "src/integrations/metadata_providers/music.py").read_text(encoding="utf-8")
    books = (ROOT / "src/integrations/metadata_providers/books.py").read_text(encoding="utf-8")
    require("class CategoryMetadataResolver" in resolver, "resolver class should remain the orchestration boundary")
    require("provider_method" in resolver and "ProviderRateLimiter" in resolver, "resolver should orchestrate adapters/cache/rate-limit state")
    require("musicbrainz.org/ws/2/release" not in resolver, "provider URLs should not live in resolver")
    require("musicbrainz.org/ws/2/release" in music, "music provider URL should live in music adapter")
    require("openlibrary.org/search.json" in books and "librivox.org/api/feed/audiobooks" in books, "book/audio-book URLs should live in book adapters")
    require("_PROVIDER_PROFILES" in registry, "provider profiles should remain declarative data")


async def test_music_scan_reconstructs_album_track_units() -> None:
    from src.core.categories.registry import CategoryRegistry

    registry = CategoryRegistry()
    registry.register_defaults()
    music = registry.get("music")
    require(music is not None, "music category should register")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        album = root / "Blur" / "1994 - Parklife"
        album.mkdir(parents=True)
        (album / "01 Girls & Boys.flac").write_bytes(b"flac")
        (album / "02 Tracy Jacks.flac").write_bytes(b"flac")
        items = await music.scan(str(root))
        require(len(items) == 1, "music top-level artist/catalog should scan as one item")
        local = items[0].local_object_model
        require(local.get("model_type") == "local_music_catalog", "music scan should expose local music catalog model")
        require(local.get("album_count") == 1 and local.get("track_count") == 2, "music local model should reconstruct album/track counts")
        units = music.library_units_from_scan(items[0])
        require({u.get("unit_type") for u in units} == {"track"}, "music units should be track units, not generic files")
        require((units[0].get("properties") or {}).get("album") == "1994 - Parklife", "track unit should preserve album folder evidence")


async def test_ebook_and_audiobook_scans_reconstruct_domain_units() -> None:
    from src.core.categories.registry import CategoryRegistry

    registry = CategoryRegistry()
    registry.register_defaults()
    ebooks = registry.get("ebooks")
    audiobooks = registry.get("audiobooks")
    require(ebooks is not None and audiobooks is not None, "book categories should register")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        author = root / "Ursula K Le Guin"
        author.mkdir(parents=True)
        (author / "The Left Hand of Darkness.epub").write_bytes(b"epub")
        (author / "The Dispossessed.pdf").write_bytes(b"pdf")
        ebook_items = await ebooks.scan(str(root))
        names = sorted(item.name for item in ebook_items)
        require(names == ["The Dispossessed", "The Left Hand of Darkness"], "ebooks should scan individual edition files, not collapse the author folder")
        units = ebooks.library_units_from_scan(ebook_items[0])
        require(units and units[0].get("unit_type") in {"ebook_file", "comic_archive"}, "ebook scan should produce ebook/comic units")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        book = root / "J R R Tolkien" / "The Hobbit"
        book.mkdir(parents=True)
        (book / "Chapter 01 - An Unexpected Party.mp3").write_bytes(b"mp3")
        (book / "Chapter 02 - Roast Mutton.mp3").write_bytes(b"mp3")
        audio_items = await audiobooks.scan(str(root))
        require(len(audio_items) == 1 and audio_items[0].name == "The Hobbit", "audiobooks should scan the narrated book folder, not just the author folder")
        local = audio_items[0].local_object_model
        require(local.get("chapter_count") == 2 and local.get("has_chapter_files"), "audiobook local model should reconstruct chapter files")
        units = audiobooks.library_units_from_scan(audio_items[0])
        require({u.get("unit_type") for u in units} == {"chapter"}, "multi-file audiobook should produce chapter units")


def test_category_metadata_refresh_policy_uses_stable_snapshot_cadence() -> None:
    # The lightweight sandbox used by this regression suite may not install
    # aiosqlite. Stub it so importing the scheduler helper does not require
    # opening a real database.
    import types
    fake_aiosqlite = sys.modules.setdefault("aiosqlite", types.ModuleType("aiosqlite"))
    if not hasattr(fake_aiosqlite, "Connection"):
        fake_aiosqlite.Connection = object
    if not hasattr(fake_aiosqlite, "Cursor"):
        fake_aiosqlite.Cursor = object
    from src.core.scheduler import MediaScheduler

    scheduler = object.__new__(MediaScheduler)

    class DummyCategory:
        def metadata_refresh_policy(self, *, provider=""):
            return {"refresh_after_days": 120, "uses_stable_id": True}

    fresh = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    fresh_policy = scheduler._category_metadata_refresh_policy(DummyCategory(), {"provider": "musicbrainz", "external_id": "mb:1", "metadata": {"stable_id": "mb:1"}, "refreshed_at": fresh})
    old_policy = scheduler._category_metadata_refresh_policy(DummyCategory(), {"provider": "musicbrainz", "external_id": "mb:1", "metadata": {"stable_id": "mb:1"}, "refreshed_at": old})
    require(not fresh_policy["due"] and fresh_policy["reason"] == "fresh_stable_snapshot", "fresh stable snapshots should not refresh every boot")
    require(old_policy["due"], "old stable snapshots should be scheduled for refresh")


def test_definition_backed_persists_best_resolved_metadata_for_library_items() -> None:
    from src.core.categories.registry import CategoryRegistry

    class Repo:
        def __init__(self) -> None:
            self.calls = []
        async def upsert_category_metadata(self, *args):
            self.calls.append(args)

    class DB:
        def __init__(self) -> None:
            self.media = Repo()

    class Context:
        def __init__(self) -> None:
            self.db = DB()

    registry = CategoryRegistry()
    registry.register_defaults()
    music = registry.get("music")
    context = Context()
    data = {
        "best": {
            "provider": "musicbrainz",
            "stable_id": "musicbrainz:release:abc",
            "title": "Parklife",
            "cover_url": "https://example.test/cover.jpg",
            "identifiers": {"musicbrainz_release_id": "abc"},
        }
    }
    persisted = asyncio.run(music._persist_resolved_metadata({"item_id": "Parklife"}, context, data))
    require(persisted and persisted["stable_id"] == "musicbrainz:release:abc", "persist helper should report stable metadata identity")
    require(context.db.media.calls, "metadata should be upserted when resolving a library item")
    stored = context.db.media.calls[0][3]
    require(stored["poster_url"].endswith("cover.jpg"), "persisted snapshot should expose cover art for UI/scheduler")
    require(stored["metadata_refresh_policy"]["uses_stable_id"], "persisted snapshot should carry category refresh policy")


def main() -> None:
    test_provider_adapters_are_split_from_resolver()
    asyncio.run(test_music_scan_reconstructs_album_track_units())
    asyncio.run(test_ebook_and_audiobook_scans_reconstruct_domain_units())
    test_category_metadata_refresh_policy_uses_stable_snapshot_cadence()
    test_definition_backed_persists_best_resolved_metadata_for_library_items()
    print("round128 provider/scan/refresh cleanup tests passed")


if __name__ == "__main__":
    main()
