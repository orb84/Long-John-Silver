"""Support services for the generic media metadata lookup tool.

The public ``metadata_lookup`` tool should remain a thin agent boundary: parse
arguments, choose provider order, and return a normalized result.  Provider
client wiring, persisted library snapshot matching, title-query cleanup, and
result scoring live here so the tool does not become a catch-all patch point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.core.config import SettingsManager
    from src.integrations.tmdb import TMDBClient
    from src.integrations.tvmaze import TVMazeClient


def _resolve_title(arguments: dict[str, Any]) -> str | None:
    """Resolve a media title from common category-neutral argument names."""
    for key in ("title", "query", "name", "item_name", "movie_name", "series_title", "q", "search"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in arguments.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _safe_int(value: Any) -> int | None:
    """Convert common numeric inputs to int, returning ``None`` on failure."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


@dataclass(frozen=True)
class MetadataLookupRequest:
    """Normalized arguments for one metadata lookup call."""

    query: str
    media_type: str = "auto"
    service: str = "auto"
    tmdb_id: int | None = None
    tvmaze_id: int | None = None
    season: int | None = None
    episode: int | None = None
    question: str | None = None
    include_episodes: bool = False

    @classmethod
    def from_arguments(cls, arguments: dict[str, Any]) -> "MetadataLookupRequest | dict[str, Any]":
        """Build a request or return a serializable validation error."""
        query = _resolve_title(arguments)
        if not query:
            return {"ok": False, "error": "Missing required argument: query"}

        media_type = str(arguments.get("media_type") or "auto").lower().strip()
        if media_type == "series":
            media_type = "tv"
        if media_type not in {"auto", "tv", "movie", "person", "multi"}:
            media_type = "auto"

        service = str(arguments.get("service") or "auto").lower().strip()
        if service not in {"auto", "tmdb", "tvmaze", "imdb"}:
            service = "auto"

        question = str(arguments.get("question") or "").strip() or None
        coordinate_text = " ".join(v for v in (query, question or "") if v)
        season = _safe_int(arguments.get("season")) or MetadataLookupRequest.infer_season_number(coordinate_text)
        episode = _safe_int(arguments.get("episode")) or MetadataLookupRequest.infer_episode_number(coordinate_text)
        include_episodes = bool(arguments.get("include_episodes", season is not None or episode is not None))
        return cls(
            query=query,
            media_type=media_type,
            service=service,
            tmdb_id=_safe_int(arguments.get("tmdb_id")),
            tvmaze_id=_safe_int(arguments.get("tvmaze_id")),
            season=season,
            episode=episode,
            question=question,
            include_episodes=include_episodes,
        )


    @staticmethod
    def infer_episode_number(text: str) -> int | None:
        """Infer small episode/unit numbers from common media phrasing.

        This parser is intentionally narrow: it only extracts a number when the
        text already contains an explicit episode designator such as S05E10,
        E10, episode 10, episodio 10, or the common typo apisode 10. It does
        not route intent or choose a title.
        """
        blob = (text or "").lower()
        match = re.search(r"\bs\d{1,2}\s*e0*(\d{1,3})\b", blob)
        if match:
            return int(match.group(1))
        match = re.search(r"\b(?:episode|episodio|ep|apisode)\s*0*(\d{1,3})\b", blob)
        if match:
            return int(match.group(1))
        match = re.search(r"\be0*(\d{1,3})\b", blob)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def infer_season_number(text: str) -> int | None:
        """Infer small season numbers from ordinary English/Italian phrasing."""
        blob = (text or "").lower()
        match = re.search(r"\b(?:season|stagione)\s*0*(\d{1,2})\b", blob)
        if match:
            return int(match.group(1))
        match = re.search(r"\bs0*(\d{1,2})(?!\s*e)\b", blob)
        if match:
            return int(match.group(1))
        word_numbers = {
            "one": 1, "first": 1, "uno": 1, "prima": 1, "primo": 1,
            "two": 2, "second": 2, "due": 2, "seconda": 2, "secondo": 2,
            "three": 3, "third": 3, "tre": 3, "terza": 3, "terzo": 3,
            "four": 4, "fourth": 4, "quattro": 4, "quarta": 4, "quarto": 4,
            "five": 5, "fifth": 5, "cinque": 5, "quinta": 5, "quinto": 5,
            "six": 6, "sixth": 6, "sei": 6, "sesta": 6, "sesto": 6,
            "seven": 7, "seventh": 7, "sette": 7, "settima": 7, "settimo": 7,
            "eight": 8, "eighth": 8, "otto": 8, "ottava": 8, "ottavo": 8,
            "nine": 9, "ninth": 9, "nove": 9, "nona": 9, "nono": 9,
            "ten": 10, "tenth": 10, "dieci": 10, "decima": 10, "decimo": 10,
        }
        for word, number in word_numbers.items():
            if re.search(rf"\bseason\s+{word}\b", blob) or re.search(rf"\b{word}\s+season\b", blob):
                return number
            if re.search(rf"\bstagione\s+{word}\b", blob) or re.search(rf"\b{word}\s+stagione\b", blob):
                return number
        return None


class MetadataClientResolver:
    """Resolve agent metadata clients from current settings without stale startup state."""

    def __init__(
        self,
        tmdb_client: Optional["TMDBClient"] = None,
        tvmaze_client: Optional["TVMazeClient"] = None,
        settings_manager: Optional["SettingsManager"] = None,
    ) -> None:
        """Initialize with optional injected clients and live settings access."""
        self._tmdb_client = tmdb_client
        self._tvmaze_client = tvmaze_client
        self._settings_manager = settings_manager
        self._tmdb_api_key = getattr(tmdb_client, "_api_key", None)
        self._owns_tmdb_client = False
        self._last_tmdb_status: tuple[bool, bool, bool] | None = None

    async def get_tmdb_client(self) -> Optional["TMDBClient"]:
        """Return a TMDB client built from the same live settings used elsewhere."""
        api_key = self._current_tmdb_key()
        settings_ready = self._settings_manager is not None
        self._log_tmdb_status(api_key=api_key, settings_ready=settings_ready)

        if settings_ready and not api_key:
            await self._clear_owned_tmdb_client("current settings have no TMDB key")
            return None
        if self._tmdb_client is not None and (not api_key or api_key == self._tmdb_api_key):
            return self._tmdb_client
        if not api_key:
            return self._tmdb_client

        await self._clear_owned_tmdb_client("TMDB key changed")
        try:
            from src.integrations.tmdb import TMDBClient

            self._tmdb_client = TMDBClient(api_key)
            self._tmdb_api_key = api_key
            self._owns_tmdb_client = True
            logger.info("Agent metadata TMDB client hydrated from current settings.")
        except Exception as exc:  # pragma: no cover - defensive import/construction guard
            logger.warning(f"Could not hydrate agent TMDB metadata client from settings: {exc}")
        return self._tmdb_client

    def get_tvmaze_client(self) -> Optional["TVMazeClient"]:
        """Return a TVMaze client, creating the no-key fallback lazily."""
        if self._tvmaze_client is not None:
            return self._tvmaze_client
        try:
            from src.integrations.tvmaze import TVMazeClient

            self._tvmaze_client = TVMazeClient()
        except Exception as exc:  # pragma: no cover - defensive import/construction guard
            logger.warning(f"Could not hydrate TVMaze metadata client: {exc}")
        return self._tvmaze_client

    def availability(self, *, library_snapshot_found: bool, imdb_client_ready: bool) -> dict[str, bool]:
        """Return sanitized metadata wiring state for diagnostics/logging."""
        return {
            "tmdb_configured": self.tmdb_configured(),
            "tmdb_client_ready": bool(self._tmdb_client),
            "tvmaze_client_ready": bool(self._tvmaze_client),
            "imdb_client_ready": imdb_client_ready,
            "library_snapshot_found": library_snapshot_found,
            "settings_manager_ready": self._settings_manager is not None,
        }

    def tmdb_configured(self) -> bool:
        """Return whether current settings expose a TMDB API key."""
        if self._settings_manager is None:
            return self._tmdb_client is not None
        return bool(self._current_tmdb_key())

    def _current_tmdb_key(self) -> str | None:
        if self._settings_manager is None:
            return None
        return getattr(self._settings_manager.settings, "tmdb_api_key", None)

    def _log_tmdb_status(self, *, api_key: str | None, settings_ready: bool) -> None:
        status = (bool(api_key), bool(self._tmdb_client), settings_ready)
        if status == self._last_tmdb_status:
            return
        logger.info(
            "Agent metadata TMDB wiring: configured={} client_ready={} settings_manager_ready={}",
            status[0],
            status[1],
            status[2],
        )
        self._last_tmdb_status = status

    async def _clear_owned_tmdb_client(self, reason: str) -> None:
        if self._tmdb_client is not None and self._owns_tmdb_client:
            close = getattr(self._tmdb_client, "close", None)
            if callable(close):
                try:
                    await close()
                except Exception as exc:  # pragma: no cover - close is best-effort cleanup
                    logger.warning(f"Could not close stale agent TMDB client: {exc}")
        if self._tmdb_client is not None:
            logger.info(f"Agent metadata TMDB client cleared because {reason}.")
        self._tmdb_client = None
        self._tmdb_api_key = None
        self._owns_tmdb_client = False


class LibraryMetadataSnapshotLookup:
    """Read already-enriched provider metadata from the category media repository."""

    def __init__(self, database: Any | None = None) -> None:
        """Initialize the lookup with an optional database facade."""
        self._database = database

    async def lookup(self, query: str, media_type: str, season: int | None) -> dict[str, Any] | None:
        """Return persisted provider metadata already attached to library items."""
        db = self._database
        media_repo = getattr(db, "media", None) if db is not None else None
        if media_repo is None:
            return None

        category_ids = ["tv", "movie"]
        if media_type == "tv":
            category_ids = ["tv"]
        elif media_type == "movie":
            category_ids = ["movie"]

        try:
            return await self._lookup_from_repo(media_repo, query, category_ids, season)
        except Exception as exc:  # pragma: no cover - defensive DB fallback
            logger.warning(f"Library metadata snapshot lookup failed for {query!r}: {exc}")
            return None

    @staticmethod
    def can_answer(result: dict[str, Any], question: str | None) -> bool:
        """Return whether the stored snapshot already covers common media facts.

        Episode-specific questions need episode-level evidence. A season count or
        season header is not enough to answer an air-date/title question for a
        particular episode, so those requests must continue to provider season
        details or web fallback instead of letting the model fill gaps.
        """
        q = (question or "").casefold()
        cast_terms = ("actor", "actress", "cast", "lead", "star", "starring")
        episode_terms = ("episode", "episod", "episodio", "apisode", "s05e", "air date", "airdate", "aired")
        season_terms = ("season", "stagione")
        if any(term in q for term in cast_terms):
            return bool(result.get("cast") or result.get("lead_cast") or result.get("cast_names"))
        if any(term in q for term in episode_terms):
            season_details = result.get("season_details") if isinstance(result.get("season_details"), dict) else {}
            return bool(result.get("episodes") or season_details.get("episodes"))
        if any(term in q for term in season_terms):
            return bool(
                result.get("number_of_seasons")
                or result.get("seasons")
                or result.get("number_of_episodes")
                or result.get("season_details")
            )
        return bool(result.get("overview") or result.get("cast") or result.get("lead_cast"))

    async def _lookup_from_repo(
        self,
        media_repo: Any,
        query: str,
        category_ids: list[str],
        season: int | None,
    ) -> dict[str, Any] | None:
        from src.utils.item_matcher import ItemMatcher

        best_item: dict[str, Any] | None = None
        best_category: str | None = None
        best_score = -1
        for category_id in category_ids:
            for item in await media_repo.list_category_items(category_id):
                candidates = self._item_candidate_names(item)
                for candidate in candidates:
                    score = self._candidate_match_score(str(candidate), query, ItemMatcher)
                    if score > best_score:
                        best_score = score
                        best_item = item
                        best_category = category_id
        if not best_item or best_score < 60 or not best_category:
            return None
        return await self._snapshot_result(media_repo, best_category, best_item, season)

    @staticmethod
    def _item_candidate_names(item: dict[str, Any]) -> list[Any]:
        candidates = [
            item.get("item_id"),
            item.get("key"),
            item.get("display_name"),
            item.get("title"),
            item.get("name"),
        ]
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        candidates.extend([
            metadata.get("display_name"),
            metadata.get("title"),
            metadata.get("name"),
        ])
        return [candidate for candidate in candidates if candidate]

    @staticmethod
    def _candidate_match_score(candidate_text: str, query: str, matcher: Any) -> int:
        if candidate_text.casefold() == query.casefold():
            return 100
        if candidate_text.casefold() in query.casefold() or query.casefold() in candidate_text.casefold():
            return 80
        if matcher.fuzzy_match_names(candidate_text, query):
            return 60
        return 0

    async def _snapshot_result(
        self,
        media_repo: Any,
        category_id: str,
        item: dict[str, Any],
        season: int | None,
    ) -> dict[str, Any] | None:
        item_id = str(item.get("item_id") or item.get("key") or "")
        snapshots = await media_repo.get_category_metadata(category_id, item_id)
        merged: dict[str, Any] = {}
        provider = "library_snapshot"
        external_id = ""
        for row in snapshots:
            payload = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if payload:
                merged.update(payload)
                provider = str(row.get("provider") or provider)
                external_id = str(row.get("external_id") or external_id or "")
                break
        item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        merged.update({key: value for key, value in item_metadata.items() if value not in (None, "", [], {})})
        if not merged:
            return None

        cast = MetadataResultNormalizer.normalize_cast(merged.get("cast") or merged.get("lead_cast") or merged.get("cast_names"))
        result = dict(merged)
        result.update({
            "provider": provider or "library_snapshot",
            "type": category_id,
            "id": external_id or merged.get("tmdb_id") or merged.get("id") or item_id,
            "title": merged.get("display_name") or merged.get("title") or item.get("display_name") or item_id,
            "cast": cast,
            "lead_cast": cast[:5],
            "source": "persisted_library_metadata",
        })
        if season is not None:
            result.setdefault("season_details", {"season_number": season})
        return result


class MetadataResultNormalizer:
    """Normalize provider payloads and choose the best metadata result."""

    @staticmethod
    def normalize_cast(value: Any) -> list[dict[str, str]]:
        """Normalize cast/cast_names fields from provider snapshots."""
        if not value:
            return []
        people = value if isinstance(value, list) else [value]
        out: list[dict[str, str]] = []
        for person in people:
            if isinstance(person, dict):
                name = person.get("name") or person.get("person") or person.get("actor")
                character = person.get("character") or person.get("role") or ""
            else:
                name = str(person)
                character = ""
            if name:
                out.append({"name": str(name), "character": str(character or "")})
        return out

    @staticmethod
    def candidate_queries(query: str) -> list[str]:
        """Build conservative metadata-search query variants."""
        raw = (query or "").strip()
        if not raw:
            return []
        variants = [raw]
        lower = raw.lower()
        for sep in (" in ", " of ", " della ", " del ", " de "):
            if sep in lower:
                tail = raw[lower.rfind(sep) + len(sep):].strip(" ?!.,:;\"'")
                if tail:
                    variants.append(tail)
        cleaned = re.sub(
            r"(?i)\b(who|what|when|where|which|is|are|was|were|the|a|an|lead|main|actor|actress|cast|star|stars|starring|tv|series|show|movie|film|original|name|called|in|of|for)\b",
            " ",
            raw,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?!.,:;\"'")
        if cleaned:
            variants.append(cleaned)
        tail_stripped = re.sub(r"(?i)\b(lead actor|main actor|cast|tv series|series|show|movie|film)\b", " ", raw)
        tail_stripped = re.sub(r"\s+", " ", tail_stripped).strip(" ?!.,:;\"'")
        if tail_stripped:
            variants.append(tail_stripped)
        deduped: list[str] = []
        seen: set[str] = set()
        for value in variants:
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                deduped.append(value)
        return deduped[:4]

    @staticmethod
    def select_tmdb_match(matches: list[dict[str, Any]], media_type: str) -> dict[str, Any] | None:
        """Select a TMDB search match respecting requested media type."""
        if not matches:
            return None
        if media_type in {"tv", "movie"}:
            for match in matches:
                if match.get("type") == media_type:
                    return match
        return matches[0]

    @staticmethod
    def normalize_tmdb_details(details: dict[str, Any], result_type: str) -> dict[str, Any]:
        """Normalize TMDB detail payloads while preserving useful raw fields."""
        cast = details.get("cast") or []
        normalized = dict(details)
        normalized.update({
            "provider": "tmdb",
            "type": result_type,
            "title": details.get("title") or details.get("name") or details.get("original_title"),
            "cast": cast,
            "lead_cast": cast[:3],
        })
        if cast:
            normalized["top_billed_actor"] = cast[0].get("name")
        return normalized

    @staticmethod
    def choose_best_result(results: list[dict[str, Any]], media_type: str) -> dict[str, Any]:
        """Choose the best service result; prefer TMDB when cast/artwork exists."""
        def score(result: dict[str, Any]) -> tuple[int, int, int]:
            """Rank normalized metadata candidates by provider, type match, and cast richness."""
            provider_score = 2 if result.get("provider") == "tmdb" else 1
            type_score = 1 if media_type in {"auto", "multi"} or result.get("type") == media_type else 0
            cast_score = 1 if result.get("cast") or result.get("lead_cast") else 0
            return provider_score, type_score, cast_score

        return sorted(results, key=score, reverse=True)[0]

    @staticmethod
    def answer_hints(best: dict[str, Any]) -> dict[str, Any]:
        """Expose compact fields commonly needed to answer media facts."""
        season_details = best.get("season_details") if isinstance(best.get("season_details"), dict) else {}
        season_cast = season_details.get("cast") or season_details.get("lead_cast") or []
        cast = season_cast or best.get("cast") or best.get("lead_cast") or []
        episode_list = best.get("episodes") or season_details.get("episodes") or []
        return {
            "title": best.get("title"),
            "type": best.get("type"),
            "provider": best.get("provider"),
            "season": season_details.get("season_number"),
            "top_billed_actor": best.get("top_billed_actor") or (cast[0].get("name") if cast and isinstance(cast[0], dict) else None),
            "lead_cast": cast[:5] if isinstance(cast, list) else [],
            "season_lead_cast": season_cast[:5] if isinstance(season_cast, list) else [],
            "creators_or_writers": best.get("creators") or best.get("writers") or [],
            "directors": best.get("directors") or [],
            "first_air_date": best.get("first_air_date"),
            "release_date": best.get("release_date"),
            "seasons": best.get("number_of_seasons"),
            "episodes": best.get("number_of_episodes"),
            "status": best.get("status"),
            "network": best.get("network") or best.get("networks"),
            "episode_count_in_result": len(episode_list) if isinstance(episode_list, list) else None,
            "episodes": [
                {
                    "season": ep.get("season") or season_details.get("season_number"),
                    "episode_number": ep.get("episode_number") or ep.get("number"),
                    "title": ep.get("name"),
                    "air_date": ep.get("air_date") or ep.get("airdate"),
                }
                for ep in episode_list[:30]
                if isinstance(ep, dict)
            ] if isinstance(episode_list, list) else [],
        }
