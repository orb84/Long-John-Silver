"""
Tests for the Detailed Logging Subsystem.

Validates asynchronous file writing, size-based rotation, thread-safety,
and the formatting of specialized loggers (Chat, LLM, Search, Torrent, Structured).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
import pytest

from src.utils.detailed_logger import (
    ThreadSafeFileWriter,
    ChatLogger,
    LLMLogger,
    SearchLogger,
    TorrentLogger,
    StructuredReplyLogger,
    DetailedLoggingSubsystem,
)
from src.core.models import NormalizedTorrentCandidate

TEST_LOGS_DIR = Path("test_detailed_logs")


@pytest.fixture(autouse=True)
def setup_and_cleanup_logs_dir() -> None:
    """Fixture to ensure the test logs directory is fresh and deleted after tests."""
    if TEST_LOGS_DIR.exists():
        shutil.rmtree(TEST_LOGS_DIR)
    TEST_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    if TEST_LOGS_DIR.exists():
        shutil.rmtree(TEST_LOGS_DIR)


class TestThreadSafeFileWriter:
    """Tests the core asynchronous thread-safe log writer and log rotation."""

    @pytest.mark.asyncio
    async def test_write_and_rotation(self) -> None:
        """Verify that writes go through and log rotation occurs when max_bytes is exceeded."""
        log_file = TEST_LOGS_DIR / "rotation.log"
        # We instantiate a writer and manually override the private size limit to 50 bytes for testing
        writer = ThreadSafeFileWriter(log_file)
        writer._max_bytes = 50

        # First write (should fit easily)
        await writer.write("Line 1 - short message\n")
        assert log_file.exists()
        content = log_file.read_text()
        assert "Line 1" in content

        # Second write (exceeds 50 bytes total but doesn't rotate yet as size was < 50)
        await writer.write("Line 2 - a very long message that definitely exceeds fifty bytes in total size\n")
        
        # Third write (triggers rotation because active log size is now 23 + 79 = 102 >= 50)
        await writer.write("Line 3 - triggers rotation!\n")

        # Verify rollover files
        rotated_file = TEST_LOGS_DIR / "rotation.log.1"
        assert rotated_file.exists()
        assert log_file.exists()

        # The first lines should have been rotated to the backup
        backup_content = rotated_file.read_text()
        assert "Line 1" in backup_content
        assert "Line 2" in backup_content

        # The new long line should be in the active log
        active_content = log_file.read_text()
        assert "Line 3" in active_content

    @pytest.mark.asyncio
    async def test_concurrent_writes(self) -> None:
        """Verify thread-safety and ordering by executing multiple writes concurrently."""
        log_file = TEST_LOGS_DIR / "concurrent.log"
        writer = ThreadSafeFileWriter(log_file)

        # Launch 50 concurrent writes
        tasks = [writer.write(f"Line {i}\n") for i in range(50)]
        await asyncio.gather(*tasks)

        assert log_file.exists()
        lines = log_file.read_text().splitlines()
        assert len(lines) == 50
        # Ensure all lines are written
        for i in range(50):
            assert any(f"Line {i}" in line for line in lines)


class TestSpecializedLoggers:
    """Validates the formatting and persistence of all specialized logger implementations."""

    @pytest.mark.asyncio
    async def test_chat_logger(self) -> None:
        """Test chat message logging formatting."""
        log_file = TEST_LOGS_DIR / "chat.log"
        writer = ThreadSafeFileWriter(log_file)
        logger = ChatLogger(writer)
        await logger.log_message(sender="USER", content="Hello, Silver!", session_id="session123")

        content = log_file.read_text()
        assert "session123" in content
        assert "USER" in content
        assert "Hello, Silver!" in content

    @pytest.mark.asyncio
    async def test_llm_logger(self) -> None:
        """Test LLM context and raw response logging."""
        context_file = TEST_LOGS_DIR / "llm_context.log"
        response_file = TEST_LOGS_DIR / "llm_raw_response.log"

        context_writer = ThreadSafeFileWriter(context_file)
        response_writer = ThreadSafeFileWriter(response_file)
        logger = LLMLogger(context_writer, response_writer)
        messages = [{"role": "user", "content": "What is 2+2?"}]
        await logger.log_context(messages=messages, model="gpt-4", task="math")
        await logger.log_raw_response(task="math", raw_text="4", model="gpt-4")

        ctx_content = context_file.read_text()
        resp_content = response_file.read_text()

        assert "gpt-4" in ctx_content
        assert "What is 2+2?" in ctx_content
        assert "math" in ctx_content
        assert "gpt-4" in resp_content
        assert "4" in resp_content

    @pytest.mark.asyncio
    async def test_search_logger(self) -> None:
        """Test aggregate searches logging."""
        log_file = TEST_LOGS_DIR / "searches.log"
        writer = ThreadSafeFileWriter(log_file)
        logger = SearchLogger(writer)

        await logger.log_search(
            query="Breaking Bad S01",
            category="tv",
            active_providers=["JackettSearch"],
            total_raw=10,
            unique_deduped=5,
            quality_filtered=3,
        )

        content = log_file.read_text()
        assert "Breaking Bad S01" in content
        assert "JackettSearch" in content
        assert "Quality Filtered (Accepted): 3" in content

    @pytest.mark.asyncio
    async def test_torrent_logger(self) -> None:
        """Test torrent candidate scoring and selection logs."""
        log_file = TEST_LOGS_DIR / "torrents.log"
        writer = ThreadSafeFileWriter(log_file)
        logger = TorrentLogger(writer)

        candidates = [
            NormalizedTorrentCandidate(
                title="Show.S01E01.1080p",
                source="Jackett",
                magnet="magnet:?xt=urn:btih:1",
                seeders=15,
                size="1.0 GB",
                info_hash="1",
                quality_score=0.8,
                detail_url="http://example.com/1",
                codec="h264",
                resolution="1080p",
            )
        ]

        await logger.log_candidates(
            item_name="Show",
            episode="S01E01",
            candidates=candidates,
            preferred_lang="it",
            selected_index=0,
            selected_title="Show.S01E01.1080p",
        )

        content = log_file.read_text()
        assert "S01E01" in content
        assert "Jackett" in content
        assert "Quality Score: 0.8" in content
        assert "Selected Title: 'Show.S01E01.1080p'" in content

    @pytest.mark.asyncio
    async def test_structured_reply_logger(self) -> None:
        """Test structured intent routing and plan execution step logs."""
        log_file = TEST_LOGS_DIR / "structured_replies.log"
        writer = ThreadSafeFileWriter(log_file)
        logger = StructuredReplyLogger(writer)

        await logger.log_intent(query="download Show", routed_intent="download", confidence=0.99)
        await logger.log_plan(
            user_goal="download Show",
            intent="download",
            steps=[{"tool_name": "SearchShow", "arguments": {"query": "Show"}}],
        )

        content = log_file.read_text()
        assert "Routed Intent: download" in content
        assert "SearchShow" in content


class TestDetailedLoggingSubsystem:
    """Validates the DetailedLoggingSubsystem high-level coordinator class."""

    def test_initialization(self) -> None:
        """Ensure all sub-loggers are instantiated correctly inside the subsystem coordinator."""
        subsystem = DetailedLoggingSubsystem(log_dir=str(TEST_LOGS_DIR))
        assert subsystem.chat_logger is not None
        assert subsystem.llm_logger is not None
        assert subsystem.search_logger is not None
        assert subsystem.torrent_logger is not None
        assert subsystem.structured_logger is not None
