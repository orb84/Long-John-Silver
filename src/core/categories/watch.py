"""Category-owned watch policy contracts.

The generic runtime owns scheduling/RSS plumbing. Categories own the meaning of
what is worth watching.  TV can map next_episode_to_air into a release watch;
future categories such as sports can map match start times into replay watches
without adding sports or TV branches to the scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CategoryRssFeedSpec:
    """One category-requested RSS query.

    The RSS service turns ``query`` into a provider URL. The category chooses the
    semantic query and target name; the provider layer owns URL/API-key details.
    """

    query: str
    target_name: str
    reason: str = ""


@dataclass(slots=True)
class CategoryReleaseWatchSpec:
    """One category-requested retry watch for a concrete unit.

    The generic scheduler/repository persists and retries the watch, but the
    category owns the semantics of the fields.  For TV, ``unit_key`` may be an
    ``SxxEyy`` episode and ``expected_air_at`` is derived from TV metadata.  A
    future sports category can use the same contract for a match replay window
    without adding sports branches to the scheduler.
    """

    unit_key: str
    preferred_language: str = ""
    interval_hours: float = 2.0
    expected_air_at: str = ""
    watch_start_at: str = ""
    expires_at: str = ""
    cadence_profile: str = "unknown"
    requirements: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CategoryWatchPlan:
    """Category decision about ongoing monitoring for one item."""

    category_id: str
    item_id: str
    mode: str = "none"
    reason: str = ""
    rss_feeds: list[CategoryRssFeedSpec] = field(default_factory=list)
    release_watches: list[CategoryReleaseWatchSpec] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.rss_feeds or self.release_watches or self.mode not in {"", "none"})
