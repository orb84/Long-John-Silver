#!/usr/bin/env python3
"""Round 127 regression tests for metadata cache/disambiguation cleanup."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.integrations.category_metadata import ProviderResult
from src.integrations.metadata_disambiguation import canonical_group_key, rank_and_group
from src.core.category_object_models import AudiobookEditionModel, BookEditionModel, ExternalIdentity, MusicReleaseModel
from src.integrations.metadata_cache import _retry_after_seconds


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_cross_provider_grouping_uses_stable_identity_not_provider_specific_ladders() -> None:
    mb = ProviderResult(
        provider="musicbrainz",
        title="Parklife",
        contributors=["Blur"],
        year="1994",
        identifiers={"musicbrainz_release_group_id": "abc"},
        object_model=MusicReleaseModel(title="Parklife", artist_credit=["Blur"], release_type="Album", year="1994").as_dict(),
    )
    mb_dup = ProviderResult(
        provider="musicbrainz",
        title="Parklife",
        contributors=["Blur"],
        year="1994",
        identifiers={"musicbrainz_release_group_id": "abc"},
        object_model=MusicReleaseModel(title="Parklife", artist_credit=["Blur"], release_type="Album", year="1994").as_dict(),
    )
    ranked = rank_and_group("Blur Parklife", [mb, mb_dup], limit=5)
    require(len(ranked.ranked) == 1, "same provider stable entity should dedupe before LLM selection")
    require(canonical_group_key(mb).startswith("id:musicbrainz_release_group_id"), "release group identity should drive music grouping")


def test_books_and_audiobooks_models_expose_disambiguation_facets() -> None:
    book = BookEditionModel(
        title="The Left Hand of Darkness",
        authors=["Ursula K. Le Guin"],
        languages=["eng"],
        series="Hainish Cycle",
        series_index="4",
        source_level="edition",
        identities=[ExternalIdentity("open_library", "openlibrary_work_key", "/works/OL123W", "work")],
    )
    audio = AudiobookEditionModel(
        title="The Left Hand of Darkness",
        authors=["Ursula K. Le Guin"],
        narrators=["George Guidall"],
        abridgement="unabridged",
        source_level="store_result",
    )
    require(book.as_dict()["series"] == "Hainish Cycle", "ebook model should expose series facets for LLM disambiguation")
    require(book.as_dict()["source_level"] == "edition", "ebook model should distinguish work/edition/source level")
    require(audio.as_dict()["narrators"] == ["George Guidall"], "audiobook model should expose narrator facets")
    require(audio.as_dict()["abridgement"] == "unabridged", "audiobook model should expose abridgement facets")


def test_retry_after_supports_http_dates_without_live_network() -> None:
    seconds = _retry_after_seconds({"Retry-After": "Wed, 21 Oct 2037 07:28:00 GMT"})
    require(seconds is not None and seconds > 0, "Retry-After HTTP-date should be parsed for provider backoff")


def test_disambiguation_packet_allows_safe_autoselect_only_when_confident() -> None:
    clear = ProviderResult(
        provider="open_library",
        title="Dune",
        contributors=["Frank Herbert"],
        year="1965",
        identifiers={"openlibrary_work_key": "/works/OL893415W"},
        object_model=BookEditionModel(title="Dune", authors=["Frank Herbert"], first_publish_year="1965").as_dict(),
    )
    clear.score = 0.8
    ambiguous = ProviderResult(
        provider="google_books",
        title="Dune",
        contributors=["Frank Herbert"],
        year="1965",
        identifiers={"google_books_id": "x"},
        object_model=BookEditionModel(title="Dune", authors=["Frank Herbert"], published_date="1965").as_dict(),
    )
    ambiguous.score = 0.79
    ranked = rank_and_group("Dune Frank Herbert", [clear, ambiguous], limit=5)
    require(ranked.disambiguation["needs_llm_selection"], "close top candidates should trigger LLM selection")
    require(not ranked.disambiguation["safe_autoselect"], "ambiguous close candidates must not auto-select")
    require("llm_tasks" in ranked.disambiguation, "LLM should receive explicit selection/pruning tasks")


def main() -> None:
    test_cross_provider_grouping_uses_stable_identity_not_provider_specific_ladders()
    test_books_and_audiobooks_models_expose_disambiguation_facets()
    test_retry_after_supports_http_dates_without_live_network()
    test_disambiguation_packet_allows_safe_autoselect_only_when_confident()
    print("round127 metadata/disambiguation/cache smell tests passed")


if __name__ == "__main__":
    main()
