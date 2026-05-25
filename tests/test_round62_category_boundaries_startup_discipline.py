"""Regression tests for category-boundary and startup-discipline fixes."""

from __future__ import annotations

import pytest

from src.ai.chat_presenter import AgentChatPresenter
from src.core.categories.metadata.enricher import MetadataRepairer
from src.core.models import CategoryItem, MovieItem
from src.core.taste_profiler import TasteMetadataRuntimeContext, TasteProfiler


@pytest.mark.asyncio
async def test_metadata_repairer_never_cross_category_rewrites_movie_items() -> None:
    """Provider hits must not move an item out of the scanned category root."""
    repairer = MetadataRepairer(settings_manager=object(), enricher=object())

    repaired = await repairer.repair_item(MovieItem(key="The Lego Batman Movie", discovered=True))

    assert repaired is None


def test_progress_messages_do_not_claim_specific_torrent_work_for_generic_questions() -> None:
    """Waiting pings should be persona-based, not hard-coded to torrent workflows."""
    presenter = AgentChatPresenter()
    text = "\n".join(presenter.progress("what is the weather tomorrow?", tick=i).lower() for i in range(8))

    banned = {"torrent", "language", "seeders", "quality", "episode", "download manifest"}
    assert not any(word in text for word in banned)


class _FakeMediaRepository:
    """Minimal metadata repository for profiler regression tests."""

    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    async def get_category_metadata(self, category_id: str, item_id: str, provider: str | None = None) -> list[dict[str, object]]:
        return [row for row in self.rows if row["category_id"] == category_id and row["item_id"] == item_id]

    async def upsert_category_metadata(
        self,
        category_id: str,
        item_id: str,
        provider: str,
        metadata: dict[str, object],
        external_id: str,
    ) -> None:
        self.rows.append({
            "category_id": category_id,
            "item_id": item_id,
            "provider": provider,
            "metadata": metadata,
            "external_id": external_id,
        })


class _FakeDatabase:
    """Minimal database facade exposing metadata storage."""

    def __init__(self) -> None:
        self.media = _FakeMediaRepository()


class _BookItem(CategoryItem):
    """Custom item used to prove startup can skip provider enrichment."""

    @property
    def item_type(self) -> str:
        return "book"


class _ExplodingCategory:
    """Category hook that must not be called during startup profile builds."""

    category_id = "book"

    async def enrich_taste_metadata(self, item: CategoryItem, context: TasteMetadataRuntimeContext) -> dict[str, object]:
        raise AssertionError("startup taste profile should not enrich missing provider metadata")


class _FakeRegistry:
    """Registry stub returning an enrichment-capable category."""

    def get(self, category_id: str) -> _ExplodingCategory | None:
        return _ExplodingCategory() if category_id == "book" else None


@pytest.mark.asyncio
async def test_taste_profile_startup_mode_skips_provider_enrichment() -> None:
    """Startup profile building should not fan out across TMDB/TVMaze providers."""
    db = _FakeDatabase()
    profiler = TasteProfiler(db=db, category_registry=_FakeRegistry())

    profile = await profiler.build_profile([_BookItem(key="dune")], enrich_missing=False)

    assert profile.category_counts == {"book": 1}
    assert db.media.rows == []


def test_metadata_search_rejects_weak_title_only_hits() -> None:
    """Loose metadata matching should not accept a different popular title."""
    from src.core.categories.metadata.enricher import TMDBMetadataEnricher

    enricher = TMDBMetadataEnricher(tmdb_client=None)
    result = enricher._choose_best_search_result(
        [{"id": 1, "title": "The Batman", "vote_count": 20000}],
        "The Lego Batman Movie",
        None,
    )

    assert result is None


def test_metadata_search_accepts_subtitle_variants() -> None:
    """Strictness should still allow known release-title variants."""
    from src.core.categories.metadata.enricher import TMDBMetadataEnricher

    enricher = TMDBMetadataEnricher(tmdb_client=None)
    result = enricher._choose_best_search_result(
        [{"id": 2, "title": "Babe", "vote_count": 5000}],
        "Babe Maialino Coraggioso",
        None,
    )

    assert result and result["id"] == 2
