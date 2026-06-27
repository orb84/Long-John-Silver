"""
TMDB (The Movie Database) integration for LJS.

Provides real show/movie metadata: ratings, genres, air dates,
episode info, and recommendations. Replaces the fake RT tool
for research tasks with structured, reliable data.
"""

import httpx
from loguru import logger
from typing import Optional


class TMDBClient:
    """Client for the TMDB API v3.

    Provides structured metadata for movies and TV shows:
    ratings, genres, episode air dates, season info, and
    trending/recommended content.

    Uses a persistent httpx.AsyncClient for connection pooling
    instead of creating a new client per request.
    """

    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._headers = {
            "accept": "application/json",
        }
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the persistent HTTP client (lazy initialization)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self) -> None:
        """Close the persistent HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _get(self, url: str, params: dict | None = None) -> dict | None:
        """Make a GET request using the persistent client.

        Args:
            url: Full URL to request.
            params: Query parameters.

        Returns:
            Parsed JSON dict or None on failure.
        """
        try:
            client = await self._get_client()
            response = await client.get(
                url, params=params or {}, headers=self._headers,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"TMDB API error {e.response.status_code}: {url}")
            return None
        except Exception as e:
            logger.error(f"TMDB request failed: {e}")
            return None

    async def search(self, query: str, media_type: str = "multi", year: int | None = None) -> list[dict]:
        """Search for movies, TV shows, or both.

        Args:
            query: The search string.
            media_type: One of 'multi', 'movie', 'tv'.
            year: Optional release/first-air year used as a provider-side hint.

        Returns:
            List of result dicts with id, title, type, year, overview.
        """
        params = {"query": query, "api_key": self._api_key}
        if year:
            if media_type == "movie":
                params["primary_release_year"] = int(year)
                params["year"] = int(year)
            elif media_type == "tv":
                params["first_air_date_year"] = int(year)
        data = await self._get(
            f"{self.BASE_URL}/search/{media_type}",
            params=params,
        )
        if not data:
            return []

        results = []
        for item in data.get("results", [])[:10]:
            media_kind = item.get("media_type", "movie")
            title = item.get("title") or item.get("name", "Unknown")
            year_str = item.get("release_date") or item.get("first_air_date", "")
            year = year_str[:4] if year_str else None

            results.append({
                "id": item.get("id"),
                "title": title,
                "type": media_kind,
                "year": year,
                "overview": item.get("overview", ""),
                "rating": item.get("vote_average"),
                "vote_count": item.get("vote_count"),
            })

        return results


    async def get_person_details(self, person_id: int) -> Optional[dict]:
        """Get person details and compact movie/TV credits by TMDB person ID.

        The assistant uses this for questions such as a director's latest
        released movie. Returning credits through the normal metadata tool is
        safer than letting the model treat a TMDB person search result as a TV
        or movie ID.
        """
        data = await self._get(
            f"{self.BASE_URL}/person/{person_id}",
            params={"api_key": self._api_key, "append_to_response": "movie_credits,tv_credits"},
        )
        if not data:
            return None

        def _compact_credit(item: dict, *, media_type: str, role_key: str) -> dict:
            date = item.get("release_date") or item.get("first_air_date") or ""
            return {
                "id": item.get("id"),
                "title": item.get("title") or item.get("name"),
                "media_type": media_type,
                "date": date,
                "year": date[:4] if date else None,
                "job": item.get("job") or role_key,
                "character": item.get("character"),
                "rating": item.get("vote_average"),
                "vote_count": item.get("vote_count"),
            }

        movie_credits = data.get("movie_credits") or {}
        tv_credits = data.get("tv_credits") or {}
        directed_movies = [
            _compact_credit(item, media_type="movie", role_key="Director")
            for item in movie_credits.get("crew", [])
            if item.get("job") == "Director"
        ]
        directed_tv = [
            _compact_credit(item, media_type="tv", role_key="Director")
            for item in tv_credits.get("crew", [])
            if item.get("job") == "Director"
        ]
        acted_movies = [
            _compact_credit(item, media_type="movie", role_key="Cast")
            for item in movie_credits.get("cast", [])[:20]
        ]
        acted_tv = [
            _compact_credit(item, media_type="tv", role_key="Cast")
            for item in tv_credits.get("cast", [])[:20]
        ]

        def _sort_key(credit: dict) -> str:
            return str(credit.get("date") or "")

        directed_movies.sort(key=_sort_key, reverse=True)
        directed_tv.sort(key=_sort_key, reverse=True)
        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "known_for_department": data.get("known_for_department"),
            "biography": data.get("biography"),
            "birthday": data.get("birthday"),
            "deathday": data.get("deathday"),
            "imdb_id": data.get("imdb_id"),
            "profile_path": data.get("profile_path"),
            "directed_movies": directed_movies[:40],
            "directed_tv": directed_tv[:30],
            "acted_movies": acted_movies,
            "acted_tv": acted_tv,
        }

    async def get_movie_details(self, movie_id: int) -> Optional[dict]:
        """Get detailed info for a movie by TMDB ID.

        Returns dict with title, overview, rating, genres, runtime,
        release date, and cast.
        """
        data = await self._get(
            f"{self.BASE_URL}/movie/{movie_id}",
            params={"api_key": self._api_key, "append_to_response": "credits,external_ids,alternative_titles,translations"},
        )
        if not data:
            return None

        genres = [g["name"] for g in data.get("genres", [])]
        cast = [
            {"name": c["name"], "character": c["character"]}
            for c in data.get("credits", {}).get("cast", [])[:5]
        ]
        crew = data.get("credits", {}).get("crew", [])
        directors = list({c["name"] for c in crew if c.get("job") == "Director"})
        writers = list({c["name"] for c in crew if c.get("department") == "Writing"})[:5]
        producers = list({c["name"] for c in crew if c.get("job") in ("Producer", "Executive Producer")})[:3]

        return {
            "id": data.get("id"),
            "title": data.get("title"),
            "original_title": data.get("original_title"),
            "overview": data.get("overview"),
            "rating": data.get("vote_average"),
            "vote_count": data.get("vote_count"),
            "genres": genres,
            "runtime_minutes": data.get("runtime"),
            "release_date": data.get("release_date"),
            "poster_path": data.get("poster_path"),
            "cast": cast,
            "directors": directors,
            "writers": writers,
            "producers": producers,
            "imdb_id": data.get("imdb_id"),
            "title_aliases": self._movie_title_aliases(data),
            "localized_titles": self._movie_localized_titles(data),
        }

    @staticmethod
    def _movie_title_aliases(data: dict) -> list[str]:
        """Extract provider-known movie titles from TMDB details."""
        aliases: list[str] = []

        def add(value: object) -> None:
            text = str(value or "").strip()
            if text and text not in aliases:
                aliases.append(text)

        add(data.get("title"))
        add(data.get("original_title"))
        for row in (data.get("alternative_titles") or {}).get("titles") or []:
            if isinstance(row, dict):
                add(row.get("title"))
        for row in (data.get("translations") or {}).get("translations") or []:
            if not isinstance(row, dict):
                continue
            payload = row.get("data") if isinstance(row.get("data"), dict) else {}
            add(payload.get("title"))
        return aliases[:40]

    @staticmethod
    def _movie_localized_titles(data: dict) -> list[dict]:
        """Return compact localized movie title rows from TMDB translations."""
        rows: list[dict] = []
        for row in (data.get("translations") or {}).get("translations") or []:
            if not isinstance(row, dict):
                continue
            payload = row.get("data") if isinstance(row.get("data"), dict) else {}
            title = payload.get("title")
            if not title:
                continue
            rows.append({
                "title": title,
                "language": row.get("english_name") or row.get("name") or row.get("iso_639_1"),
                "iso_639_1": row.get("iso_639_1"),
                "country": row.get("iso_3166_1"),
            })
        return rows[:40]

    async def get_tv_details(self, tv_id: int) -> Optional[dict]:
        """Get detailed info for a TV show by TMDB ID.

        Returns dict with title, overview, rating, genres, seasons,
        next episode, and network.
        """
        data = await self._get(
            f"{self.BASE_URL}/tv/{tv_id}",
            params={"api_key": self._api_key, "append_to_response": "credits,external_ids,alternative_titles,translations"},
        )
        if not data:
            return None

        genres = [g["name"] for g in data.get("genres", [])]
        seasons = [
            {
                "season_number": s["season_number"],
                "episode_count": s["episode_count"],
                "air_date": s.get("air_date"),
                "name": s.get("name"),
            }
            for s in data.get("seasons", [])
        ]
        networks = [n["name"] for n in data.get("networks", [])]
        title_aliases = self._tv_title_aliases(data)
        localized_titles = self._tv_localized_titles(data)
        cast = [
            {"name": c["name"], "character": c["character"]}
            for c in data.get("credits", {}).get("cast", [])[:5]
        ]
        crew = data.get("credits", {}).get("crew", [])
        directors = list({c["name"] for c in crew if c.get("job") == "Director"})
        writers = list({c["name"] for c in crew if c.get("department") == "Writing"})[:5]
        producers = list({c["name"] for c in crew if c.get("job") in ("Producer", "Executive Producer")})[:3]

        return {
            "id": data.get("id"),
            "title": data.get("name"),
            "original_title": data.get("original_name"),
            "title_aliases": title_aliases,
            "localized_titles": localized_titles,
            "overview": data.get("overview"),
            "rating": data.get("vote_average"),
            "vote_count": data.get("vote_count"),
            "genres": genres,
            "seasons": seasons,
            "number_of_seasons": data.get("number_of_seasons"),
            "number_of_episodes": data.get("number_of_episodes"),
            "status": data.get("status"),
            "first_air_date": data.get("first_air_date"),
            "last_air_date": data.get("last_air_date"),
            "next_episode_to_air": data.get("next_episode_to_air"),
            "networks": networks,
            "poster_path": data.get("poster_path"),
            "cast": cast,
            "directors": directors,
            "writers": writers,
            "producers": producers,
            "imdb_id": data.get("external_ids", {}).get("imdb_id"),
        }


    @staticmethod
    def _tv_title_aliases(data: dict) -> list[str]:
        """Extract provider-known TV titles from TMDB details."""
        aliases: list[str] = []

        def add(value: object) -> None:
            text = str(value or "").strip()
            if text and text not in aliases:
                aliases.append(text)

        add(data.get("name"))
        add(data.get("original_name"))
        for row in (data.get("alternative_titles") or {}).get("results") or []:
            if isinstance(row, dict):
                add(row.get("title"))
        for row in (data.get("translations") or {}).get("translations") or []:
            if not isinstance(row, dict):
                continue
            payload = row.get("data") if isinstance(row.get("data"), dict) else {}
            add(payload.get("name"))
            add(payload.get("title"))
        return aliases[:40]

    @staticmethod
    def _tv_localized_titles(data: dict) -> list[dict]:
        """Return compact localized TV title rows from TMDB translations."""
        rows: list[dict] = []
        for row in (data.get("translations") or {}).get("translations") or []:
            if not isinstance(row, dict):
                continue
            payload = row.get("data") if isinstance(row.get("data"), dict) else {}
            title = payload.get("name") or payload.get("title")
            if not title:
                continue
            rows.append({
                "title": title,
                "language": row.get("english_name") or row.get("name") or row.get("iso_639_1"),
                "iso_639_1": row.get("iso_639_1"),
                "country": row.get("iso_3166_1"),
            })
        return rows[:40]

    async def get_tv_season_details(self, tv_id: int,
                                     season_number: int) -> Optional[dict]:
        """Get episode details for a specific TV season.

        Returns dict with season info and list of episodes with
        air dates, names, and overviews.
        """
        data = await self._get(
            f"{self.BASE_URL}/tv/{tv_id}/season/{season_number}",
            params={"api_key": self._api_key, "append_to_response": "credits"},
        )
        if not data:
            return None

        episodes = [
            {
                "episode_number": ep.get("episode_number"),
                "name": ep.get("name"),
                "air_date": ep.get("air_date"),
                "overview": ep.get("overview"),
                "rating": ep.get("vote_average"),
                "runtime_minutes": ep.get("runtime"),
            }
            for ep in data.get("episodes", [])
        ]

        cast = [
            {"name": c.get("name"), "character": c.get("character", "")}
            for c in data.get("credits", {}).get("cast", [])[:8]
            if c.get("name")
        ]

        return {
            "season_number": data.get("season_number"),
            "name": data.get("name"),
            "overview": data.get("overview"),
            "air_date": data.get("air_date"),
            "episodes": episodes,
            "cast": cast,
            "lead_cast": cast[:3],
        }

    async def get_trending(self, media_type: str = "tv",
                           time_window: str = "week") -> list[dict]:
        """Get trending movies or TV shows.

        Args:
            media_type: 'movie' or 'tv'.
            time_window: 'day' or 'week'.

        Returns:
            List of trending items with id, title, rating, and overview.
        """
        data = await self._get(
            f"{self.BASE_URL}/trending/{media_type}/{time_window}",
            params={"api_key": self._api_key},
        )
        if not data:
            return []

        results = []
        for item in data.get("results", [])[:10]:
            title = item.get("title") or item.get("name", "Unknown")
            year_str = item.get("release_date") or item.get("first_air_date", "")
            results.append({
                "id": item.get("id"),
                "title": title,
                "type": media_type,
                "year": year_str[:4] if year_str else None,
                "rating": item.get("vote_average"),
                "overview": item.get("overview", ""),
            })

        return results

    async def get_recommendations(self, media_type: str, item_id: int,
                                  page: int = 1) -> list[dict]:
        """Get recommendations based on a specific movie or TV show.

        Args:
            media_type: 'movie' or 'tv'.
            item_id: The TMDB ID of the source item.
            page: Page number for results.

        Returns:
            List of recommended items.
        """
        data = await self._get(
            f"{self.BASE_URL}/{media_type}/{item_id}/recommendations",
            params={"api_key": self._api_key, "page": page},
        )
        if not data:
            return []

        results = []
        for item in data.get("results", [])[:10]:
            title = item.get("title") or item.get("name", "Unknown")
            year_str = item.get("release_date") or item.get("first_air_date", "")
            results.append({
                "id": item.get("id"),
                "title": title,
                "type": media_type,
                "year": year_str[:4] if year_str else None,
                "rating": item.get("vote_average"),
                "overview": item.get("overview", ""),
            })

        return results