#!/usr/bin/env python3
"""Round 270 regression checks for category prompt-file skill source of truth."""
from __future__ import annotations

from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class _AioSqliteStub(types.ModuleType):
    def __getattr__(self, _name: str) -> object:
        return object

sys.modules.setdefault("aiosqlite", _AioSqliteStub("aiosqlite"))

from src.ai.download_candidate_adjudicator import DownloadCandidateAdjudicator
from src.core.categories.registry import CategoryRegistry
from src.core.categories.tv import TvShowCategory
from src.core.categories.movie import MovieCategory


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def registry() -> CategoryRegistry:
    return CategoryRegistry.with_defaults()


def test_prompt_file_torrent_skill_extracts_only_search_relevant_sections() -> None:
    tv = TvShowCategory()
    tv_skill = tv.prompt_file_torrent_skill()
    require("## TV release-name skill" in tv_skill, "TV torrent skill should include release-name section")
    require("## TV language skill" in tv_skill, "TV torrent skill should include language section")
    require("## Automation safety" in tv_skill, "TV torrent skill should include automation safety section")
    require("## Metadata and local-state workflow" not in tv_skill, "TV torrent skill should not include metadata workflow section")

    movie = MovieCategory()
    movie_skill = movie.prompt_file_torrent_skill()
    require("## Movie release-name skill" in movie_skill, "Movie torrent skill should include release-name section")
    require("## Movie language and collection skill" in movie_skill, "Movie torrent skill should include collection/language section")
    require("## Metadata and local-state workflow" not in movie_skill, "Movie torrent skill should not include metadata workflow section")


def test_concrete_categories_do_not_duplicate_inline_skill_examples() -> None:
    tv_source = (ROOT / "src/core/categories/tv_workflows.py").read_text(encoding="utf-8")
    movie_source = (ROOT / "src/core/categories/movie.py").read_text(encoding="utf-8")
    for phrase in ("S01E01-E06", "S01E01-06", "iTALiAN", "DLMux"):
        require(phrase not in tv_source, f"TV source should not duplicate prompt-file release skill phrase: {phrase}")
    for phrase in ("Director's Cut", "Open Matte", "multi-film/collection", "DLMux"):
        require(phrase not in movie_source, f"Movie source should not duplicate prompt-file release skill phrase: {phrase}")


def test_torrent_guidance_is_prompt_file_backed_for_all_download_categories() -> None:
    cats = registry()
    expected_markers = {
        "tv": ["TV release-name skill", "TV language skill", "Automation safety"],
        "movie": ["Movie release-name skill", "Movie language and collection skill"],
        "music": ["Music release-name skill", "Music safety and import skill"],
        "ebooks": ["Ebook release-name skill", "Ebook safety and import skill"],
        "audiobooks": ["Audiobook release-name skill", "Audiobook safety and import skill"],
    }
    for category_id, markers in expected_markers.items():
        category = cats.get(category_id)
        require(category is not None, f"{category_id} should be registered")
        guidance = category.build_torrent_selection_guidance()
        for marker in markers:
            require(marker in guidance, f"{category_id} torrent guidance should include prompt-file marker {marker!r}")


def test_llm_candidate_prompt_does_not_truncate_tv_safety_tail() -> None:
    tv_guidance = TvShowCategory().build_torrent_selection_guidance()
    adjudicator = DownloadCandidateAdjudicator()
    prompt = adjudicator._build_prompt(
        request={"user_prompt": "download Star City in Italian", "tool_arguments": {"category_id": "tv"}},
        rows=[{"candidate_id": "c1", "title": "Star.City.S01E01.ITA.1080p", "seeders": 50}],
        category_guidance=tv_guidance,
        review_stage="single_pass",
        chunk_index=1,
        chunk_count=1,
        context_limit_tokens=8192,
    )
    require("series-title portion" in prompt, "TV release-name instructions should reach candidate reviewer")
    require("subtitle evidence" in prompt, "TV language/audio distinction should reach candidate reviewer")
    require("does not enable future tracking" in prompt, "TV automation safety tail should not be truncated")


def test_generic_torrent_prompt_language_rules_are_category_neutral() -> None:
    from src.ai.torrent_selection_prompt import TorrentSelectionPromptBuilder

    prompt = TorrentSelectionPromptBuilder.build(
        item_display_name="Example Target",
        unit_key="",
        preferred_language="Italian",
        media_category="ebooks",
        quality_context="",
        quality_ref="Use the ebook category skill.",
        candidates=[],
        selection_guidance="Ebook release-name skill: preserve author/title/language/format.",
    )
    require("audio/subtitle track" not in prompt, "generic language rules must not assume AV audio/subtitle semantics")
    require("lang:Hindi when preferred is English" not in prompt, "generic language examples should not name arbitrary languages")
    require("category guidance" in prompt, "generic language rules should defer evidence semantics to category guidance")


def main() -> None:
    test_prompt_file_torrent_skill_extracts_only_search_relevant_sections()
    test_concrete_categories_do_not_duplicate_inline_skill_examples()
    test_torrent_guidance_is_prompt_file_backed_for_all_download_categories()
    test_llm_candidate_prompt_does_not_truncate_tv_safety_tail()
    test_generic_torrent_prompt_language_rules_are_category_neutral()
    print("round270_category_skill_source_of_truth_tests: OK")


if __name__ == "__main__":
    main()
