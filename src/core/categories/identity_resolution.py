"""Evidence-based media category identity resolution.

Natural-language router vocabulary is useful as a cheap hint, but it cannot be
an authority: users may speak any language, titles can exist in several media
forms, and an LLM may invent a category argument.  This service compares exact
local identity with bounded evidence supplied by each installed category.  It
never maps provider-specific media types to categories itself.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from src.integrations.metadata_disambiguation import norm_text, title_query_score
from src.core.categories.identity_packets import CategoryIdentityPackets
from src.core.categories.identity_web_fallback import CategoryIdentityWebProbe


class CategoryIdentityResolver:
    """Resolve a literal title to one installed category using owned evidence."""

    _TOTAL_TIMEOUT_SECONDS = 12.0
    _CATEGORY_TIMEOUT_SECONDS = 9.0
    _STRONG_SCORE = 0.76
    _PLAUSIBLE_SCORE = 0.62
    _AMBIGUITY_MARGIN = 0.12

    def __init__(
        self,
        *,
        settings_manager: Any,
        database: Any,
        category_registry: Any,
        metadata_clients: dict[str, object] | None = None,
    ) -> None:
        self._settings_manager = settings_manager
        self._database = database
        self._registry = category_registry
        self._metadata_clients = dict(metadata_clients or {})
        self._packets = CategoryIdentityPackets(category_registry)

    async def resolve(
        self,
        item_name: str,
        *,
        category_hint: str | None = None,
        request_text: str | None = None,
    ) -> dict[str, Any]:
        """Return a resolved, ambiguous, or unresolved category identity packet."""
        title = str(item_name or "").strip()
        if not title:
            return self._packets.unresolved(title, [], "A media title is required before category resolution.")

        local = await self._local_candidates(title)
        if len(local) == 1:
            return self._packets.resolved(title, local[0], local, source="local_library_or_tracking")
        if len(local) > 1:
            return self._packets.ambiguous(
                title,
                local,
                reason="The title exists in more than one installed library category.",
            )

        router_hints = self._router_hint_ids(request_text or title)
        # Hints are never category authority.  They are, however, useful for
        # choosing which category-owned verifier runs first.  When that verifier
        # returns strong exact metadata evidence, probing unrelated categories is
        # both wasteful and actively harmful: common titles such as "Silo" can
        # produce weak book/audio catalog hits after TMDB has already confirmed a
        # TV series.  Only fall back to the cross-category comparison when the
        # hinted category cannot verify the title strongly.
        valid_hint = self._valid_hint(category_hint)
        if valid_hint is None and len(router_hints) == 1:
            valid_hint = self._valid_hint(router_hints[0])

        hinted_resolution, hinted_candidates = await self._resolve_selected_category(
            title,
            valid_hint=valid_hint,
            router_hints=router_hints,
        )
        if hinted_resolution is not None:
            return hinted_resolution

        provider_candidates = hinted_candidates + await self._bounded_category_candidates(
            title,
            exclude_category_ids={valid_hint} if valid_hint else None,
        )
        ranked = self._rank_candidates(
            title,
            provider_candidates,
            category_hint=valid_hint,
            router_hints=router_hints,
        )
        if not ranked:
            return self._packets.unresolved(
                title,
                [],
                "No installed category metadata service produced a sufficiently close match.",
                router_hints=router_hints,
                metadata_attempted=True,
            )

        best = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None
        close_competitor = bool(
            runner_up
            and runner_up["category_id"] != best["category_id"]
            and float(best["score"]) - float(runner_up["score"]) < self._AMBIGUITY_MARGIN
        )
        plausible_categories = {
            str(row["category_id"])
            for row in ranked
            if float(row["score"]) >= self._PLAUSIBLE_SCORE
        }
        if close_competitor or len(plausible_categories) > 1:
            return self._packets.ambiguous(
                title,
                ranked,
                reason="Category-owned metadata services found plausible matches in more than one category.",
                router_hints=router_hints,
            )
        if float(best["score"]) >= self._STRONG_SCORE:
            return self._packets.resolved(title, best, ranked, source="category_metadata")
        return self._packets.unresolved(
            title,
            ranked,
            "Metadata evidence was too weak to choose a category safely.",
            router_hints=router_hints,
            metadata_attempted=True,
        )

    async def _resolve_selected_category(
        self,
        title: str,
        *,
        valid_hint: str | None,
        router_hints: list[str],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Verify the hinted category first and return its evidence for fallback."""
        if not valid_hint:
            return None, []

        candidates = await self._bounded_category_candidates(title, category_ids={valid_hint})
        ranked = self._rank_candidates(
            title,
            candidates,
            category_hint=valid_hint,
            router_hints=router_hints,
        )
        if ranked and float(ranked[0]["score"]) >= self._STRONG_SCORE:
            return (
                self._packets.resolved(
                    title,
                    ranked[0],
                    ranked,
                    source="hint_selected_category_metadata",
                ),
                candidates,
            )

        # Structured metadata can be disabled, unavailable, or temporarily
        # empty. Ask only the selected category for its bounded public-web
        # identity fallback; never fan public searches out across all installed
        # categories.
        web_candidates = await self._bounded_category_web_candidates(title, valid_hint)
        candidates.extend(web_candidates)
        ranked = self._rank_candidates(
            title,
            candidates,
            category_hint=valid_hint,
            router_hints=router_hints,
        )
        if ranked and float(ranked[0]["score"]) >= self._STRONG_SCORE:
            return (
                self._packets.resolved(
                    title,
                    ranked[0],
                    ranked,
                    source="hint_selected_category_web",
                ),
                candidates,
            )
        return None, candidates

    async def _local_candidates(self, title: str) -> list[dict[str, Any]]:
        """Return exact tracked/library matches, which outrank remote metadata."""
        wanted = norm_text(title)
        candidates: list[dict[str, Any]] = []
        settings = getattr(self._settings_manager, "settings", None)
        for item in getattr(settings, "tracked_items", []) or []:
            key = str(getattr(item, "key", "") or "").strip()
            category_id = str(getattr(item, "item_type", "") or "").strip()
            if key and norm_text(key) == wanted and self._valid_category(category_id):
                candidates.append(self._candidate(category_id, key, "tracked_item", 1.0))

        media_repo = getattr(self._database, "media", None) if self._database is not None else None
        if media_repo is not None and hasattr(media_repo, "list_category_items"):
            try:
                rows = await media_repo.list_category_items()
                candidates.extend(self._library_rows(title, wanted, rows))
            except Exception as exc:
                logger.debug("Category identity local-item lookup failed for {!r}: {}", title, exc)
        return self._dedupe_by_category(candidates)

    def _library_rows(self, title: str, wanted: str, rows: Any) -> list[dict[str, Any]]:
        """Convert exact canonical library rows into authoritative candidates."""
        candidates: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            names = [row.get("item_id"), row.get("display_name"), row.get("title"), row.get("name")]
            if not any(norm_text(str(value or "")) == wanted for value in names if value):
                continue
            category_id = str(row.get("category_id") or row.get("item_type") or "").strip()
            if self._valid_category(category_id):
                candidates.append(
                    self._candidate(
                        category_id,
                        str(row.get("display_name") or row.get("item_id") or title),
                        "canonical_library_item",
                        1.0,
                    )
                )
        return candidates

    async def _bounded_category_candidates(
        self,
        title: str,
        *,
        category_ids: set[str] | None = None,
        exclude_category_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Ask installed categories for identity evidence within one hard budget."""
        try:
            return await asyncio.wait_for(
                self._category_candidates(
                    title,
                    category_ids=category_ids,
                    exclude_category_ids=exclude_category_ids,
                ),
                timeout=self._TOTAL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("Category identity metadata probes timed out for {!r}", title)
            return []
        except Exception as exc:
            logger.warning("Category identity metadata probes failed for {!r}: {}", title, exc)
            return []

    async def _bounded_category_web_candidates(self, title: str, category_id: str) -> list[dict[str, Any]]:
        """Normalize bounded web evidence from the one selected category."""
        rows = await CategoryIdentityWebProbe.probe(
            title,
            category_id,
            category_registry=self._registry,
            settings=getattr(self._settings_manager, "settings", None),
            database=self._database,
            metadata_clients=self._metadata_clients,
            timeout_seconds=self._CATEGORY_TIMEOUT_SECONDS + 12.0,
        )
        return [
            normalized
            for row in rows
            if (normalized := self._normalize_provider_candidate(category_id, row)) is not None
        ]

    async def _category_candidates(
        self,
        title: str,
        *,
        category_ids: set[str] | None = None,
        exclude_category_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Run each category-owned identity hook concurrently."""
        included = {str(value) for value in (category_ids or set()) if value}
        excluded = {str(value) for value in (exclude_category_ids or set()) if value}
        categories = [
            category for category in self._installed_categories()
            if (not included or str(getattr(category, "category_id", "")) in included)
            and str(getattr(category, "category_id", "")) not in excluded
        ]
        tasks = [self._probe_category(category, title) for category in categories]
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        candidates: list[dict[str, Any]] = []
        for category, result in zip(categories, results):
            if isinstance(result, Exception):
                logger.debug(
                    "Category identity probe failed for {}: {}",
                    getattr(category, "category_id", "unknown"),
                    result,
                )
                continue
            candidates.extend(result)
        return candidates

    async def _probe_category(self, category: Any, title: str) -> list[dict[str, Any]]:
        """Call one category hook and validate its compact evidence envelope."""
        hook = getattr(category, "identify_agent_item", None)
        if not callable(hook):
            return []
        settings = getattr(self._settings_manager, "settings", None)
        rows = await asyncio.wait_for(
            hook(
                title,
                settings=settings,
                db=self._database,
                metadata_clients=self._metadata_clients,
            ),
            timeout=self._CATEGORY_TIMEOUT_SECONDS,
        )
        category_id = str(getattr(category, "category_id", "") or "")
        candidates: list[dict[str, Any]] = []
        for row in rows or []:
            normalized = self._normalize_provider_candidate(category_id, row)
            if normalized is not None:
                candidates.append(normalized)
        return candidates

    def _normalize_provider_candidate(
        self,
        category_id: str,
        row: Any,
    ) -> dict[str, Any] | None:
        """Accept only evidence owned by the category that returned it."""
        if not isinstance(row, dict) or not self._valid_category(category_id):
            return None
        title = str(row.get("title") or "").strip()
        if not title:
            return None
        declared = str(row.get("category_id") or category_id).strip()
        if declared != category_id:
            logger.warning(
                "Category {} returned identity evidence for {}; discarding it",
                category_id,
                declared,
            )
            return None
        return self._candidate(
            category_id,
            title,
            str(row.get("source") or "category_metadata"),
            min(0.35, max(0.0, float(row.get("base_score") or 0.0))),
            external_id=str(row.get("external_id") or ""),
            year=row.get("year"),
            evidence=list(row.get("evidence") or []),
        )

    def _rank_candidates(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        category_hint: str | None,
        router_hints: list[str],
    ) -> list[dict[str, Any]]:
        """Rank one best metadata result per category without letting hints decide."""
        ranked: list[dict[str, Any]] = []
        for row in candidates:
            title_score, evidence = title_query_score(query, str(row.get("title") or ""), [])
            score = float(row.get("base_score") or 0.0) + title_score
            if row.get("category_id") == category_hint:
                score += 0.025
                evidence.append("matches the model category hint (weak evidence)")
            if row.get("category_id") in router_hints:
                score += 0.02
                evidence.append("matches deterministic vocabulary (weak evidence)")
            enriched = dict(row)
            enriched["score"] = round(min(score, 1.0), 4)
            enriched["evidence"] = sorted(set(list(row.get("evidence") or []) + evidence))
            ranked.append(enriched)
        best_by_category: dict[str, dict[str, Any]] = {}
        for row in ranked:
            category_id = str(row.get("category_id") or "")
            current = best_by_category.get(category_id)
            if current is None or float(row["score"]) > float(current["score"]):
                best_by_category[category_id] = row
        return sorted(best_by_category.values(), key=lambda row: float(row["score"]), reverse=True)

    def _router_hint_ids(self, text: str) -> list[str]:
        """Return weak deterministic category hints for diagnostics only."""
        if not self._registry or not hasattr(self._registry, "routing_evidence"):
            return []
        try:
            evidence = self._registry.routing_evidence(text)
        except Exception:
            return []
        return [
            str(row.get("category_id"))
            for row in evidence
            if row.get("score") and not row.get("authoritative")
        ]

    def _installed_categories(self) -> list[Any]:
        """Return concrete installed categories without interpreting their domains."""
        if not self._registry or not hasattr(self._registry, "list_all"):
            return []
        return [
            category
            for category in self._registry.list_all()
            if self._valid_category(str(getattr(category, "category_id", "") or ""))
        ]

    def _valid_hint(self, category_hint: str | None) -> str | None:
        """Return an installed concrete hint, never an abstract category."""
        value = str(category_hint or "").strip()
        return value if self._valid_category(value) else None

    def _valid_category(self, category_id: str) -> bool:
        if not category_id or category_id == "media" or self._registry is None:
            return False
        try:
            return self._registry.get(category_id) is not None
        except Exception:
            return False

    @staticmethod
    def _candidate(
        category_id: str,
        title: str,
        source: str,
        base_score: float,
        *,
        external_id: str = "",
        year: Any = None,
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "category_id": category_id,
            "title": title,
            "source": source,
            "base_score": float(base_score),
            "external_id": external_id,
            "year": str(year or "")[:4] or None,
            "evidence": list(evidence or []),
        }

    @staticmethod
    def _dedupe_by_category(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            result.setdefault(str(candidate.get("category_id") or ""), candidate)
        return [value for key, value in result.items() if key]
