"""
Built-in provider presets for the LLM Providers library.

Each preset ships with the correct base URL, models endpoint,
and metadata so users can connect with zero configuration.
"""

from src.llm_providers.models import ProviderPreset, ProviderType

BUILTIN_PRESETS: dict[str, ProviderPreset] = {
    "openrouter": ProviderPreset(
        id="openrouter",
        name="OpenRouter",
        provider_type=ProviderType.OPENROUTER,
        api_base="https://openrouter.ai/api/v1",
        models_endpoint="https://openrouter.ai/api/v1/models",
        requires_api_key=True,
        description="Multi-provider cloud gateway. Supports GPT-4, Claude, Llama, Mixtral and 200+ more.",
        icon="routing",
        supported_features=["tools", "vision", "streaming"],
    ),

    "openai": ProviderPreset(
        id="openai",
        name="OpenAI",
        provider_type=ProviderType.CUSTOM,
        api_base="https://api.openai.com/v1",
        models_endpoint="https://api.openai.com/v1/models",
        requires_api_key=True,
        description="Direct OpenAI API endpoint for GPT models.",
        icon="sparkles",
        supported_features=["tools", "vision", "streaming"],
    ),
    "deepseek": ProviderPreset(
        id="deepseek",
        name="DeepSeek",
        provider_type=ProviderType.CUSTOM,
        api_base="https://api.deepseek.com/v1",
        models_endpoint="https://api.deepseek.com/v1/models",
        requires_api_key=True,
        description="OpenAI-compatible DeepSeek endpoint.",
        icon="brain",
        supported_features=["tools", "streaming"],
    ),
    "groq": ProviderPreset(
        id="groq",
        name="Groq",
        provider_type=ProviderType.CUSTOM,
        api_base="https://api.groq.com/openai/v1",
        models_endpoint="https://api.groq.com/openai/v1/models",
        requires_api_key=True,
        description="Low-latency OpenAI-compatible Groq endpoint.",
        icon="zap",
        supported_features=["tools", "streaming"],
    ),
    "mistral": ProviderPreset(
        id="mistral",
        name="Mistral AI",
        provider_type=ProviderType.CUSTOM,
        api_base="https://api.mistral.ai/v1",
        models_endpoint="https://api.mistral.ai/v1/models",
        requires_api_key=True,
        description="Mistral's OpenAI-compatible API endpoint.",
        icon="wind",
        supported_features=["tools", "streaming"],
    ),
    "xai": ProviderPreset(
        id="xai",
        name="xAI",
        provider_type=ProviderType.CUSTOM,
        api_base="https://api.x.ai/v1",
        models_endpoint="https://api.x.ai/v1/models",
        requires_api_key=True,
        description="xAI's OpenAI-compatible API endpoint.",
        icon="x",
        supported_features=["tools", "vision", "streaming"],
    ),
    "together": ProviderPreset(
        id="together",
        name="Together AI",
        provider_type=ProviderType.CUSTOM,
        api_base="https://api.together.xyz/v1",
        models_endpoint="https://api.together.xyz/v1/models",
        requires_api_key=True,
        description="OpenAI-compatible endpoint for many open-weight models.",
        icon="layers",
        supported_features=["tools", "streaming"],
    ),
    "fireworks": ProviderPreset(
        id="fireworks",
        name="Fireworks AI",
        provider_type=ProviderType.CUSTOM,
        api_base="https://api.fireworks.ai/inference/v1",
        models_endpoint="https://api.fireworks.ai/inference/v1/models",
        requires_api_key=True,
        description="OpenAI-compatible Fireworks inference endpoint.",
        icon="flame",
        supported_features=["tools", "streaming"],
    ),
    "deepinfra": ProviderPreset(
        id="deepinfra",
        name="DeepInfra",
        provider_type=ProviderType.CUSTOM,
        api_base="https://api.deepinfra.com/v1/openai",
        models_endpoint="https://api.deepinfra.com/v1/openai/models",
        requires_api_key=True,
        description="OpenAI-compatible endpoint for hosted open-weight models.",
        icon="server",
        supported_features=["tools", "streaming"],
    ),
    "cerebras": ProviderPreset(
        id="cerebras",
        name="Cerebras",
        provider_type=ProviderType.CUSTOM,
        api_base="https://api.cerebras.ai/v1",
        models_endpoint="https://api.cerebras.ai/v1/models",
        requires_api_key=True,
        description="Fast OpenAI-compatible Cerebras inference endpoint.",
        icon="cpu",
        supported_features=["tools", "streaming"],
    ),
    "perplexity": ProviderPreset(
        id="perplexity",
        name="Perplexity",
        provider_type=ProviderType.CUSTOM,
        api_base="https://api.perplexity.ai",
        models_endpoint="https://api.perplexity.ai/models",
        requires_api_key=True,
        description="Perplexity API endpoint, useful for web-grounded models.",
        icon="search",
        supported_features=["streaming"],
    ),
    "nvidia_nim": ProviderPreset(
        id="nvidia_nim",
        name="NVIDIA NIM",
        provider_type=ProviderType.NVIDIA_NIM,
        api_base="https://integrate.api.nvidia.com/v1",
        models_endpoint="https://integrate.api.nvidia.com/v1/models",
        requires_api_key=True,
        description="NVIDIA Inference Microservices. GPU-accelerated Llama, Mistral, Mixtral, Nemotron.",
        icon="gpu",
        supported_features=["tools", "streaming"],
    ),
    "ollama_cloud": ProviderPreset(
        id="ollama_cloud",
        name="Ollama Cloud",
        provider_type=ProviderType.OLLAMA_CLOUD,
        api_base="https://api.ollama.ai/v1",
        models_endpoint="https://api.ollama.ai/v1/models",
        requires_api_key=True,
        description="Ollama managed cloud. Run open-source models without local hardware.",
        icon="cloud",
        supported_features=["tools", "streaming"],
    ),
    "ollama_local": ProviderPreset(
        id="ollama_local",
        name="Ollama (Local)",
        provider_type=ProviderType.OLLAMA_LOCAL,
        api_base="http://localhost:11434/v1",
        models_endpoint="http://localhost:11434/v1/models",
        requires_api_key=False,
        description="Run models locally with Ollama. No API key needed. Requires Ollama installed.",
        icon="server",
        supported_features=["tools", "vision", "streaming"],
    ),
    "lm_studio": ProviderPreset(
        id="lm_studio",
        name="LM Studio (Local)",
        provider_type=ProviderType.LM_STUDIO,
        api_base="http://localhost:1234/v1",
        models_endpoint="http://localhost:1234/v1/models",
        requires_api_key=False,
        description="Run models locally with LM Studio. No API key needed. Requires LM Studio running.",
        icon="server",
        supported_features=["tools", "vision", "streaming"],
    ),
}


def get_builtin_presets() -> dict[str, ProviderPreset]:
    """Return all built-in presets."""
    return dict(BUILTIN_PRESETS)


def get_ordered_presets() -> list[ProviderPreset]:
    """Return built-in presets in display order."""
    order = [
        "openrouter", "openai", "deepseek", "groq", "mistral", "xai",
        "together", "fireworks", "deepinfra", "cerebras", "perplexity",
        "nvidia_nim", "ollama_cloud", "ollama_local", "lm_studio",
    ]
    return [BUILTIN_PRESETS[k] for k in order if k in BUILTIN_PRESETS]