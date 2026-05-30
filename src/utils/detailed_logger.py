"""
Detailed logging subsystem for LJS.

Provides highly structured, multi-file logs for conversational tracking,
LLM query prompts/context, raw LLM text responses, parsed plans/intents,
indexer search queries, and full torrent evaluations. All file operations
are thread-safe and offloaded asynchronously via asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Sequence


class ThreadSafeFileWriter:
    """Thread-safe, non-blocking file writer that offloads I/O to threads.

    Automatically handles file rotation when size exceeds the configured max limit.
    """

    def __init__(self, file_path: Path, max_bytes: int = 10 * 1024 * 1024) -> None:
        """Initialize the file writer.

        Args:
            file_path: The target log file path.
            max_bytes: The maximum size in bytes before file rotation.
        """
        self._file_path = file_path
        self._max_bytes = max_bytes
        self._lock = asyncio.Lock()

    async def write(self, content: str) -> None:
        """Write the given string content to the log file asynchronously.

        Args:
            content: The text content to write.
        """
        async with self._lock:
            await asyncio.to_thread(self._sync_write, content)

    def _sync_write(self, content: str) -> None:
        """Synchronously write to the file and handle rotation if needed.

        Args:
            content: The text content to write.
        """
        # Ensure containing directory exists
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

        # Handle size rotation check
        if self._file_path.exists() and self._file_path.stat().st_size >= self._max_bytes:
            self._sync_rotate()

        with open(self._file_path, "a", encoding="utf-8") as f:
            f.write(content)

    def _sync_rotate(self) -> None:
        """Perform simple single-file rollover log rotation."""
        backup_path = self._file_path.with_suffix(self._file_path.suffix + ".1")
        try:
            resolver = SafePathResolver.for_application(extra_roots=[self._file_path.parent])
            if backup_path.exists():
                resolver.safe_unlink(backup_path, purpose="log.rotate.cleanup", move_to_trash=False)
            resolver.safe_rename(self._file_path, backup_path, purpose="log.rotate.rename")
        except Exception:
            # Degrade gracefully if rotation fails due to lock/file issues
            pass


class ChatLogger:
    """Logs conversation interaction transcripts.

    Tracks incoming user queries and final outgoing agent responses.
    """

    def __init__(self, writer: ThreadSafeFileWriter) -> None:
        """Initialize the ChatLogger.

        Args:
            writer: The underlying thread-safe file writer.
        """
        self._writer = writer

    async def log_message(self, sender: str, content: str, session_id: str = "default") -> None:
        """Log a chat message to chat.log.

        Args:
            sender: The sender of the message ('USER' or 'ASSISTANT').
            content: The message text body.
            session_id: Session identifier.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp} | Session: {session_id}\n"
            f"Sender: {sender}\n"
            "Message:\n"
            f"  {content!r}\n"
            "================================================================================\n\n"
        )
        await self._writer.write(log_entry)


class LLMLogger:
    """Logs raw prompt contexts, parameters, and generated text responses.

    Ensures full auditability of the input messages and raw outputs.
    """

    def __init__(self, context_writer: ThreadSafeFileWriter, response_writer: ThreadSafeFileWriter) -> None:
        """Initialize the LLMLogger.

        Args:
            context_writer: The writer for llm_context.log.
            response_writer: The writer for llm_raw_response.log.
        """
        self._context_writer = context_writer
        self._response_writer = response_writer

    async def log_context(
        self,
        task: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str = "unknown",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Log full message list and tool schema context to llm_context.log.

        Args:
            task: Task classification tag.
            messages: Prior conversation and prompt messages list.
            tools: Optional dictionary tools list.
            model: Target model name.
            temperature: LLM temperature parameter.
            max_tokens: LLM max tokens parameter.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Build message history block
        msg_lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            # Escape strings to preserve block readable alignment
            msg_lines.append(f"  [{role.upper()}] {content.strip()}")
        msg_block = "\n".join(msg_lines)

        # Build tools block
        tools_block = "None"
        if tools:
            tool_lines = []
            for t in tools:
                func = t.get("function", {})
                name = func.get("name", "unknown")
                tool_lines.append(f"  - Tool: '{name}'")
            tools_block = "\n".join(tool_lines)

        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp}\n"
            f"Task: {task} | Model: {model} | Temperature: {temperature} | Max Tokens: {max_tokens}\n"
            "--- MESSAGES ---\n"
            f"{msg_block}\n"
            "--- TOOLS ---\n"
            f"{tools_block}\n"
            "================================================================================\n\n"
        )
        await self._context_writer.write(log_entry)

    async def log_raw_response(self, task: str, raw_text: str, model: str = "unknown") -> None:
        """Log raw text returned from completions to llm_raw_response.log.

        Args:
            task: Task classification tag.
            raw_text: Full response string.
            model: Target model name.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp}\n"
            f"Task: {task} | Model: {model}\n"
            "--- RAW RESPONSE ---\n"
            f"{raw_text}\n"
            "================================================================================\n\n"
        )
        await self._response_writer.write(log_entry)


class SearchLogger:
    """Logs exact search query parameters and target results metrics."""

    def __init__(self, writer: ThreadSafeFileWriter) -> None:
        """Initialize the SearchLogger.

        Args:
            writer: The underlying thread-safe file writer.
        """
        self._writer = writer

    async def log_search(
        self,
        query: str,
        category: str,
        active_providers: list[str],
        total_raw: int,
        unique_deduped: int,
        quality_filtered: int,
        *,
        provider_diagnostics: dict[str, Any] | None = None,
        raw_results: Sequence[Any] | None = None,
        deduped_results: Sequence[Any] | None = None,
        accepted_results: Sequence[Any] | None = None,
        ranked_results: Sequence[Any] | None = None,
        fallback_used: bool | None = None,
        max_results_to_log: int = 50,
    ) -> None:
        """Log indexing queries, provider diagnostics, and visible candidates.

        Search failures are often query-shape failures, not provider outages.
        Counts alone hide the important evidence, so this logger records a
        redacted candidate snapshot for every query: provider/source, title,
        seeders, size, quality score, and whether a magnet/link was present.
        Full magnet URLs are intentionally not written because private tracker
        passkeys can appear in them.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        diagnostics_lines = self._format_provider_diagnostics(provider_diagnostics or {})
        raw_lines = self._format_result_block("Raw Results", raw_results or [], max_results_to_log)
        deduped_lines = self._format_result_block("Deduped Results", deduped_results or [], max_results_to_log)
        accepted_lines = self._format_result_block("Accepted Results", accepted_results or [], max_results_to_log)
        ranked_lines = self._format_result_block("Ranked Results", ranked_results or [], max_results_to_log)
        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp}\n"
            f"Query: {query!r} | Category: {category}\n"
            f"Active Providers: {active_providers}\n"
            f"Fallback Used: {fallback_used}\n"
            "Result Status:\n"
            f"  - Total Raw Results: {total_raw}\n"
            f"  - Unique Deduplicated: {unique_deduped}\n"
            f"  - Quality Filtered (Accepted): {quality_filtered}\n"
            f"{diagnostics_lines}"
            f"{raw_lines}"
            f"{deduped_lines}"
            f"{accepted_lines}"
            f"{ranked_lines}"
            "================================================================================\n\n"
        )
        await self._writer.write(log_entry)

    @staticmethod
    def _format_provider_diagnostics(provider_diagnostics: dict[str, Any]) -> str:
        if not provider_diagnostics:
            return "Provider Diagnostics: []\n"
        lines = ["Provider Diagnostics:"]
        for key, diag in provider_diagnostics.items():
            provider = getattr(diag, "provider", key)
            ok = getattr(diag, "ok", None)
            result_count = getattr(diag, "result_count", None)
            magnet_count = getattr(diag, "magnet_count", None)
            elapsed_ms = getattr(diag, "elapsed_ms", None)
            blocked = getattr(diag, "blocked_reason", None)
            error = getattr(diag, "error", None)
            suffix = []
            if blocked:
                suffix.append(f"blocked={blocked}")
            if error:
                suffix.append(f"error={error}")
            suffix_text = f" ({'; '.join(suffix)})" if suffix else ""
            lines.append(
                f"  - {provider}: ok={ok}, results={result_count}, magnets={magnet_count}, elapsed_ms={elapsed_ms}{suffix_text}"
            )
        return "\n".join(lines) + "\n"

    @classmethod
    def _format_result_block(cls, label: str, results: Sequence[Any], max_results: int) -> str:
        lines = [f"{label} ({len(results)}):"]
        if not results:
            lines.append("  - none")
            return "\n".join(lines) + "\n"
        clipped = list(results)[:max(0, max_results)]
        for idx, result in enumerate(clipped, start=1):
            lines.append(f"  {idx:02d}. {cls._format_result(result)}")
        remaining = len(results) - len(clipped)
        if remaining > 0:
            lines.append(f"  ... {remaining} more result(s) omitted from log snapshot")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_result(result: Any) -> str:
        title = str(getattr(result, "title", "") or "").replace("\n", " ").strip()
        source = str(getattr(result, "source", "unknown") or "unknown")
        seeders = getattr(result, "seeders", None)
        size = getattr(result, "size", None) or "Unknown"
        size_bytes = getattr(result, "size_bytes", None)
        quality_score = getattr(result, "quality_score", None)
        has_magnet = bool(getattr(result, "magnet", None))
        url = str(getattr(result, "url", "") or "")
        url_hint = ""
        if url:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                url_hint = f", url_host={parsed.netloc or 'unknown'}"
            except Exception:
                url_hint = ", url_host=unparsed"
        return (
            f"source={source!r}, seeders={seeders}, size={size!r}, size_bytes={size_bytes}, "
            f"score={quality_score}, magnet={'yes' if has_magnet else 'no'}{url_hint}, title={title!r}"
        )


class TorrentLogger:
    """Logs candidate torrent options evaluated and final LLM ranking scores."""

    def __init__(self, writer: ThreadSafeFileWriter) -> None:
        """Initialize the TorrentLogger.

        Args:
            writer: The underlying thread-safe file writer.
        """
        self._writer = writer

    async def log_candidates(
        self,
        item_name: str,
        episode: str,
        candidates: Sequence[Any],
        preferred_lang: str,
        selected_index: int,
        selected_title: str,
    ) -> None:
        """Log parsed torrent candidate features and ratings to torrents.log.

        Args:
            item_name: Target category item name.
            episode: Target episode tag.
            candidates: Sorted normalized torrent candidate objects.
            preferred_lang: Configured language requirement.
            selected_index: The chosen index in the candidate list.
            selected_title: The name of the selected torrent.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Build evaluation candidate blocks
        eval_lines = []
        for i, n in enumerate(candidates):
            title = getattr(n, "title", "unknown")
            size = getattr(n, "size", "unknown")
            seeders = getattr(n, "seeders", 0)
            source = getattr(n, "source", "unknown")
            score = getattr(n, "quality_score", 0.0)
            
            # Extract additional properties if they exist
            red_flags = getattr(n, "red_flags", [])
            lang = getattr(n, "language", "unknown")
            
            flag_str = f" | Flags: {red_flags}" if red_flags else ""
            eval_lines.append(
                f"[{i}] Title: {title!r}\n"
                f"    Size: {size} | Seeders: {seeders} | Source: {source} | Quality Score: {score:.1f} | Lang: {lang}{flag_str}"
            )
        eval_block = "\n".join(eval_lines)

        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp}\n"
            f"Media: {item_name} {episode} | Preferred Language: {preferred_lang}\n"
            "--- CANDIDATES EVALUATED ---\n"
            f"{eval_block}\n"
            "--- LLM SELECTION RESULT ---\n"
            f"Selected Index: {selected_index}\n"
            f"Selected Title: {selected_title!r}\n"
            "================================================================================\n\n"
        )
        await self._writer.write(log_entry)


class StructuredReplyLogger:
    """Logs structured outputs like parsed plans and intent router predictions."""

    def __init__(self, writer: ThreadSafeFileWriter) -> None:
        """Initialize the StructuredReplyLogger.

        Args:
            writer: The underlying thread-safe file writer.
        """
        self._writer = writer

    async def log_plan(self, user_goal: str, intent: str, steps: list[dict[str, Any]]) -> None:
        """Log structured plans generated for complex workflows.

        Args:
            user_goal: Parsed user intent goal.
            intent: Categorized intent classification.
            steps: List of generated AgentPlan steps with tool payloads.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Build step execution layout
        step_lines = []
        for i, step in enumerate(steps):
            name = step.get("tool_name", "unknown")
            args = step.get("arguments", {})
            step_lines.append(f"  - Step [{i+1}] -> Tool: {name} (args: {json.dumps(args)})")
        steps_block = "\n".join(step_lines)

        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp}\n"
            f"Structured Plan Generated for Intent '{intent}':\n"
            f"Goal: {user_goal}\n"
            "Steps:\n"
            f"{steps_block}\n"
            "================================================================================\n\n"
        )
        await self._writer.write(log_entry)

    async def log_intent(self, query: str, routed_intent: str, confidence: float = 1.0) -> None:
        """Log query intent classification results.

        Args:
            query: The user query string.
            routed_intent: Classified target intent.
            confidence: LLM classification confidence score.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp}\n"
            f"Intent Routed:\n"
            f"  Query: {query!r}\n"
            f"  Routed Intent: {routed_intent}\n"
            f"  Confidence: {confidence:.2f}\n"
            "================================================================================\n\n"
        )
        await self._writer.write(log_entry)


class DetailedLoggingSubsystem:
    """Central manager coordinating all structured multi-file loggers."""

    def __init__(self, log_dir: str | Path = "logs") -> None:
        """Initialize the subsystem log directory and individual loggers.

        Args:
            log_dir: The target logs root directory.
        """
        self._log_dir = Path(log_dir).resolve()

        # Initialize writers
        chat_writer = ThreadSafeFileWriter(self._log_dir / "chat.log")
        context_writer = ThreadSafeFileWriter(self._log_dir / "llm_context.log")
        response_writer = ThreadSafeFileWriter(self._log_dir / "llm_raw_response.log")
        structured_writer = ThreadSafeFileWriter(self._log_dir / "structured_replies.log")
        search_writer = ThreadSafeFileWriter(self._log_dir / "searches.log")
        torrent_writer = ThreadSafeFileWriter(self._log_dir / "torrents.log")

        # Initialize individual loggers
        self._chat_logger = ChatLogger(chat_writer)
        self._llm_logger = LLMLogger(context_writer, response_writer)
        self._structured_logger = StructuredReplyLogger(structured_writer)
        self._search_logger = SearchLogger(search_writer)
        self._torrent_logger = TorrentLogger(torrent_writer)

    @property
    def chat_logger(self) -> ChatLogger:
        """Return the conversational chat logger."""
        return self._chat_logger

    @property
    def llm_logger(self) -> LLMLogger:
        """Return the LLM request/response context logger."""
        return self._llm_logger

    @property
    def structured_logger(self) -> StructuredReplyLogger:
        """Return the structured plan/intent logger."""
        return self._structured_logger

    @property
    def search_logger(self) -> SearchLogger:
        """Return the query indexer search logger."""
        return self._search_logger

    @property
    def torrent_logger(self) -> TorrentLogger:
        """Return the torrent candidate evaluation logger."""
        return self._torrent_logger
