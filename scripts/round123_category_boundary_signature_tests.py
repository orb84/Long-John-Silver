#!/usr/bin/env python3
"""Round 123 regression checks for category-boundary signature ownership."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_music_does_not_own_video_vocabulary() -> None:
    body = (ROOT / "config/category-definitions/music.yaml").read_text(encoding="utf-8").lower()
    for term in ("720p", "1080p", "2160p", "bluray", "web-dl", "x264", "x265"):
        require(term not in body, f"foreign video token leaked into music definition: {term}")


def test_foreign_signatures_are_derived_from_other_categories() -> None:
    from src.core.categories.boundary_signatures import default_boundary_signature_index

    index = default_boundary_signature_index()
    video = index.strongest_foreign_match(
        active_category_id="music",
        title="Www Torrenting Com - Blur To The End (2024) 1080p BluRay 5.1-LAMA",
    )
    require(video is not None, "video-looking Blur documentary candidate should match a foreign category signature")
    require(video.category_id in {"movie", "tv"}, f"expected movie/tv foreign match, got {video}")
    require(len(video.matched_terms) >= 2, f"expected multiple foreign terms, got {video.matched_terms}")

    music = index.strongest_foreign_match(
        active_category_id="music",
        title="Blur Complete Discography 1991-2023 FLAC",
    )
    require(music is None, f"music-looking discography should not be foreign-matched: {music}")


def test_music_validation_uses_generic_foreign_signature_filter() -> None:
    from src.core.categories.registry import CategoryRegistry
    from src.core.models import SearchResult

    category = CategoryRegistry.with_defaults().get("music")
    item = category.create_item("Blur")
    video = SearchResult(
        title="Www Torrenting Com - Blur To The End (2024) 1080p BluRay 5.1-LAMA",
        source="test",
        magnet="magnet:?xt=urn:btih:abc",
        size="1.9 GB",
        seeders=4,
    )
    album = SearchResult(
        title="Blur The Ballad of Darren 2023 FLAC",
        source="test",
        magnet="magnet:?xt=urn:btih:def",
        size="780 MB",
        seeders=32,
    )
    require(not category.validate_search_result_for_request(video, item, None), "foreign signature should reject video-looking candidate")
    require(category.validate_search_result_for_request(album, item, None), "music signature should accept audio-looking candidate")


def test_generic_download_prompt_uses_category_neutral_examples() -> None:
    from src.ai.prompt_builder import PromptBuilder
    from src.core.models import Intent

    prompt = PromptBuilder().build_system_prompt(
        Intent.DOWNLOAD,
        category_guidance="ACTIVE CATEGORY: Music (music)",
        active_category_id="music",
    )
    require("fast 1080p" not in prompt, "generic prompt should not use video-specific ambiguity examples")
    require("high-quality Bluray" not in prompt, "generic prompt should not use video-specific ambiguity examples")
    require("Do not import quality, language, or release" in prompt, "non-video category prompt should stay category-neutral")
    require("For music," not in prompt, "generic prompt should not encode category-specific music rules")


if __name__ == "__main__":
    for name in sorted(k for k in globals() if k.startswith("test_")):
        globals()[name]()
        print(f"PASS {name}")
