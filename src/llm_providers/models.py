"""
Data models for the LLM Providers library.

All Pydantic models are self-contained here so this package
has zero coupling to any project-specific code.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


class ProviderType(str, Enum):
    """Supported LLM provider backends."""

    OPENROUTER = "openrouter"
    NVIDIA_NIM = "nvidia_nim"
    OLLAMA_CLOUD = "ollama_cloud"
    OLLAMA_LOCAL = "ollama_local"
    LM_STUDIO = "lm_studio"
    CUSTOM = "custom"


class PricingInfo(BaseModel):
    """Pricing details for a single model."""
    prompt_per_million: Optional[float] = None
    completion_per_million: Optional[float] = None
    currency: str = "USD"


class ContextInfo(BaseModel):
    """Context window and capability details for a model."""
    max_context_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_vision: bool = False
    supports_tools: bool = False
    supports_streaming: bool = True


class ModelInfo(BaseModel):
    """Full metadata for a single model from a provider."""
    id: str
    name: str
    provider_id: str
    pricing: PricingInfo = Field(default_factory=PricingInfo)
    context: ContextInfo = Field(default_factory=ContextInfo)
    owned_by: str = ""
    description: str = ""
    available: bool = True


class APIKeyEntry(BaseModel):
    """A stored API key with a label for identification."""
    id: str
    provider_id: str
    key: str
    label: str = "default"
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.now)


class ProviderPreset(BaseModel):
    """A preconfigured LLM provider endpoint."""
    id: str
    name: str
    provider_type: ProviderType = ProviderType.CUSTOM
    api_base: str = ""
    models_endpoint: str = ""
    requires_api_key: bool = True
    description: str = ""
    icon: str = ""
    supported_features: list[str] = Field(default_factory=list)


class ProviderStatus(BaseModel):
    """Runtime status of a provider connection."""
    provider_id: str
    reachable: bool = False
    model_count: int = 0
    last_checked: Optional[datetime] = None
    error: Optional[str] = None


class ProviderConfig(BaseModel):
    """A fully configured provider: preset + active key + resolved settings."""
    provider_id: str
    preset: ProviderPreset
    active_key_id: Optional[str] = None
    api_base_override: Optional[str] = None