"""
Unified LLM client for the LLM Providers library.

Provides a single interface for making completions through any
configured provider, resolving API keys and base URLs automatically
from the registry and key store.
"""

import litellm
from loguru import logger
from typing import Optional, Any
from src.llm_providers.registry import ProviderRegistry
from src.llm_providers.models import ModelInfo, ContextInfo


class LLMClient:
    """Unified client that routes completions through the active provider."""

    def __init__(self, registry: ProviderRegistry):
        self._registry = registry

    async def completion(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> Any:
        """Send a completion request through the active provider.

        Args:
            messages: Chat messages list.
            model: Model ID override. Uses active provider's default if not set.
            tools: Optional tool definitions for function calling.
            max_tokens: Maximum output tokens.
            temperature: Sampling temperature.
            **kwargs: Additional kwargs passed to litellm.acompletion.

        Returns:
            The litellm completion response.
        """
        config = self._registry.get_config()
        if not config:
            raise ValueError("No active provider configured. Call registry.set_active_provider() first.")

        if not model:
            raise ValueError(
                "A model must be provided for completion calls. "
                "The provider preset ID is not a valid model name."
            )
        resolved_model = model
        api_base = config.api_base_override or config.preset.api_base
        api_key = self._registry.get_resolved_api_key(config.provider_id)

        call_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "api_base": api_base,
            "temperature": temperature,
        }

        if api_key:
            call_kwargs["api_key"] = api_key
        if tools:
            call_kwargs["tools"] = tools
        if max_tokens:
            call_kwargs["max_tokens"] = max_tokens
        call_kwargs.update(kwargs)

        return await litellm.acompletion(**call_kwargs)

    def get_completion_params(self, model: Optional[str] = None) -> Optional[dict]:
        """Return the resolved parameters that would be used for a completion call.

        Useful for debugging or passing to other libraries.
        """
        config = self._registry.get_config()
        if not config:
            return None

        return {
            "model": model or config.preset.id,
            "api_base": config.api_base_override or config.preset.api_base,
            "api_key": self._registry.get_resolved_api_key(config.provider_id),
        }