"""
TVMaze integration for LJS.

Provides TV show metadata, episode scheduling, and next-episode
lookup. TVMaze is free (no API key required) and excels at episode
air date information — the key data for schedule-aware downloading.
"""

import httpx
from loguru import logger
from typing import Optional


class TVMazeClient:
    """Client for the TVMaze API.

    Free, no API key needed. Best used for:
    - Episode air dates (more reliable than TMDB for this)
    - Next episode lookup
    - Show schedule by country/date
    """

    BASE_URL = "https://api.tvmaze.com"

    def __init__(self) -> None:
        self.last_error: str = ""

    def _clear_error(self) -> None:
        self.last_error = ""

    def _record_error(self, exc: Exception) -> None:
        self.last_error = str(exc)

    async def search(self, query: str) -> list[dict]:
        """Search for TV shows on TVMaze.

        Returns:
            List of show dicts with id, name, year, rating, genres, and status.
        """
        self._clear_error()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/search/shows",
                    params={"q": query},
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            self._record_error(e)
            logger.error(f"TVMaze search failed: {e}")
            return []

        results = []
        for item in data[:10]:
            show = item.get("show", {})
            rating = show.get("rating", {}).get("average")
            results.append({
                "id": show.get("id"),
                "name": show.get("name"),
                "year": show.get("premiered", "")[:4] if show.get("premiered") else None,
                "rating": rating,
                "genres": show.get("genres", []),
                "status": show.get("status"),
                "summary": (show.get("summary") or "").replace("<p>", "").replace("</p>", "").replace("<b>", "").replace("</b>", "").strip()[:300],
                "imdb_id": show.get("externals", {}).get("imdb"),
                "tvrage_id": show.get("externals", {}).get("tvrage"),
            })

        return results

    # Alias for compatibility with some callers
    async def search_show(self, query: str) -> list[dict]:
        """Search using the TVMazeClient provider contract.

        Normalize inputs before calling external providers and return stable
        model objects.  Add new provider-specific behavior behind adapters,
        not in callers.
        """
        return await self.search(query)

    async def get_show_details(self, show_id: int) -> Optional[dict]:
        """Get detailed info for a TV show by TVMaze ID.

        Returns dict with name, genres, rating, schedule, next episode,
        episode count, and network.
        """
        self._clear_error()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/shows/{show_id}",
                    params={"embed": "nextepisode"},
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            self._record_error(e)
            logger.error(f"TVMaze show details failed: {e}")
            return None

        next_ep = data.get("_embedded", {}).get("nextepisode") if data.get("_embedded") else None

        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "genres": data.get("genres", []),
            "rating": data.get("rating", {}).get("average"),
            "status": data.get("status"),
            "premiered": data.get("premiered"),
            "official_site": data.get("officialSite"),
            "schedule": data.get("schedule"),
            "network": data.get("network", {}).get("name") if data.get("network") else None,
            "web_channel": data.get("webChannel", {}).get("name") if data.get("webChannel") else None,
            "total_episodes": None,  # TVMaze doesn't provide a total episode count directly
            "next_episode": {
                "name": next_ep.get("name"),
                "season": next_ep.get("season"),
                "number": next_ep.get("number"),
                "airdate": next_ep.get("airdate"),
                "summary": (next_ep.get("summary") or "").replace("<p>", "").replace("</p>", "").strip()[:200],
            } if next_ep else None,
            "imdb_id": data.get("externals", {}).get("imdb"),
        }

    async def get_episode_list(self, show_id: int,
                               season: int | None = None) -> list[dict]:
        """Get episodes for a TV show, optionally filtered by season.

        Args:
            show_id: TVMaze show ID.
            season: Optional season number to filter.

        Returns:
            List of episode dicts with season, number, name, airdate.
        """
        self._clear_error()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                if season is not None:
                    # TVMaze requires a different endpoint for season-filtered queries
                    response = await client.get(
                        f"{self.BASE_URL}/shows/{show_id}/seasons/{season}/episodes",
                    )
                else:
                    response = await client.get(
                        f"{self.BASE_URL}/shows/{show_id}/episodes",
                    )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            self._record_error(e)
            logger.error(f"TVMaze episode list failed: {e}")
            return []

        episodes = []
        for ep in data:
            episodes.append({
                "season": ep.get("season"),
                "number": ep.get("number"),
                "name": ep.get("name"),
                "airdate": ep.get("airdate"),
                "airtime": ep.get("airtime"),
                "runtime_minutes": ep.get("runtime"),
                "summary": (ep.get("summary") or "").replace("<p>", "").replace("</p>", "").strip()[:200],
            })

        return episodes

    # Alias for compatibility with some callers
    async def get_show_schedule(self, show_id: int) -> list[dict]:
        """Return the requested get show schedule value.

        This public accessor should normalize missing or optional data at the
        boundary and avoid leaking storage/provider internals to callers.
        """
        return await self.get_episode_list(show_id)

    async def get_next_episode(self, show_id: int) -> Optional[dict]:
        """Get the next unaired episode for a show.

        Returns:
            Dict with name, season, number, airdate, or None if no
            next episode is scheduled (show ended or on hiatus).
        """
        self._clear_error()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/shows/{show_id}",
                    params={"embed": "nextepisode"},
                )
                response.raise_for_status()
                data = response.json()

            next_ep = data.get("_embedded", {}).get("nextepisode")
            if not next_ep:
                return None

            return {
                "name": next_ep.get("name"),
                "season": next_ep.get("season"),
                "number": next_ep.get("number"),
                "airdate": next_ep.get("airdate"),
                "airtime": next_ep.get("airtime"),
                "runtime_minutes": next_ep.get("runtime"),
                "summary": (next_ep.get("summary") or "").replace("<p>", "").replace("</p>", "").strip()[:200],
            }
        except Exception as e:
            self._record_error(e)
            logger.error(f"TVMaze next episode failed: {e}")
            return None

    async def get_schedule(self, country: str = "US",
                           date: str | None = None) -> list[dict]:
        """Get the TV schedule for a country and optional date.

        Args:
            country: ISO 3166-1 country code (default: US).
            date: ISO date string (YYYY-MM-DD). Defaults to today.

        Returns:
            List of airing episodes with show name and air time.
        """
        self._clear_error()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                params = {"country": country}
                if date:
                    params["date"] = date
                response = await client.get(
                    f"{self.BASE_URL}/schedule",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            self._record_error(e)
            logger.error(f"TVMaze schedule failed: {e}")
            return []

        schedule = []
        for item in data[:20]:
            show = item.get("show", {})
            episode = item.get("episode") or {}
            schedule.append({
                "show_name": show.get("name"),
                "show_id": show.get("id"),
                "season": episode.get("season"),
                "episode_number": episode.get("number"),
                "episode_name": episode.get("name"),
                "airdate": item.get("airdate"),
                "airtime": item.get("airtime"),
                "network": item.get("show", {}).get("network", {}).get("name"),
            })

        return schedule
