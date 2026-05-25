"""
Vector store for LJS using sqlite-vec.

Provides semantic search over conversation history, category-scoped taste
signals, preference embeddings, and media descriptions. Uses sqlite-vec as a
lightweight, zero-config vector database that runs inside SQLite — no separate
service needed, fully cross-platform.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from loguru import logger
from typing import Optional, TYPE_CHECKING, Any
from src.core.database import Database
from src.core.embedding_runtime import BuiltinEmbeddingRuntime

if TYPE_CHECKING:
    from src.llm_providers.task_client import TaskLLMClient
    from src.core.models import EmbeddingSettings


class VectorStore:
    """Manages vector embeddings in SQLite using the sqlite-vec extension.

    Stores and retrieves float32 vectors for semantic similarity search. The
    preferred default in the full app is the built-in FastEmbed/ONNX model. If
    that model is disabled or unavailable, the store can use an explicitly
    configured embedding task endpoint. Hash embeddings remain only as a safe
    degradation path, never as a pretend semantic model.
    """

    DIMENSION = 384  # sentence-transformers/all-MiniLM-L6-v2 and bge-small dimension
    TASTE_SIGNAL_ID_OFFSET = 9_000_000_000_000

    def __init__(self, db: Database, embedding_model: str | None = None,
                 embedding_api_base: str | None = None,
                 embedding_api_key: str | None = None,
                 llm_client: "TaskLLMClient | None" = None,
                 embedding_settings: "EmbeddingSettings | None" = None,
                 builtin_runtime: BuiltinEmbeddingRuntime | None = None) -> None:
        """Initialize the vector store.

        Args:
            db: Database instance for persisting vectors.
            embedding_model: Embedding model name (legacy, for direct litellm).
            embedding_api_base: API base URL for embedding model (legacy).
            embedding_api_key: API key for embedding model (legacy).
            llm_client: Optional TaskLLMClient for embedding calls.
                Used only when the embedding task is explicitly configured.
            embedding_settings: Local embedding runtime settings. When provided
                with provider="builtin", LJS uses a local FastEmbed model.
            builtin_runtime: Optional prebuilt runtime, mainly for tests.
        """
        self._db = db
        self._embedding_model = embedding_model
        self._embedding_api_base = embedding_api_base
        self._embedding_api_key = embedding_api_key
        self._llm_client = llm_client
        self._embedding_settings = embedding_settings
        self._builtin_runtime = builtin_runtime
        self._dimension = int(getattr(embedding_settings, "dimension", self.DIMENSION) or self.DIMENSION)
        self._embedding_fn = None
        self._is_async_embed = False
        self._initialized = False
        self._provider_label = "uninitialized"
        self._fallback_count = 0
        self._last_embedding_error: str | None = None
        self._namespace = self._build_namespace()

    @property
    def is_initialized(self) -> bool:
        """Whether the vector store has been successfully initialized."""
        return self._initialized

    @property
    def provider_label(self) -> str:
        """Return the active embedding provider label for diagnostics."""
        return self._provider_label

    @property
    def namespace(self) -> str:
        """Return the vector namespace for the configured embedding model."""
        return self._namespace

    @property
    def db(self) -> Database:
        """Return the backing database for maintenance collaborators."""
        return self._db

    def _build_namespace(self) -> str:
        """Return a stable namespace for provider/model/dimension isolation."""
        provider = getattr(self._embedding_settings, "provider", None) or ("llm" if self._llm_client else "legacy")
        model = (
            getattr(self._embedding_settings, "builtin_model", "")
            or self._embedding_model
            or "hash"
        )
        raw = f"{provider}:{model}:{self._dimension}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"v1:{digest}"

    def _storage_id(self, item_id: int) -> int:
        """Map caller item IDs to namespace-safe sqlite-vec integer IDs."""
        raw = f"{self._namespace}:{int(item_id)}".encode("utf-8")
        return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") & ((1 << 63) - 1)

    async def health_status(self) -> dict[str, Any]:
        """Return operator-facing memory/vector health information."""
        runtime_status = None
        if self._builtin_runtime is not None:
            status = self._builtin_runtime.status
            runtime_status = {
                "provider": status.provider,
                "model": status.model,
                "dimension": status.dimension,
                "ready": status.ready,
                "cache_dir": status.cache_dir,
                "message": status.message,
            }
        return {
            "initialized": self._initialized,
            "provider": self._provider_label,
            "namespace": self._namespace,
            "dimension": self._dimension,
            "runtime": runtime_status,
            "fallback_count": self._fallback_count,
            "last_error": self._last_embedding_error,
            "semantic": self._is_semantic_provider_active(),
        }


    def _record_embedding_fallback(self, message: str) -> list[float] | None:
        """Record a degraded embedding event for diagnostics."""
        self._fallback_count += 1
        self._last_embedding_error = message
        if not self._provider_label.endswith("hash_fallback"):
            self._provider_label = f"{self._provider_label}_hash_fallback"
        return None

    def _is_semantic_provider_active(self) -> bool:
        """Return whether embeddings are currently semantic, not hash fallback."""
        return self._initialized and not self._provider_label.endswith("hash_fallback")

    async def purge_namespace(self, namespace: str | None = None) -> int:
        """Remove all vectors and metadata for one namespace.

        This is used before re-indexing after a model/provider change so stale
        vectors from a previous model are never mixed with fresh semantic memory.
        """
        if not self._initialized:
            return 0
        target = namespace or self._namespace
        prefs = await self._db.system.get_all_preferences()
        ids: list[int] = []
        for key, value in prefs.items():
            if not key.startswith("vec_meta:"):
                continue
            try:
                meta = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                continue
            if meta.get("embedding_namespace") == target:
                try:
                    ids.append(int(key.split(":")[-1]))
                except (ValueError, IndexError):
                    continue
        conn = await self._db.get_connection()
        for storage_id in ids:
            await conn.execute("DELETE FROM vec_embeddings WHERE id = ?", (storage_id,))
            await self._db.system.delete_preference(f"vec_meta:{storage_id}")
        await conn.commit()
        return len(ids)

    async def purge_source_type(self, source_type: str) -> int:
        """Remove vector rows in this namespace matching one metadata source type."""
        if not self._initialized:
            return 0
        prefs = await self._db.system.get_all_preferences()
        storage_ids: list[int] = []
        for key, value in prefs.items():
            if not key.startswith("vec_meta:"):
                continue
            try:
                metadata = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                continue
            if metadata.get("embedding_namespace") != self._namespace:
                continue
            if metadata.get("source_type") != source_type:
                continue
            try:
                storage_ids.append(int(key.split(":")[-1]))
            except (ValueError, IndexError):
                continue
        conn = await self._db.get_connection()
        for storage_id in storage_ids:
            await conn.execute("DELETE FROM vec_embeddings WHERE id = ?", (storage_id,))
            await self._db.system.delete_preference(f"vec_meta:{storage_id}")
        await conn.commit()
        return len(storage_ids)

    async def reindex_conversations(self, limit: int = 10000) -> dict[str, int | str]:
        """Rebuild conversation vectors for the current embedding namespace."""
        from src.core.vector_reindexer import SemanticMemoryReindexer
        return await SemanticMemoryReindexer(self).reindex_conversations(limit=limit)

    async def reindex_taste_signals(self, limit: int = 10000) -> dict[str, int | str]:
        """Rebuild category taste-signal vectors for the current namespace."""
        from src.core.vector_reindexer import SemanticMemoryReindexer
        return await SemanticMemoryReindexer(self).reindex_taste_signals(limit=limit)

    async def reindex_all_memory(self, limit: int = 10000) -> dict[str, Any]:
        """Rebuild all semantic-memory vector families for this namespace."""
        from src.core.vector_reindexer import SemanticMemoryReindexer
        return await SemanticMemoryReindexer(self).reindex_all(limit=limit)

    async def initialize(self) -> None:
        """Set up the virtual table for vector storage.

        Requires sqlite-vec extension to be loaded. Falls back gracefully with a
        warning if the extension is unavailable.
        """
        try:
            import sqlite_vec
            conn = await self._db.get_connection()
            await conn.enable_load_extension(True)
            # Load extension via SQL so it runs in aiosqlite's worker thread,
            # avoiding "SQLite objects created in a thread" errors.
            await conn.execute("SELECT load_extension(?)", (sqlite_vec.loadable_path(),))
            await conn.enable_load_extension(False)

            await conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_embeddings
                USING vec0(
                    id INTEGER PRIMARY KEY,
                    embedding float[{self._dimension}]
                )
            """)
            await conn.commit()
            self._initialized = True
            logger.info(f"Vector store initialized (dimension={self._dimension})")
        except Exception as e:
            logger.warning(
                f"Vector store initialization failed (sqlite-vec unavailable): {e}. "
                f"Semantic search features will be disabled."
            )
            self._initialized = False

    async def warm_up(self) -> None:
        """Best-effort warm-up that downloads/loads the built-in model early."""
        if not self._initialized:
            return
        try:
            await self.embed("ljs semantic memory warmup")
            logger.info(f"Embedding provider warmed up: {self._provider_label}")
        except Exception as exc:
            logger.warning(f"Embedding warm-up failed: {exc}")

    def _get_embedding_fn(self):
        """Lazily load the embedding function.

        Provider order:
        1. Built-in FastEmbed model when embedding settings request provider=builtin.
        2. Explicit embedding task endpoint via TaskLLMClient.
        3. Legacy direct litellm embedding model.
        4. Hash fallback with clear diagnostics.
        """
        if self._embedding_fn is not None:
            return self._embedding_fn, self._is_async_embed

        provider = getattr(self._embedding_settings, "provider", None)
        enabled = bool(getattr(self._embedding_settings, "enabled", True)) if self._embedding_settings else True

        if self._embedding_settings and (not enabled or provider == "disabled"):
            logger.info("Embeddings disabled by settings; using deterministic fallback only")
            self._provider_label = "disabled_hash_fallback"
            self._embedding_fn = self._hash_embed
            self._is_async_embed = False
            return self._embedding_fn, self._is_async_embed

        # Priority 1: built-in local model, only when settings opt in.
        if self._embedding_settings and provider == "builtin":
            runtime = self._builtin_runtime or BuiltinEmbeddingRuntime(self._embedding_settings)
            self._builtin_runtime = runtime

            async def _builtin_embed(text: str) -> list[float]:
                try:
                    return await runtime.embed(text)
                except Exception as exc:
                    message = f"Built-in embedding model unavailable ({exc}); using hash fallback for this item."
                    logger.warning(message)
                    self._record_embedding_fallback(message)
                    return self._hash_embed(text)

            self._embedding_fn = _builtin_embed
            self._is_async_embed = True
            self._provider_label = f"builtin:{runtime.model_name}"
            logger.info(f"Using built-in embedding model: {runtime.model_name}")
            return self._embedding_fn, self._is_async_embed

        # Priority 2: TaskLLMClient with explicit embedding config.
        if self._llm_client and (provider in (None, "llm")):
            config = self._llm_client.llm_config
            if config.has_explicit_task_config("embedding"):
                async def _task_client_embed(text: str) -> list[float]:
                    """Generate embedding via TaskLLMClient.

                    Falls back to hash on failure or dimension mismatch. Chat
                    models must never be used for embeddings.
                    """
                    result = await self._llm_client.embedding("embedding", text)
                    if result is None:
                        message = "TaskLLMClient embedding returned None, using hash fallback"
                        logger.warning(message)
                        self._record_embedding_fallback(message)
                        return self._hash_embed(text)
                    if len(result) != self._dimension:
                        message = (
                            f"Embedding dimension mismatch: got {len(result)}, "
                            f"expected {self._dimension}. Using hash fallback. "
                            f"Configure an embedding model with {self._dimension}-dimensional output."
                        )
                        logger.warning(message)
                        self._record_embedding_fallback(message)
                        return self._hash_embed(text)
                    return result

                self._embedding_fn = _task_client_embed
                self._is_async_embed = True
                self._provider_label = "llm_task_embedding"
                logger.info("Using TaskLLMClient for embeddings (explicit config)")
                return self._embedding_fn, self._is_async_embed

        # Priority 3: Direct litellm if an embedding model is configured.
        if self._embedding_model:
            try:
                import litellm

                embed_kwargs: dict = {}
                if self._embedding_api_key:
                    embed_kwargs["api_key"] = self._embedding_api_key
                if self._embedding_api_base:
                    embed_kwargs["api_base"] = self._embedding_api_base

                async def _litellm_embed(text: str) -> list[float]:
                    response = await litellm.aembedding(
                        model=self._embedding_model,
                        input=[text],
                        **embed_kwargs,
                    )
                    embedding = response.data[0]["embedding"]
                    if len(embedding) != self._dimension:
                        message = (
                            f"Embedding dimension mismatch: model '{self._embedding_model}' "
                            f"produced {len(embedding)}d vectors, expected {self._dimension}d. "
                            f"Using hash fallback. Configure a model with {self._dimension}d output."
                        )
                        logger.warning(message)
                        self._record_embedding_fallback(message)
                        return self._hash_embed(text)
                    return embedding

                self._embedding_fn = _litellm_embed
                self._is_async_embed = True
                self._provider_label = f"litellm:{self._embedding_model}"
                logger.info(
                    f"Using embedding model: {self._embedding_model} "
                    f"(api_base={self._embedding_api_base or 'default'})"
                )
                return self._embedding_fn, self._is_async_embed
            except Exception as e:
                logger.warning(
                    f"Configured embedding model '{self._embedding_model}' "
                    f"failed to initialize: {e}. Falling back to hash-based."
                )
        else:
            logger.debug("No semantic embedding provider configured, using hash fallback")

        self._provider_label = "hash_fallback"
        self._embedding_fn = self._hash_embed
        self._is_async_embed = False
        return self._embedding_fn, self._is_async_embed

    def _hash_embed(self, text: str) -> list[float]:
        """Generate a deterministic pseudo-embedding from text.

        Not semantically meaningful, but provides stable vectors when every
        semantic provider is unavailable. The provider label makes this visible
        in diagnostics instead of silently pretending semantic memory works.
        """
        h = hashlib.sha256(text.encode()).digest()
        vec = []
        for i in range(0, min(len(h), self._dimension * 4), 4):
            chunk = h[i:i + 4]
            if len(chunk) < 4:
                chunk = chunk + b'\x00' * (4 - len(chunk))
            val = struct.unpack('>f', chunk)[0]
            vec.append(val)
        while len(vec) < self._dimension:
            vec.append(0.0)
        return vec[:self._dimension]

    async def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for the given text."""
        fn, is_async = self._get_embedding_fn()
        if is_async:
            return await fn(text)
        return fn(text)

    async def upsert(self, item_id: int, text: str, metadata: dict | None = None) -> None:
        """Store or update an embedding for an item.

        Args:
            item_id: Unique identifier for the item (e.g., conversation turn ID).
            text: The text to embed and store.
            metadata: Optional JSON metadata to store alongside the embedding.
        """
        if not self._initialized:
            return

        vector = await self.embed(text)
        vector_json = json.dumps(vector)
        conn = await self._db.get_connection()
        storage_id = self._storage_id(item_id)

        await conn.execute(
            "DELETE FROM vec_embeddings WHERE id = ?",
            (storage_id,),
        )
        await conn.execute(
            "INSERT INTO vec_embeddings (id, embedding) VALUES (?, ?)",
            (storage_id, vector_json),
        )
        await conn.commit()

        metadata = metadata or {}
        metadata = {
            **metadata,
            "embedding_provider": self._provider_label,
            "embedding_namespace": self._namespace,
            "original_item_id": int(item_id),
        }
        await self._db.system.set_preference(
            f"vec_meta:{storage_id}", json.dumps(metadata)
        )

    async def search(self, query_text: str, top_k: int = 5) -> list[dict]:
        """Search for similar items by embedding.

        Args:
            query_text: The text to search for similar matches.
            top_k: Number of results to return.

        Returns:
            List of dicts with 'id', 'distance', and optionally 'metadata'.
        """
        if not self._initialized:
            return []

        vector = await self.embed(query_text)
        vector_json = json.dumps(vector)
        conn = await self._db.get_connection()

        try:
            cursor = await conn.execute(
                """
                SELECT id, distance
                FROM vec_embeddings
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (vector_json, top_k),
            )
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                item_id = row["id"]
                meta_pref = await self._db.system.get_preference(f"vec_meta:{item_id}")
                metadata = json.loads(meta_pref) if meta_pref else None
                if metadata and metadata.get("embedding_namespace") != self._namespace:
                    continue
                results.append({
                    "id": metadata.get("original_item_id", item_id) if metadata else item_id,
                    "storage_id": item_id,
                    "distance": row["distance"],
                    "metadata": metadata,
                })
            return results
        except Exception as e:
            logger.debug(f"Vector search failed: {e}")
            return []

    async def delete(self, item_id: int) -> None:
        """Remove an embedding by item ID."""
        if not self._initialized:
            return

        conn = await self._db.get_connection()
        storage_id = self._storage_id(item_id)
        for candidate_id in {storage_id, int(item_id)}:
            await conn.execute(
                "DELETE FROM vec_embeddings WHERE id = ?",
                (candidate_id,),
            )
            await self._db.system.delete_preference(f"vec_meta:{candidate_id}")
        await conn.commit()

    async def delete_many(self, item_ids: list[int]) -> None:
        """Remove multiple embedding rows and their metadata."""
        if not self._initialized or not item_ids:
            return
        for item_id in item_ids:
            await self.delete(int(item_id))
