#!/usr/bin/env python3
"""Round 261 regression tests for movie search recall/precision and Soulseek recovery.

These tests lock the failures seen in the Hotel Exotica / Virtual Encounters 2
session: exact movie rows must not be buried behind keyword-neighbor titles,
movie queries must preserve numeric sequels, legacy Soulseek category defaults
must not disable direct movie searches, and provider responses without choices
must fail as a useful retryable provider error instead of KeyError('choices').
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.categories.movie import MovieCategory
from src.core.domain_models.downloads import SearchResult
from src.core.domain_models.settings import SoulseekSettings
from src.integrations.tmdb import TMDBClient


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def assert_false(value: bool, message: str) -> None:
    if value:
        raise AssertionError(message)


def test_movie_title_gate_accepts_exact_and_rejects_keyword_neighbors() -> None:
    movie = MovieCategory()
    item = movie.create_item("Hotel Exotica", year=1998, language="English")
    assert_true(
        movie.validate_search_result_for_request(
            SearchResult(title="Hotel Exotica [1998 Ahmo Hight Taylor St Clair]", size="1.37 GB", seeders=1),
            item,
            None,
        ),
        "Exact Hotel Exotica release rows must survive movie validation even with low seeders.",
    )
    assert_false(
        movie.validate_search_result_for_request(
            SearchResult(title="The Best Exotic Marigold Hotel 2011 1080p BluRay x264-OFT", size="2.5 GB", seeders=50),
            item,
            None,
        ),
        "Keyword-neighbor movie titles must not be accepted for Hotel Exotica.",
    )
    assert_false(
        movie.validate_search_result_for_request(
            SearchResult(title="Star Trek Lower Decks S03E08 The Best Exotic Nanite Hotel 1080p", size="900 MB", seeders=25),
            item,
            None,
        ),
        "TV episode rows must not be accepted as movie results.",
    )


def test_movie_payload_filter_drops_unrelated_rows_before_llm() -> None:
    movie = MovieCategory()
    candidates = [
        {
            "title": "The Best Exotic Marigold Hotel 2011 1080p BluRay x264-OFT",
            "unit_descriptor": {"label": "Hotel Exotica", "coordinates": {"title": "Hotel Exotica", "year": 1998}},
        },
        {
            "title": "Hotel Exotica [1998 Ahmo Hight Taylor St Clair]",
            "unit_descriptor": {"label": "Hotel Exotica", "coordinates": {"title": "Hotel Exotica", "year": 1998}},
        },
    ]
    filtered = movie.filter_agent_candidate_payloads_for_request(candidates, language="English")
    assert_true(len(filtered) == 1, f"Expected one Hotel Exotica candidate after payload filter, got {filtered!r}")
    assert_true(filtered[0]["title"].startswith("Hotel Exotica"), "Exact Hotel Exotica payload should remain.")


def test_movie_query_ladder_preserves_numeric_sequel_titles() -> None:
    movie = MovieCategory()
    item = movie.create_item("Virtual Encounters 2", language="English")
    queries = movie._agent_movie_search_queries(item, language="English")
    joined = "\n".join(queries)
    assert_true("Virtual Encounters 2" in joined, f"Query ladder dropped sequel number: {queries!r}")
    assert_false(
        any(query.strip().casefold() == "virtual encounters" for query in queries),
        f"Query ladder should not silently replace explicit sequel title with base title only: {queries!r}",
    )


def test_tmdb_movie_title_aliases_are_exposed() -> None:
    data = {
        "title": "Hotel Exotica",
        "original_title": "Hotel Exotica Original",
        "alternative_titles": {"titles": [{"title": "Hôtel Exotica"}]},
        "translations": {"translations": [{"iso_639_1": "it", "iso_3166_1": "IT", "english_name": "Italian", "data": {"title": "Hotel Exotica IT"}}]},
    }
    aliases = TMDBClient._movie_title_aliases(data)
    localized = TMDBClient._movie_localized_titles(data)
    assert_true("Hotel Exotica" in aliases, f"Canonical title missing from aliases: {aliases!r}")
    assert_true("Hotel Exotica Original" in aliases, f"Original title missing from aliases: {aliases!r}")
    assert_true("Hôtel Exotica" in aliases, f"Alternative title missing from aliases: {aliases!r}")
    assert_true(any(row.get("title") == "Hotel Exotica IT" and row.get("iso_639_1") == "it" for row in localized), f"Localized title missing: {localized!r}")


def test_legacy_soulseek_categories_migrate_to_media_sources() -> None:
    cfg = SoulseekSettings(search_enabled_categories=["music", "audiobooks", "ebooks"])
    enabled = set(cfg.search_enabled_categories)
    assert_true({"movie", "tv", "general"}.issubset(enabled), f"Legacy Soulseek category defaults were not migrated: {enabled!r}")


def test_nvidia_provider_handles_missing_choices_explicitly() -> None:
    source = Path("src/llm_providers/task_client.py").read_text()
    ast.parse(source)
    assert_true("NVIDIA NIM response missing choices" in source, "Missing-choices provider guard not present.")
    assert_false('data["choices"][0]' in source.split("NVIDIA NIM response missing choices", 1)[0], "choices access occurs before missing-choices guard.")


def main() -> None:
    test_movie_title_gate_accepts_exact_and_rejects_keyword_neighbors()
    test_movie_payload_filter_drops_unrelated_rows_before_llm()
    test_movie_query_ladder_preserves_numeric_sequel_titles()
    test_tmdb_movie_title_aliases_are_exposed()
    test_legacy_soulseek_categories_migrate_to_media_sources()
    test_nvidia_provider_handles_missing_choices_explicitly()
    print("round261 movie search / Soulseek recovery tests passed")


if __name__ == "__main__":
    main()
