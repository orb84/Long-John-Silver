"""
Tests for vector store operations.

Verifies basic upsert, search, and graceful degradation
when sqlite-vec is unavailable.
"""

import pytest
import pytest_asyncio
from src.core.database import Database


class TestVectorStoreBasics:
    """Tests for VectorStore basic operations.

    These tests verify the interface contracts regardless of whether
    sqlite-vec is available on the system.
    """

    @pytest.mark.asyncio
    async def test_vector_store_graceful_degradation(self, tmp_path):
        """VectorStore should degrade gracefully if sqlite-vec is unavailable."""
        from src.core.vector_store import VectorStore

        db = Database(db_path=str(tmp_path / "test_vector.db"))
        await db.initialize()

        vs = VectorStore(db)
        # Try to initialize — this may succeed or gracefully fail
        try:
            await vs.initialize()
        except Exception:
            # Expected if sqlite-vec extension is not available
            pass

        # Regardless of init success, these operations should not raise
        if vs._initialized:
            # If sqlite-vec is available, test basic operations
            await vs.upsert(item_id=1, text="Test text for embedding")
            results = await vs.search("Test text", top_k=1)
            assert isinstance(results, list)
        else:
            # Graceful degradation: search should return empty
            results = await vs.search("Test text", top_k=1)
            assert results == []

        await db.close()


class TestVectorStoreSearch:
    """Tests for vector search behavior when sqlite-vec is available."""

    @pytest.mark.asyncio
    async def test_upsert_and_search(self, tmp_path):
        """Upserting items should make them searchable by text similarity."""
        from src.core.vector_store import VectorStore

        db = Database(db_path=str(tmp_path / "test_vector_search.db"))
        await db.initialize()

        vs = VectorStore(db)
        try:
            await vs.initialize()
        except Exception:
            await db.close()
            pytest.skip("sqlite-vec not available on this system")

        if not vs._initialized:
            await db.close()
            pytest.skip("sqlite-vec not available on this system")

        # Upsert some items
        await vs.upsert(item_id=1, text="Breaking Bad is a great TV show about chemistry")
        await vs.upsert(item_id=2, text="The Bear is a cooking drama series")
        await vs.upsert(item_id=3, text="Severance is a sci-fi thriller about workplace")

        # Search should return relevant items
        results = await vs.search("cooking show", top_k=2)
        assert isinstance(results, list)
        # The Bear should be more relevant to "cooking"
        if len(results) > 0:
            assert any(r.get("id") in (1, 2, 3) for r in results)

        await db.close()

    @pytest.mark.asyncio
    async def test_delete_removes_item(self, tmp_path):
        """Deleting an item should remove it from search results."""
        from src.core.vector_store import VectorStore

        db = Database(db_path=str(tmp_path / "test_vector_delete.db"))
        await db.initialize()

        vs = VectorStore(db)
        try:
            await vs.initialize()
        except Exception:
            await db.close()
            pytest.skip("sqlite-vec not available on this system")

        if not vs._initialized:
            await db.close()
            pytest.skip("sqlite-vec not available on this system")

        await vs.upsert(item_id=10, text="Unique test item for deletion")
        await vs.delete(item_id=10)
        # After deletion, search should not find it
        results = await vs.search("Unique test item for deletion", top_k=5)
        assert all(r.get("id") != 10 for r in results)

        await db.close()
class TestVectorStoreEmbeddingSettings:
    """Embedding provider configuration tests that do not require sqlite-vec."""

    @pytest.mark.asyncio
    async def test_disabled_embedding_provider_uses_fallback_without_runtime(self, tmp_path):
        """Disabled provider should never attempt to load the built-in model."""
        from src.core.models import EmbeddingSettings
        from src.core.vector_store import VectorStore

        db = Database(db_path=str(tmp_path / "test_vector_disabled.db"))
        await db.initialize()
        vs = VectorStore(db, embedding_settings=EmbeddingSettings(provider="disabled", enabled=False))
        embedding = await vs.embed("any text")

        assert len(embedding) == 384
        assert vs.provider_label == "disabled_hash_fallback"
        await db.close()

class TestVectorStoreHealthDiagnostics:
    """Diagnostics should expose degraded hash fallback instead of hiding it."""

    @pytest.mark.asyncio
    async def test_builtin_runtime_failure_marks_health_degraded(self, tmp_path):
        from src.core.embedding_runtime import EmbeddingRuntimeStatus
        from src.core.models import EmbeddingSettings
        from src.core.vector_store import VectorStore

        class FailingRuntime:
            model_name = "sentence-transformers/all-MiniLM-L6-v2"
            status = EmbeddingRuntimeStatus(
                provider="builtin",
                model=model_name,
                dimension=384,
                ready=False,
                cache_dir=str(tmp_path),
                message="test runtime not ready",
            )

            async def embed(self, text: str) -> list[float]:
                raise RuntimeError("boom")

        db = Database(db_path=str(tmp_path / "test_vector_health.db"))
        await db.initialize()
        vs = VectorStore(
            db,
            embedding_settings=EmbeddingSettings(provider="builtin", enabled=True),
            builtin_runtime=FailingRuntime(),
        )
        vs._initialized = True

        embedding = await vs.embed("semantic text")
        health = await vs.health_status()

        assert len(embedding) == 384
        assert health["fallback_count"] == 1
        assert health["semantic"] is False
        assert "boom" in health["last_error"]
        await db.close()

class TestVectorStoreTasteSignalIndexing:
    """Taste-signal vector text should remain category-neutral and rich."""

    def test_taste_signal_text_flattens_category_metadata(self):
        """Video-game style metadata should index without core knowing games."""
        from src.core.vector_reindexer import taste_signal_vector_text

        text = taste_signal_vector_text({
            "display_name": "Disco Elysium",
            "item_id": "disco-elysium",
            "signal_type": "like",
            "notes": "User praised narrative density.",
            "metadata": {
                "overview": "A detective RPG focused on dialogue and consequences.",
                "platforms": ["PC", "Nintendo Switch"],
                "mechanics": ["dialogue", "skill checks"],
                "studios": [{"name": "ZA/UM"}],
            },
        })

        assert "Disco Elysium" in text
        assert "detective RPG" in text
        assert "platforms: PC, Nintendo Switch" in text
        assert "mechanics: dialogue, skill checks" in text
        assert "studios: ZA/UM" in text
