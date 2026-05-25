"""
Behavior tracker for LJS implicit preference learning.

Records user actions (downloads, rejections, searches) and aggregates
them into behavioral profiles. These profiles feed into the preference
manager and quality inferrer so the system learns from what the user
actually does, not just what they explicitly declare.
"""

from collections import Counter
from loguru import logger
from typing import Optional
from src.core.database import Database
from src.core.models import BehaviorEvent


class BehaviorTracker:
    """Tracks and aggregates user behavior for implicit preference learning.

    Every download, rejection, and search is recorded. The tracker can
    produce a behavioral profile that summarizes the user's patterns:
    preferred resolutions, codecs, release groups, average file sizes,
    and genre affinities. This profile is merged into the LLM context
    so the agent makes better decisions over time.
    """

    def __init__(self, db: Database):
        self._db = db

    async def get_behavior_profile(self, user_id: str) -> dict:
        """Aggregate behavior events into a preference profile.

        Analyzes the user's download and rejection history to produce
        a summary of their behavioral patterns. Used by the preference
        manager to enrich the LLM context.

        Returns:
            Dict with keys: preferred_resolution, preferred_codecs,
            preferred_release_groups, avg_file_size_mb, genre_affinity,
            total_downloads, total_rejections.
        """
        downloads = await self._db.system.get_behavior_log(user_id, action="download", limit=200)
        rejections = await self._db.system.get_behavior_log(user_id, action="reject", limit=100)

        if not downloads:
            return self._empty_profile()

        # Aggregate resolution preferences
        resolutions = [d["resolution"] for d in downloads if d.get("resolution")]
        resolution_counts = Counter(resolutions)
        preferred_resolution = resolution_counts.most_common(1)[0][0] if resolution_counts else None

        # Aggregate codec preferences
        codecs = [d["codec"] for d in downloads if d.get("codec")]
        codec_counts = Counter(codecs)
        preferred_codecs = [c for c, _ in codec_counts.most_common(3)]

        # Aggregate release group preferences
        groups = [d["release_group"] for d in downloads if d.get("release_group")]
        group_counts = Counter(groups)
        preferred_release_groups = [g for g, _ in group_counts.most_common(5)]

        # Average file size
        sizes = [d["file_size_mb"] for d in downloads if d.get("file_size_mb")]
        avg_file_size = sum(sizes) / len(sizes) if sizes else None

        # Item affinity from category item names
        item_names = [d["item_name"] for d in downloads if d.get("item_name")]
        item_counts = Counter(item_names)
        top_items = [s for s, _ in item_counts.most_common(10)]

        # Rejection patterns: what resolution/codec the user rejected
        rejected_resolutions = [r["resolution"] for r in rejections if r.get("resolution")]
        rejected_codecs = [r["codec"] for r in rejections if r.get("codec")]

        profile = {
            "preferred_resolution": preferred_resolution,
            "preferred_codecs": preferred_codecs,
            "preferred_release_groups": preferred_release_groups,
            "avg_file_size_mb": round(avg_file_size, 1) if avg_file_size else None,
            "top_items": top_items,
            "rejected_resolutions": list(set(rejected_resolutions)),
            "rejected_codecs": list(set(rejected_codecs)),
            "total_downloads": len(downloads),
            "total_rejections": len(rejections),
        }

        logger.info(
            f"Behavior profile for user {user_id}: "
            f"res={preferred_resolution}, codecs={preferred_codecs}, "
            f"avg_size={avg_file_size:.0f}MB" if avg_file_size else f"avg_size=?MB, "
            f"downloads={len(downloads)}"
        )
        return profile

    def format_profile_for_prompt(self, profile: dict) -> str:
        """Format a behavior profile into a human-readable string for LLM context.

        Args:
            profile: A behavior profile dict from get_behavior_profile().

        Returns:
            A formatted string suitable for injecting into a system prompt.
        """
        if not profile or (not profile.get("total_downloads") and not profile.get("total_rejections")):
            return ""

        lines = ["Behavioral profile (learned from your actions):"]

        if profile.get("preferred_resolution"):
            lines.append(f"  Preferred resolution: {profile['preferred_resolution']}")
        if profile.get("preferred_codecs"):
            lines.append(f"  Preferred codecs: {', '.join(profile['preferred_codecs'])}")
        if profile.get("preferred_release_groups"):
            lines.append(
                f"  Trusted release groups: {', '.join(profile['preferred_release_groups'])}"
            )
        if profile.get("avg_file_size_mb"):
            lines.append(f"  Average file size: {profile['avg_file_size_mb']}MB")
        if profile.get("top_items"):
            items = ", ".join(profile["top_items"][:5])
            lines.append(f"  Most downloaded: {items}")
        if profile.get("rejected_resolutions"):
            lines.append(
                f"  Rejected resolutions: {', '.join(profile['rejected_resolutions'])}"
            )
        if profile.get("rejected_codecs"):
            lines.append(
                f"  Rejected codecs: {', '.join(profile['rejected_codecs'])}"
            )

        return "\n".join(lines)

    @staticmethod
    def _empty_profile() -> dict:
        """Return an empty profile with all fields set to defaults."""
        return {
            "preferred_resolution": None,
            "preferred_codecs": [],
            "preferred_release_groups": [],
            "avg_file_size_mb": None,
            "top_items": [],
            "rejected_resolutions": [],
            "rejected_codecs": [],
            "total_downloads": 0,
            "total_rejections": 0,
        }