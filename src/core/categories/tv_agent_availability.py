"""Structured TV release-frontier facts for assistant search responses."""

from __future__ import annotations

from typing import Any, Iterable


class TVAgentAvailabilityFactsBuilder:
    """Separate season catalogue size from the currently released target set."""

    @classmethod
    def build(
        cls,
        *,
        season: int,
        season_total_episode_count: int | None,
        aired_episode_numbers: Iterable[int],
        target_episode_numbers: Iterable[int],
        requested_unit_scope: str | None,
    ) -> dict[str, Any]:
        """Return compact category-owned facts for search/query/presentation layers."""
        aired = cls._positive_sorted(aired_episode_numbers)
        targets = cls._positive_sorted(target_episode_numbers)
        total = cls._positive_int(season_total_episode_count)
        frontier = max(aired) if aired else None
        state = cls._release_state(total=total, aired_count=len(aired))
        scope = str(requested_unit_scope or "").strip().lower() or "released_missing_units_default"
        return {
            "season_number": int(season),
            "season_total_episode_count": total,
            "aired_episode_count": len(aired),
            "aired_unit_labels": cls._labels(season, aired),
            "release_frontier_episode": frontier,
            "target_unit_count": len(targets),
            "target_unit_labels": cls._labels(season, targets),
            "requested_unit_scope": scope,
            "season_release_state": state,
            # Compatibility field used by TV bundle coverage. It means the
            # released/searchable frontier, never the provider's future order.
            # When release dates are unavailable, keep this unknown rather
            # than silently reinterpreting catalogue capacity as availability.
            "expected_episode_count": frontier,
        }

    @staticmethod
    def _positive_sorted(values: Iterable[int]) -> list[int]:
        result: set[int] = set()
        for value in values or []:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                result.add(number)
        return sorted(result)

    @staticmethod
    def _positive_int(value: object) -> int | None:
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _labels(season: int, episodes: Iterable[int]) -> list[str]:
        return [f"S{int(season):02d}E{int(episode):02d}" for episode in episodes]

    @staticmethod
    def _release_state(*, total: int | None, aired_count: int) -> str:
        if aired_count <= 0:
            return "released_count_unknown"
        if total and aired_count < total:
            return "currently_airing"
        return "released_or_complete"
