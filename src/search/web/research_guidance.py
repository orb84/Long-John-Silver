"""Prompt guidance for public web research.

The classes in this module only describe source-discovery and evidence-quality
contracts for LLM prompts.  They do not route user intent, interpret category
facts, mutate items, or queue downloads.
"""

from __future__ import annotations

from typing import Any

from src.utils.runtime_prompt_context import RuntimePromptContext


class WebResearchPromptGuidance:
    """Reusable LLM-facing guidance for public web research planning.

    Keep this category-neutral.  Category-specific additions belong in category
    contracts/prompts and are appended by the caller.
    """

    @staticmethod
    def runtime_context() -> str:
        """Return current date/time context for time-sensitive research."""
        return RuntimePromptContext.llm_guidance_block()

    @staticmethod
    def general_rules() -> str:
        """Return generic research rules for the main tool-calling agent."""
        return (
            "PUBLIC WEB RESEARCH GUIDANCE:\n"
            "1. Use public web evidence for current public information: news, rumours, leaks, renewal/cancellation reports, production/shooting status, creator interviews, patch notes, changelogs, roadmaps, public discussions, and future schedules. Structured metadata alone is not sufficient unless it directly contains a fresh, exact schedule/fact for the requested unit.\n"
            "2. Search like a researcher: preserve the user's exact focus. Combine the entity name with concrete qualifiers from the prompt: season/episode/version/bug/platform/creator/network, the current year when freshness matters, and intent terms such as confirmed, renewed, production, filming, interview, official, press release, air date, patch notes, changelog, roadmap, delay, cancelled, or postponed.\n"
            "3. Use provider controls instead of hoping generic ranking is fresh: when a tool exposes categories, use ['news','general'] for current reporting and ['general'] for official/reference pages; when a tool exposes time_range, use day/month/year for freshness-sensitive queries.\n"
            "4. Query syntax: exact phrases in quotes are useful for titles or specific bugs; site:official-domain can be useful for official/source-of-record checks; OR can broaden equivalent terms. Do not overload queries with too many operators, and do not use negative filters unless a result set is clearly polluted.\n"
            "5. Source quality: official/platform/developer/publisher pages, press rooms, creator/showrunner/developer interviews, reputable trades/news, and structured reference databases outrank fan/social/forum/SEO calendar pages. Social/forum pages can reveal rumours but cannot confirm them alone.\n"
            "6. Evidence quality: search snippets are leads, not facts. Prefer fetched pages. If a page cannot be fetched, either fetch another source or explicitly treat the snippet as unverified.\n"
            "7. Freshness: check source publication/update dates when available. A high-ranked old article may be stale. For current questions, prefer recent dated sources or explicitly say that the available evidence is stale/undated.\n"
            "8. Negative claims: do not say 'no official word', 'nothing found', or 'no rumours' unless you actually checked suitable current official/reference/trade sources and the result set was not degraded.\n"
            "9. Conflict handling: if sources disagree, state the conflict and rank the sources instead of smoothing disagreement into a single confident answer.\n"
            "10. Schedules: never manufacture a schedule by extrapolating weekly cadence unless a source explicitly states the cadence and gives enough dates. Use deterministic date comparison for tense.\n"
            "11. Downloads/tracking: public web evidence can trigger tracking/watch setup or further category/download searches, but it never directly authorizes queueing a download. Use category/download tools for availability and queue decisions."
        )

    @staticmethod
    def planner_rules() -> str:
        """Return compact rules for an LLM that outputs search-plan JSON."""
        return (
            "Research planning rules:\n"
            "- Treat caller intent labels as hints, not enums. Infer the actual research job from the user's wording, item identity, category contract, and current date.\n"
            "- Preserve the user's focus exactly in at least one query. If they mention a season, episode, version, bug, platform, creator, interview, or rumour, include that focus.\n"
            "- Produce 2 to 4 diverse searches: one source-of-record/official/reference search and one recent reporting/news search for current topics. Add a social/forum search only when the user specifically asks about public chatter or rumours.\n"
            "- Use categories ['news'] for current reporting and ['general'] for official/reference sources. Use time_range='day', 'month', or 'year' for fresh/current topics; leave it blank only for timeless/background facts.\n"
            "- Include current-year or recency terms when freshness matters, but do not rely only on the year; also use intent terms like confirmed, renewed, production, filming, interview, official, press, patch notes, changelog, roadmap, delay, cancelled, postponed.\n"
            "- Use exact title phrases or site: constraints when they improve precision. Do not overfit to one source family; plan enough diversity to verify or detect conflict.\n"
            "- Require page extraction before facts unless the user only asked for raw links.\n"
            "- Do not answer the user, invent facts, decide downloads, or mutate category items. Plan source discovery only."
        )

    @staticmethod
    def sufficiency_checklist() -> str:
        """Return criteria for deciding whether evidence is enough to answer."""
        return (
            "Evidence sufficiency checklist:\n"
            "- Does at least one fetched page actually mention the requested entity and the requested focus?\n"
            "- For current/future claims, is at least one source dated or clearly current?\n"
            "- For official/confirmed claims, is there an official/source-of-record page or reputable reporting that quotes/links the source?\n"
            "- For rumours/chatter, is the answer framed as unconfirmed and separated from official facts?\n"
            "- For schedules, does the source explicitly state the date/cadence instead of requiring extrapolation?\n"
            "- If the primary provider failed or fallback was used, is confidence lowered and are negative claims avoided?\n"
            "If any required answer is missing from fetched evidence, search again with a better query or state the limitation."
        )

    @staticmethod
    def category_contract_text(contract: dict[str, Any]) -> str:
        """Extract category-owned prompt text from a web-research contract."""
        if not isinstance(contract, dict):
            return ""
        pieces: list[str] = []
        for key in ("llm_research_guidance", "search_strategy", "source_quality_policy", "freshness_policy", "answer_policy"):
            value = contract.get(key)
            if isinstance(value, str) and value.strip():
                pieces.append(value.strip())
            elif isinstance(value, list):
                clean = [str(item).strip() for item in value if str(item).strip()]
                if clean:
                    pieces.append(f"{key.replace('_', ' ').title()}:\n" + "\n".join(f"- {item}" for item in clean))
        examples = contract.get("query_examples")
        if isinstance(examples, list):
            rows = []
            for item in examples:
                if isinstance(item, dict):
                    rows.append("; ".join(f"{k}={v}" for k, v in item.items() if v))
                elif str(item).strip():
                    rows.append(str(item).strip())
            if rows:
                pieces.append("Category query examples:\n" + "\n".join(f"- {row}" for row in rows))
        return "\n".join(pieces)
