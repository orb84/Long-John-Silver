"""Shared contracts for category metadata provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Awaitable, Callable

import httpx

from src.core.category_object_models import ExternalIdentity


def compact(value: Any) -> str:
    """Return a single-line non-empty string for public metadata fields."""
    return str(value or "").strip()


def as_list(value: Any) -> list[Any]:
    """Normalize provider values that may be missing, scalar, or already a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def safe_query_fragment(query: str) -> str:
    """Return a conservative text fragment for Internet Archive advanced search."""
    return re.sub(r"[^A-Za-z0-9 _.'-]", " ", query).strip()


def norm_text(value: str) -> str:
    """Normalize text for scoring/deduplication."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", str(value or "").lower())).strip()


def identity(provider: str, key: str, value: Any, entity_type: str = "") -> ExternalIdentity | None:
    """Build a stable identity object when the provider returned a value."""
    text = compact(value)
    if not text:
        return None
    return ExternalIdentity(provider=provider, key=key, value=text, entity_type=entity_type)


def identifier_map(identities: list[ExternalIdentity]) -> dict[str, str]:
    """Return a flat identifier map from identity objects."""
    return {identity.key: identity.value for identity in identities if identity.value}


def make_stable_id(provider: str, identifiers: dict[str, str], title: str, contributors: list[str], year: str | None = None) -> str:
    """Build a category-independent stable metadata candidate ID."""
    for key, value in identifiers.items():
        if value:
            return f"{provider}:{key}:{value}"
    fallback = json.dumps([provider, norm_text(title), [norm_text(x) for x in contributors], str(year or "")], ensure_ascii=False)
    return f"{provider}:fingerprint:{hashlib.sha1(fallback.encode('utf-8')).hexdigest()}"


@dataclass
class ProviderResult:
    """Normalized metadata result returned by one provider adapter."""

    provider: str
    title: str
    identifiers: dict[str, str]
    summary: str = ""
    contributors: list[str] | None = None
    year: str | None = None
    cover_url: str | None = None
    raw: dict[str, Any] | None = None
    object_model: dict[str, Any] | None = None
    stable_id: str = ""
    entity_type: str = ""
    score: float = 0.0
    evidence: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.stable_id:
            self.stable_id = make_stable_id(self.provider, self.identifiers, self.title, self.contributors or [], self.year)

    def as_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable shape suitable for ActionReceipt.data."""
        return {
            "provider": self.provider,
            "title": self.title,
            "summary": self.summary,
            "contributors": self.contributors or [],
            "year": self.year,
            "cover_url": self.cover_url,
            "identifiers": self.identifiers,
            "stable_id": self.stable_id,
            "entity_type": self.entity_type,
            "score": round(float(self.score or 0.0), 4),
            "evidence": list(self.evidence or []),
            "object_model": self.object_model or {},
            "raw": self.raw or {},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderResult":
        """Rehydrate a cached provider result."""
        return cls(
            provider=compact(data.get("provider")),
            title=compact(data.get("title")),
            summary=compact(data.get("summary")),
            contributors=[str(x) for x in as_list(data.get("contributors")) if str(x).strip()],
            year=compact(data.get("year")) or None,
            cover_url=compact(data.get("cover_url")) or None,
            identifiers={str(k): str(v) for k, v in (data.get("identifiers") or {}).items() if str(v).strip()},
            raw=data.get("raw") if isinstance(data.get("raw"), dict) else {},
            object_model=data.get("object_model") if isinstance(data.get("object_model"), dict) else {},
            stable_id=compact(data.get("stable_id")),
            entity_type=compact(data.get("entity_type")),
            score=float(data.get("score") or 0.0),
            evidence=[str(x) for x in as_list(data.get("evidence")) if str(x).strip()],
        )


@dataclass(frozen=True)
class ProviderInvocation:
    """Declarative provider call in a category metadata profile."""

    provider: str
    method_name: str = ""
    optional: bool = False
    keyed: bool = False
    key_name: str = "api_key"
    enabled_default: bool = True
    kwargs: dict[str, Any] | None = None
    skip_when_enabled_reason: str = ""
    ttl_seconds: int = 7 * 24 * 60 * 60
    min_interval_seconds: float = 1.0


@dataclass
class ProviderAdapterContext:
    """Runtime context passed to provider adapters."""

    category: Any
    settings: Any
    client: httpx.AsyncClient
    get_json: Callable[[httpx.AsyncClient, str, str], Awaitable[dict[str, Any]]]

    def enabled(self, provider: str, *, default: bool = True) -> bool:
        """Return whether a category service/provider is enabled."""
        return bool(self.category.category_service_enabled(self.settings, provider, default=default))

    def secret(self, provider: str, key: str) -> str | None:
        """Return a provider secret from private category config, if present."""
        return self.category.category_service_secret(self.settings, provider, key)

    async def json(self, provider: str, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET provider JSON through the resolver's throttled HTTP boundary."""
        return await self.get_json(self.client, provider, url, params=params or {})
