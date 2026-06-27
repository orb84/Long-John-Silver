#!/usr/bin/env python3
"""Round 233 TV download/search regression checks."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.media_title_repair import MediaTitleRepair
from src.core.categories.tv import TvShowCategory
from src.ai.tools.search_workspace import SearchBatchRecommendationBuilder


class _PackQueryTv(TvShowCategory):
    async def _expected_episode_count(self, title, season, arguments, context):  # type: ignore[override]
        return 6

    async def resolve_agent_pack_season(self, item, context):  # type: ignore[override]
        return 1


def assert_true(value: object, message: str) -> None:
    if not value:
        raise AssertionError(message)


def test_literal_title_repair() -> None:
    repaired = MediaTitleRepair.recover_literal_title(
        "A Knight the Seven Kingdoms",
        "Lets wait on that. Instead, please grab me A Knight of the Seven Kingdoms in italian",
    )
    assert repaired == "A Knight of the Seven Kingdoms", repaired


def test_tv_exact_language_query_is_primary_and_preserved() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Star City", language="Italian")
    primary = tv.build_search_query(item, "S01E03", "Italian")
    assert "S01E03" in primary, primary
    assert "Ita" in primary or "ITA" in primary or "Italian" in primary, primary
    alternatives = tv.build_alternative_search_queries(item, "S01E03", "Italian")
    joined = "\n".join(alternatives[:6])
    assert "Star City S01E03" in joined, joined
    assert "S01E03" in joined and ("Ita" in joined or "ITA" in joined or "Italian" in joined), joined
    # Language-specific exact episode queries must appear before broad pack/season forms.
    language_index = next(i for i, q in enumerate(alternatives) if "S01E03" in q and ("Ita" in q or "ITA" in q or "Italian" in q))
    broad_pack_index = next(i for i, q in enumerate(alternatives) if q.endswith("S01") or q.endswith(".S01"))
    assert language_index < broad_pack_index, alternatives


def test_tv_pack_queries_prioritize_episode_range_language() -> None:
    async def run() -> None:
        tv = _PackQueryTv()
        item = tv.create_item("A Knight of the Seven Kingdoms", language="Italian")
        queries = await tv.agent_pack_search_queries(item, 1, language="Italian", context=object())
        joined = "\n".join(queries)
        assert any("S01E01-E06" in q for q in queries[:4]), queries
        assert any("S01E01-06" in q for q in queries[:6]), queries
        assert any(("Ita" in q or "ITA" in q or "Italian" in q) and "S01E01" in q for q in queries[:6]), queries
        assert len(queries) <= 12, queries
        assert "A Knight of the Seven Kingdoms" in joined, joined

    asyncio.run(run())


def test_tv_default_scope_for_title_only_download_is_bundle_preferred() -> None:
    tv = TvShowCategory()
    item = tv.create_item("A Knight of the Seven Kingdoms", language="Italian")
    scope = tv.default_agent_search_scope(item, season=None, episode=None, search_scope="default", language="Italian", context=None)
    assert scope == "bundle_preferred", scope
    explicit = tv.default_agent_search_scope(item, season=None, episode=None, search_scope="individual_units_only", language="Italian", context=None)
    assert explicit == "individual_units_only", explicit


def test_broad_default_search_does_not_make_fake_batch() -> None:
    result = SearchBatchRecommendationBuilder.build(
        name="A Knight of the Seven Kingdoms",
        category_id="tv",
        season=None,
        episode=None,
        search_scope="default",
        result_set_id="rs_test",
        candidates=[
            {"candidate_id": "a", "title": "A Knight of the Seven Kingdoms S01E01 Ita", "seeders": 10},
            {"candidate_id": "b", "title": "Game of Thrones S08E02 A Knight of the Seven Kingdoms", "seeders": 20},
        ],
        category=TvShowCategory(),
        preferred_language="Italian",
    )
    assert result is None, result


def main() -> None:
    test_literal_title_repair()
    test_tv_exact_language_query_is_primary_and_preserved()
    test_tv_pack_queries_prioritize_episode_range_language()
    test_tv_default_scope_for_title_only_download_is_bundle_preferred()
    test_broad_default_search_does_not_make_fake_batch()
    print("round233 TV download/search regression tests passed")


if __name__ == "__main__":
    main()
