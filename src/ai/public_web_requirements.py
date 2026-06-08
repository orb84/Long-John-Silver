"""Source-sufficiency helpers for public web evidence requirements.

This module does not route user intent.  Intent remains LLM-owned.  These
helpers only decide whether an already-routed SEARCH/DOWNLOAD media research
turn requires public web evidence in addition to structured metadata.
"""

from __future__ import annotations


class PublicWebEvidencePolicy:
    """Identify cases where provider metadata is not a sufficient source.

    Structured metadata services are authoritative for stable catalogue facts,
    but they do not answer live/public questions such as rumours, news,
    production reports, patch notes, or public discussion.  When a turn is
    already inside an evidence-gathering flow, this policy marks those cases so
    the tool loop keeps source discovery available and the advisory plan
    includes a public-web evidence step.
    """

    _PUBLIC_SOURCE_TERMS = (
        "rumor", "rumour", "rumors", "rumours", "rumeur", "rumore", "rumori",
        "voci", "indiscrezion", "leak", "leaks", "leaked", "trapelat",
        "news", "notizia", "notizie", "article", "articolo", "articoli",
        "report", "reports", "reported", "riport", "interview", "intervista",
        "press", "statement", "announc", "annuncio", "comunicato",
        "renewal", "renewed", "renew", "rinnov", "season order", "greenlit",
        "cancelled", "canceled", "cancell", "annullat", "delay", "delayed",
        "postponed", "posticip", "production", "produzione", "filming", "riprese",
        "casting", "cast", "patch note", "patch notes", "changelog", "roadmap",
        "bugfix", "bug fix", "hotfix", "discussed", "discussione", "forum",
        # Future/current catalogue questions are public-current too.  Metadata
        # may know already-released seasons but cannot prove absence/presence of
        # not-yet-released seasons, production, or schedule updates.
        "next season", "upcoming season", "future season", "new season",
        "prossima stagione", "nuova stagione", "stagione futura",
        "when is season", "when will season", "quando esce", "quando uscir",
        "not out yet", "not released yet", "non ancora usc",
    )

    _RUMOR_TERMS = (
        "rumor", "rumour", "rumors", "rumours", "rumore", "rumori", "voci",
        "indiscrezion", "leak", "leaks", "leaked", "trapelat",
    )

    _DELAY_TERMS = (
        "delay", "delayed", "postponed", "posticip", "rescheduled", "rinviat",
    )

    _PATCH_TERMS = (
        "patch note", "patch notes", "changelog", "roadmap", "bugfix", "bug fix", "hotfix",
    )

    @classmethod
    def requires_public_web_evidence(cls, *texts: str | None) -> bool:
        """Return True when the current source request needs public web evidence."""
        text = cls._fold_text(*texts)
        return any(term in text for term in cls._PUBLIC_SOURCE_TERMS)

    @classmethod
    def category_research_intent(cls, *texts: str | None) -> str:
        """Return a semantic planning intent, not a category enum.

        Exact domain mapping belongs to the LLM/category research planner.  This
        source-sufficiency helper only marks that metadata is insufficient and
        asks the category web-research tool to plan from the user's wording.
        """
        _ = texts
        return "llm_planned_public_research"

    @classmethod
    def web_research_query(cls, title: str, user_prompt: str) -> str:
        """Build a title-bound public web research query without category semantics."""
        title_part = str(title or "").strip()
        prompt_part = str(user_prompt or "").strip()
        if title_part and title_part.casefold() not in prompt_part.casefold():
            return f"{title_part} {prompt_part}".strip()
        return prompt_part or title_part

    @staticmethod
    def _fold_text(*texts: str | None) -> str:
        return " ".join(str(text or "") for text in texts).casefold()
