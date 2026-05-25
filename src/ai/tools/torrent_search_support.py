"""Search-torrent tool support services.

This module keeps direct torrent search result normalization, candidate ID
attachment, and result-set persistence out of the tool facade.  It exists so
new providers and candidate presentation rules can be extended without changing
LLM tool registration code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from src.core.models import ToolExecutionContext

if TYPE_CHECKING:
    from src.core.database import Database
    from src.search.aggregator import SearchAggregator


class TorrentCandidatePresenter:
    """Convert cached torrent candidates into LLM-visible summaries."""

    def candidate_for_cache(self, index: int, result: object) -> dict[str, Any] | None:
        """Return a cacheable candidate or None when the title is blacklisted."""
        from src.utils.quality import extract_quality_tags

        tags = extract_quality_tags(getattr(result, "title", ""))
        if tags.get("content_blacklisted"):
            return None
        return {
            "index": index,
            "title": getattr(result, "title", None),
            "magnet": getattr(result, "magnet", None),
            "size": getattr(result, "size", None),
            "size_bytes": getattr(result, "size_bytes", None),
            "seeders": getattr(result, "seeders", None),
            "source": getattr(result, "source", None),
        }

    def public_candidate(self, candidate: dict[str, Any], result_set_id: str) -> dict[str, Any]:
        """Return the LLM-facing shape for one cached candidate."""
        from src.utils.quality import extract_quality_tags

        tags = extract_quality_tags(candidate.get("title") or "")
        item = {
            "index": candidate["index"],
            "candidate_id": candidate["candidate_id"],
            "result_set_id": result_set_id,
            "title": candidate.get("title"),
            "size": candidate.get("size"),
            "source": candidate.get("source"),
            "languages": tags.get("languages", []),
            "is_multi_language": tags.get("is_multi_language", False),
            "resolution": tags.get("resolution"),
            "codec": tags.get("codec"),
            "release_type": tags.get("release_type"),
        }
        if candidate.get("seeders") is not None:
            item["seeders"] = candidate.get("seeders")
        return item


class TorrentSearchToolService:
    """Search torrents and persist the resulting candidate set for later queueing."""

    def __init__(self, search_aggregator: "SearchAggregator", database: "Database" | None = None) -> None:
        """Create the service with a torrent search aggregator and optional DB cache."""
        self._search_aggregator = search_aggregator
        self._database = database
        self._presenter = TorrentCandidatePresenter()

    async def search(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        """Execute the search_torrents behavior and return public candidates."""
        query = arguments["query"]
        session_id = context.session_id or "default"
        raw_results = await self._search_aggregator.search(query)
        cache_candidates = self._cache_candidates(raw_results[:20])
        result_set_id = self._result_set_id(session_id, query, cache_candidates)
        await self._store_result_set(session_id, query, result_set_id, cache_candidates)
        return {
            "query": query,
            "result_set_id": result_set_id,
            "candidates": [self._presenter.public_candidate(candidate, result_set_id) for candidate in cache_candidates],
        }

    def _cache_candidates(self, results: list[object]) -> list[dict[str, Any]]:
        """Build cache records with stable IDs for non-blacklisted results."""
        from src.utils.candidate_ids import attach_candidate_ids

        records: list[dict[str, Any]] = []
        for result in results:
            candidate = self._presenter.candidate_for_cache(len(records) + 1, result)
            if candidate:
                records.append(candidate)
        return attach_candidate_ids(records)

    def _result_set_id(self, session_id: str, query: str, candidates: list[dict[str, Any]]) -> str:
        """Return the stable result-set identifier for this visible search page."""
        from src.utils.candidate_ids import stable_result_set_id

        return stable_result_set_id(
            session_id=session_id,
            name=query,
            query=query,
            season=None,
            episode=None,
            candidate_ids=[candidate["candidate_id"] for candidate in candidates],
        )

    async def _store_result_set(
        self,
        session_id: str,
        query: str,
        result_set_id: str,
        candidates: list[dict[str, Any]],
    ) -> None:
        """Persist visible candidates so queue_download can resolve IDs later."""
        if not self._database:
            return
        from src.utils.candidate_ids import store_result_set

        cache_data = {
            "name": query,
            "query": query,
            "season": None,
            "episode": None,
            "category_id": "",
            "result_set_id": result_set_id,
            "candidates": candidates,
        }
        try:
            await store_result_set(self._database, session_id=session_id, cache_data=cache_data)
        except Exception as exc:
            logger.warning(f"Failed to cache search_torrents options: {exc}")


class SupportToolProvider:
    """Compatibility provider for helper-only tool modules.

    This module contributes service collaborators consumed by a higher-level
    provider, so it intentionally returns no standalone agent tools.  Keeping a
    provider-shaped facade preserves package-wide smoke checks while still
    allowing implementation modules to remain focused and dependency-light.
    """

    def get_tools(self) -> list:
        """Return no tools because this support module is not an agent boundary."""
        return []
