#!/usr/bin/env python3
"""Round 273 regressions for language-token and generic quality drift cleanup."""
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

from src.ai.torrent_candidate_policy import TorrentCandidateRanking
from src.ai.tools.scheduling import SearchMediaTorrentsTool
from src.core.categories.base import CategoryMedia
from src.core.categories.types import ParsedMedia
from src.core.categories.language import LanguageTokenPolicy
from src.core.models import NormalizedTorrentCandidate, SearchResult
from src.ai.tools.search_workspace import SearchQualityChoicePolicy


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_language_token_policy_is_shared_and_bounded() -> None:
    require(LanguageTokenPolicy.canonical_token("ITA") == "italian", "ITA should normalize to italian")
    require(LanguageTokenPolicy.canonical_token("Inglese") == "english", "localized English alias should normalize")
    require(LanguageTokenPolicy.canonical_token("dual-audio") == "multi", "dual-audio should normalize to multi")
    require(LanguageTokenPolicy.title_has_language_token("Widows.Bay.S01.ITA.1080p", "italian"), "bounded ITA token should match")
    require(not LanguageTokenPolicy.title_has_language_token("Capital.City.S01.1080p", "ita"), "substring inside Capital must not match ITA")
    require(LanguageTokenPolicy.canonical_token("ENG") == "english", "scheduling compatibility wrapper should use shared language token policy")
    require(LanguageTokenPolicy.title_has_language_token("Show.S01.ENG.1080p", "english"), "scheduling title wrapper should use shared bounded matcher")


def test_batch_score_can_ignore_language_and_video_quality_for_non_video_categories() -> None:
    low_seed_videoish = {
        "title": "Author - Book [ITA] 2160p HEVC",
        "languages": ["Italian"],
        "resolution": "2160p",
        "codec": "HEVC",
        "seeders": 1,
        "quality_score": 99,
        "size_bytes": 100,
    }
    high_seed_plain = {
        "title": "Author - Book EPUB",
        "languages": [],
        "seeders": 50,
        "quality_score": 1,
        "size_bytes": 100,
    }
    videoish_score = SearchQualityChoicePolicy.batch_candidate_score(
        low_seed_videoish,
        "Italian",
        language_relevant=False,
        use_global_quality_profile=False,
    )
    plain_score = SearchQualityChoicePolicy.batch_candidate_score(
        high_seed_plain,
        "Italian",
        language_relevant=False,
        use_global_quality_profile=False,
    )
    require(plain_score > videoish_score, "non-video batch ordering should not reward language/resolution/codec leakage over seeders")


def test_torrent_ranking_can_ignore_language_and_global_video_quality() -> None:
    result = SearchResult(title="Author.Book.ITA.2160p.HEVC", source="test", magnet="magnet:?xt=urn:btih:1", seeders=1, quality_score=100)
    neutral = TorrentCandidateRanking.pre_score(result, "Italian", language_relevant=False)
    language_weighted = TorrentCandidateRanking.pre_score(result, "Italian", language_relevant=True)
    require(neutral[0:2] == (0, 0), "pre_score should clear language dimensions when category says language is irrelevant")
    require(language_weighted[0] >= neutral[0], "language-relevant categories may still benefit from language evidence")

    candidate = NormalizedTorrentCandidate(
        title="Author.Book.ITA.2160p.HEVC",
        source="test",
        magnet="magnet:?xt=urn:btih:1",
        magnet_available=True,
        size="1 GB",
        size_bytes=1024 ** 3,
        seeders=1,
        language="Italian",
        resolution="2160p",
        codec="HEVC",
        quality_score=100,
    )
    no_video = TorrentCandidateRanking.selection_score(
        candidate,
        "Italian",
        "2160p",
        1000,
        language_relevant=False,
        use_global_quality_profile=False,
    )
    require(no_video[0:5] == (0, 0, 0.0, 0, 0), "selection_score should not apply language/video target/codec dimensions when category opts out")


class _DummyCategory(CategoryMedia):
    category_id = "dummy"
    display_name = "Dummy"
    accepted_file_patterns = ["*.*"]

    def get_properties(self, settings):
        return []

    async def scan(self, root_path: str, existing_keys=None):
        return []

    def parse_name(self, name: str) -> ParsedMedia:
        return ParsedMedia(original_title=name, title=name)


def test_generic_torrent_guidance_no_longer_hard_rejects_adult_or_archive_words() -> None:
    guidance = _DummyCategory().build_torrent_selection_guidance().lower()
    require("adult content" not in guidance, "base category guidance must not globally reject adult-rated media")
    require("multi-part archives (.rar, .zip, .7z)" not in guidance, "base guidance must not globally reject archive containers")
    require("another installed category" in guidance, "base guidance should still reject cross-category payloads")


def test_search_scope_schema_uses_category_neutral_bundle_language() -> None:
    schema = SearchMediaTorrentsTool().parameters()
    desc = schema["properties"]["search_scope"]["description"]
    require("whole TV show/season" not in desc, "generic search_scope schema should not teach TV-only wording")
    require("season, volume, album, collection" in desc, "schema should describe category-neutral bundle examples")


def test_selective_download_warning_is_category_neutral_in_source() -> None:
    source = (ROOT / "src/ai/tools/scheduling.py").read_text(encoding="utf-8")
    require("contains extra TV units" not in source, "generic scheduling source must not emit TV-specific selective-download wording")
    require("season-pack candidates differ" not in source, "quality-choice wording should not be hard-coded to season packs")


def test_source_comments_do_not_keep_incident_specific_show_examples() -> None:
    banned = ("Silicon Valley", "Yellowstone", "Rooster", "The Wire", "Widows Bay", "Widow's Bay", "Star City")
    findings: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                findings.append(f"{path.relative_to(ROOT)}: {token}")
    require(not findings, "incident-specific show examples must not remain in source/comments: " + "; ".join(findings))


def main() -> None:
    test_language_token_policy_is_shared_and_bounded()
    test_batch_score_can_ignore_language_and_video_quality_for_non_video_categories()
    test_torrent_ranking_can_ignore_language_and_global_video_quality()
    test_generic_torrent_guidance_no_longer_hard_rejects_adult_or_archive_words()
    test_search_scope_schema_uses_category_neutral_bundle_language()
    test_selective_download_warning_is_category_neutral_in_source()
    test_source_comments_do_not_keep_incident_specific_show_examples()
    print("ROUND273_CATEGORY_LANGUAGE_QUALITY_DRIFT_TESTS_PASS")


if __name__ == "__main__":
    main()
