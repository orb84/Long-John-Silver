"""Credential-to-endpoint ownership rules for LJS LLM provider routes."""

from __future__ import annotations

from typing import Any


class ProviderCredentialPolicy:
    """Decide when an automatically stored provider key may leave the process."""

    @staticmethod
    def is_provider_owned_endpoint(registry: Any, provider_id: str, api_base: str | None) -> bool:
        """Return whether ``api_base`` is the provider preset's canonical endpoint.

        Registry endpoint overrides are intentionally not trusted here: an override
        is an operator-selected/custom route and must never automatically inherit a
        credential stored for the provider's canonical service.
        """
        if not provider_id:
            return False
        preset = registry.get_preset(provider_id)
        preset_base = str(getattr(preset, "api_base", "") or "").rstrip("/")
        if not api_base:
            return bool(preset_base)
        return bool(preset_base) and str(api_base).rstrip("/") == preset_base
