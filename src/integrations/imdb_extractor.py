"""
IMDb extractor for LJS.

Extracts ratings, vote counts, and plot summaries from IMDb pages
using the existing Cinemagoer library when available, with browser
rendering as a complementary source.
"""

import re
from loguru import logger
from src.core.models import ExtractedFacts, Fact, BrowserFetchRequest, BrowserFetchResult
from src.integrations.rotten_tomatoes import PageExtractor


class IMDbExtractor(PageExtractor):
    """Extracts IMDb rating and metadata from browser-rendered pages."""

    SOURCE = "IMDb"

    @property
    def source_name(self) -> str:
        """Execute the public IMDbExtractor.source_name behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return self.SOURCE

    def can_extract(self, page_result: BrowserFetchResult) -> bool:
        """Return whether IMDbExtractor satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        return "imdb" in page_result.final_url.lower()

    async def extract(self, page_result: BrowserFetchResult,
                      question: str | None = None) -> ExtractedFacts:
        """Fetch or extract provider data for IMDbExtractor.

        Keep network and parsing errors contained so the caller can continue
        with fallback providers.  Extend by adding parser helpers instead of
        changing the public return contract.
        """
        facts = ExtractedFacts(schema_name="movie_details", facts=[])

        rating = None
        for match in re.finditer(r"(\d+\.\d+)/10\b", page_result.text[:2000]):
            rating = match.group(1)
            break

        votes = None
        for match in re.finditer(r"(\d[\d,]*)\s*(?:votes|ratings|IMDb\s*ratings)",
                                 page_result.text[:2000], re.IGNORECASE):
            votes = match.group(1).replace(",", "")
            break

        if rating:
            facts.facts.append(Fact(
                label="IMDb Rating",
                value=f"{rating}/10",
                evidence=f"{votes} votes" if votes else "",
                url=page_result.final_url,
                confidence=0.8 if votes else 0.6,
            ))

        if not facts.facts:
            snippet = page_result.text[:300] if page_result.text else ""
            facts.facts.append(Fact(
                label="Page Snippet", value=snippet,
                evidence=snippet,
                url=page_result.final_url, confidence=0.2,
            ))
        return facts

    async def fetch_for_title(self, title: str,
                              runtime: "BrowserRuntime") -> BrowserFetchResult | None:
        """Fetch or extract provider data for IMDbExtractor.

        Keep network and parsing errors contained so the caller can continue
        with fallback providers.  Extend by adding parser helpers instead of
        changing the public return contract.
        """
        from urllib.parse import quote_plus
        url = f"https://www.imdb.com/find/?q={quote_plus(title)}"
        request = BrowserFetchRequest(
            url=url, wait_seconds=2.0, max_content_chars=4000,
            screenshot_on_failure=False, purpose="reviews",
        )
        result = await runtime.fetch(request)
        if result.ok and self.can_extract(result):
            return result
        return None
