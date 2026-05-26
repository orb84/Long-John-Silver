"""Music metadata provider adapters."""

from __future__ import annotations

from typing import Any

from loguru import logger
import httpx

from src.core.category_object_models import MusicMedium, MusicReleaseModel, MusicTrack
from src.integrations.metadata_providers.base import (
    ProviderAdapterContext,
    ProviderResult,
    as_list,
    compact,
    identifier_map,
    identity,
)


class MusicMetadataProviders:
    """MusicBrainz/Cover Art Archive/Discogs adapters for music categories."""

    def __init__(self, context: ProviderAdapterContext) -> None:
        self.context = context

    async def musicbrainz(self, query: str, limit: int) -> list[ProviderResult]:
        """Search MusicBrainz releases and normalize them to MusicReleaseModel."""
        data = await self.context.json(
            "musicbrainz",
            "https://musicbrainz.org/ws/2/release/",
            params={"query": query, "fmt": "json", "limit": limit},
        )
        items: list[ProviderResult] = []
        for index, release in enumerate(data.get("releases") or []):
            artists = [str(part.get("name") or "") for part in release.get("artist-credit") or [] if isinstance(part, dict)]
            identities = [
                identity("musicbrainz", "musicbrainz_release_id", release.get("id"), "release"),
                identity("musicbrainz", "musicbrainz_release_group_id", (release.get("release-group") or {}).get("id"), "release_group"),
                identity("musicbrainz", "barcode", release.get("barcode"), "release"),
            ]
            identities = [i for i in identities if i]
            media: list[MusicMedium] = []
            for medium in release.get("media") or []:
                if not isinstance(medium, dict):
                    continue
                tracks: list[MusicTrack] = []
                for track in medium.get("tracks") or []:
                    if not isinstance(track, dict):
                        continue
                    rec = track.get("recording") or {}
                    tracks.append(MusicTrack(
                        title=compact(track.get("title")) or compact(rec.get("title")),
                        position=compact(track.get("position")) or compact(track.get("number")),
                        duration_ms=track.get("length") if isinstance(track.get("length"), int) else None,
                        recording_id=compact(rec.get("id")),
                    ))
                media.append(MusicMedium(
                    position=int(medium.get("position") or len(media) + 1),
                    format=compact(medium.get("format")),
                    track_count=medium.get("track-count") if isinstance(medium.get("track-count"), int) else None,
                    tracks=tracks,
                ))
            rg = release.get("release-group") or {}
            model = MusicReleaseModel(
                title=compact(release.get("title")),
                artist_credit=[artist for artist in artists if artist],
                release_group_title=compact(rg.get("title")),
                release_type=compact(rg.get("primary-type")),
                release_status=compact(release.get("status")),
                country=compact(release.get("country")),
                date=compact(release.get("date")),
                year=compact(release.get("date"))[:4],
                barcode=compact(release.get("barcode")),
                disc_count=len(media) or None,
                total_track_count=sum(int(m.track_count or len(m.tracks) or 0) for m in media) or None,
                aliases=[compact(rg.get("title"))] if rg.get("title") and compact(rg.get("title")) != compact(release.get("title")) else [],
                media=media,
                identities=identities,
            )
            cover_url = None
            if self.context.enabled("cover_art_archive", default=True) and release.get("id") and index < min(3, limit):
                cover_url = await self.cover_art_archive(compact(release.get("id")))
            items.append(ProviderResult(
                provider="musicbrainz",
                title=model.title,
                contributors=model.artist_credit,
                year=model.year or None,
                identifiers=identifier_map(model.identities),
                summary="; ".join(str(x) for x in [model.release_type, model.release_status, model.country] if x),
                cover_url=cover_url,
                raw={"score": release.get("score"), "date": release.get("date")},
                object_model=model.as_dict(),
                entity_type="music_release",
                score=(float(release.get("score") or 0) / 100.0) * 0.35,
                evidence=["MusicBrainz release result"],
            ))
        return [item for item in items if item.title]

    async def cover_art_archive(self, mbid: str) -> str | None:
        """Return the first front-cover URL for a MusicBrainz release ID."""
        try:
            data = await self.context.json("cover_art_archive", f"https://coverartarchive.org/release/{mbid}")
            for image in data.get("images") or []:
                if image.get("front"):
                    return compact(image.get("image")) or None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                logger.debug(f"Cover Art Archive lookup failed for {mbid}: {exc}")
        except Exception as exc:
            logger.debug(f"Cover Art Archive lookup failed for {mbid}: {exc}")
        return None

    async def discogs(self, query: str, limit: int) -> list[ProviderResult]:
        """Search Discogs releases and normalize lightweight release evidence."""
        token = self.context.secret("discogs", "token")
        data = await self.context.json(
            "discogs",
            "https://api.discogs.com/database/search",
            params={"q": query, "token": token, "per_page": limit, "type": "release"},
        )
        items: list[ProviderResult] = []
        for row in data.get("results") or []:
            identities = [identity("discogs", "discogs_id", row.get("id"), "release")]
            identities = [i for i in identities if i]
            model = MusicReleaseModel(
                title=compact(row.get("title")),
                artist_credit=[],
                release_type=", ".join(str(x) for x in as_list(row.get("format")) if x),
                country=compact(row.get("country")),
                year=str(row.get("year") or ""),
                identities=identities,
            )
            items.append(ProviderResult(
                provider="discogs",
                title=model.title,
                identifiers=identifier_map(model.identities),
                year=model.year or None,
                cover_url=compact(row.get("cover_image")) or None,
                raw={"format": row.get("format"), "country": row.get("country")},
                object_model=model.as_dict(),
                entity_type="music_release",
                evidence=["Discogs release search result"],
            ))
        return [item for item in items if item.title]
