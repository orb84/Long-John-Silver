"""TV metadata enrichment and enquiry workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

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
                        today = datetime.utcnow().date()
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
        else:
            response["note"] = "TMDB reality details could not be loaded; displaying local library state only."
            
        return response

