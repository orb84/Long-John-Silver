"""Declarative provider profile and adapter lookup for category metadata."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.integrations.metadata_providers.base import ProviderAdapterContext, ProviderInvocation, ProviderResult
from src.integrations.metadata_providers.books import BookMetadataProviders
from src.integrations.metadata_providers.music import MusicMetadataProviders

ProviderCallable = Callable[..., Awaitable[list[ProviderResult]]]

_PROVIDER_PROFILES: dict[str, tuple[ProviderInvocation, ...]] = {
    "music": (
        ProviderInvocation("musicbrainz", "musicbrainz", ttl_seconds=14 * 24 * 60 * 60, min_interval_seconds=1.1),
        ProviderInvocation("discogs", "discogs", keyed=True, key_name="token", ttl_seconds=14 * 24 * 60 * 60, min_interval_seconds=1.2),
        ProviderInvocation(
            "acoustid",
            optional=True,
            enabled_default=False,
            skip_when_enabled_reason="requires a Chromaprint fingerprint; filename/title lookup is intentionally skipped",
        ),
    ),
    "ebooks": (
        ProviderInvocation("open_library", "open_library", ttl_seconds=7 * 24 * 60 * 60, min_interval_seconds=0.4),
        ProviderInvocation("gutendex", "gutendex", ttl_seconds=30 * 24 * 60 * 60, min_interval_seconds=0.5),
        ProviderInvocation("internet_archive", "internet_archive", kwargs={"mediatype": "texts"}, ttl_seconds=7 * 24 * 60 * 60, min_interval_seconds=1.0),
        ProviderInvocation("google_books", "google_books", optional=True, ttl_seconds=7 * 24 * 60 * 60, min_interval_seconds=1.0),
        ProviderInvocation("apple_itunes_search", "apple_search", optional=True, kwargs={"media": "ebook"}, ttl_seconds=24 * 60 * 60, min_interval_seconds=3.1),
        ProviderInvocation("comic_vine", "comic_vine", keyed=True, ttl_seconds=14 * 24 * 60 * 60, min_interval_seconds=1.0),
    ),
    "audiobooks": (
        ProviderInvocation("librivox", "librivox", ttl_seconds=14 * 24 * 60 * 60, min_interval_seconds=0.5),
        ProviderInvocation("open_library", "open_library", ttl_seconds=7 * 24 * 60 * 60, min_interval_seconds=0.4),
        ProviderInvocation("internet_archive", "internet_archive", kwargs={"mediatype": "audio"}, ttl_seconds=7 * 24 * 60 * 60, min_interval_seconds=1.0),
        ProviderInvocation("google_books", "google_books", optional=True, ttl_seconds=7 * 24 * 60 * 60, min_interval_seconds=1.0),
        ProviderInvocation("apple_itunes_search", "apple_search", optional=True, kwargs={"media": "audiobook"}, ttl_seconds=24 * 60 * 60, min_interval_seconds=3.1),
    ),
}


def provider_profile(category_id: str) -> tuple[ProviderInvocation, ...]:
    """Return the provider profile for a category ID."""
    return _PROVIDER_PROFILES.get(str(category_id), ())


def provider_method(category_id: str, context: ProviderAdapterContext, method_name: str) -> ProviderCallable | None:
    """Return an adapter method for a category/provider profile entry."""
    category_id = str(category_id)
    if category_id == "music":
        adapter: Any = MusicMetadataProviders(context)
    elif category_id in {"ebooks", "audiobooks"}:
        adapter = BookMetadataProviders(context)
    else:
        return None
    method = getattr(adapter, method_name, None)
    return method if callable(method) else None
