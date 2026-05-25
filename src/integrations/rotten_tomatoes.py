"""
Rotten Tomatoes and Metacritic extractors for LJS.

Extracts review scores (Tomatometer, audience score, Metascore) and
critic consensus from rendered review pages through the browser runtime.
"""

from abc import ABC, abstractmethod
import re
from loguru import logger
from src.core.models import ExtractedFacts, Fact, BrowserFetchRequest, BrowserFetchResult


class PageExtractor(ABC):
    """Extracts structured facts from a rendered page."""

    @abstractmethod
    def can_extract(self, page_result: BrowserFetchResult) -> bool:
        """Return whether PageExtractor satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        ...

    @abstractmethod
    async def extract(self, page_result: BrowserFetchResult,
                      question: str | None = None) -> ExtractedFacts:
        """Fetch or extract provider data for PageExtractor.

        Keep network and parsing errors contained so the caller can continue
        with fallback providers.  Extend by adding parser helpers instead of
        changing the public return contract.
        """
        ...

    @abstractmethod
    async def fetch_for_title(self, title: str,
                              runtime: "BrowserRuntime") -> BrowserFetchResult | None:
        """Fetch or extract provider data for PageExtractor.

        Keep network and parsing errors contained so the caller can continue
        with fallback providers.  Extend by adding parser helpers instead of
        changing the public return contract.
        """
        ...

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Execute the public PageExtractor.source_name behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        ...


class RottenTomatoesExtractor(PageExtractor):
    """Extracts review scores from Rotten Tomatoes."""

    SOURCE = "Rotten Tomatoes"

    @property
    def source_name(self) -> str:
        """Execute the public RottenTomatoesExtractor.source_name behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return self.SOURCE

    def can_extract(self, page_result: BrowserFetchResult) -> bool:
        """Return whether RottenTomatoesExtractor satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        url_lower = page_result.final_url.lower()
        title_lower = page_result.title.lower()
        return "rottentomatoes" in url_lower or "rotten tomatoes" in title_lower

    async def extract(self, page_result: BrowserFetchResult,
                      question: str | None = None) -> ExtractedFacts:
        """Fetch or extract provider data for RottenTomatoesExtractor.

        Keep network and parsing errors contained so the caller can continue
        with fallback providers.  Extend by adding parser helpers instead of
        changing the public return contract.
        """
        facts = ExtractedFacts(schema_name="review_scores", facts=[])

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(page_result.html or "", "html.parser")
        except Exception:
            return facts

        tomatometer = None
        audience = None
        consensus = None

        for tag in soup.find_all(["rt-text", "span", "score-board"]):
            text = tag.get_text(strip=True).lower()
            if "tomatometer" in text or "fresh" in text:
                percentage = self._find_percentage_near(tag)
                if percentage:
                    tomatometer = percentage
            if "audience" in text or "popcorn" in text:
                percentage = self._find_percentage_near(tag)
                if percentage:
                    audience = percentage

        for p in soup.find_all("p"):
            ptext = p.get_text(strip=True)
            if 20 < len(ptext) < 500:
                if any(w in ptext.lower() for w in ["consensus", "critics", "reviews", "tomatometer"]):
                    consensus = ptext
                    break

        conf = 0.7
        if tomatometer:
            facts.facts.append(Fact(
                label="Tomatometer", value=tomatometer,
                evidence=consensus or "",
                url=page_result.final_url, confidence=conf,
            ))
        if audience:
            facts.facts.append(Fact(
                label="Audience Score", value=audience,
                evidence="",
                url=page_result.final_url, confidence=conf,
            ))
        if not facts.facts and consensus:
            facts.facts.append(Fact(
                label="Critic Consensus", value=consensus[:200],
                evidence=consensus,
                url=page_result.final_url, confidence=0.5,
            ))
        if not facts.facts:
            snippet = page_result.text[:500] if page_result.text else ""
            facts.facts.append(Fact(
                label="Page Snippet", value=snippet,
                evidence=snippet,
                url=page_result.final_url, confidence=0.3,
            ))
        return facts

    @staticmethod
    def _find_percentage_near(element) -> str | None:
        parent = element.parent if hasattr(element, "parent") else element
        if parent:
            text = parent.get_text(strip=True)
            matches = re.findall(r"(\d{1,3})%", text)
            if matches:
                return f"{matches[0]}%"
        return None

    async def fetch_for_title(self, title: str,
                              runtime: "BrowserRuntime") -> BrowserFetchResult | None:
        """Fetch or extract provider data for RottenTomatoesExtractor.

        Keep network and parsing errors contained so the caller can continue
        with fallback providers.  Extend by adding parser helpers instead of
        changing the public return contract.
        """
        from urllib.parse import quote_plus
        url = f"https://www.rottentomatoes.com/search?search={quote_plus(title)}"
        request = BrowserFetchRequest(
            url=url, wait_seconds=2.0, max_content_chars=4000,
            screenshot_on_failure=False, purpose="reviews",
        )
        result = await runtime.fetch(request)
        if result.ok and self.can_extract(result):
            return result
        return None


class MetacriticExtractor(PageExtractor):
    """Extracts Metascore from Metacritic."""

    SOURCE = "Metacritic"

    @property
    def source_name(self) -> str:
        """Execute the public MetacriticExtractor.source_name behavior.

        This method is a supported extension point for callers outside the
        class.  Keep its input/output contract stable and move specialized
        logic into collaborators or protected helpers as the feature grows.
        """
        return self.SOURCE

    def can_extract(self, page_result: BrowserFetchResult) -> bool:
        """Return whether MetacriticExtractor satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        return "metacritic" in page_result.final_url.lower()

    async def extract(self, page_result: BrowserFetchResult,
                      question: str | None = None) -> ExtractedFacts:
        """Fetch or extract provider data for MetacriticExtractor.

        Keep network and parsing errors contained so the caller can continue
        with fallback providers.  Extend by adding parser helpers instead of
        changing the public return contract.
        """
        facts = ExtractedFacts(schema_name="review_scores", facts=[])

        metascore = None
        for match in re.finditer(r"(?:metascore|metascore\s*is)?\s*(\d{1,3})",
                                 page_result.text[:2000], re.IGNORECASE):
            score = int(match.group(1))
            if 0 <= score <= 100:
                metascore = str(score)
                break

        userscore = None
        for match in re.finditer(r"(?:user\s*score|userscore)?\s*(\d+\.\d+)",
                                 page_result.text[:2000], re.IGNORECASE):
            userscore = match.group(1)
            break

        if metascore:
            facts.facts.append(Fact(
                label="Metascore", value=metascore,
                evidence="",
                url=page_result.final_url, confidence=0.7,
            ))
        if userscore:
            facts.facts.append(Fact(
                label="User Score", value=userscore,
                evidence="",
                url=page_result.final_url, confidence=0.6,
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
        """Fetch or extract provider data for MetacriticExtractor.

        Keep network and parsing errors contained so the caller can continue
        with fallback providers.  Extend by adding parser helpers instead of
        changing the public return contract.
        """
        from urllib.parse import quote_plus
        url = f"https://www.metacritic.com/search/{quote_plus(title)}/"
        request = BrowserFetchRequest(
            url=url, wait_seconds=2.0, max_content_chars=4000,
            screenshot_on_failure=False, purpose="reviews",
        )
        result = await runtime.fetch(request)
        if result.ok and self.can_extract(result):
            return result
        return None
