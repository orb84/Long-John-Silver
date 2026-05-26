"""Category metadata provider adapter registry."""

from .base import ProviderInvocation, ProviderResult, make_stable_id
from .registry import provider_profile, provider_method

__all__ = ["ProviderInvocation", "ProviderResult", "make_stable_id", "provider_profile", "provider_method"]
