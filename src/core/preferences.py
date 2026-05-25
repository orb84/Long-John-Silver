"""
User preferences module for LJS.

Stores and retrieves user likes, dislikes, and behavioral preferences
in the SQLite database for persistent personalization. Merges explicit
preferences (manually declared) with implicit ones (learned from behavior)
to produce a rich preference summary for the LLM context.
"""

from loguru import logger
from typing import TYPE_CHECKING
from src.core.database import Database
from src.core.models import ScannedLibraryItem
from src.core.smart_quality import SmartQualityInferrer

if TYPE_CHECKING:
    from src.core.behavior_tracker import BehaviorTracker


class PreferenceManager:
    """Manages user preferences stored in the database.

    Combines three sources of preference signal:
    1. Explicit likes/dislikes (manually declared by the user)
    2. Behavioral profile (learned from download/rejection actions)
    3. Smart quality inferences (derived from library file patterns)

    The merged summary is injected into the LLM system prompt so
    the agent can make decisions aligned with the user's tastes.
    """

    def __init__(self, db: Database, behavior_tracker: "BehaviorTracker | None" = None,
                 quality_inferrer: SmartQualityInferrer | None = None) -> None:
        self._db = db
        self._behavior_tracker = behavior_tracker
        self._quality_inferrer = quality_inferrer or SmartQualityInferrer()

    async def add_like(self, value: str, user_id: str | None = None) -> None:
        """Add a liked genre/show to preferences.

        Args:
            value: The preference value (e.g., "Action", "Breaking Bad").
            user_id: Optional user ID for per-user preferences.
                If None, the preference is global.
        """
        if user_id:
            key = f"like:{value}:user:{user_id}"
        else:
            key = f"like:{value}"
        await self._db.system.set_preference(key, "true")

    async def add_dislike(self, value: str, user_id: str | None = None) -> None:
        """Add a disliked genre/show to preferences.

        Args:
            value: The preference value (e.g., "Horror").
            user_id: Optional user ID for per-user preferences.
        """
        if user_id:
            key = f"dislike:{value}:user:{user_id}"
        else:
            key = f"dislike:{value}"
        await self._db.system.set_preference(key, "true")

    async def remove_like(self, value: str, user_id: str | None = None) -> None:
        """Remove a liked genre/show from preferences."""
        if user_id:
            key = f"like:{value}:user:{user_id}"
        else:
            key = f"like:{value}"
        await self._db.system.set_preference(key, "false")

    async def remove_dislike(self, value: str, user_id: str | None = None) -> None:
        """Remove a disliked genre/show from preferences."""
        if user_id:
            key = f"dislike:{value}:user:{user_id}"
        else:
            key = f"dislike:{value}"
        await self._db.system.set_preference(key, "false")

    async def get_likes(self, user_id: str | None = None) -> list[str]:
        """Return all liked genres/shows.

        If user_id is provided, returns user-specific likes plus global likes.
        If None, returns only global likes.
        """
        prefs = await self._db.system.get_all_preferences()
        results = []
        for k, v in prefs.items():
            if not k.startswith("like:") or v != "true":
                continue
            # Extract the value (between "like:" and optional ":user:...")
            stripped = k[len("like:"):]
            if ":user:" in stripped:
                # Per-user preference
                value, uid = stripped.rsplit(":user:", 1)
                if user_id and uid == user_id:
                    results.append(value)
            else:
                # Global preference — always include
                results.append(stripped)
        return results

    async def get_dislikes(self, user_id: str | None = None) -> list[str]:
        """Return all disliked genres/shows.

        If user_id is provided, returns user-specific dislikes plus global ones.
        If None, returns only global dislikes.
        """
        prefs = await self._db.system.get_all_preferences()
        results = []
        for k, v in prefs.items():
            if not k.startswith("dislike:") or v != "true":
                continue
            stripped = k[len("dislike:"):]
            if ":user:" in stripped:
                value, uid = stripped.rsplit(":user:", 1)
                if user_id and uid == user_id:
                    results.append(value)
            else:
                results.append(stripped)
        return results

    async def get_summary(self, user_id: str | None = None,
                          scanned_item: ScannedLibraryItem | None = None) -> str:
        """Return a formatted summary of all preference signals for LLM context.

        Merges explicit likes/dislikes with the behavioral profile
        and smart quality inferences to produce a comprehensive preference
        string that the agent can use to make better decisions.

        Args:
            user_id: Optional user ID for per-user behavioral data.
            scanned_item: Optional scanned item for quality context.

        Returns:
            Multi-line formatted preference string.
        """
        likes = await self.get_likes(user_id)
        dislikes = await self.get_dislikes(user_id)

        lines = ["User Preferences:"]

        # Explicit preferences
        if likes:
            lines.append(f"- Explicit likes: {', '.join(likes)}")
        if dislikes:
            lines.append(f"- Explicit dislikes: {', '.join(dislikes)}")

        # Behavioral profile (from download history)
        if self._behavior_tracker and user_id:
            profile = await self._behavior_tracker.get_behavior_profile(user_id)
            behavior_text = self._behavior_tracker.format_profile_for_prompt(profile)
            if behavior_text:
                lines.append(behavior_text)

        # Smart quality inference (from library scan)
        if scanned_item:
            quality_context = self._quality_inferrer.build_quality_context(
                scanned_item.name, self._quality_inferrer.infer_for_item(scanned_item), scanned_item
            )
            if quality_context:
                lines.append(quality_context)

        if len(lines) <= 1:
            lines.append("- No preferences recorded yet.")

        return "\n".join(lines)
