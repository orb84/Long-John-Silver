"""Bounded execution for optional search-candidate LLM ranking."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from loguru import logger


_RankResult = TypeVar("_RankResult")


class SearchRankGuard:
    """Run one optional ranker without allowing it to block search results."""

    def __init__(self, timeout_seconds: float = 120.0) -> None:
        self.timeout_seconds = float(timeout_seconds)

    async def run(
        self,
        operation: Callable[[], Awaitable[_RankResult]],
        *,
        item_key: str,
        unit_label: str,
    ) -> _RankResult | None:
        """Return the ranker result or ``None`` on timeout/failure."""
        try:
            return await asyncio.wait_for(operation(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning(
                "Torrent LLM ranker timed out after %.1fs for %s %s; using deterministic candidate order",
                self.timeout_seconds,
                item_key,
                unit_label,
            )
        except RecursionError as exc:
            logger.error(
                "Torrent LLM ranker recursed for {} {}: {}; using unranked candidates",
                item_key,
                unit_label,
                exc,
            )
        except Exception as exc:
            logger.warning(
                "Torrent LLM ranker failed for {} {}: {}; using unranked candidates",
                item_key,
                unit_label,
                exc,
            )
        return None
