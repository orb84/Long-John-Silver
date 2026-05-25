"""LLM Providers — portable LLM endpoint management.

The package exports lightweight data models eagerly and loads heavy runtime
clients lazily.  Importing metadata helpers such as
``src.llm_providers.context_limits`` must not require optional completion
libraries like litellm.
"""

from src.llm_providers.models import (
    ProviderType,
    ProviderPreset,
    ProviderConfig,
    ProviderStatus,
    ModelInfo,
    PricingInfo,
    ContextInfo,
    APIKeyEntry,
)
from src.llm_providers.key_store import KeyStore
from src.llm_providers.registry import ProviderRegistry
from src.llm_providers.catalog import ModelCatalog
from src.llm_providers.presets import get_ordered_presets


def __getattr__(name: str):
    """Lazy-load runtime clients that may depend on optional packages."""
    if name == "LLMProviderManager":
        from src.llm_providers.manager import LLMProviderManager
        return LLMProviderManager
    if name == "LLMClient":
        from src.llm_providers.client import LLMClient
        return LLMClient
    if name in {"TaskLLMClient", "ResolvedLLMTask"}:
        from src.llm_providers.task_client import TaskLLMClient, ResolvedLLMTask
        return {"TaskLLMClient": TaskLLMClient, "ResolvedLLMTask": ResolvedLLMTask}[name]
    raise AttributeError(name)


__all__ = [
    "LLMProviderManager",
    "KeyStore",
    "ProviderRegistry",
    "ModelCatalog",
    "LLMClient",
    "TaskLLMClient",
    "ResolvedLLMTask",
    "ProviderType",
    "ProviderPreset",
    "ProviderConfig",
    "ProviderStatus",
    "ModelInfo",
    "PricingInfo",
    "ContextInfo",
    "APIKeyEntry",
    "get_ordered_presets",
]
