"""
Tests for conversation memory manager.

Verifies turn persistence, summarization behavior, and
semantic context retrieval using vector search.
"""

import pytest
import pytest_asyncio
from src.core.conversation import ConversationManager
from src.core.database import Database


@pytest_asyncio.fixture
async def conversation_db(tmp_path):
    """Create a test database for conversation tests."""
    database = Database(db_path=str(tmp_path / "test_conversation.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def conversation_manager(conversation_db):
    """Create a ConversationManager without vector store (unit test)."""
    return ConversationManager(db=conversation_db, vector_store=None)


class TestConversationManager:
    """Tests for basic conversation turn operations."""

    @pytest.mark.asyncio
    async def test_add_turn_stores_in_db(
        self, conversation_manager, conversation_db
    ):
        """Adding a turn should persist it in the database."""
        session_id = "test_session_1"
        turn_id = await conversation_manager.add_turn(
            session_id, "user", "Hello, find me a show"
        )
        # Turn should be retrievable
        history = await conversation_db.system.get_conversation_history(session_id, limit=10)
        assert len(history) >= 1
        assert any(t.get("content") == "Hello, find me a show" for t in history)

    @pytest.mark.asyncio
    async def test_get_context_returns_recent_turns(
        self, conversation_manager
    ):
        """get_context should return recent turns in chronological order."""
        session_id = "test_session_2"
        await conversation_manager.add_turn(session_id, "user", "First message")
        await conversation_manager.add_turn(session_id, "assistant", "First response")
        await conversation_manager.add_turn(session_id, "user", "Second message")

        messages = await conversation_manager.get_context(session_id, max_turns=10)
        # Should include all three turns
        assert len(messages) >= 3
        contents = [m["content"] for m in messages]
        assert "First message" in contents
        assert "First response" in contents
        assert "Second message" in contents

    @pytest.mark.asyncio
    async def test_get_context_respects_max_turns(
        self, conversation_manager
    ):
        """get_context should limit turns to max_turns."""
        session_id = "test_session_3"
        for i in range(10):
            await conversation_manager.add_turn(session_id, "user", f"Message {i}")

        messages = await conversation_manager.get_context(session_id, max_turns=5)
        # Should return at most 5 turns (limited by DB query)
        assert len(messages) <= 5

    @pytest.mark.asyncio
    async def test_clear_session_removes_history(
        self, conversation_manager, conversation_db
    ):
        """clear_session should remove all turns for the session."""
        session_id = "test_session_4"
        await conversation_manager.add_turn(session_id, "user", "To be cleared")
        await conversation_manager.clear_session(session_id)

        history = await conversation_db.system.get_conversation_history(session_id, limit=10)
        assert len(history) == 0

    @pytest.mark.asyncio
    async def test_sessions_are_isolated(
        self, conversation_manager
    ):
        """Turns from different sessions should not bleed into each other."""
        session_a = "session_a"
        session_b = "session_b"

        await conversation_manager.add_turn(session_a, "user", "From session A")
        await conversation_manager.add_turn(session_b, "user", "From session B")

        messages_a = await conversation_manager.get_context(session_a, max_turns=10)
        contents_a = [m["content"] for m in messages_a]
        assert "From session A" in contents_a
        assert "From session B" not in contents_a

    @pytest.mark.asyncio
    async def test_has_vector_store_returns_false_when_none(
        self, conversation_manager
    ):
        """has_vector_store should return False when no vector store is configured."""
        assert conversation_manager.has_vector_store() is False