"""Semantic-memory reindex workflows for conversation and taste vectors."""

from __future__ import annotations

import json
from typing import Any, Protocol


class VectorStoreProtocol(Protocol):
    """Subset of vector-store behavior needed by memory reindex workflows."""

    TASTE_SIGNAL_ID_OFFSET: int

    @property
    def is_initialized(self) -> bool:
        """Return whether vector storage is available for indexing."""
        ...

    @property
    def db(self) -> Any:
        """Return the database facade used to enumerate source records."""
        ...

    async def purge_source_type(self, source_type: str) -> int:
        """Remove existing vectors for one source type and return the count."""
        ...

    async def upsert(self, item_id: int, text: str, metadata: dict | None = None) -> None:
        """Insert or replace one embedded source document."""
        ...


class SemanticMemoryReindexer:
    """Coordinates vector rebuilds without bloating the storage class.

    VectorStore owns embedding/search/storage mechanics. This helper owns the
    maintenance workflows that decide which domain records should be embedded.
    """

    def __init__(self, store: VectorStoreProtocol) -> None:
        self._store = store

    async def reindex_conversations(self, limit: int = 10000) -> dict[str, int | str]:
        """Rebuild conversation vectors for the active embedding namespace."""
        if not self._store.is_initialized:
            return {"status": "disabled", "indexed": 0, "purged": 0, "reindexed": 0}
        purged = await self._store.purge_source_type("conversation_turn")
        rows = await self._store.db.system.list_conversation_turns(limit=limit)
        indexed = 0
        for row in rows:
            role = row.get("role")
            content = row.get("content") or ""
            if role not in {"user", "assistant"} or not content.strip():
                continue
            await self._store.upsert(
                int(row.get("id") or 0),
                content,
                metadata={
                    "source_type": "conversation_turn",
                    "session_id": row.get("session_id", ""),
                    "role": role,
                },
            )
            indexed += 1
        return {"status": "ok", "indexed": indexed, "reindexed": indexed, "purged": purged}

    async def reindex_taste_signals(self, limit: int = 10000) -> dict[str, int | str]:
        """Rebuild vectors for category taste signals."""
        if not self._store.is_initialized:
            return {"status": "disabled", "indexed": 0, "purged": 0, "reindexed": 0}
        if not hasattr(self._store.db.system, "list_taste_signals"):
            return {"status": "unsupported", "indexed": 0, "purged": 0, "reindexed": 0}
        purged = await self._store.purge_source_type("taste_signal")
        rows = await self._store.db.system.list_taste_signals(limit=limit)
        indexed = 0
        for row in rows:
            signal_id = int(row.get("id") or 0)
            text = taste_signal_vector_text(row)
            if signal_id <= 0 or not text.strip():
                continue
            await self._store.upsert(
                self._store.TASTE_SIGNAL_ID_OFFSET + signal_id,
                text,
                metadata={
                    "source_type": "taste_signal",
                    "signal_id": signal_id,
                    "category_id": row.get("category_id", ""),
                    "item_id": row.get("item_id", ""),
                    "display_name": row.get("display_name", ""),
                    "signal_type": row.get("signal_type", ""),
                },
            )
            indexed += 1
        return {"status": "ok", "indexed": indexed, "reindexed": indexed, "purged": purged}

    async def reindex_all(self, limit: int = 10000) -> dict[str, Any]:
        """Rebuild all semantic-memory vector families."""
        conversations = await self.reindex_conversations(limit=limit)
        taste_signals = await self.reindex_taste_signals(limit=limit)
        total = int(conversations.get("indexed", 0)) + int(taste_signals.get("indexed", 0))
        return {
            "status": "ok" if conversations.get("status") != "disabled" else "disabled",
            "reindexed": total,
            "conversations": conversations,
            "taste_signals": taste_signals,
        }


def taste_signal_vector_text(row: dict[str, Any]) -> str:
    """Build category-neutral searchable text for one taste signal."""
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    facets = row.get("interpreted_facets") or {}
    if isinstance(facets, str):
        try:
            facets = json.loads(facets)
        except json.JSONDecodeError:
            facets = {}
    parts: list[str] = []
    for value in (
        row.get("display_name"), row.get("item_id"), row.get("signal_type"),
        row.get("polarity"), row.get("notes"), row.get("evidence_text"),
        metadata.get("overview"), metadata.get("description"), metadata.get("summary"),
    ):
        if value:
            parts.append(str(value))
    for key, value in metadata.items():
        if key in {"overview", "description", "summary"}:
            continue
        formatted = _format_metadata_value(value)
        if formatted:
            parts.append(f"{key}: {formatted}")
    for key, value in facets.items():
        formatted = _format_metadata_value(value)
        if formatted:
            parts.append(f"interpreted_{key}: {formatted}")
    return "\n".join(parts)


def _format_metadata_value(value: Any) -> str:
    """Format category metadata as compact indexable text."""
    if isinstance(value, list):
        flattened: list[str] = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("name") or item.get("title") or item.get("id")
            if item:
                flattened.append(str(item))
        return ", ".join(flattened[:12])
    if isinstance(value, (str, int, float)) and value:
        return str(value)
    return ""
