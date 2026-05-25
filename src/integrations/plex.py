"""
Plex integration for LJS.

Connects to a Plex Media Server to track watch status so the system
can automatically delete watched content or report what the user has
seen. Uses the Plex HTTP API directly (no external dependency) for
maximum compatibility.
"""

import httpx
from datetime import datetime, timezone
from loguru import logger
from src.core.models import WatchedItem


class PlexClient:
    """Client for communicating with a Plex Media Server.

    Fetches watch status (viewed episodes/movies) so LJS can
    auto-delete watched content when configured. Uses the Plex HTTP
    API with an X-Plex-Token for authentication.
    """

    def __init__(self, url: str, token: str):
        self._url = url.rstrip("/")
        self._token = token
        self._headers = {
            "X-Plex-Token": token,
            "Accept": "application/json",
        }
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the persistent HTTP client (lazy initialization)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def close(self) -> None:
        """Close the persistent HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(self, path: str, params: dict | None = None) -> dict | list | None:
        """Make an authenticated request to the Plex API.

        Args:
            path: API path (e.g., /library/sections).
            params: Optional query parameters.

        Returns:
            Parsed JSON response or None on failure.
        """
        url = f"{self._url}{path}"
        try:
            client = await self._get_client()
            response = await client.get(
                url, params=params or {}, headers=self._headers,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"Plex API error {e.response.status_code}: {path}")
            return None
        except Exception as e:
            logger.warning(f"Plex request failed: {e}")
            return None

    async def health_check(self) -> bool:
        """Verify the Plex server is reachable and the token is valid."""
        result = await self._request("/")
        return result is not None

    async def get_sections(self) -> list[dict]:
        """List all library sections (Movies, TV Shows, etc.)."""
        result = await self._request("/library/sections")
        if not result:
            return []
        # Response structure: {"MediaContainer": {"Directory": [...]}}
        container = result.get("MediaContainer", result)
        directories = container.get("Directory", [])
        return directories

    async def get_watched_items(self, section_type: str = "both",
                                 min_view_count: int = 1) -> list[WatchedItem]:
        """Fetch watched items from the Plex library.

        Args:
            section_type: 'movie', 'show', or 'both'.
            min_view_count: Minimum viewCount to consider an item watched.

        Returns:
            List of WatchedItem objects for watched content.
        """
        sections = await self.get_sections()
        items = []

        for section in sections:
            section_type_plex = section.get("type", "")
            section_key = section.get("key")
            section_title = section.get("title", "Unknown")

            if section_type not in ("both", section_type_plex):
                continue

            if not section_key:
                continue

            # Fetch all items in this section
            result = await self._request(f"/library/sections/{section_key}/all")
            if not result:
                continue

            container = result.get("MediaContainer", result)
            metadata = container.get("Metadata", [])

            if not isinstance(metadata, list):
                metadata = [metadata]

            for item in metadata:
                view_count = item.get("viewCount", 0)
                if not isinstance(view_count, int):
                    view_count = int(view_count) if view_count else 0

                if view_count < min_view_count:
                    continue

                title = item.get("title", "Unknown")
                media_type = section_type_plex

                if media_type == "show":
                    # For shows, we need the episode-level data
                    watched_episodes = await self._get_watched_episodes(
                        item, min_view_count=min_view_count
                    )
                    items.extend(watched_episodes)
                elif media_type == "movie":
                    last_viewed = item.get("lastViewedAt")
                    year = item.get("year")
                    items.append(WatchedItem(
                        title=title,
                        media_type="movie",
                        year=year,
                        watched_at=self._parse_timestamp(last_viewed),
                    ))

        logger.info(f"Found {len(items)} watched items from Plex")
        return items

    async def _get_watched_episodes(self, show_metadata: dict,
                                     min_view_count: int = 1) -> list[WatchedItem]:
        """Get watched episodes for a TV show.

        Args:
            show_metadata: Plex metadata dict for the show.
            min_view_count: Minimum viewCount to consider an episode watched.

        Returns:
            List of WatchedItem objects for watched episodes.
        """
        rating_key = show_metadata.get("ratingKey")
        if not rating_key:
            return []

        show_title = show_metadata.get("title", "Unknown")
        result = await self._request(f"/library/metadata/{rating_key}/allLeaves")
        if not result:
            return []

        container = result.get("MediaContainer", result)
        episodes = container.get("Metadata", [])

        if not isinstance(episodes, list):
            episodes = [episodes]

        watched = []
        for ep in episodes:
            view_count = ep.get("viewCount", 0)
            if not isinstance(view_count, int):
                view_count = int(view_count) if view_count else 0

            if view_count < min_view_count:
                continue

            season = ep.get("parentIndex")
            episode_num = ep.get("index")
            last_viewed = ep.get("lastViewedAt")

            # Season 0 (specials) is valid — use explicit None check, not truthiness
            watched.append(WatchedItem(
                title=show_title,
                media_type="episode",
                season=int(season) if season is not None else None,
                episode=int(episode_num) if episode_num is not None else None,
                watched_at=self._parse_timestamp(last_viewed),
            ))

        return watched

    async def get_item_progress(self, item_name: str) -> dict | None:
        """Get the user's watch progress for a specific show.

        Args:
            item_name: The show name to search for.

        Returns:
            Dict with 'watched_episodes' and 'total_episodes', or None.
        """
        # Search for the show
        result = await self._request("/hubs/search", params={"query": item_name})
        if not result:
            return None

        container = result.get("MediaContainer", result)
        hubs = container.get("Hub", [])

        for hub in hubs:
            metadata = hub.get("Metadata", [])
            if not isinstance(metadata, list):
                metadata = [metadata]
            for item in metadata:
                if item.get("type") == "show" and item_name.lower() in item.get("title", "").lower():
                    rating_key = item.get("ratingKey")
                    if rating_key:
                        total = int(item.get("leafCount", 0))
                        viewed = int(item.get("viewedLeafCount", 0))
                        return {
                            "item_name": item.get("title"),
                            "watched_episodes": viewed,
                            "total_episodes": total,
                            "progress_pct": round(viewed / total * 100, 1) if total else 0,
                        }

        return None

    @staticmethod
    def _parse_timestamp(ts) -> datetime | None:
        """Parse a Plex timestamp (Unix epoch seconds) to UTC datetime.

        Args:
            ts: Unix timestamp from Plex API, or None.

        Returns:
            Timezone-aware datetime in UTC, or None.
        """
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            return None