"""Book, ebook, comic, and audiobook metadata provider adapters."""

from __future__ import annotations

from typing import Any

from src.core.category_object_models import AudiobookChapterModel, AudiobookEditionModel, BookEditionModel
from src.integrations.metadata_providers.base import (
    ProviderAdapterContext,
    ProviderResult,
    as_list,
    compact,
    identifier_map,
    identity,
    safe_query_fragment,
)


class BookMetadataProviders:
    """Adapters for Open Library, Gutendex, IA, Google Books, Apple, LibriVox, Comic Vine."""

    def __init__(self, context: ProviderAdapterContext) -> None:
        self.context = context

    async def open_library(self, query: str, limit: int) -> list[ProviderResult]:
        """Search Open Library work/edition metadata and normalize book candidates."""
        data = await self.context.json("open_library", "https://openlibrary.org/search.json", params={"q": query, "limit": limit})
        items: list[ProviderResult] = []
        for doc in data.get("docs") or []:
            cover_id = doc.get("cover_i")
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else None
            identities = [
                identity("open_library", "openlibrary_work_key", doc.get("key"), "work"),
                identity("open_library", "openlibrary_edition_key", (as_list(doc.get("edition_key")) or [""])[0], "edition"),
            ]
            identities = [i for i in identities if i]
            isbn = [str(x) for x in as_list(doc.get("isbn")) if str(x).strip()]
            model = BookEditionModel(
                title=compact(doc.get("title")),
                work_title=compact(doc.get("title")),
                authors=[str(a) for a in as_list(doc.get("author_name"))[:8]],
                languages=[str(x) for x in as_list(doc.get("language"))[:8]],
                first_publish_year=str(doc.get("first_publish_year") or ""),
                isbn_10=[x for x in isbn if len(x.replace("-", "")) == 10][:4],
                isbn_13=[x for x in isbn if len(x.replace("-", "")) == 13][:4],
                subjects=[str(x) for x in as_list(doc.get("subject"))[:8]],
                source_level="work_or_edition_search",
                identities=identities,
            )
            items.append(ProviderResult(
                provider="open_library",
                title=model.title,
                contributors=model.authors,
                year=model.first_publish_year or None,
                identifiers=identifier_map(model.identities),
                cover_url=cover_url,
                raw={"language": doc.get("language"), "isbn": isbn[:5]},
                object_model=model.as_dict(),
                entity_type="book_work_or_edition",
                evidence=["Open Library work/edition search result"],
            ))
        return [item for item in items if item.title]

    async def gutendex(self, query: str, limit: int) -> list[ProviderResult]:
        """Search Gutendex/Gutenberg metadata and normalize public-domain ebook candidates."""
        data = await self.context.json("gutendex", "https://gutendex.com/books/", params={"search": query})
        items: list[ProviderResult] = []
        for book in (data.get("results") or [])[:limit]:
            contributors = [compact(author.get("name")) for author in book.get("authors") or [] if isinstance(author, dict)]
            formats = book.get("formats") if isinstance(book.get("formats"), dict) else {}
            identities = [identity("gutendex", "gutenberg_id", book.get("id"), "edition")]
            identities = [i for i in identities if i]
            model = BookEditionModel(
                title=compact(book.get("title")),
                authors=[c for c in contributors if c],
                languages=[str(x) for x in as_list(book.get("languages"))],
                subjects=[str(x) for x in as_list(book.get("subjects"))[:8]],
                source_level="edition",
                formats=sorted(str(k) for k in formats.keys())[:12],
                identities=identities,
            )
            items.append(ProviderResult(
                provider="gutendex",
                title=model.title,
                contributors=model.authors,
                identifiers=identifier_map(model.identities),
                cover_url=compact(formats.get("image/jpeg")) or None,
                raw={"languages": book.get("languages"), "subjects": book.get("subjects")},
                object_model=model.as_dict(),
                entity_type="book_edition",
                evidence=["Gutendex/Gutenberg public-domain metadata"],
            ))
        return [item for item in items if item.title]

    async def internet_archive(self, query: str, limit: int, *, mediatype: str) -> list[ProviderResult]:
        """Search Internet Archive by media type and normalize catalog candidates."""
        fragment = safe_query_fragment(query)
        if not fragment:
            return []
        ia_query = f'title:("{fragment}") AND mediatype:({mediatype})'
        data = await self.context.json(
            "internet_archive",
            "https://archive.org/advancedsearch.php",
            params={"q": ia_query, "fl[]": ["identifier", "title", "creator", "date", "downloads"], "rows": limit, "output": "json"},
        )
        items: list[ProviderResult] = []
        docs = ((data.get("response") or {}).get("docs") or [])
        for doc in docs:
            ident = compact(doc.get("identifier"))
            identities = [identity("internet_archive", "internet_archive_identifier", ident, mediatype)]
            identities = [i for i in identities if i]
            base_kwargs = dict(
                title=compact(doc.get("title")) or ident,
                authors=[str(c) for c in as_list(doc.get("creator")) if c],
                published_date=compact(doc.get("date")),
                identities=identities,
            )
            model = AudiobookEditionModel(**base_kwargs) if mediatype == "audio" else BookEditionModel(**base_kwargs)
            items.append(ProviderResult(
                provider="internet_archive",
                title=model.title,
                contributors=model.authors,
                year=compact(doc.get("date"))[:4] or None,
                identifiers=identifier_map(model.identities),
                cover_url=f"https://archive.org/services/img/{ident}" if ident else None,
                raw={"downloads": doc.get("downloads"), "mediatype": mediatype},
                object_model=model.as_dict(),
                entity_type="audiobook_or_audio" if mediatype == "audio" else "book_edition",
                evidence=["Internet Archive catalog metadata"],
            ))
        return [item for item in items if item.title]

    async def google_books(self, query: str, limit: int) -> list[ProviderResult]:
        """Search Google Books volume metadata and normalize edition candidates."""
        params: dict[str, Any] = {"q": query, "maxResults": limit}
        key = self.context.secret("google_books", "api_key")
        if key:
            params["key"] = key
        data = await self.context.json("google_books", "https://www.googleapis.com/books/v1/volumes", params=params)
        items: list[ProviderResult] = []
        for volume in data.get("items") or []:
            info = volume.get("volumeInfo") or {}
            images = info.get("imageLinks") or {}
            identifiers = info.get("industryIdentifiers") or []
            isbn10: list[str] = []
            isbn13: list[str] = []
            for ident in identifiers:
                if not isinstance(ident, dict):
                    continue
                if ident.get("type") == "ISBN_10":
                    isbn10.append(compact(ident.get("identifier")))
                if ident.get("type") == "ISBN_13":
                    isbn13.append(compact(ident.get("identifier")))
            identities = [identity("google_books", "google_books_id", volume.get("id"), "volume")]
            identities = [i for i in identities if i]
            model = BookEditionModel(
                title=compact(info.get("title")),
                subtitle=compact(info.get("subtitle")),
                authors=[str(a) for a in as_list(info.get("authors"))],
                languages=[compact(info.get("language"))] if info.get("language") else [],
                published_date=compact(info.get("publishedDate")),
                publisher=compact(info.get("publisher")),
                isbn_10=[x for x in isbn10 if x],
                isbn_13=[x for x in isbn13 if x],
                subjects=[str(x) for x in as_list(info.get("categories"))],
                page_count=info.get("pageCount") if isinstance(info.get("pageCount"), int) else None,
                source_level="volume",
                identities=identities,
            )
            items.append(ProviderResult(
                provider="google_books",
                title=model.title,
                contributors=model.authors,
                year=model.published_date[:4] or None,
                identifiers=identifier_map(model.identities),
                summary=model.subtitle,
                cover_url=compact(images.get("thumbnail")) or compact(images.get("smallThumbnail")) or None,
                raw={"publisher": info.get("publisher"), "language": info.get("language")},
                object_model=model.as_dict(),
                entity_type="book_volume",
                evidence=["Google Books public volume metadata"],
            ))
        return [item for item in items if item.title]

    async def apple_search(self, query: str, limit: int, *, media: str) -> list[ProviderResult]:
        """Search Apple Search API for ebook/audiobook store metadata."""
        data = await self.context.json("apple_itunes_search", "https://itunes.apple.com/search", params={"term": query, "media": media, "limit": limit})
        items: list[ProviderResult] = []
        for row in data.get("results") or []:
            title = compact(row.get("trackName")) or compact(row.get("collectionName"))
            identities = [identity("apple_itunes_search", "apple_track_id", row.get("trackId") or row.get("collectionId"), media)]
            identities = [i for i in identities if i]
            if media == "audiobook":
                model = AudiobookEditionModel(
                    title=title,
                    authors=[compact(row.get("artistName"))] if row.get("artistName") else [],
                    published_date=compact(row.get("releaseDate")),
                    audio_formats=[compact(row.get("kind"))] if row.get("kind") else [],
                    identities=identities,
                )
                entity_type = "audiobook_edition"
            else:
                model = BookEditionModel(
                    title=title,
                    authors=[compact(row.get("artistName"))] if row.get("artistName") else [],
                    published_date=compact(row.get("releaseDate")),
                    subjects=[compact(row.get("primaryGenreName"))] if row.get("primaryGenreName") else [],
                    source_level="store_result",
                    identities=identities,
                )
                entity_type = "book_store_result"
            items.append(ProviderResult(
                provider="apple_itunes_search",
                title=title,
                contributors=model.authors,
                year=compact(row.get("releaseDate"))[:4] or None,
                identifiers=identifier_map(model.identities),
                cover_url=compact(row.get("artworkUrl100")) or None,
                raw={"kind": row.get("kind"), "genre": row.get("primaryGenreName")},
                object_model=model.as_dict(),
                entity_type=entity_type,
                evidence=["Apple Search catalog result"],
            ))
        return [item for item in items if item.title]

    async def librivox(self, query: str, limit: int) -> list[ProviderResult]:
        """Search LibriVox audiobook metadata with reader/chapter evidence."""
        data = await self.context.json(
            "librivox",
            "https://librivox.org/api/feed/audiobooks",
            params={"title": query, "format": "json", "extended": "1", "coverart": "1", "limit": limit},
        )
        items: list[ProviderResult] = []
        for book in data.get("books") or []:
            authors: list[str] = []
            for author in book.get("authors") or []:
                if isinstance(author, dict):
                    authors.append(" ".join(part for part in [compact(author.get("first_name")), compact(author.get("last_name"))] if part))
            sections: list[AudiobookChapterModel] = []
            for index, section in enumerate(book.get("sections") or [], start=1):
                if not isinstance(section, dict):
                    continue
                sections.append(AudiobookChapterModel(
                    title=compact(section.get("title")) or f"Chapter {index}",
                    index=index,
                    duration_seconds=seconds_from_librivox_time(section.get("playtime")),
                    reader=compact(section.get("reader")),
                    source_url=compact(section.get("listen_url")),
                ))
            identities = [identity("librivox", "librivox_id", book.get("id"), "audiobook")]
            identities = [i for i in identities if i]
            model = AudiobookEditionModel(
                title=compact(book.get("title")),
                authors=[a for a in authors if a],
                languages=[compact(book.get("language"))] if book.get("language") else [],
                narrators=[compact(book.get("reader"))] if book.get("reader") else [],
                readers=[compact(book.get("reader"))] if book.get("reader") else [],
                duration_seconds=seconds_from_librivox_time(book.get("totaltime")),
                chapter_count=int(book.get("num_sections") or 0) or len(sections) or None,
                has_chapters=bool(book.get("num_sections") or sections),
                chapters=sections,
                audio_formats=["mp3", "m4b"] if book.get("url_iarchive") else ["mp3"],
                identities=identities,
            )
            items.append(ProviderResult(
                provider="librivox",
                title=model.title,
                contributors=model.authors,
                identifiers=identifier_map(model.identities),
                summary=compact(book.get("description"))[:280],
                cover_url=compact(book.get("url_image")) or None,
                raw={"language": book.get("language"), "num_sections": book.get("num_sections"), "totaltime": book.get("totaltime")},
                object_model=model.as_dict(),
                entity_type="audiobook_edition",
                evidence=["LibriVox public-domain audiobook catalog"],
            ))
        return [item for item in items if item.title]

    async def comic_vine(self, query: str, limit: int) -> list[ProviderResult]:
        """Search Comic Vine volumes/issues and normalize comic archive metadata."""
        api_key = self.context.secret("comic_vine", "api_key")
        data = await self.context.json(
            "comic_vine",
            "https://comicvine.gamespot.com/api/search/",
            params={"api_key": api_key, "format": "json", "query": query, "resources": "volume,issue", "limit": limit},
        )
        items: list[ProviderResult] = []
        for row in data.get("results") or []:
            image = row.get("image") or {}
            identities = [identity("comic_vine", "comic_vine_id", row.get("id"), compact(row.get("resource_type")))]
            identities = [i for i in identities if i]
            model = BookEditionModel(
                title=compact(row.get("name")) or compact(row.get("volume", {}).get("name")),
                subjects=[compact(row.get("resource_type"))] if row.get("resource_type") else [],
                published_date=compact(row.get("cover_date")),
                identities=identities,
            )
            items.append(ProviderResult(
                provider="comic_vine",
                title=model.title,
                identifiers=identifier_map(model.identities),
                summary=compact(row.get("deck")),
                year=model.published_date[:4] or None,
                cover_url=compact(image.get("super_url")) or compact(image.get("medium_url")) or None,
                raw={"resource_type": row.get("resource_type")},
                object_model=model.as_dict(),
                entity_type="comic_volume_or_issue",
                evidence=["Comic Vine comics metadata result"],
            ))
        return [item for item in items if item.title]


def seconds_from_librivox_time(value: Any) -> int | None:
    """Convert LibriVox HH:MM:SS-ish durations to seconds."""
    text = compact(value)
    if not text:
        return None
    parts = [p for p in text.split(":") if p.strip()]
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return None
    total = 0
    for number in numbers:
        total = total * 60 + number
    return total or None
