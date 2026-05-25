"""
Search patterns for media categories in LJS.

Provides the base SearchPatterns dataclass that every media category
uses to build torrent search queries. Subclasses override methods to
add category-specific query formats.
"""

from dataclasses import dataclass

from src.core.categories.language import LanguageSearchTagger


@dataclass
class SearchPatterns:
    """How to build torrent search queries for this category."""

    @staticmethod
    def _append_language(query: str, language: str | None) -> str:
        """Append the language tag (e.g. 'ITA') to a query if appropriate.

        Single source of truth for how language gets stitched into queries —
        subclasses should call this instead of reimplementing.
        """
        return LanguageSearchTagger.append_to_query(query, language)

    def build_primary_query(self, media_name: str, language: str,
                            progress: dict | None = None) -> str:
        """Build the primary search query. Override for custom formats."""
        return self._append_language(f"{media_name}", language)

    def build_alternative_queries(self, media_name: str, language: str,
                                   progress: dict | None = None) -> list[str]:
        """Alternative query formats to try if primary fails."""
        return []

    def build_pack_query(self, media_name: str, language: str,
                          season: int | None = None) -> str | None:
        """Build a season/album/discography pack query. Return None if N/A."""
        if season is not None:
            return self._append_language(
                f"{media_name} S{season:02d} Complete", language,
            )
        return None
