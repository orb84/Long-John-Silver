"""Regression tests for evidence-based category taste memory."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.ai.taste_signal_ingestion import TasteSignalIngestionService
from src.core.models import Intent
from src.core.taste_profiler import TasteProfiler


class FakeLLM:
    """LLM fake returning strict JSON extraction output."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content, tool_calls=[]))])


class MemorySystemRepo:
    """In-memory signal/facet/snapshot store with the production repo contract."""

    def __init__(self) -> None:
        self.signals: list[dict] = []
        self.facet_scores: list[dict] = []
        self.snapshots: list[dict] = []

    async def upsert_taste_signal(self, signal: dict) -> int:
        row = dict(signal)
        row["id"] = len(self.signals) + 1
        self.signals.append(row)
        return row["id"]

    async def list_taste_signals(self, user_id=None, category_id=None, signal_types=None, limit=200):
        rows = [row for row in self.signals if user_id is None or row.get("user_id") == user_id]
        if category_id:
            rows = [row for row in rows if row.get("category_id") == category_id]
        if signal_types:
            rows = [row for row in rows if row.get("signal_type") in signal_types]
        return rows[:limit]

    async def replace_taste_facet_scores(self, user_id: str, category_id: str, scores: list[dict]) -> None:
        self.facet_scores = [
            row for row in self.facet_scores
            if not (row.get("user_id") == user_id and row.get("category_id") == category_id)
        ]
        for score in scores:
            row = dict(score)
            row["user_id"] = user_id
            row["category_id"] = category_id
            self.facet_scores.append(row)

    async def list_taste_facet_scores(self, user_id=None, category_id=None, facet_key=None, limit=500):
        rows = [row for row in self.facet_scores if user_id is None or row.get("user_id") == user_id]
        if category_id:
            rows = [row for row in rows if row.get("category_id") == category_id]
        if facet_key:
            rows = [row for row in rows if row.get("facet_key") == facet_key]
        return rows[:limit]

    async def upsert_taste_profile_snapshot(self, user_id, category_id, profile, summary="", evidence_count=0):
        self.snapshots.append({
            "user_id": user_id,
            "category_id": category_id,
            "profile": profile,
            "summary": summary,
            "evidence_count": evidence_count,
        })


class EmptyMediaRepo:
    async def get_all_category_metadata(self, category_id=None):
        return []


class TasteDB:
    def __init__(self) -> None:
        self.system = MemorySystemRepo()
        self.media = EmptyMediaRepo()


@pytest.mark.asyncio
async def test_llm_led_ingestion_records_explicit_like_with_interpreted_facets() -> None:
    db = TasteDB()
    profiler = TasteProfiler(db=db)
    llm = FakeLLM(
        '{"signals":[{"category_id":"movie","item_id":"Heat","display_name":"Heat",'
        '"signal_type":"explicit_like","polarity":"positive","strength":0.9,"confidence":0.95,'
        '"evidence_text":"I loved Heat years ago",'
        '"metadata":{"genres":["Crime","Thriller"],"directors":["Michael Mann"]},'
        '"interpreted_facets":{"liked_aspects":["grounded realism","procedural tension"],'
        '"do_not_infer":["all thrillers"]}}]}'
    )
    service = TasteSignalIngestionService(
        llm_client=llm,
        settings=SimpleNamespace(llm=SimpleNamespace(
            get_model_for_task=lambda task: "test",
            get_api_base_for_task=lambda task: None,
            get_api_key_for_task=lambda task: None,
            get_max_tokens_for_task=lambda task: None,
            get_temperature_for_task=lambda task: None,
        )),
        taste_profiler=profiler,
    )

    result = await service.ingest_user_turn(
        user_message="I loved Heat years ago.",
        assistant_response="Great film.",
        user_id="captain",
        active_category_id="movie",
        intent=Intent.CHAT,
    )

    assert result.stored == 1
    assert db.system.signals[0]["signal_type"] == "explicit_like"
    assert db.system.signals[0]["polarity"] == "positive"
    assert db.system.signals[0]["interpreted_facets"]["liked_aspects"] == ["grounded realism", "procedural tension"]
    aspect_scores = [row for row in db.system.facet_scores if row["facet_key"] == "aspects"]
    assert {row["facet_value"] for row in aspect_scores} >= {"grounded realism", "procedural tension"}


@pytest.mark.asyncio
async def test_negative_item_feedback_does_not_become_strong_genre_dislike() -> None:
    db = TasteDB()
    profiler = TasteProfiler(db=db)

    await profiler.record_taste_signal(
        category_id="movie",
        item_id="one-thriller",
        display_name="One Thriller",
        signal_type="explicit_dislike",
        polarity="negative",
        strength=0.9,
        confidence=0.95,
        metadata={"genres": ["Thriller"]},
        interpreted_facets={"do_not_infer": ["all thrillers"]},
        user_id="captain",
        evidence_text="I didn't like this one.",
    )

    profile = await profiler.build_category_profile("movie", user_id="captain", include_library=False)
    thriller_score = profile.genres.counts.get("Thriller", 0)

    assert thriller_score < 0
    assert thriller_score > -0.1
    assert "One Thriller" not in profile.top_items


@pytest.mark.asyncio
async def test_downloaded_library_signal_is_interest_not_like() -> None:
    db = TasteDB()
    profiler = TasteProfiler(db=db)

    await profiler.record_taste_signal(
        category_id="movie",
        item_id="curious-movie",
        display_name="Curious Movie",
        signal_type="downloaded",
        polarity="interest",
        strength=0.25,
        confidence=0.9,
        metadata={"genres": ["War"], "themes": ["historical detail"]},
        user_id="captain",
    )

    profile = await profiler.build_category_profile("movie", user_id="captain", include_library=False)

    assert profile.top_items == ["Curious Movie"]
    assert 0 < profile.genres.counts["War"] < 0.05
    assert profile.metadata_dimensions["themes"]["historical detail"] < 0.05
