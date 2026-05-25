"""Tests for category-owned taste-profile metadata enrichment."""

from __future__ import annotations

import pytest

from src.core.models import CategoryItem
from src.core.taste_profiler import TasteMetadataRuntimeContext, TasteProfiler


class FakeMediaRepository:
    """In-memory category metadata repository used by taste-profiler tests."""

    def __init__(self) -> None:
        """Initialize an empty metadata row list."""
        self.rows: list[dict[str, object]] = []

    async def get_category_metadata(
        self,
        category_id: str,
        item_id: str,
        provider: str | None = None,
    ) -> list[dict[str, object]]:
        """Return metadata rows matching the category item."""
        return [
            row for row in self.rows
            if row["category_id"] == category_id and row["item_id"] == item_id
        ]

    async def upsert_category_metadata(
        self,
        category_id: str,
        item_id: str,
        provider: str,
        metadata: dict[str, object],
        external_id: str,
    ) -> None:
        """Record the category-owned metadata envelope."""
        self.rows.append({
            "category_id": category_id,
            "item_id": item_id,
            "provider": provider,
            "metadata": metadata,
            "external_id": external_id,
        })


class FakeDatabase:
    """Minimal database facade exposing only the media repository."""

    def __init__(self) -> None:
        """Create the in-memory repository."""
        self.media = FakeMediaRepository()


class BookItem(CategoryItem):
    """Custom non-video category item used to prove generic dispatch."""

    @property
    def item_type(self) -> str:
        """Return the custom category id."""
        return "book"


class BookCategory:
    """Custom category that owns taste metadata enrichment."""

    category_id = "book"

    async def enrich_taste_metadata(
        self,
        item: CategoryItem,
        context: TasteMetadataRuntimeContext,
    ) -> dict[str, object]:
        """Return category-owned metadata without generic profiler branching."""
        return {
            "provider": "book_provider",
            "genres": ["Science Fiction"],
            "cast_names": ["Narrator"],
            "rating": 4.5,
        }

    def taste_metadata_provider_name(self, metadata: dict[str, object]) -> str:
        """Return the provider name from the metadata envelope."""
        return str(metadata.get("provider") or "book_taste")


class FakeCategoryRegistry:
    """Small category registry stub for taste-profiler tests."""

    def __init__(self) -> None:
        """Register the custom category."""
        self.category = BookCategory()

    def get(self, category_id: str) -> BookCategory | None:
        """Return the custom category when requested."""
        return self.category if category_id == "book" else None


@pytest.mark.asyncio
async def test_taste_profiler_uses_category_enrichment_hook() -> None:
    """TasteProfiler should aggregate metadata provided by the owning category."""
    db = FakeDatabase()
    profiler = TasteProfiler(
        db=db,
        category_registry=FakeCategoryRegistry(),
        metadata_context=TasteMetadataRuntimeContext(),
    )

    profile = await profiler.build_profile([BookItem(key="dune")])

    assert profile.category_counts == {"book": 1}
    assert profile.genres.primary == ["Science Fiction"]
    assert profile.people.actors == {"Narrator": 1}
    assert profile.top_items == ["dune"]
    assert db.media.rows[0]["provider"] == "book_provider"

class FakeSystemRepository:
    """In-memory category taste signal store for profiler tests."""

    def __init__(self) -> None:
        self.signals: list[dict[str, object]] = []

    async def upsert_taste_signal(self, signal: dict[str, object]) -> int:
        self.signals.append(dict(signal))
        return len(self.signals)

    async def list_taste_signals(
        self,
        user_id: str | None = None,
        category_id: str | None = None,
        signal_types: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        rows = [row for row in self.signals if (not user_id or row.get("user_id") == user_id)]
        if category_id:
            rows = [row for row in rows if row.get("category_id") == category_id]
        if signal_types:
            rows = [row for row in rows if row.get("signal_type") in signal_types]
        return rows[:limit]


class TasteSignalDatabase(FakeDatabase):
    """Fake database facade with media and system repositories."""

    def __init__(self) -> None:
        super().__init__()
        self.system = FakeSystemRepository()


@pytest.mark.asyncio
async def test_category_taste_signals_are_scoped_and_weighted() -> None:
    """Profiler should aggregate explicit and researched signals per category."""
    db = TasteSignalDatabase()
    profiler = TasteProfiler(db=db, category_registry=FakeCategoryRegistry())

    await profiler.record_taste_signal(
        category_id="book",
        item_id="dune",
        display_name="Dune",
        signal_type="like",
        metadata={"genres": ["Science Fiction"], "overview": "Desert politics."},
        user_id="captain",
        notes="User praised dense political worldbuilding.",
    )
    await profiler.record_taste_signal(
        category_id="book",
        item_id="bad-romance",
        display_name="Bad Romance",
        signal_type="dislike",
        metadata={"genres": ["Romance"]},
        user_id="captain",
    )

    profile = await profiler.build_category_profile("book", user_id="captain", include_library=False)

    assert profile.top_items == ["Dune"]
    assert profile.genres.primary == ["Science Fiction"]
    assert profile.genres.counts["Science Fiction"] > 0
    assert profile.genres.counts["Romance"] < 0


@pytest.mark.asyncio
async def test_category_profile_prompt_text_is_category_tagged() -> None:
    """Prompt formatting should make category-scoped memory explicit."""
    db = TasteSignalDatabase()
    profiler = TasteProfiler(db=db, category_registry=FakeCategoryRegistry())
    await profiler.record_taste_signal(
        category_id="book",
        item_id="dune",
        display_name="Dune",
        signal_type="favorite",
        metadata={"genres": ["Science Fiction"]},
    )

    profile = await profiler.build_category_profile("book", include_library=False)
    text = profiler.format_category_profile_for_prompt("book", profile)

    assert "CATEGORY TASTE PROFILE [book]" in text
    assert "Science Fiction" in text
    assert "Dune" in text

@pytest.mark.asyncio
async def test_category_taste_profile_keeps_custom_dimensions() -> None:
    """Custom categories can teach the taste profile about non-movie fields."""
    db = TasteSignalDatabase()
    profiler = TasteProfiler(db=db, category_registry=FakeCategoryRegistry())

    await profiler.record_taste_signal(
        category_id="video_game",
        item_id="outer-wilds",
        display_name="Outer Wilds",
        signal_type="like",
        metadata={
            "genres": ["Adventure"],
            "platforms": ["PC", "Switch"],
            "mechanics": ["exploration", "time loop"],
            "studios": ["Mobius Digital"],
        },
    )

    profile = await profiler.build_category_profile("video_game", include_library=False)
    prompt = profiler.format_category_profile_for_prompt("video_game", profile)

    assert profile.metadata_dimensions["platforms"]["PC"] > 0
    assert profile.metadata_dimensions["mechanics"]["time loop"] > 0
    assert "Platforms" in prompt
    assert "time loop" in prompt
