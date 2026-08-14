"""Tracked-item language enforcement for advisory agent plans.

The model may propose a media language, but an ungrounded proposal must not
silently override a durable tracked-item preference.  This policy keeps the
configured language unless the current request contains evidence of an
explicit override or the plan marks that provenance directly.
"""

from __future__ import annotations

import re

from src.core.models import AgentPlan, Intent


class TrackedLanguagePlanPolicy:
    """Apply tracked language defaults without trusting invented plan values."""

    _LANGUAGE_TOOLS = {
        "search_torrents",
        "search_media_torrents",
        "queue_download",
        "queue_media_download",
    }
    _SEARCH_TOOLS = {"search_torrents", "search_media_torrents"}
    _EXPLICIT_FLAGS = {"language_is_explicit", "explicit_language"}

    @classmethod
    def apply(
        cls,
        plan: AgentPlan,
        *,
        configured_language: str,
        user_prompt: str,
        intent: Intent,
    ) -> None:
        """Bind the configured language unless current-request evidence overrides it."""
        if intent is not Intent.DOWNLOAD or not configured_language.strip():
            return
        if cls._has_explicit_override(plan, user_prompt):
            return
        plan.constraints["language"] = configured_language
        plan.constraints["language_is_explicit"] = False
        for step in plan.steps:
            if not isinstance(step.arguments, dict) or step.tool_name not in cls._LANGUAGE_TOOLS:
                continue
            step.arguments["language"] = configured_language
            if step.tool_name in cls._SEARCH_TOOLS:
                step.arguments["language_is_explicit"] = False

    @classmethod
    def _has_explicit_override(cls, plan: AgentPlan, user_prompt: str) -> bool:
        """Return whether a non-default plan language is grounded in this request."""
        if any(bool(plan.constraints.get(flag)) for flag in cls._EXPLICIT_FLAGS):
            return True
        proposed = str(plan.constraints.get("language") or "").strip()
        for step in plan.steps:
            if not isinstance(step.arguments, dict) or step.tool_name not in cls._LANGUAGE_TOOLS:
                continue
            if any(bool(step.arguments.get(flag)) for flag in cls._EXPLICIT_FLAGS):
                return True
            if not proposed and step.arguments.get("language"):
                proposed = str(step.arguments["language"]).strip()
        if not proposed:
            return False
        return re.search(rf"(?<!\w){re.escape(proposed)}(?!\w)", user_prompt, re.IGNORECASE) is not None
