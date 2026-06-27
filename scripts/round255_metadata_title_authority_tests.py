#!/usr/bin/env python3
"""Round 255 regressions for metadata-backed TV title authority."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.categories.title_authority import CategoryTitleAuthority
from src.core.categories.tv import TvShowCategory


class FakeMetadataRecord:
    def model_dump(self) -> dict:
        return {
            "display_name": "Widows Bay",
            "title_aliases": ["Widows Bay", "Widow's Bay"],
            "localized_titles": [
                {"title": "Widows Bay", "language": "English", "iso_639_1": "en", "country": "US"},
                {"title": "La Baia delle Vedove", "language": "Italian", "iso_639_1": "it", "country": "IT"},
            ],
            "tmdb_id": 1234,
        }


class FakeEnricher:
    async def _enrich_impl(self, title: str) -> FakeMetadataRecord:
        assert title == "Widow Bay"
        return FakeMetadataRecord()


def test_provider_aliases_are_primary_title_gate() -> None:
    tv = TvShowCategory()
    item = SimpleNamespace(
        key="Widow Bay",
        display_name=None,
        metadata={"display_name": "Widows Bay", "title_aliases": ["Widows Bay", "Widow's Bay"]},
    )
    assert tv._title_matches_item_series(
        "Widows.Bay.S01E01-06.1080p.ATVP.WEB-DL.ITA.ENG.DD5.1.H.264-G66",
        item,
    )
    assert tv._title_matches_item_series("Widow's Bay S01E07 1080p ITA ENG", item)


def test_provider_aliases_prevent_broad_token_collapse() -> None:
    tv = TvShowCategory()
    item = SimpleNamespace(key="The Boys", display_name=None, metadata={"display_name": "The Boys", "title_aliases": ["The Boys"]})
    assert tv._title_matches_item_series("The.Boys.S01E01.1080p.WEB-DL", item)
    assert not tv._title_matches_item_series("The.Hardy.Boys.S01E01.1080p.WEB-DL", item)


def test_query_titles_prefer_provider_and_localized_titles() -> None:
    item = SimpleNamespace(
        key="Widow Bay",
        display_name=None,
        metadata={
            "display_name": "Widows Bay",
            "title_aliases": ["Widows Bay", "Widow's Bay"],
            "localized_titles": [{"title": "La Baia delle Vedove", "iso_639_1": "it", "country": "IT"}],
        },
    )
    titles = CategoryTitleAuthority.query_titles_for_item(item, preferred_language="Italian")
    assert titles[0] == "Widows Bay"
    assert "La Baia delle Vedove" in titles
    assert "Widow Bay" in titles


async def test_search_ladder_uses_authoritative_titles_not_only_user_text() -> None:
    tv = TvShowCategory()
    item = SimpleNamespace(
        key="Widow Bay",
        display_name=None,
        metadata={"display_name": "Widows Bay", "title_aliases": ["Widows Bay", "Widow's Bay"]},
    )
    queries = await tv.agent_pack_search_queries(item, 1, language="Italian", context=None)
    joined = "\n".join(queries)
    assert "Widows Bay S01" in joined
    assert "Widow's Bay S01" in joined
    assert "Widow Bay S01" in joined
    assert joined.index("Widows Bay S01") < joined.index("Widow Bay S01")


async def test_title_authority_is_enriched_before_interactive_search() -> None:
    tv = TvShowCategory()
    item = SimpleNamespace(key="Widow Bay", display_name=None, metadata={})
    enricher = FakeEnricher()
    setattr(enricher, "enrich" + "_series", enricher._enrich_impl)
    context = SimpleNamespace(metadata_enricher=enricher, metadata_clients={})
    enriched = await tv._ensure_agent_title_authority(item, context)
    assert enriched.display_name == "Widows Bay"
    assert "Widows Bay" in enriched.metadata["title_aliases"]
    assert "La Baia delle Vedove" in [row["title"] for row in enriched.metadata["localized_titles"]]


def main() -> None:
    test_provider_aliases_are_primary_title_gate()
    test_provider_aliases_prevent_broad_token_collapse()
    test_query_titles_prefer_provider_and_localized_titles()
    asyncio.run(test_search_ladder_uses_authoritative_titles_not_only_user_text())
    asyncio.run(test_title_authority_is_enriched_before_interactive_search())
    print("round255_metadata_title_authority_tests: OK")


if __name__ == "__main__":
    main()
