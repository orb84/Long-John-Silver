#!/usr/bin/env python3
"""Round 269 regression checks for category-owned torrent skill expansion."""
from __future__ import annotations

from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The minimal sandbox used by these direct regression scripts may not include
# optional runtime DB dependencies. TorrentSelectionService imports the release
# group tracker, which imports the database layer; the test below does not touch
# database I/O, so a stub module keeps the import focused on prompt behavior.
class _AioSqliteStub(types.ModuleType):
    def __getattr__(self, _name: str) -> object:
        return object

sys.modules.setdefault("aiosqlite", _AioSqliteStub("aiosqlite"))

from src.ai.torrent_selection import TorrentSelectionService
from src.ai.torrent_selection_prompt import TorrentSelectionPromptBuilder
from src.core.categories.registry import CategoryRegistry
from src.core.categories.tv import TvShowCategory
from src.core.models import SearchResult


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def registry() -> CategoryRegistry:
    return CategoryRegistry.with_defaults()


def test_definition_backed_prompt_files_are_loaded() -> None:
    cats = registry()
    for category_id, expected in {
        "music": "Music release-name skill",
        "ebooks": "Ebook release-name skill",
        "audiobooks": "Audiobook release-name skill",
    }.items():
        category = cats.get(category_id)
        require(category is not None, f"{category_id} should be registered")
        guidance = category.build_prompt_guidance("download")
        require("Category prompt file guidance" in guidance, f"{category_id} prompt file should be injected")
        require(expected in guidance, f"{category_id} prompt should include its release-name skill")


def test_definition_backed_torrent_selection_uses_prompt_skill() -> None:
    cats = registry()
    music = cats.get("music")
    ebooks = cats.get("ebooks")
    audiobooks = cats.get("audiobooks")
    require(music is not None and ebooks is not None and audiobooks is not None, "definition-backed categories should exist")

    music_guidance = music.build_torrent_selection_guidance()
    ebook_guidance = ebooks.build_torrent_selection_guidance()
    audiobook_guidance = audiobooks.build_torrent_selection_guidance()

    require("Music release-name skill" in music_guidance, "music torrent guidance should include music prompt skill")
    require("FLAC" in music_guidance and "discography" in music_guidance, "music skill should teach music release terms")
    require("Ebook release-name skill" in ebook_guidance, "ebook torrent guidance should include ebook prompt skill")
    require("EPUB" in ebook_guidance and "AZW3" in ebook_guidance, "ebook skill should teach format terms")
    require("Audiobook release-name skill" in audiobook_guidance, "audiobook torrent guidance should include audiobook prompt skill")
    require("narrator" in audiobook_guidance.lower() and "M4B" in audiobook_guidance, "audiobook skill should teach narrator/container terms")


def test_tv_prompt_skill_was_expanded_for_real_release_shapes() -> None:
    guidance = TvShowCategory().build_prompt_guidance("download")
    require("Complete Series" in guidance and "S01-S05" in guidance, "TV skill should teach complete-series containers")
    require("subtitle evidence" in guidance, "TV skill should distinguish subtitles from audio language")
    require("S01E01E02" in guidance and "1x02" in guidance, "TV skill should teach multi-episode and alternate episode formats")


def test_non_video_categories_do_not_get_video_quality_ladder() -> None:
    service = TorrentSelectionService(category_registry=registry())
    music_ref = service.build_quality_reference(
        [SearchResult(title="Artist - Album [FLAC]", source="fixture", magnet="magnet:?xt=urn:btih:1")],
        category_id="music",
    )
    require("REMUX" not in music_ref and "WEB-DL" not in music_ref and "Blu-ray" not in music_ref,
            "music quality reference must not inherit video quality ladder")
    require("owning category" in music_ref and "format/edition" in music_ref,
            "music quality reference should defer to category skill")

    tv_ref = service.build_quality_reference(
        [SearchResult(title="Show.S01E01.1080p.WEB-DL", source="fixture", magnet="magnet:?xt=urn:btih:2")],
        category_id="tv",
    )
    require("WEB-DL" in tv_ref and "1080p" in tv_ref, "TV should still receive video quality guidance")


def test_generic_no_language_prompt_is_category_neutral() -> None:
    prompt = TorrentSelectionPromptBuilder.build(
        item_display_name="Artist - Album",
        unit_key="",
        preferred_language="",
        media_category="music",
        quality_context="",
        quality_ref="Use the owning category skill.",
        candidates=[],
        selection_guidance="Music release-name skill: prefer FLAC album folders.",
    )
    require("English/Italian/MULTI" not in prompt, "generic no-language prompt must not name movie/TV language examples")
    require("language-specific or multi-audio preferences" in prompt, "generic prompt should remain category-neutral")


def main() -> None:
    test_definition_backed_prompt_files_are_loaded()
    test_definition_backed_torrent_selection_uses_prompt_skill()
    test_tv_prompt_skill_was_expanded_for_real_release_shapes()
    test_non_video_categories_do_not_get_video_quality_ladder()
    test_generic_no_language_prompt_is_category_neutral()
    print("round269_category_torrent_skill_expansion_tests: OK")


if __name__ == "__main__":
    main()
