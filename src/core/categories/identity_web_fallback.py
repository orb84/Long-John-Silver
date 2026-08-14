"""Bounded invocation helper for category-owned public-web identity checks."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class CategoryIdentityWebProbe:
    """Invoke one category-owned public-web identity hook safely."""

    @staticmethod
    async def probe(
        title: str,
        category_id: str,
        *,
        category_registry: Any,
        settings: Any,
        database: Any,
        metadata_clients: dict[str, object],
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        """Run one selected category's web identity hook within a hard timeout."""
        category = category_registry.get(category_id) if category_registry else None
        hook = getattr(category, "identify_agent_item_via_web", None) if category else None
        if not callable(hook):
            return []
        try:
            rows = await asyncio.wait_for(
                hook(
                    title,
                    settings=settings,
                    db=database,
                    metadata_clients=metadata_clients,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Category {} web identity fallback timed out for {!r}", category_id, title)
            return []
        except Exception as exc:
            logger.debug("Category {} web identity fallback failed for {!r}: {}", category_id, title, exc)
            return []
        return [row for row in (rows or []) if isinstance(row, dict)]
