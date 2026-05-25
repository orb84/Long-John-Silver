"""Local embedding runtime for semantic memory.

This module isolates model loading/downloading from VectorStore so the rest of
LJS can treat embeddings as a small, local capability instead of a chat-model
side effect. The default backend is FastEmbed/ONNX: it is CPU-first,
cross-platform, and downloads the configured model into the application cache on
first use.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

try:  # Runtime type only; Settings imports this module early in some tests.
    from src.core.models import EmbeddingSettings
except Exception:  # pragma: no cover - import-cycle guard for static analyzers
    EmbeddingSettings = Any  # type: ignore


BUILTIN_EMBEDDING_MODELS: dict[str, dict[str, int | str]] = {
    "sentence-transformers/all-MiniLM-L6-v2": {
        "dimension": 384,
        "approx_size_mb": 90,
        "description": "Tiny general-purpose sentence embeddings, good default for local semantic memory.",
    },
    "BAAI/bge-small-en-v1.5": {
        "dimension": 384,
        "approx_size_mb": 133,
        "description": "Small English retrieval model for users who prefer BGE-style embeddings.",
    },
}


@dataclass(frozen=True)
class EmbeddingRuntimeStatus:
    """Current health and readiness of the local embedding runtime."""

    provider: str
    model: str
    dimension: int
    ready: bool
    cache_dir: str
    message: str = ""


class BuiltinEmbeddingRuntime:
    """Generate embeddings with the configured built-in FastEmbed model.

    The runtime lazy-loads the model, which lets first-run setup remain fast. A
    background warm-up may call :meth:`ensure_ready` to force the download and
    cache population invisibly after the app has started.
    """

    def __init__(self, settings: EmbeddingSettings | None = None) -> None:
        """Initialize with embedding settings from the application config."""
        self._settings = settings
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()
        self._status = EmbeddingRuntimeStatus(
            provider=getattr(settings, "provider", "builtin") if settings else "builtin",
            model=getattr(settings, "builtin_model", "sentence-transformers/all-MiniLM-L6-v2") if settings else "sentence-transformers/all-MiniLM-L6-v2",
            dimension=int(getattr(settings, "dimension", 384) if settings else 384),
            ready=False,
            cache_dir=str(getattr(settings, "cache_dir", "./data/embedding_models") if settings else "./data/embedding_models"),
            message="Not loaded yet.",
        )

    @property
    def status(self) -> EmbeddingRuntimeStatus:
        """Return the last known runtime status."""
        return self._status

    @property
    def model_name(self) -> str:
        """Return the configured built-in embedding model name."""
        return str(getattr(self._settings, "builtin_model", self._status.model))

    @property
    def dimension(self) -> int:
        """Return the configured embedding dimension."""
        return int(getattr(self._settings, "dimension", self._status.dimension))

    @property
    def cache_dir(self) -> Path:
        """Return the resolved model cache directory."""
        return Path(str(getattr(self._settings, "cache_dir", self._status.cache_dir))).expanduser()

    def enabled(self) -> bool:
        """Return whether the built-in runtime should be used."""
        if self._settings is None:
            return True
        return bool(getattr(self._settings, "enabled", True)) and getattr(self._settings, "provider", "builtin") == "builtin"

    async def ensure_ready(self) -> EmbeddingRuntimeStatus:
        """Load the model and download cache artifacts when necessary."""
        if not self.enabled():
            self._status = EmbeddingRuntimeStatus(
                provider=getattr(self._settings, "provider", "disabled") if self._settings else "disabled",
                model=self.model_name,
                dimension=self.dimension,
                ready=False,
                cache_dir=str(self.cache_dir),
                message="Built-in embeddings are disabled by settings.",
            )
            return self._status

        await self._load_model()
        return self._status

    async def embed(self, text: str) -> list[float]:
        """Embed one text string with the local model."""
        await self._load_model()
        model = self._model
        if model is None:
            raise RuntimeError(self._status.message or "Embedding model is not loaded")

        def _encode() -> list[float]:
            vectors = list(model.embed([text or ""]))
            if not vectors:
                raise RuntimeError("FastEmbed returned no vectors")
            vector = vectors[0]
            if hasattr(vector, "tolist"):
                vector = vector.tolist()
            return [float(v) for v in vector]

        vector = await asyncio.to_thread(_encode)
        if len(vector) != self.dimension:
            raise RuntimeError(
                f"Built-in embedding dimension mismatch: got {len(vector)}, expected {self.dimension}"
            )
        return vector

    async def _load_model(self) -> None:
        """Instantiate the FastEmbed model exactly once."""
        if self._model is not None:
            return
        async with self._load_lock:
            if self._model is not None:
                return
            cache_dir = self.cache_dir
            cache_dir.mkdir(parents=True, exist_ok=True)
            auto_download = bool(getattr(self._settings, "auto_download", True) if self._settings else True)
            if not auto_download and not any(cache_dir.iterdir()):
                raise RuntimeError(
                    "Built-in embedding model is not cached and automatic download is disabled."
                )

            self._validate_model_policy()

            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                self._status = EmbeddingRuntimeStatus(
                    provider="builtin",
                    model=self.model_name,
                    dimension=self.dimension,
                    ready=False,
                    cache_dir=str(cache_dir),
                    message=(
                        "fastembed is not installed. Install application dependencies; "
                        "LJS may auto-download model files, but it never mutates the Python environment at runtime."
                    ),
                )
                raise RuntimeError(self._status.message) from exc

            try:
                self._model = await asyncio.to_thread(
                    lambda: TextEmbedding(model_name=self.model_name, cache_dir=str(cache_dir))
                )
                self._status = EmbeddingRuntimeStatus(
                    provider="builtin",
                    model=self.model_name,
                    dimension=self.dimension,
                    ready=True,
                    cache_dir=str(cache_dir),
                    message="Built-in embedding model is ready.",
                )
                logger.info(
                    "Built-in embedding model ready: {} (cache={})",
                    self.model_name,
                    cache_dir,
                )
            except Exception as exc:
                self._status = EmbeddingRuntimeStatus(
                    provider="builtin",
                    model=self.model_name,
                    dimension=self.dimension,
                    ready=False,
                    cache_dir=str(cache_dir),
                    message=f"Failed to load built-in embedding model: {exc}",
                )
                raise

    def _validate_model_policy(self) -> None:
        """Validate the configured built-in model against local-memory policy.

        Dependency installation is handled by the packaged app or development
        environment.  At runtime we only allow model-file download/caching.  The
        catalog keeps the default setup honest about the ~150 MB target while
        still allowing advanced users to explicitly configure another provider.
        """
        info = BUILTIN_EMBEDDING_MODELS.get(self.model_name)
        if not info:
            logger.warning(
                "Embedding model '{}' is not in the built-in size catalog; proceeding as an advanced override.",
                self.model_name,
            )
            return
        expected_dim = int(info.get("dimension", self.dimension))
        if expected_dim != self.dimension:
            raise RuntimeError(
                f"Built-in embedding model '{self.model_name}' expects {expected_dim} dimensions, "
                f"but settings request {self.dimension}."
            )
        max_mb = int(getattr(self._settings, "max_model_size_mb", 150) if self._settings else 150)
        approx_mb = int(info.get("approx_size_mb", 0) or 0)
        if approx_mb and approx_mb > max_mb:
            raise RuntimeError(
                f"Built-in embedding model '{self.model_name}' is about {approx_mb} MB, "
                f"above the configured {max_mb} MB limit."
            )
