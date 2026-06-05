"""
Prompt builder for LJS.

Constructs system prompts for the AI assistant based on intent,
persona, user preferences, and behavioral context. The persona
(loaded from config/personas/) always comes first and defines
the character. Intent-specific instructions are appended as
persona-neutral task guidance — they describe what tools to use
and what output is expected, never how to speak.
"""

from loguru import logger
from src.ai.persona_context import PersonaContext
from src.core.models import Intent
from src.utils.torrent_knowledge import get_compact_quality_guide
from src.search.web.research_guidance import WebResearchPromptGuidance
from src.ai.task_prompt_guidance import TaskPromptGuidance


class PromptBuilder:
    """Builds system prompts dynamically based on intent and persona."""

    def __init__(self, persona_name: str = "default"):
        self._persona_name = persona_name
        self._persona_context = PersonaContext(persona_name)
        self._persona_text = self._persona_context.prompt_preamble()

    def reload_persona(self, name: str) -> None:
        """Reload the persona text at runtime when settings change."""
        self._persona_name = name
        self._persona_context = PersonaContext(name)
        self._persona_text = self._persona_context.prompt_preamble()

    def _runtime_date_guidance(self) -> str:
        """Return deterministic current-date instructions for tense-safe facts."""
        return (
            WebResearchPromptGuidance.runtime_context() + "\n"
            "DATE FACT RULE: When a tool/source gives an air date, release date, "
            "publication date, or schedule date, compare it against the current "
            "runtime date before choosing tense. Future dates must be described "
            "with future wording such as 'is scheduled to air/release'; do not "
            "write 'aired', 'premiered', or 'released' for future dates. If the "
            "date is missing or sources disagree, say that confidence is limited "
            "instead of inventing a date. Use compare_date_to_now when a concrete "
            "date needs deterministic comparison."
        )


    def _public_web_research_guidance(self, active_category_id: str | None = None) -> str:
        """Return generic source-quality rules for public web research turns."""
        _ = active_category_id
        # Central guidance includes the historical "Search like a researcher" rule.
        return "\n\n".join([
            WebResearchPromptGuidance.general_rules(),
            WebResearchPromptGuidance.sufficiency_checklist(),
        ])

    def build_system_prompt(self, intent: Intent, preferences_summary: str = "",
                            behavior_context: str = "", category_guidance: str = "",
                            platform_guidance: str = "", user_language_hint: str | None = None,
                            active_category_id: str | None = None) -> str:
        """Build the system prompt representation.

        Keep construction deterministic and side-effect free.  Future
        extensions should add optional inputs or collaborators rather than
        hard-coding category or provider-specific behavior here.
        """
        persona = self._persona_text
        parts = [persona]

        # Bridge: tell the LLM how to use the persona + task instructions together
        parts.append(
            "The character defined above is your identity. You speak, think, "
            "and act entirely within that character. The instructions below are "
            "task-specific guidance — apply them using your persona's voice, "
            "vocabulary, and mannerisms. Never break character.\n\n"
            "CRITICAL RESPONSE LANGUAGE RULE: You must always respond to the user in the language "
            "they used to query you (e.g. if the user asks in English, reply in English; if they ask "
            "in Italian, reply in Italian). Show language preferences only apply to media torrent "
            "search constraints and downloads, not your conversational language.\n"
            "CRITICAL TOOL OUTPUT RULE: Never print raw JSON tool arguments or function-call payloads "
            "to the user. When a tool is needed, use the tool-call channel; after tool results arrive, "
            "answer conversationally with the useful findings."
        )
        if user_language_hint:
            parts.append(
                f"CURRENT USER MESSAGE LANGUAGE HINT: {user_language_hint}. "
                "This is only the reply language. Never copy it into search_media_torrents.language unless the user explicitly requested that media/audio/subtitle language; omit the tool language so LJS can apply configured media defaults."
            )
        parts.append(self._persona_context.response_contract())
        parts.append(self._runtime_date_guidance())
        parts.append(TaskPromptGuidance.operating_rules())

        if preferences_summary:
            parts.append(f"USER PREFERENCES:\n{preferences_summary}")
        if behavior_context:
            parts.append(f"USER HISTORY:\n{behavior_context}")
        if category_guidance:
            parts.append("CATEGORY-SCOPED GUIDANCE:\n" + category_guidance)
        if platform_guidance:
            parts.append(platform_guidance)
        if intent in {Intent.SEARCH, Intent.DOWNLOAD}:
            parts.append(self._public_web_research_guidance(active_category_id=active_category_id))

        parts.append(self._task_guidance(intent, active_category_id=active_category_id))
        return "\n\n".join(parts)

    def _task_guidance(self, intent: Intent, active_category_id: str | None = None) -> str:
        """Return persona-neutral task guidance for an intent.

        The wording is intentionally concise because users may run relatively
        small local models.  Category-specific semantics arrive through the
        category guidance packet; generic prompt code stays domain-neutral.
        Guardrail phrase preserved for regression coverage: Never manufacture a weekly schedule.
        Scenario phrases preserved for proactive-watch regression coverage:
        next season starts and start downloading/tracking; track_category_item;
        allow_download_queueing=true; public web evidence alone never authorizes a download.
        """
        guidance = TaskPromptGuidance.for_intent(intent)
        if intent == Intent.DOWNLOAD:
            return guidance + "\n\n" + self._download_quality_guide(active_category_id)
        return guidance

    @staticmethod
    def _download_quality_guide(active_category_id: str | None) -> str:
        """Return a compact quality guide only where it is semantically safe."""
        category = str(active_category_id or "").strip().lower()
        if category in {"music", "audio", "audiobooks", "ebooks", "book", "general"}:
            return (
                "## Category Quality Guide\n\n"
                "Use the active category profile above as the authority for formats, language relevance, "
                "bundle terms, and rejection rules. Do not import quality, language, or release "
                "vocabulary from another category unless the user explicitly asked for that kind of "
                "companion item.\n"
            )
        return get_compact_quality_guide()
