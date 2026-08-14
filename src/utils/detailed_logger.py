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
import time
from pathlib import Path
from typing import Any, Sequence

from src.core.security.path_policy import SafePathResolver
from src.core.operation_trace import OperationTraceContext
from src.utils.log_sanitizer import redact_secrets


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

    async def log_message(
        self, sender: str, content: str, session_id: str = "default", turn_id: str | None = None
    ) -> None:
        """Log a chat message to chat.log.

        Args:
            sender: The sender of the message ('USER' or 'ASSISTANT').
            content: The message text body.
            session_id: Session identifier.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        trace = OperationTraceContext.fields()
        effective_session = session_id or str(trace.get("session_id") or "default")
        effective_turn = turn_id or trace.get("turn_id")
        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp} | Session: {effective_session} | Turn: {effective_turn or '-'} | Turn elapsed ms: {trace.get('turn_elapsed_ms')}\n"
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
        """Log the exact provider payload plus explicit size diagnostics.

        Messages are serialized rather than reduced to role/content lines so
        assistant tool calls and every function schema remain inspectable.  The
        estimates are labelled as such; provider-reported usage is recorded by
        the live activity monitor after completion.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        trace = OperationTraceContext.fields()
        safe_messages = messages or []
        safe_tools = tools or []
        try:
            messages_json = json.dumps(safe_messages, ensure_ascii=False, indent=2, default=str)
        except Exception:
            messages_json = repr(safe_messages)
        try:
            tools_json = json.dumps(safe_tools, ensure_ascii=False, indent=2, default=str)
        except Exception:
            tools_json = repr(safe_tools)

        message_chars = len(messages_json)
        tool_chars = len(tools_json)
        # Match the conservative live monitor closely enough for a readable log
        # audit while avoiding a dependency from utils back into the AI runtime.
        message_tokens_est = int((message_chars / 3.4) * 1.15)
        tool_tokens_est = int((tool_chars / 2.2) * 1.15) if safe_tools else 0
        total_tokens_est = message_tokens_est + tool_tokens_est
        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp}\n"
            f"Session: {trace.get('session_id') or '-'} | Turn: {trace.get('turn_id') or '-'} | Turn elapsed ms: {trace.get('turn_elapsed_ms')}\n"
            f"Task: {task} | Model: {model} | Temperature: {temperature} | Max Tokens: {max_tokens}\n"
            "--- PAYLOAD SIZE (ESTIMATED BEFORE PROVIDER TOKENIZATION) ---\n"
            f"Messages: {len(safe_messages)} | Message chars: {message_chars} | Estimated message tokens: {message_tokens_est}\n"
            f"Tools: {len(safe_tools)} | Tool-schema chars: {tool_chars} | Estimated tool tokens: {tool_tokens_est}\n"
            f"Estimated total prompt tokens: {total_tokens_est}\n"
            "--- EXACT MESSAGES JSON ---\n"
            f"{messages_json}\n"
            "--- EXACT TOOL SCHEMAS JSON ---\n"
            f"{tools_json if safe_tools else '[]'}\n"
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
        trace = OperationTraceContext.fields()
        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp}\n"
            f"Session: {trace.get('session_id') or '-'} | Turn: {trace.get('turn_id') or '-'} | Turn elapsed ms: {trace.get('turn_elapsed_ms')}\n"
            f"Task: {task} | Model: {model}\n"
            "--- RAW RESPONSE ---\n"
            f"{raw_text}\n"
            "================================================================================\n\n"
        )
        await self._response_writer.write(log_entry)


class SearchLogger:
    """Logs exact search query parameters and target results metrics.

    The human-readable ``searches.log`` is useful for quick inspection, but it
    is not enough for repeated torrent-search debugging.  The optional JSONL
    writer records a structured per-query snapshot that can be grepped or loaded
    into Python without reverse-parsing prose logs.  Magnets are never written;
    only a short info-hash fingerprint is kept.
    """

    def __init__(self, writer: ThreadSafeFileWriter, json_writer: ThreadSafeFileWriter | None = None) -> None:
        """Initialize the SearchLogger.

        Args:
            writer: The underlying thread-safe text writer.
            json_writer: Optional structured JSONL writer.
        """
        self._writer = writer
        self._json_writer = json_writer

    async def begin_search(
        self,
        *,
        query: str,
        category: str,
        active_providers: list[str],
    ) -> str:
        """Record the start of one provider search and return its stable ID.

        Completion-only logs made cancelled searches disappear entirely, which
        forced operators to infer duration from unrelated later timestamps.
        Every search now has an explicit started/terminal lifecycle.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        trace = OperationTraceContext.fields()
        search_id = self._query_id(timestamp, query, category)
        await self._write_lifecycle(
            event="torrent_search_started",
            search_id=search_id,
            timestamp=timestamp,
            query=query,
            category=category,
            active_providers=active_providers,
            elapsed_ms=0,
            detail=None,
            trace=trace,
        )
        return search_id

    async def log_search_event(
        self,
        *,
        event: str,
        search_id: str,
        query: str,
        category: str,
        active_providers: list[str],
        elapsed_ms: int,
        detail: str | None = None,
    ) -> None:
        """Record a terminal/non-success search lifecycle event."""
        await self._write_lifecycle(
            event=event,
            search_id=search_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            query=query,
            category=category,
            active_providers=active_providers,
            elapsed_ms=max(0, int(elapsed_ms or 0)),
            detail=detail,
            trace=OperationTraceContext.fields(),
        )

    async def _write_lifecycle(
        self,
        *,
        event: str,
        search_id: str,
        timestamp: str,
        query: str,
        category: str,
        active_providers: list[str],
        elapsed_ms: int,
        detail: str | None,
        trace: dict[str, object],
    ) -> None:
        """Write one compact lifecycle record to both search ledgers."""
        safe_detail = redact_secrets(str(detail)) if detail else None
        human = (
            "--------------------------------------------------------------------------------\n"
            f"Timestamp: {timestamp}\n"
            f"Session: {trace.get('session_id') or '-'} | Turn: {trace.get('turn_id') or '-'} | Turn elapsed ms: {trace.get('turn_elapsed_ms')}\n"
            f"Search ID: {search_id} | Event: {event} | Search elapsed ms: {elapsed_ms}\n"
            f"Query: {query!r} | Category: {category} | Providers: {active_providers}\n"
            f"Detail: {safe_detail or '-'}\n"
            "--------------------------------------------------------------------------------\n\n"
        )
        await self._writer.write(human)
        if self._json_writer:
            record = {
                "event": event,
                "search_id": search_id,
                "timestamp": timestamp,
                "session_id": trace.get("session_id"),
                "turn_id": trace.get("turn_id"),
                "turn_elapsed_ms": trace.get("turn_elapsed_ms"),
                "search_elapsed_ms": elapsed_ms,
                "query": query,
                "category": category,
                "active_providers": active_providers,
                "detail": safe_detail,
            }
            await self._json_writer.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

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
        max_results_to_log: int = 200,
        search_id: str | None = None,
        search_elapsed_ms: int | None = None,
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
        trace = OperationTraceContext.fields()
        query_id = str(search_id or self._query_id(timestamp, query, category))
        diagnostics_lines = self._format_provider_diagnostics(provider_diagnostics or {})
        raw_lines = self._format_result_block("Raw Results", raw_results or [], max_results_to_log)
        deduped_lines = self._format_result_block("Deduped Results", deduped_results or [], max_results_to_log)
        accepted_lines = self._format_result_block("Accepted Results", accepted_results or [], max_results_to_log)
        ranked_lines = self._format_result_block("Ranked Results", ranked_results or [], max_results_to_log)
        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp}\n"
            f"Session: {trace.get('session_id') or '-'} | Turn: {trace.get('turn_id') or '-'} | Turn elapsed ms: {trace.get('turn_elapsed_ms')}\n"
            f"Search ID: {query_id} | Event: torrent_search_completed | Search elapsed ms: {search_elapsed_ms}\n"
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
        if self._json_writer:
            record = {
                # Preserve the historical event name for structured-log
                # compatibility, but state terminal truth explicitly so
                # operators do not need to infer completion from that legacy
                # label.
                "event": "torrent_search_query",
                "terminal_state": "completed",
                "search_id": query_id,
                "timestamp": timestamp,
                "session_id": trace.get("session_id"),
                "turn_id": trace.get("turn_id"),
                "turn_elapsed_ms": trace.get("turn_elapsed_ms"),
                "search_elapsed_ms": search_elapsed_ms,
                "query": query,
                "category": category,
                "active_providers": active_providers,
                "fallback_used": fallback_used,
                "counts": {
                    "raw": total_raw,
                    "deduped": unique_deduped,
                    "accepted": quality_filtered,
                    "ranked": len(ranked_results or []),
                },
                "provider_diagnostics": self._diagnostics_to_json(provider_diagnostics or {}),
                "stages": {
                    "raw": self._results_to_json(raw_results or [], max_results_to_log),
                    "deduped": self._results_to_json(deduped_results or [], max_results_to_log),
                    "accepted": self._results_to_json(accepted_results or [], max_results_to_log),
                    "ranked": self._results_to_json(ranked_results or [], max_results_to_log),
                },
            }
            await self._json_writer.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _query_id(timestamp: str, query: str, category: str) -> str:
        import hashlib
        material = f"{timestamp}|{category}|{query}".encode("utf-8", "ignore")
        return hashlib.sha256(material).hexdigest()[:16]

    @classmethod
    def _results_to_json(cls, results: Sequence[Any], max_results: int) -> dict[str, Any]:
        clipped = list(results)[:max(0, max_results)]
        return {
            "count": len(results),
            "omitted": max(0, len(results) - len(clipped)),
            "rows": [cls._result_to_json(result, idx) for idx, result in enumerate(clipped, start=1)],
        }

    @staticmethod
    def _result_to_json(result: Any, index: int) -> dict[str, Any]:
        magnet = str(getattr(result, "magnet", "") or "")
        info_hash = ""
        if magnet:
            import re
            m = re.search(r"xt=urn:btih:([a-z0-9]+)", magnet, re.I)
            info_hash = (m.group(1).lower()[:16] if m else "present_unparsed")
        url = str(getattr(result, "url", "") or "")
        url_host = ""
        if url:
            try:
                from urllib.parse import urlparse
                url_host = urlparse(url).netloc or ""
            except Exception:
                url_host = "unparsed"
        return {
            "index": index,
            "title": str(getattr(result, "title", "") or ""),
            "source": str(getattr(result, "source", "unknown") or "unknown"),
            "seeders": getattr(result, "seeders", None),
            "size": getattr(result, "size", None),
            "size_bytes": getattr(result, "size_bytes", None),
            "quality_score": getattr(result, "quality_score", None),
            "magnet_present": bool(magnet),
            "info_hash_prefix": info_hash,
            "url_host": url_host,
        }

    @staticmethod
    def _diagnostics_to_json(provider_diagnostics: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, diag in provider_diagnostics.items():
            out[str(key)] = {
                "provider": getattr(diag, "provider", key),
                "ok": getattr(diag, "ok", None),
                "result_count": getattr(diag, "result_count", None),
                "magnet_count": getattr(diag, "magnet_count", None),
                "elapsed_ms": getattr(diag, "elapsed_ms", None),
                "outcome": getattr(diag, "outcome", None),
                "blocked_reason": getattr(diag, "blocked_reason", None),
                "error": getattr(diag, "error", None),
            }
        return out

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
        trace = OperationTraceContext.fields()
        
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
            f"Session: {trace.get('session_id') or '-'} | Turn: {trace.get('turn_id') or '-'} | Turn elapsed ms: {trace.get('turn_elapsed_ms')}\n"
            f"Structured Plan Generated for Intent '{intent}':\n"
            f"Goal: {user_goal}\n"
            "Steps:\n"
            f"{steps_block}\n"
            "================================================================================\n\n"
        )
        await self._writer.write(log_entry)

    async def log_intent(
        self,
        query: str,
        routed_intent: str,
        confidence: float = 1.0,
        status: str = "success",
        error: str | None = None,
    ) -> None:
        """Log intent classification with honest confidence and failure state."""
        timestamp = datetime.now(timezone.utc).isoformat()
        trace = OperationTraceContext.fields()
        error_line = f"  Error: {error}\n" if error else ""
        log_entry = (
            "================================================================================\n"
            f"Timestamp: {timestamp}\n"
            f"Session: {trace.get('session_id') or '-'} | Turn: {trace.get('turn_id') or '-'} | Turn elapsed ms: {trace.get('turn_elapsed_ms')}\n"
            "Intent Routed:\n"
            f"  Query: {query!r}\n"
            f"  Routed Intent: {routed_intent}\n"
            f"  Confidence: {confidence:.2f}\n"
            f"  Status: {status}\n"
            f"{error_line}"
            "================================================================================\n\n"
        )
        await self._writer.write(log_entry)


class ChatTurnAuditLogger:
    """Write an explicit lifecycle ledger for every user-visible chat turn.

    The ledger exists so cancellation and duration are never inferred from gaps
    between transcript messages.  Every event carries the same session/turn id
    used by LLM and search telemetry.
    """

    def __init__(self, writer: ThreadSafeFileWriter, json_writer: ThreadSafeFileWriter) -> None:
        self._writer = writer
        self._json_writer = json_writer
        self._started_monotonic: dict[tuple[str, str], float] = {}

    async def log_event(
        self,
        event: str,
        *,
        session_id: str,
        turn_id: str,
        transport: str,
        detail: str | None = None,
        message: str | None = None,
        state: str | None = None,
    ) -> None:
        """Append one turn lifecycle event to human and structured ledgers."""
        timestamp = datetime.now(timezone.utc).isoformat()
        trace = OperationTraceContext.fields()
        key = (str(session_id or "default"), str(turn_id or ""))
        now_mono = time.monotonic()
        if event in {"turn_received", "turn_started"} and key not in self._started_monotonic:
            self._started_monotonic[key] = now_mono
            # Keep enough recently finished starts for late cancellation
            # acknowledgements to retain the same elapsed clock.  Different
            # transports can observe turn_cancelled and cancel_settled in either
            # order, so deleting on the first terminal event loses truth.
            while len(self._started_monotonic) > 4096:
                self._started_monotonic.pop(next(iter(self._started_monotonic)))
        started = self._started_monotonic.get(key)
        elapsed_ms = max(0, int((now_mono - started) * 1000)) if started is not None else None
        if trace.get("turn_id") == turn_id and trace.get("turn_elapsed_ms") is not None:
            elapsed_ms = trace.get("turn_elapsed_ms")
        safe_detail = redact_secrets(str(detail)) if detail else None
        record = {
            "event": str(event or "turn_event"),
            "timestamp": timestamp,
            "session_id": str(session_id or "default"),
            "turn_id": str(turn_id or ""),
            "transport": str(transport or "unknown"),
            "state": state,
            "detail": safe_detail,
            "message": message,
            "turn_elapsed_ms": elapsed_ms,
        }
        human = (
            "================================================================================\n"
            f"Timestamp: {timestamp} | Session: {record['session_id']} | Turn: {record['turn_id']}\n"
            f"Event: {record['event']} | Transport: {record['transport']} | State: {state or '-'} | Turn elapsed ms: {record['turn_elapsed_ms']}\n"
            f"Detail: {safe_detail or '-'}\n"
            f"Message: {message!r}\n"
            "================================================================================\n\n"
        )
        await self._writer.write(human)
        await self._json_writer.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


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
        search_json_writer = ThreadSafeFileWriter(self._log_dir / "searches.jsonl", max_bytes=25 * 1024 * 1024)
        torrent_writer = ThreadSafeFileWriter(self._log_dir / "torrents.log")
        turn_writer = ThreadSafeFileWriter(self._log_dir / "chat_turns.log")
        turn_json_writer = ThreadSafeFileWriter(self._log_dir / "chat_turns.jsonl", max_bytes=25 * 1024 * 1024)

        # Initialize individual loggers
        self._chat_logger = ChatLogger(chat_writer)
        self._llm_logger = LLMLogger(context_writer, response_writer)
        self._structured_logger = StructuredReplyLogger(structured_writer)
        self._search_logger = SearchLogger(search_writer, search_json_writer)
        self._torrent_logger = TorrentLogger(torrent_writer)
        self._turn_logger = ChatTurnAuditLogger(turn_writer, turn_json_writer)

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

    @property
    def turn_logger(self) -> ChatTurnAuditLogger:
        """Return the explicit chat-turn lifecycle ledger."""
        return self._turn_logger
