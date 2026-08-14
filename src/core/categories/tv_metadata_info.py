"""TV metadata enrichment and enquiry workflows."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from loguru import logger

if TYPE_CHECKING:
    from src.core.database import Database
    from src.core.models import Settings


class TvMetadataInfoMixin:
    """Provide TV metadata enrichment and local-library enquiry helpers.

    These methods are intentionally isolated from filename parsing and workflow
    dispatch so alternative metadata providers can be introduced here without
    destabilizing search or organization behavior.
    """

    async def identify_agent_item(
        self,
        name: str,
        *,
        settings: "Settings",
        db: "Database" | None = None,
        metadata_clients: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        """Return provider evidence that ``name`` identifies a TV series.

        TV owns both TMDB-TV and TVMaze identity semantics.  The generic
        resolver only ranks the compact evidence returned here and therefore
        never maps provider media types to category ids itself.
        """
        evidence: list[dict[str, Any]] = []
        clients = metadata_clients or {}
        tmdb_key = settings.category_service_value(self.category_id, "tmdb", "api_key")
        if tmdb_key and self.metadata_provider_enabled(settings, "tmdb", True):
            from src.integrations.tmdb import TMDBClient

            tmdb = clients.get("tmdb") or TMDBClient(tmdb_key)
            owns_tmdb = "tmdb" not in clients
            try:
                for row in await tmdb.search(name, media_type="tv"):
                    title = str(row.get("title") or "").strip()
                    if title:
                        evidence.append(self._identity_evidence(
                            title, "tmdb_tv", 0.24, row.get("id"), row.get("year")
                        ))
            except Exception as exc:
                logger.debug("TV identity TMDB probe failed for {!r}: {}", name, exc)
            finally:
                if owns_tmdb:
                    await tmdb.close()

        from src.integrations.tvmaze import TVMazeClient

        tvmaze = clients.get("tvmaze") or TVMazeClient()
        try:
            for row in await tvmaze.search(name):
                title = str(row.get("name") or "").strip()
                if title:
                    evidence.append(self._identity_evidence(
                        title, "tvmaze", 0.22, row.get("id"), row.get("year")
                    ))
        except Exception as exc:
            logger.debug("TV identity TVMaze probe failed for {!r}: {}", name, exc)
        return evidence

    async def identify_agent_item_via_web(
        self,
        name: str,
        *,
        settings: "Settings",
        db: "Database" | None = None,
        metadata_clients: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        """Corroborate a TV identity through bounded category-owned web evidence.

        This is a fallback for unavailable/empty TMDB and TVMaze results, not a
        parallel search across every installed category.  Search snippets alone
        are accepted only when a trusted TV/reference host is present or two
        independent sources agree; fetched page text can provide the second
        corroboration signal.
        """
        collector = (metadata_clients or {}).get("web_identity_search")
        if collector is None or not hasattr(collector, "collect"):
            return []
        query = f'"{name}" TV series seasons episodes official'
        try:
            packet = await collector.collect(query, max_results=6, max_pages=2)
        except Exception as exc:
            logger.debug("TV identity web fallback failed for {!r}: {}", name, exc)
            return []
        if not isinstance(packet, dict) or not packet.get("ok"):
            return []

        wanted_tokens = [token for token in re.findall(r"[a-z0-9]+", str(name).casefold()) if token]
        tv_terms = ("tv series", "television series", "episode", "season", "show", "streaming series", "series")
        trusted_hosts = (
            "tvmaze.com", "themoviedb.org", "imdb.com", "tv.apple.com",
            "netflix.com", "hbo.com", "max.com", "primevideo.com",
            "paramountplus.com", "peacocktv.com", "disneyplus.com", "wikipedia.org",
        )
        corroborating_hosts: set[str] = set()
        trusted = False
        evidence: list[str] = []

        def consider(text: str, url: str, *, fetched: bool) -> None:
            nonlocal trusted
            folded = str(text or "").casefold()
            if wanted_tokens and not all(token in folded for token in wanted_tokens):
                return
            if not any(term in folded for term in tv_terms):
                return
            host = urlparse(str(url or "")).netloc.casefold().removeprefix("www.")
            if not host:
                return
            corroborating_hosts.add(host)
            is_trusted = any(host == value or host.endswith(f".{value}") for value in trusted_hosts)
            trusted = trusted or is_trusted
            kind = "fetched page" if fetched else "search result"
            evidence.append(f"{kind} on {host} identifies {name} as episodic television")

        for hit in packet.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            consider(f"{hit.get('title', '')} {hit.get('snippet', '')}", str(hit.get("url") or ""), fetched=False)
        for page in packet.get("pages") or []:
            if not isinstance(page, dict) or page.get("ok") is False:
                continue
            consider(f"{page.get('title', '')} {page.get('content', '')}", str(page.get("url") or ""), fetched=True)

        if not trusted and len(corroborating_hosts) < 2:
            return []
        source = "web_tv_identity_fallback"
        if packet.get("fallback_used"):
            source += "_degraded_provider"
        return [{
            "category_id": self.category_id,
            "title": str(name).strip(),
            "source": source,
            "base_score": 0.20 if trusted else 0.16,
            "external_id": "",
            "year": None,
            "evidence": evidence[:6],
        }]

    def _identity_evidence(
        self,
        title: str,
        source: str,
        base_score: float,
        external_id: Any,
        year: Any,
    ) -> dict[str, Any]:
        """Build one compact TV identity candidate for the generic resolver."""
        return {
            "category_id": self.category_id,
            "title": title,
            "source": source,
            "base_score": base_score,
            "external_id": str(external_id or ""),
            "year": str(year or "")[:4] or None,
            "evidence": [],
        }

    async def enrich_taste_metadata(self, item: Any, context: Any) -> dict[str, Any] | None:
        """Return TV-owned metadata for taste profiling.

        The generic taste profiler does not decide that this category is
        episodic or which external source should be used. TV owns the choice
        to use TMDB series metadata and exposes only the normalized envelope.
        """
        enricher = getattr(context, "metadata_enricher", None)
        if not enricher:
            return None
        record = await enricher.enrich_series(item.key)
        metadata = self.normalize_taste_metadata_payload(item, record, "tmdb_tv")
        if metadata:
            metadata = await self.cache_metadata_artwork(item, metadata, context, provider="tmdb_tv")
        return metadata

    async def enquire(self, name: str, settings: "Settings", db: "Database") -> dict[str, Any]:
        """Enquire about a TV show (local database watch progress, preferred language, downloaded episodes, and TMDB delta)."""
        logger.info(f"[TvShowCategory] Enquiring about TV show '{name}'")
        
        # 1. Local tracking settings
        tracked_item = None
        configured_language = "English"
        enabled = False
        for item in settings.tracked_items:
            if item.key.lower() == name.lower():
                tracked_item = item
                configured_language = getattr(item, "language", "English")
                enabled = item.enabled
                break
                
        # 2. Local DB downloaded episodes
        downloaded = []
        if db and db.media:
            try:
                eps = await db.media.list_category_units(self.category_id, name, status="downloaded")
                if isinstance(eps, list):
                    for ep in eps:
                        downloaded.append({
                            "season": ep.get("season"),
                            "episode": ep.get("episode"),
                            "title": ep.get("title", ""),
                            "quality": ep.get("quality", ""),
                            "language": ep.get("language", ""),
                            "downloaded_at": ep.get("downloaded_at", "")
                        })
            except Exception as e:
                logger.error(f"[TvShowCategory] Failed to get category-unit episodes: {e}")
            if not downloaded and hasattr(db.media, "get_downloaded_episodes"):
                try:
                    legacy_eps = await db.media.get_downloaded_episodes(name)
                    if isinstance(legacy_eps, list):
                        for ep in legacy_eps:
                            downloaded.append({
                                "season": ep.get("season") if isinstance(ep, dict) else getattr(ep, "season", None),
                                "episode": ep.get("episode") if isinstance(ep, dict) else getattr(ep, "episode", None),
                                "title": ep.get("title", "") if isinstance(ep, dict) else getattr(ep, "title", ""),
                                "quality": ep.get("quality", "") if isinstance(ep, dict) else getattr(ep, "quality", ""),
                                "language": ep.get("language", "") if isinstance(ep, dict) else getattr(ep, "language", ""),
                                "downloaded_at": ep.get("downloaded_at", "") if isinstance(ep, dict) else getattr(ep, "downloaded_at", ""),
                            })
                except Exception as e:
                    logger.error(f"[TvShowCategory] Failed to get legacy downloaded episodes: {e}")

        # 3. Retrieve or refresh the persistent TMDB provider snapshot.
        from datetime import datetime, timezone
        from src.core.categories.metadata.enricher import TMDBMetadataEnricher
        
        cached_meta = None
        if db and db.media:
            try:
                from src.core.models import CategoryMediaMetadata
                rows = await db.media.get_category_metadata(self.category_id, name, provider="tmdb_tv")
                if isinstance(rows, list) and rows:
                    cached_meta = CategoryMediaMetadata(**rows[0]["metadata"])
            except Exception as e:
                logger.error(f"[TvShowCategory] Failed to load category TV metadata: {e}")
            if cached_meta is None and hasattr(db.media, "get_show_metadata"):
                try:
                    cached_meta = await db.media.get_show_metadata(name)
                except Exception as e:
                    logger.error(f"[TvShowCategory] Failed to load legacy TV metadata: {e}")
                
        now = datetime.now(timezone.utc)
        should_refresh = True
        
        if cached_meta and cached_meta.enriched_at:
            try:
                enriched_time = datetime.fromisoformat(cached_meta.enriched_at)
                # Refresh by provider snapshot policy when identity is present.
                # Artwork can be refreshed independently; missing poster art should
                # not force network metadata calls during status enquiries.
                has_tmdb = getattr(cached_meta, "tmdb_id", None) is not None
                if (now - enriched_time).total_seconds() < 7 * 86400 and has_tmdb:
                    should_refresh = False
            except Exception:
                pass
                
        if should_refresh:
            logger.info(f"[TvShowCategory] Provider snapshot stale/missing. Querying TMDB for '{name}'...")
            from src.integrations.tmdb import TMDBClient
            api_key = settings.category_service_value(self.category_id, "tmdb", "api_key")
            if api_key and self.metadata_provider_enabled(settings, "tmdb", True):
                try:
                    client = TMDBClient(api_key)
                    enricher = TMDBMetadataEnricher(tmdb_client=client)
                    refreshed_meta = await enricher.enrich_series(name)
                    if refreshed_meta and refreshed_meta.tmdb_id:
                        cached_meta = refreshed_meta
                        if db and db.media:
                            if hasattr(db.media, "upsert_category_metadata"):
                                await db.media.upsert_category_metadata(
                                    self.category_id,
                                    refreshed_meta.display_name or name,
                                    "tmdb_tv",
                                    refreshed_meta.model_dump() if hasattr(refreshed_meta, "model_dump") else dict(refreshed_meta),
                                    str(refreshed_meta.tmdb_id or getattr(refreshed_meta, "tvmaze_id", "") or ""),
                                )
                            if hasattr(db.media, "upsert_show_metadata"):
                                await db.media.upsert_show_metadata(refreshed_meta)
                    await client.close()
                except Exception as e:
                    logger.error(f"[TvShowCategory] Failed to refresh TMDB show metadata: {e}")
                    
        # 4. Fetch season details from TMDB to determine aired episodes and compute delta
        missing_aired = []
        all_aired = []
        tv_details = None
        
        if cached_meta and cached_meta.tmdb_id:
            from src.integrations.tmdb import TMDBClient
            api_key = settings.category_service_value(self.category_id, "tmdb", "api_key")
            if api_key and self.metadata_provider_enabled(settings, "tmdb", True):
                try:
                    client = TMDBClient(api_key)
                    tv_details = await client.get_tv_details(cached_meta.tmdb_id)
                    if tv_details:
                        today = datetime.now(timezone.utc).date()
                        seasons = tv_details.get("seasons", [])
                        
                        # Fetch episodes for each season
                        for s in seasons:
                            s_num = s.get("season_number")
                            if s_num == 0 or s_num is None:  # Skip specials
                                continue
                            
                            season_details = await client.get_tv_season_details(cached_meta.tmdb_id, s_num)
                            if season_details and "episodes" in season_details:
                                for ep in season_details["episodes"]:
                                    air_date_str = ep.get("air_date")
                                    is_aired = False
                                    if air_date_str:
                                        try:
                                            air_date = datetime.strptime(air_date_str, "%Y-%m-%d").date()
                                            if air_date <= today:
                                                is_aired = True
                                        except ValueError:
                                            pass
                                            
                                    ep_num = ep.get("episode_number")
                                    if is_aired and ep_num is not None:
                                        all_aired.append({
                                            "season": s_num,
                                            "episode": ep_num,
                                            "title": ep.get("name"),
                                            "air_date": air_date_str
                                        })
                                        
                                        # Check if already downloaded
                                        already_has = any(
                                            d["season"] == s_num and d["episode"] == ep_num
                                            for d in downloaded
                                        )
                                        if not already_has:
                                            missing_aired.append({
                                                "season": s_num,
                                                "episode": ep_num,
                                                "title": ep.get("name"),
                                                "air_date": air_date_str
                                            })
                    await client.close()
                except Exception as e:
                    logger.error(f"[TvShowCategory] Failed to fetch TMDB season details for delta: {e}")
                    
        # 5. Formulate response
        response = {
            "category_id": self.category_id,
            "item_name": name,
            "tracked": tracked_item is not None,
            "enabled": enabled,
            "configured_language": configured_language,
            "downloaded_episodes_count": len(downloaded),
            "downloaded_episodes": downloaded,
        }
        
        if cached_meta:
            response["overview"] = cached_meta.overview
            response["genres"] = cached_meta.genres
            
        if tv_details:
            response.update({
                "tmdb_status": tv_details.get("status"),
                "total_seasons": tv_details.get("number_of_seasons"),
                "total_episodes": tv_details.get("number_of_episodes"),
                "aired_episodes_count": len(all_aired),
                "missing_aired_episodes_count": len(missing_aired),
                "missing_aired_episodes": missing_aired,
            })
            if missing_aired:
                response["recommended_search_arguments"] = {
                    "name": name,
                    "category_id": self.category_id,
                    "language": configured_language,
                    "unit_scope": "missing_units",
                }
        else:
            response["note"] = "TMDB reality details could not be loaded; displaying local library state only."
            
        return response

