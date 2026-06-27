"""Provider-adapter registry for category metadata lookup.

The registry is keyed by provider capability rather than category id.  Category
YAML declares which providers participate in metadata lookup; this module only
knows which adapter family implements a provider and the provider's safe default
TTL/rate-limit envelope.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from src.integrations.metadata_providers.base import ProviderAdapterContext, ProviderInvocation, ProviderResult
from src.integrations.metadata_providers.books import BookMetadataProviders
from src.integrations.metadata_providers.music import MusicMetadataProviders

ProviderCallable = Callable[..., Awaitable[list[ProviderResult]]]


@dataclass(frozen=True)
class ProviderRegistryEntry:
    """Map one metadata provider id to its adapter family and invocation defaults."""

    adapter_family: str
    invocation: ProviderInvocation


class MetadataProviderRegistry:
    """Resolve category-declared metadata providers to adapter callables.

    Category definitions decide provider membership and per-category kwargs such
    as Internet Archive media type.  This registry deliberately avoids procedural
    category-id branches; adding a provider means adding one provider entry and,
    when needed, an adapter family class.
    """

    _PROVIDERS: dict[str, ProviderRegistryEntry] = {
        "musicbrainz": ProviderRegistryEntry(
            "music",
            ProviderInvocation("musicbrainz", "musicbrainz", ttl_seconds=14 * 24 * 60 * 60, min_interval_seconds=1.1),
        ),
        "discogs": ProviderRegistryEntry(
            "music",
            ProviderInvocation(
                "discogs",
                "discogs",
                keyed=True,
                key_name="token",
                ttl_seconds=14 * 24 * 60 * 60,
                min_interval_seconds=1.2,
            ),
        ),
        "acoustid": ProviderRegistryEntry(
            "music",
            ProviderInvocation(
                "acoustid",
                optional=True,
                enabled_default=False,
                skip_when_enabled_reason="requires a Chromaprint fingerprint; filename/title lookup is intentionally skipped",
            ),
        ),
        "open_library": ProviderRegistryEntry(
            "books",
            ProviderInvocation("open_library", "open_library", ttl_seconds=7 * 24 * 60 * 60, min_interval_seconds=0.4),
        ),
        "gutendex": ProviderRegistryEntry(
            "books",
            ProviderInvocation("gutendex", "gutendex", ttl_seconds=30 * 24 * 60 * 60, min_interval_seconds=0.5),
        ),
        "internet_archive": ProviderRegistryEntry(
            "books",
            ProviderInvocation("internet_archive", "internet_archive", ttl_seconds=7 * 24 * 60 * 60, min_interval_seconds=1.0),
        ),
        "google_books": ProviderRegistryEntry(
            "books",
            ProviderInvocation("google_books", "google_books", optional=True, ttl_seconds=7 * 24 * 60 * 60, min_interval_seconds=1.0),
        ),
        "apple_itunes_search": ProviderRegistryEntry(
            "books",
            ProviderInvocation(
                "apple_itunes_search",
                "apple_search",
                optional=True,
                ttl_seconds=24 * 60 * 60,
                min_interval_seconds=3.1,
            ),
        ),
        "comic_vine": ProviderRegistryEntry(
            "books",
            ProviderInvocation("comic_vine", "comic_vine", keyed=True, ttl_seconds=14 * 24 * 60 * 60, min_interval_seconds=1.0),
        ),
        "librivox": ProviderRegistryEntry(
            "books",
            ProviderInvocation("librivox", "librivox", ttl_seconds=14 * 24 * 60 * 60, min_interval_seconds=0.5),
        ),
    }
    _ADAPTERS: dict[str, type[Any]] = {
        "music": MusicMetadataProviders,
        "books": BookMetadataProviders,
    }

    def profile_for_category(self, category: Any) -> tuple[ProviderInvocation, ...]:
        """Return provider invocations declared by a category definition."""
        definitions = self._declared_provider_configs(category)
        invocations: list[ProviderInvocation] = []
        for provider, config in definitions.items():
            entry = self._PROVIDERS.get(provider)
            if entry is None:
                continue
            invocations.append(self._configured_invocation(entry.invocation, config))
        return tuple(invocations)

    def method_for_invocation(
        self,
        invocation: ProviderInvocation,
        context: ProviderAdapterContext,
    ) -> ProviderCallable | None:
        """Return the adapter method for one provider invocation, when implemented."""
        entry = self._PROVIDERS.get(invocation.provider)
        if entry is None:
            return None
        adapter_cls = self._ADAPTERS.get(entry.adapter_family)
        if adapter_cls is None:
            return None
        adapter = adapter_cls(context)
        method = getattr(adapter, invocation.method_name, None)
        return method if callable(method) else None

    def _declared_provider_configs(self, category: Any) -> dict[str, dict[str, Any]]:
        """Return ordered provider config mappings from the category definition."""
        definition = getattr(category, "definition", None)
        payload = definition if isinstance(definition, dict) else {}
        providers = ((payload.get("metadata") or {}).get("providers") or {}) if isinstance(payload.get("metadata"), dict) else {}
        if not isinstance(providers, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for provider, config in providers.items():
            provider_id = str(provider or "").strip()
            if not provider_id:
                continue
            result[provider_id] = dict(config) if isinstance(config, dict) else {"enabled": bool(config)}
        return result

    def _configured_invocation(self, base: ProviderInvocation, config: dict[str, Any]) -> ProviderInvocation:
        """Apply category-declared invocation overrides to provider defaults."""
        overrides: dict[str, Any] = {}
        for key in ("optional", "keyed", "key_name", "enabled_default", "skip_when_enabled_reason", "ttl_seconds", "min_interval_seconds"):
            if key in config:
                overrides[key] = config[key]
        if isinstance(config.get("kwargs"), dict):
            overrides["kwargs"] = dict(config["kwargs"])
        return replace(base, **overrides) if overrides else base


# Backward-compatible module-level facade for older tests/imports.  New runtime
# code should inject/use MetadataProviderRegistry directly.
_DEFAULT_REGISTRY = MetadataProviderRegistry()


def provider_profile(category: Any) -> tuple[ProviderInvocation, ...]:
    """Return provider invocations declared by a category object."""
    return _DEFAULT_REGISTRY.profile_for_category(category)


def provider_method(invocation: ProviderInvocation, context: ProviderAdapterContext) -> ProviderCallable | None:
    """Return an adapter method for one provider invocation."""
    return _DEFAULT_REGISTRY.method_for_invocation(invocation, context)
