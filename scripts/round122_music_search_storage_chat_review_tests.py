#!/usr/bin/env python3
"""Round 122 review checks for music leakage, storage math, and chat rendering."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
GB = 1024 ** 3


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load_yaml(path: str) -> dict:
    data = yaml.safe_load(text(path)) or {}
    require(isinstance(data, dict), f"{path} should contain a mapping")
    return data


def test_music_definition_declares_no_language_or_video_quality_leakage() -> None:
    music = load_yaml("config/category-definitions/music.yaml")
    policy = music.get("search_policy") or {}
    require(policy.get("language_relevant") is False, "music should not inherit global language constraints")
    require(policy.get("use_global_quality_profile") is False, "music should not inherit global video quality defaults")
    for term in ("discography", "OST", "Original Soundtrack", "bootleg"):
        require(term in str(music), f"music definition should teach/search term {term}")
    music_text = text("config/category-definitions/music.yaml").lower()
    for foreign in ("720p", "1080p", "bluray", "web-dl", "x264", "x265"):
        require(foreign not in music_text, f"music must not declare foreign video terms: {foreign}")


def test_definition_backed_music_query_and_validation_are_category_owned() -> None:
    from src.core.categories.registry import CategoryRegistry
    from src.core.models import SearchResult

    registry = CategoryRegistry.with_defaults()
    music = registry.get("music")
    item = music.create_item("Blur discography")

    require(music.language_is_search_relevant() is False, "music language policy should report irrelevant")
    require(music.uses_global_quality_profile() is False, "music should opt out of global QualityProfile")
    require(item.language == "", "music temporary items should not default to English")
    require(item.quality.preferred_resolution == "", "music temporary items should not default to 1080p")
    require(music.build_search_query(item, None, "English") == "Blur discography", "music query should not append English")

    video = SearchResult(
        title="Www Torrenting Com - Blur To The End (2024) 1080p BluRay 5.1-LAMA",
        source="test", magnet="magnet:?xt=urn:btih:abc", size="1.9 GB", seeders=4,
    )
    album_bundle = SearchResult(
        title="Blur Complete Discography 1991-2023 FLAC",
        source="test", magnet="magnet:?xt=urn:btih:def", size="20 GB", seeders=40,
    )
    require(not music.validate_search_result_for_request(video, item, None), "music should reject video-looking Blur documentary candidates")
    require(music.validate_search_result_for_request(album_bundle, item, None), "music should accept discography-looking audio candidates")


def test_prompt_and_selection_guides_do_not_inject_video_rules_for_music() -> None:
    from src.ai.prompt_builder import PromptBuilder
    from src.core.models import Intent

    from src.core.categories.registry import CategoryRegistry

    music_category = CategoryRegistry.with_defaults().get("music")
    category_guidance = "ACTIVE CATEGORY: Music (music)\n\n" + music_category.build_prompt_guidance("download")
    prompt = PromptBuilder().build_system_prompt(Intent.DOWNLOAD, category_guidance=category_guidance, active_category_id="music")
    require("For music," not in prompt, "generic download prompt should not hardcode music-specific rules")
    require("include the term 'discography'" in prompt, "music category guidance should teach discography query expansion")
    require("soundtrack" in prompt and "OST" in prompt, "music category guidance should teach OST soundtrack expansion")
    require("Do not import quality, language, or release" in prompt, "music prompt should replace cross-category quality guide")
    require("REMUX / BluRay / BDRip" not in prompt, "music prompt should not include compact video quality tiers")
    require("fast 1080p" not in prompt and "high-quality Bluray" not in prompt, "generic decision examples should be category-neutral")

    guidance = music_category.build_torrent_selection_guidance().lower()
    require("including games, ebooks, software, music" not in guidance, "music guidance must not reject music itself")
    require("another category's declared release/file signatures" in guidance, "music guidance should use generic category-boundary signatures")
    for foreign in ("720p", "1080p", "bluray", "web-dl"):
        require(foreign not in guidance, f"music guidance must not enumerate foreign video terms: {foreign}")


def test_storage_capacity_uses_candidate_size_not_low_percentage_alone() -> None:
    from src.core.models import Settings, StoragePathUsage, StorageReport
    from src.core.storage import StorageMonitor

    class Manager:
        settings = Settings()

    monitor = StorageMonitor(Manager())
    usage = StoragePathUsage(
        path="/library/Music", purpose="category_library", category_id="music",
        volume_id="dev:test", mount_point="/library", total_bytes=int(465.5 * GB),
        used_bytes=int(442.2 * GB), free_bytes=int(23.3 * GB), free_percent=5.0,
        status="critical", message="Disk /library is critically low: 23.3 GB free (5.0%).",
    )
    report = StorageReport(ok=False, paths=[usage], volumes=[], critical=[usage.message])
    monitor.build_report = lambda: report  # type: ignore[method-assign]

    small = monitor.check_download_capacity(category_id="music", estimated_bytes=int(1.91 * GB))
    require(small.ok is True, "2 GB should fit on 23 GB free when reserve remains satisfied")
    require(small.status == "warning", "low-percent but fitting downloads should be warnings, not hard failures")
    require("would fit" in small.reason, "storage decision should explain deterministic fit")

    huge = monitor.check_download_capacity(category_id="music", estimated_bytes=int(22 * GB))
    require(huge.ok is False, "downloads that would violate the reserve should still be blocked")
    require("minimum configured reserve" in huge.reason, "blocked decision should cite reserve, not vague critical status")


def test_chat_markdown_images_are_constrained() -> None:
    css = text("src/web/static/css/style.css")
    for phrase in (
        ".msg-bubble img",
        "max-height: 180px",
        ".msg-bubble td img",
        "max-height: 96px",
        "overflow-x: auto",
    ):
        require(phrase in css, f"chat CSS should constrain markdown images/tables: {phrase}")


def test_review_docs_record_round122_findings() -> None:
    doc = ROOT / "docs/project-history/ROUND122_MUSIC_SEARCH_STORAGE_CHAT_REVIEW.md"
    require(doc.exists(), "Round 122 review doc should exist")
    body = doc.read_text(encoding="utf-8")
    for phrase in (
        "Blur discography",
        "English",
        "check_storage_capacity",
        "cover art",
        "discography",
        "OST",
        "bootleg",
    ):
        require(phrase in body, f"Round 122 doc should mention {phrase}")


if __name__ == "__main__":
    for name in sorted(k for k in globals() if k.startswith("test_")):
        globals()[name]()
        print(f"PASS {name}")
