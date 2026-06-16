"""Category-owned metadata enrichment for built-in media categories.

This module intentionally lives below ``src.core.categories`` because TMDB and
episodic metadata semantics belong to category implementations rather than to
generic repositories, assistant tools, or scheduler code.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from loguru import logger
from typing import TYPE_CHECKING

from src.core.models import CategoryMediaMetadata
from src.core.categories.identity import clean_display_title, clean_release_title, extract_release_year

if TYPE_CHECKING:
    from src.integrations.tmdb import TMDBClient
    from src.core.config import SettingsManager
    from src.core.models import CategoryItem
    from src.core.categories.registry import CategoryRegistry


class MetadataEnricher(ABC):
    """Abstract interface for enriching media items with external metadata.

    Each implementation queries an external source (TMDB, TVMaze, IMDb, etc.)
    and returns structured metadata. New enrichers can be added by subclassing
    without modifying the taste profiler or scheduler.
    """

    @abstractmethod
    async def enrich_series(self, item_name: str) -> CategoryMediaMetadata | None:
        """Fetch metadata for an episodic category item.

        Args:
            item_name: The category item name to look up.

        Returns:
            A ``CategoryMediaMetadata`` record or ``None`` if it could not be found.
        """
        ...

    @abstractmethod
    async def enrich_feature(self, item_name: str) -> CategoryMediaMetadata | None:
        """Fetch metadata for a feature-length category item.

        Args:
            item_name: The category item name to look up.

        Returns:
            A ``CategoryMediaMetadata`` record or ``None`` if it could not be found.
        """
        ...


class TMDBMetadataEnricher(MetadataEnricher):
    """Enriches built-in media category items using the TMDB API.

    Extracts genres, cast, directors, writers, producers, overviews,
    ratings, release dates, runtime/status, and poster paths.
    """

    def __init__(self, tmdb_client: "TMDBClient | None" = None, settings_manager: "SettingsManager | None" = None) -> None:
        """Inject the TMDBClient dependency.
        
        Args:
            tmdb_client: An optional TMDBClient instance.
            settings_manager: An optional SettingsManager instance.
        """
        self._client = tmdb_client
        self._sm = settings_manager

    @property
    def client(self) -> "TMDBClient | None":
        """Get or lazily construct/update the TMDB client."""
        if hasattr(self, "_sm") and self._sm:
            api_key = self._sm.settings.first_category_service_value(["tv", "movie", "media"], "tmdb", "api_key")
            if api_key:
                if not self._client or getattr(self._client, "_api_key", None) != api_key:
                    from src.integrations.tmdb import TMDBClient
                    self._client = TMDBClient(api_key)
        return self._client

    def _clean_item_name(self, item_name: str, media_hint: str | None = None) -> str:
        """Clean a raw torrent or folder name to get a pure media item_name."""
        cleaned = clean_release_title(item_name, fallback=str(item_name or ""), media_hint=media_hint)
        cleaned = self._truncate_by_year(cleaned)
        cleaned = self._truncate_by_quality(cleaned)
        cleaned = self._remove_language_junk(cleaned)
        return clean_display_title(cleaned.strip().rstrip(' (['), fallback=str(item_name or ''))


    def _truncate_by_year(self, item_name: str) -> str:
        """Truncate item_name at year if present."""
        import re
        year_match = re.search(r'\b(19\d{2}|20\d{2})\b', item_name)
        if year_match and year_match.start() > 2:
            return item_name[:year_match.start()]
        return item_name

    def _truncate_by_quality(self, item_name: str) -> str:
        """Truncate item_name at resolution/codec quality markers."""
        import re
        patterns = [
            r'\b(2160p|1080p|720p|480p|4k|bdrip|brrip|dvdrip|webrip|web\s*dl|bluray|hdtv)\b',
            r'\b(x264|x265|h264|h265|hevc|av1|xvid|divx|mpeg)\b',
            r'\b(ac3|dts|dd5\s*1|aac|mp3|truehd|atmos)\b',
        ]
        for pat in patterns:
            m = re.search(pat, item_name, flags=re.IGNORECASE)
            if m and m.start() > 2:
                item_name = item_name[:m.start()]
        return item_name

    def _remove_language_junk(self, item_name: str) -> str:
        """Remove common language and subitem_name terms."""
        import re
        pat = r'\b(italian|ita|english|eng|french|fre|spanish|spa|german|ger|multi|sub|subs|dual|audio)\b'
        cleaned = re.sub(pat, ' ', item_name, flags=re.IGNORECASE)
        return re.sub(r'\s+', ' ', cleaned)

    def _extract_lookup_identity(self, item_name: str, media_hint: str | None = None) -> tuple[str, int | None]:
        """Return a metadata lookup title plus strong disambiguation year.

        Library names are often release/folder names, not canonical titles.
        Keep the year before cleaning quality tags so ambiguous posters such as
        remakes, shorts, and foreign titles can be selected by more than rough
        name similarity.  Examples: ``DrStrangelove_(1964)`` ->
        (``Dr Strangelove``, 1964), ``Movie.Title.2011.1080p`` ->
        (``Movie Title``, 2011).
        """
        import re
        raw = clean_display_title(item_name, fallback=str(item_name or ""))
        year = extract_release_year(raw)
        cleaned = self._clean_item_name(raw, media_hint=media_hint)
        if year:
            cleaned = re.sub(r'\b' + str(year) + r'\b', ' ', cleaned)
        cleaned = re.sub(r'[()\[\]{}]+', ' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return clean_display_title(cleaned or raw, fallback=raw), year


    @staticmethod
    def _score_search_result(result: dict, title: str, year: int | None) -> tuple[int, str]:
        """Score TMDB search hits by title and year before fetching details."""
        import re
        def norm(value: str) -> str:
            """Normalize titles for fuzzy TMDB result comparison."""
            return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()
        desired = norm(title)
        candidate = norm(result.get('title') or result.get('name') or '')
        score = 0
        if desired and candidate == desired:
            score += 80
        elif desired and (desired in candidate or candidate in desired):
            score += 40
        else:
            desired_tokens = set(desired.split())
            cand_tokens = set(candidate.split())
            if desired_tokens and cand_tokens:
                score += int(30 * (len(desired_tokens & cand_tokens) / max(1, len(desired_tokens | cand_tokens))))
        if year:
            result_year = str(result.get('year') or '')[:4]
            if result_year == str(year):
                score += 100
            elif result_year:
                try:
                    delta = abs(int(result_year) - int(year))
                    score -= min(delta * 8, 80)
                except Exception:
                    pass
        score += min(int(result.get('vote_count') or 0) // 100, 25)
        return score, candidate

    def _choose_best_search_result(self, results: list[dict], title: str, year: int | None) -> dict | None:
        """Choose a metadata result using title/year/popularity with a floor.

        Provider search is allowed to improve a category-local item, not invent
        a different identity.  A weak fuzzy hit such as ``The Lego Batman Movie``
        -> ``The Batman`` must be rejected instead of receiving wrong artwork or
        cascading into category confusion.
        """
        if not results:
            return None
        ranked = sorted(results, key=lambda r: self._score_search_result(r, title, year), reverse=True)
        best = ranked[0]
        best_score = self._score_search_result(best, title, year)[0]
        minimum_score = 85 if year else 45
        if best_score < minimum_score:
            logger.info(
                f"Metadata disambiguation rejected weak hit for '{title}': "
                f"'{best.get('title') or best.get('name')}' score={best_score} min={minimum_score}"
            )
            return None
        if year:
            best_year = str(best.get('year') or '')[:4]
            if best_year and best_year != str(year):
                logger.info(
                    f"Metadata disambiguation: best result for '{title}' year {year} is "
                    f"'{best.get('title') or best.get('name')}' ({best_year}); accepted by title score but year differs."
                )
        return best

    async def enrich_series(self, item_name: str) -> CategoryMediaMetadata | None:
        """Fetch episodic-item metadata from TMDB."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            client = self.client
            if not client:
                logger.warning(f"TMDB client not configured, cannot enrich show '{item_name}'")
                return CategoryMediaMetadata(display_name=clean_release_title(item_name, fallback=item_name), enriched_at=now)

            clean_name, year = self._extract_lookup_identity(item_name, media_hint="tv")
            logger.info(f"Querying TMDB for TV show '{item_name}' cleaned as '{clean_name}' year={year}")
            results = await client.search(clean_name, media_type="tv", year=year)
            if not results:
                return CategoryMediaMetadata(display_name=clean_release_title(item_name, fallback=item_name), enriched_at=now)
            best = self._choose_best_search_result(results, clean_name, year)
            if not best:
                return CategoryMediaMetadata(display_name=clean_release_title(item_name, fallback=item_name), enriched_at=now)

            details = await client.get_tv_details(best["id"])
            if not details:
                return CategoryMediaMetadata(display_name=clean_release_title(item_name, fallback=item_name), enriched_at=now)

            return CategoryMediaMetadata(
                display_name=details.get("title") or details.get("name") or clean_name,
                title_aliases=list(details.get("title_aliases") or []),
                localized_titles=list(details.get("localized_titles") or []),
                tmdb_id=details.get("id"),
                genres=details.get("genres", []),
                overview=details.get("overview", ""),
                cast_names=[c["name"] for c in details.get("cast", [])],
                directors=details.get("directors", []),
                writers=details.get("writers", []),
                producers=details.get("producers", []),
                rating=details.get("rating"),
                vote_count=details.get("vote_count", 0),
                lifecycle_status=details.get("status", ""),
                seasons=details.get("seasons", []),
                number_of_seasons=details.get("number_of_seasons"),
                number_of_episodes=details.get("number_of_episodes"),
                first_release_date=details.get("first_air_date", ""),
                last_release_date=details.get("last_air_date", ""),
                network=", ".join(details.get("networks", [])),
                poster_path=details.get("poster_path", ""),
                enriched_at=now,
            )
        except Exception as e:
            logger.warning(f"TMDB enrich failed for show '{item_name}': {e}")
            return CategoryMediaMetadata(display_name=clean_release_title(item_name, fallback=item_name), enriched_at=now)

    async def enrich_feature(self, item_name: str) -> CategoryMediaMetadata | None:
        """Fetch feature-item metadata from TMDB."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            client = self.client
            if not client:
                logger.warning(f"TMDB client not configured, cannot enrich movie '{item_name}'")
                return CategoryMediaMetadata(display_name=clean_release_title(item_name, fallback=item_name), enriched_at=now)

            clean_item_name, year = self._extract_lookup_identity(item_name, media_hint="movie")
            logger.info(f"Querying TMDB for movie '{item_name}' cleaned as '{clean_item_name}' year={year}")
            results = await client.search(clean_item_name, media_type="movie", year=year)
            if not results:
                return CategoryMediaMetadata(display_name=clean_release_title(item_name, fallback=item_name), enriched_at=now)
            best = self._choose_best_search_result(results, clean_item_name, year)
            if not best:
                return CategoryMediaMetadata(display_name=clean_release_title(item_name, fallback=item_name), enriched_at=now)

            details = await client.get_movie_details(best["id"])
            if not details:
                return CategoryMediaMetadata(display_name=clean_release_title(item_name, fallback=item_name), enriched_at=now)

            return CategoryMediaMetadata(
                display_name=details.get("title") or details.get("name") or clean_item_name,
                tmdb_id=details.get("id"),
                genres=details.get("genres", []),
                overview=details.get("overview", ""),
                cast_names=[c["name"] for c in details.get("cast", [])],
                directors=details.get("directors", []),
                writers=details.get("writers", []),
                producers=details.get("producers", []),
                rating=details.get("rating"),
                vote_count=details.get("vote_count", 0),
                first_release_date=details.get("release_date", ""),
                runtime_minutes=details.get("runtime_minutes"),
                poster_path=details.get("poster_path", ""),
                enriched_at=now,
            )
        except Exception as e:
            logger.warning(f"TMDB enrich failed for movie '{item_name}': {e}")
            return CategoryMediaMetadata(display_name=clean_release_title(item_name, fallback=item_name), enriched_at=now)


class MetadataRepairer:
    """Category-bound metadata sanity checker.

    Earlier rounds attempted to "repair" a scanned item's category by asking
    TMDB and swapping TV/movie models when the provider result looked different.
    That violates the category boundary: a file discovered below the Movies root
    belongs to the movie category, and a provider search hit must never move it
    into TV.  This class is retained as a compatibility no-op/logger so older
    call sites do not break, but it no longer mutates item_type.
    """

    def __init__(
        self,
        settings_manager: "SettingsManager",
        enricher: MetadataEnricher,
        category_registry: "CategoryRegistry | None" = None,
    ) -> None:
        self._settings_manager = settings_manager
        self._enricher = enricher
        self._categories = category_registry

    async def repair_item(self, item: "CategoryItem") -> "CategoryItem | None":
        """Return no cross-category repair for a category-scoped item.

        Provider mismatches should be handled by rejecting/low-scoring metadata
        candidates inside the owning category, not by changing the item's
        category.  Returning ``None`` preserves the caller contract: no repair.
        """
        return None

    async def repair_all(self) -> None:
        """Compatibility entry point; no longer rewrites tracked categories."""
        logger.info("MetadataRepairer: category-boundary mode active; no cross-category rewrites performed.")
