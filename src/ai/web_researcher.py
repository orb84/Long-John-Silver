"""
Web researcher for LJS agentic browsing.

Orchestrates browser-powered media research: reviews, release info,
and general article reading. Composes domain-specific extractors and
the browser runtime to produce structured WebResearchReport objects
with sourced evidence and confidence scores.
"""

from loguru import logger
from typing import Optional

from src.core.models import BrowserFetchResult, WebEvidence, WebResearchReport


class WebResearcher:
    """Browser-assisted media web research service.

    Provides high-level research methods that the LLM can call as tools,
    reducing the burden on smaller models. Each method returns a structured
    WebResearchReport with evidence, citations, and unresolved questions.
    """

    def __init__(
        self,
        runtime: "BrowserRuntime",
        web_reader: "WebReader",
        web_search_config: object | None = None,
        web_research_repository: object | None = None,
    ):
        """Initialize with browser runtime, web reader, and optional research services.

        Args:
            runtime: BrowserRuntime for page fetching.
            web_reader: WebReader for httpx-based page reading.
            web_search_config: Optional configured WebSearchConfig.
            web_research_repository: Optional repository for evidence provenance.
        """
        self._runtime = runtime
        self._web_reader = web_reader
        self._web_search_config = web_search_config
        self._web_research_repository = web_research_repository
        self._extractors: list["PageExtractor"] = []

    def register_extractor(self, extractor: "PageExtractor") -> None:
        """Register a domain-specific page extractor.

        Args:
            extractor: A PageExtractor instance.
        """
        self._extractors.append(extractor)

    async def research_reviews(self, title: str, media_type: str | None = None) -> dict:
        """Find review scores and critic/audience consensus.

        Uses registered extractors that can handle review sites. Each
        extractor determines whether it can handle the request and
        contributes evidence to the report.

        Args:
            title: The media title to research.
            media_type: Optional media type ('movie' or 'tv').

        Returns:
            Dict with 'topic', 'summary', 'evidence', 'visited_urls'.
        """
        report = WebResearchReport(
            topic=f"Reviews for {title}",
            summary="",
            evidence=[],
            visited_urls=[],
        )

        for extractor in self._extractors:
            try:
                fetch_result = await extractor.fetch_for_title(title, self._runtime)
                if not fetch_result or not fetch_result.ok:
                    continue

                facts = await extractor.extract(fetch_result)
                report.visited_urls.append(fetch_result.final_url)

                for fact in facts.facts:
                    report.evidence.append(WebEvidence(
                        claim=f"{fact.label}: {fact.value}",
                        value=fact.value,
                        source_name=extractor.source_name,
                        url=fact.url or fetch_result.final_url,
                        snippet=fact.evidence or fact.label,
                        confidence=fact.confidence,
                    ))
            except Exception as e:
                logger.debug(f"Extractor {extractor.source_name} failed for {title}: {e}")
                continue

        if report.evidence:
            report.summary = f"Found {len(report.evidence)} review facts for {title}."
        else:
            report.summary = f"No review evidence found for {title}."
            report.unresolved_questions = ["No extractors could retrieve review data."]

        return report.model_dump()

    async def research_release_info(self, title: str) -> dict:
        """Find release dates, renewal status, and episode schedule info.

        Uses the configured WebResearchService instead of a hard-coded search
        scraper.  SearXNG/other providers discover candidate public sources;
        WebReader fetches pages before evidence is surfaced.
        """
        from src.core.models import WebResearchBudget, WebResearchRequest, WebSearchConfig
        from src.search.web.research import WebResearchService

        config = self._web_search_config or WebSearchConfig()
        request = WebResearchRequest(
            query=f"{title} release date season announced renewal status",
            intent="release_info_public_evidence",
            item_name=title,
            categories=["general", "news"],
            max_results=getattr(config, "max_results", 5),
            budget=WebResearchBudget(max_urls_to_fetch=5, require_page_extraction_before_facts=True),
        )
        bundle = await WebResearchService(
            config,
            web_reader=self._web_reader,
            repository=self._web_research_repository,
        ).collect_evidence(request)
        report = WebResearchReport(
            topic=f"Release info for {title}",
            summary=(
                f"Collected {len(bundle.evidence)} fetched public-source evidence item(s) for {title}."
                if bundle.evidence else f"No fetched release-info evidence found for {title}."
            ),
            evidence=bundle.evidence,
            visited_urls=[source.canonical_url or source.url for source in bundle.sources],
            unresolved_questions=bundle.unresolved_questions or bundle.warnings,
        )
        return report.model_dump()

    async def research_article(self, url: str, question: str | None = None) -> dict:
        """Read one page and answer a focused question with citations.

        Args:
            url: The article URL to read.
            question: Optional focused question about the article.

        Returns:
            Dict with 'topic', 'summary', 'evidence', 'visited_urls'.
        """
        report = WebResearchReport(
            topic=question or f"Article at {url}",
            summary="",
            evidence=[],
            visited_urls=[url],
        )

        try:
            from src.core.models import BrowserFetchRequest
            request = BrowserFetchRequest(
                url=url, wait_seconds=2.0, max_content_chars=4000,
                screenshot_on_failure=False, purpose="article",
            )
            result = await self._runtime.fetch(request)
            if result.ok and result.text:
                snippet = result.text[:1000] if result.text else ""
                report.evidence.append(WebEvidence(
                    claim=question or f"Content from {url}",
                    source_name=result.final_url,
                    url=result.final_url,
                    snippet=snippet,
                    confidence=0.7,
                ))
                report.summary = f"Read article from {result.final_url}"
            else:
                report.summary = f"Failed to read {url}"
                report.unresolved_questions = ["Page could not be fetched."]
        except Exception as e:
            report.summary = f"Error reading article: {e}"
            report.unresolved_questions = [str(e)]

        return report.model_dump()
