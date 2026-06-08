"""TV-owned public web-research planning and evidence interpretation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from src.ai.runtime_date_grounding import RuntimeDateGrounding

from src.core.models import (
    CategoryResearchFact,
    CategoryResearchInterpretation,
    CategoryWebResearchInput,
    CategoryWebResearchPlan,
    CategoryWebResearchSearch,
    WebEvidenceBundle,
)


class TvWebResearchInterpreter:
    """Interpret public web evidence using TV-specific semantics."""

    _AIR_TERMS = (
        "air date",
        "airdate",
        "aired",
        "airs",
        "premiere",
        "premieres",
        "release date",
        "episode guide",
    )
    _DELAY_TERMS = (
        "delay",
        "delayed",
        "postponed",
        "rescheduled",
        "cancelled",
        "canceled",
        "hiatus",
    )
    _DATE_RE = re.compile(
        r"\b(?:\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
        re.IGNORECASE,
    )

    def interpret(self, bundle: WebEvidenceBundle, research_input: CategoryWebResearchInput) -> CategoryResearchInterpretation:
        """Return non-mutating TV facts/signals from fetched public evidence."""
        facts: list[CategoryResearchFact] = []
        source_by_url = {source.canonical_url or source.url: source for source in bundle.sources}
        seen: set[tuple[str, str, str]] = set()
        title = research_input.item_name or research_input.item_id
        for evidence in bundle.evidence:
            url = str(evidence.url or "")
            source = source_by_url.get(url)
            source_id = int(getattr(source, "evidence_id", 0) or 0) if source else 0
            text = self._combined_text(evidence, source)
            if not self._is_title_bound_tv_source(text, title):
                continue
            if self._looks_primary_source(url, text, source):
                self._append_fact(
                    facts,
                    seen,
                    fact_type="official_or_primary_source_candidate",
                    value={
                        "url": url,
                        "title": evidence.value or getattr(source, "title", "") or "",
                        "source_kind": getattr(source, "source_kind", "") if source else "",
                    },
                    source_id=source_id,
                    confidence=max(float(evidence.confidence or 0.0), 0.58),
                    authoritative=False,
                )
            if self._contains_any(text, self._AIR_TERMS):
                dates = self._date_mentions(text)
                if not dates:
                    continue
                self._append_fact(
                    facts,
                    seen,
                    fact_type="air_date_reference",
                    value={
                        "url": url,
                        "date_mentions": dates[:5],
                        "date_relations": [RuntimeDateGrounding.classify_date(value) for value in dates[:5] if RuntimeDateGrounding.classify_date(value)],
                        "runtime_date_context": RuntimeDateGrounding.runtime_context(),
                        "evidence_snippet": evidence.snippet[:500],
                    },
                    source_id=source_id,
                    confidence=max(float(evidence.confidence or 0.0), 0.50),
                    authoritative=False,
                )
            if self._contains_any(text, self._DELAY_TERMS):
                self._append_fact(
                    facts,
                    seen,
                    fact_type="delay_or_cancellation_signal",
                    value={
                        "url": url,
                        "evidence_snippet": evidence.snippet[:500],
                    },
                    source_id=source_id,
                    confidence=max(float(evidence.confidence or 0.0), 0.48),
                    authoritative=False,
                )
        warnings = list(bundle.warnings)
        for evidence in bundle.evidence:
            text = self._combined_text(evidence, source_by_url.get(str(evidence.url or "")))
            freshness = RuntimeDateGrounding.source_freshness_signals(text, query=research_input.context.get("user_query", ""), intent=research_input.intent)
            warnings.extend(f"TV date-grounding warning: {warning}" for warning in freshness.get("warnings", []))
        summary = self._summary(facts, research_input)
        unresolved = list(bundle.unresolved_questions)
        if not facts and bundle.evidence:
            unresolved.append("Fetched pages did not contain deterministic TV air-date, official-page, or delay signals.")
        return CategoryResearchInterpretation(
            category_id="tv",
            item_id=research_input.item_id,
            intent=research_input.intent,
            summary=summary,
            facts=facts,
            warnings=warnings,
            unresolved_questions=unresolved,
            can_mutate_item=False,
        )

    def _combined_text(self, evidence: Any, source: Any) -> str:
        pieces = [
            getattr(evidence, "claim", ""),
            getattr(evidence, "value", ""),
            getattr(evidence, "snippet", ""),
            getattr(evidence, "url", ""),
        ]
        if source:
            pieces.extend([
                getattr(source, "title", ""),
                getattr(source, "snippet", ""),
                getattr(source, "source_kind", ""),
            ])
        return " ".join(str(piece or "") for piece in pieces).lower()

    def _looks_primary_source(self, url: str, text: str, source: Any) -> bool:
        host = urlparse(url).netloc.lower()
        if "official" in text:
            return True
        return any(token in host for token in (
            "tv.apple.com",
            "apple.com",
            "tvmaze.com",
            "trakt.tv",
            "themoviedb.org",
            "imdb.com",
        ))

    @staticmethod
    def _is_title_bound_tv_source(text: str, title: str) -> bool:
        title = str(title or "").strip().lower()
        if not title:
            return True
        title_tokens = [token for token in re.findall(r"[a-z0-9]+", title) if len(token) > 1]
        if title_tokens and not all(token in text for token in title_tokens):
            return False
        tv_terms = ("episode", "season", "series", "show", "air date", "premiere", "apple tv", "tv+", "for all mankind", "s01", "s1")
        return any(term in text for term in tv_terms)

    def _date_mentions(self, text: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for match in self._DATE_RE.finditer(text):
            value = match.group(0).strip()
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    @staticmethod
    def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
        return any(term in text for term in terms)

    def _append_fact(
        self,
        facts: list[CategoryResearchFact],
        seen: set[tuple[str, str, str]],
        *,
        fact_type: str,
        value: dict[str, Any],
        source_id: int,
        confidence: float,
        authoritative: bool,
    ) -> None:
        key = (fact_type, str(value.get("url") or ""), str(value.get("date_mentions") or value.get("title") or ""))
        if key in seen:
            return
        seen.add(key)
        facts.append(CategoryResearchFact(
            fact_type=fact_type,
            value=value,
            source_evidence_ids=[source_id] if source_id else [],
            confidence=confidence,
            decided_by="tv_web_research_interpreter",
            authoritative=authoritative,
        ))

    @staticmethod
    def _summary(facts: list[CategoryResearchFact], research_input: CategoryWebResearchInput) -> str:
        if not facts:
            return f"No TV-specific public web evidence signals were extracted for {research_input.item_name or research_input.item_id}."
        counts: dict[str, int] = {}
        for fact in facts:
            counts[fact.fact_type] = counts.get(fact.fact_type, 0) + 1
        parts = [f"{count} {fact_type.replace('_', ' ')}" for fact_type, count in sorted(counts.items())]
        return f"Extracted TV web-research signals for {research_input.item_name or research_input.item_id}: " + ", ".join(parts) + "."


class TvWebResearchMixin:
    """TV-owned web-research hook implementation."""

    def web_research_contract(self, settings: Any = None) -> dict[str, Any]:
        """Describe TV public web-research capabilities."""
        return {
            "enabled": True,
            "intents": [
                "official_page_discovery",
                "airdate_corroboration",
                "delay_news_check",
                "news_and_rumor_watch",
                "next_episode_airdate",
                "next_season_start_tracking",
                "title_ambiguity_resolution",
            ],
            "notes": [
                "TV uses public web research only to discover and corroborate public sources.",
                "Fetched evidence does not directly authorize download availability or item mutation.",
                "Intent labels are semantic hints. The LLM planner should map user wording to searches rather than relying on exact enum names.",
            ],
            "llm_research_guidance": [
                "For TV current-public questions, plan from the user's exact wording: next season, season number, episode number, creator interview, renewal, cancellation, production, filming, trailer, or release schedule.",
                "For next-season rumours/renewal/production questions, include the title, inferred season number when present/inferable, current year, streamer/network, and terms such as renewed, confirmed, production, filming, shooting, creator, showrunner, interview, casting, premiere, release date, Apple TV, press, Deadline, Variety, Hollywood Reporter.",
                "For next-episode air-date questions, combine structured metadata/provider lookup with public corroboration. The search plan should seek official/platform/reference episode guides with explicit dates; do not infer dates by weekly cadence unless a fetched source explicitly states it.",
                "For title collisions, include 'TV series', streamer/network, year, and known creator/cast hints from context. Avoid ambiguous queries that could match places, games, companies, or unrelated media.",
                "For rumours/social chatter, separate unconfirmed fan/social/forum evidence from official or trade-reported confirmation. Social evidence can establish chatter exists; it cannot prove renewal, cancellation, shooting, or dates by itself.",
                "Use current-year/freshness terms and time_range month/year for ongoing shows, renewal news, production status, and future schedules. Use general/reference searches alongside news searches so old episode guides do not dominate the whole answer.",
                "For next/upcoming/future season questions, compare every season/page/source date to the runtime current date. A page saying a season is upcoming in a past year is stale background, not the answer. If season 3 aired in 2025 and the runtime year is 2026, the next-season question is about season 4 unless the user explicitly named season 3.",
            ],
            "source_quality_policy": [
                "Strong TV sources: streamer/network official pages and press rooms; TVMaze/TMDB/IMDb episode pages for episode lists; reputable trades such as Deadline, Variety, Hollywood Reporter; direct creator/showrunner/interview sources.",
                "Medium sources: established entertainment news that cites primary/trade sources; reference pages with dated episode data.",
                "Weak sources: fan calendars, SEO schedule sites, Reddit/X/Twitter/forum posts, undated blogs. Use them only as unconfirmed chatter unless corroborated.",
            ],
            "freshness_policy": [
                "For active/returning shows, old season pages are background, not evidence about future seasons. Already-aired seasons must not be described as upcoming.",
                "For questions phrased as rumours/news/current/next/upcoming/latest, at least one planned search should use category news and a time_range of month or year.",
                "A negative answer requires current official/reference/trade coverage, not just absence from a generic episode guide.",
            ],
            "answer_policy": [
                "Do not say a season is unconfirmed if fetched current sources show renewal/production/interviews; state the strongest source type and confidence.",
                "Do not state an episode aired or will air unless a source provides a concrete date and the date has been compared to the runtime date.",
                "Do not answer next/upcoming season questions from a past season page alone; say it is stale and search/answer about the later season.",
                "If evidence is degraded/fallback-only or mostly snippets, explicitly lower confidence and continue searching when the user needs a factual answer.",
            ],
            "query_examples": [
                {"objective": "next season rumours", "query": '"{title}" "season {n}" renewed confirmed production filming interview Apple TV 2026', "categories": ["news", "general"], "time_range": "year"},
                {"objective": "creator/showrunner interview", "query": '"{title}" "season {n}" showrunner creator interview production', "categories": ["news", "general"], "time_range": "year"},
                {"objective": "next episode date", "query": '"{title}" TV series episode guide next episode air date official', "categories": ["general"], "time_range": "year"},
                {"objective": "official streamer page", "query": 'site:tv.apple.com "{title}" episodes', "categories": ["general"], "time_range": ""},
            ],
        }

    def build_web_research_plan(self, research_input: CategoryWebResearchInput) -> CategoryWebResearchPlan:
        """Build TV-owned public web searches for source discovery/corroboration."""
        title = research_input.item_name or research_input.item_id
        if not title:
            return CategoryWebResearchPlan(
                category_id=self.category_id,
                item_id=research_input.item_id,
                intent=research_input.intent,
                notes=["TV web research requires an item title."],
            )
        language = research_input.language or "auto"
        label = self._research_episode_label(research_input.context)
        user_query = str((research_input.context or {}).get("user_query") or "").strip()
        searches = self._research_searches_for_intent(title, label, research_input.intent, language, user_query=user_query)
        return CategoryWebResearchPlan(
            category_id=self.category_id,
            item_id=research_input.item_id or title,
            intent=research_input.intent,
            searches=searches,
            max_searches=4,
            require_page_extraction_before_facts=True,
            notes=["TV web research collects public evidence only; category state is not mutated by this plan."],
        )

    async def interpret_web_evidence(self, bundle: WebEvidenceBundle, research_input: CategoryWebResearchInput) -> CategoryResearchInterpretation:
        """Interpret fetched evidence through TV-specific public-source rules."""
        return TvWebResearchInterpreter().interpret(bundle, research_input)

    def _research_searches_for_intent(
        self,
        title: str,
        episode_label: str,
        intent: str,
        language: str,
        *,
        user_query: str = "",
    ) -> list[CategoryWebResearchSearch]:
        normalized_intent = str(intent or "").strip().lower()
        label_part = f" {episode_label}" if episode_label else ""
        current_year = datetime.now().year
        season_focus = self._season_focus_from_query(user_query)
        if normalized_intent == "official_page_discovery":
            return [
                CategoryWebResearchSearch(
                    query=f"{title} TV series official episode guide Apple TV TVMaze {current_year}",
                    intent="official_page_discovery",
                    categories=["general"],
                    language=language,
                    max_results=6,
                    max_urls_to_fetch=4,
                )
            ]
        if normalized_intent == "delay_news_check":
            return [
                CategoryWebResearchSearch(
                    query=f"{title}{label_part} TV series delayed postponed rescheduled {current_year}",
                    intent="delay_news_check",
                    categories=["news"],
                    language=language,
                    time_range="year",
                    max_results=10,
                    max_urls_to_fetch=5,
                )
            ]
        if normalized_intent == "news_and_rumor_watch":
            return self._user_focused_public_searches(
                title=title,
                user_query=user_query,
                season_focus=season_focus,
                language=language,
                current_year=current_year,
                intent="news_and_rumor_watch",
            )
        if user_query and normalized_intent not in {"official_page_discovery", "delay_news_check", "next_season_start_tracking"}:
            # Fallback for arbitrary LLM/user intent labels.  Do not enumerate
            # every synonym here; preserve the LLM/user's focus and let the
            # generic evidence layer fetch/corroborate sources.
            return self._user_focused_public_searches(
                title=title,
                user_query=user_query,
                season_focus=season_focus,
                language=language,
                current_year=current_year,
                intent=normalized_intent or "llm_planned_public_research",
            )
        if normalized_intent == "next_season_start_tracking":
            return [
                CategoryWebResearchSearch(
                    query=f"{title} TV series next season premiere date official Apple TV {current_year}",
                    intent="airdate_corroboration",
                    categories=["general"],
                    language=language,
                    time_range="year",
                    max_results=10,
                    max_urls_to_fetch=5,
                ),
                CategoryWebResearchSearch(
                    query=f"{title} TV series renewed cancelled next season production news {current_year}",
                    intent="news_and_rumor_watch",
                    categories=["news"],
                    language=language,
                    time_range="month",
                    max_results=6,
                    max_urls_to_fetch=4,
                ),
            ]
        return [
            CategoryWebResearchSearch(
                query=f"{title}{label_part} TV series air date official episode guide Apple TV TVMaze {current_year}",
                intent="airdate_corroboration",
                categories=["general"],
                language=language,
                max_results=6,
                max_urls_to_fetch=4,
            ),
            CategoryWebResearchSearch(
                query=f"{title}{label_part} TV series delayed postponed rescheduled {current_year}",
                intent="delay_news_check",
                categories=["news"],
                language=language,
                time_range="month",
                max_results=6,
                max_urls_to_fetch=3,
            ),
            CategoryWebResearchSearch(
                query=f"{title} TV series official site episodes release date Apple TV TVMaze {current_year}",
                intent="official_page_discovery",
                categories=["general"],
                language=language,
                max_results=6,
                max_urls_to_fetch=3,
            ),
        ]

    def _user_focused_public_searches(
        self,
        *,
        title: str,
        user_query: str,
        season_focus: str,
        language: str,
        current_year: int,
        intent: str,
    ) -> list[CategoryWebResearchSearch]:
        """Build fallback searches around the user's/LLM's own TV research focus."""
        focus_query = user_query.strip() if user_query else f"{title} {season_focus}".strip()
        if title.casefold() not in focus_query.casefold():
            focus_query = f"{title} {focus_query}".strip()
        return [
            CategoryWebResearchSearch(
                query=f"{focus_query} {current_year} interview production renewal confirmed Apple TV",
                intent=intent,
                categories=["news"],
                language=language,
                time_range="year",
                max_results=10,
                max_urls_to_fetch=5,
            ),
            CategoryWebResearchSearch(
                query=f"{title} {season_focus} official press Apple TV showrunner interview production {current_year}",
                intent=intent,
                categories=["general"],
                language=language,
                time_range="year",
                max_results=10,
                max_urls_to_fetch=5,
            ),
            CategoryWebResearchSearch(
                query=f"{title} {season_focus} news release date casting filming {current_year}",
                intent=intent,
                categories=["news"],
                language=language,
                time_range="year",
                max_results=10,
                max_urls_to_fetch=4,
            ),
        ]

    @staticmethod
    def _season_focus_from_query(user_query: str) -> str:
        match = re.search(r"\bseason\s+(\d{1,2})\b", str(user_query or ""), re.IGNORECASE)
        if match:
            return f"season {match.group(1)}"
        return "next season"

    @staticmethod
    def _research_episode_label(context: dict[str, Any]) -> str:
        unit_key = str((context or {}).get("unit_key") or "").strip()
        if unit_key:
            return unit_key
        try:
            season = int((context or {}).get("season") or 0)
            episode = int((context or {}).get("episode") or 0)
        except (TypeError, ValueError):
            return ""
        if season > 0 and episode > 0:
            return f"S{season:02d}E{episode:02d}"
        return ""
