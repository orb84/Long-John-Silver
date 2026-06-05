"""Reusable concise LLM task guidance for LJS.

This module centralizes cross-cutting prompt rules that are not category
semantics.  Categories still own domain rules; generic prompts provide the LLM
with stable operating discipline, scheduling/download guardrails, and compact
instructions suitable for smaller local models.
"""

from __future__ import annotations

from src.core.models import Intent
from src.search.web.research_guidance import WebResearchPromptGuidance


class TaskPromptGuidance:
    """Small reusable prompt blocks for agent, planner, and scheduler prompts."""

    @staticmethod
    def operating_rules() -> str:
        """Return compact rules that apply to every tool-capable task."""
        return (
            "LLM OPERATING RULES:\n"
            "- Use tools for facts/actions; do not answer from memory when tools can verify.\n"
            "- Preserve the user's exact target, qualifiers, language, and follow-up constraints.\n"
            "- Prefer small, purposeful tool chains. Stop and ask when ambiguity affects a side effect.\n"
            "- Do not invent tool names, private IDs, JSON paths, dates, schedules, torrent properties, or source claims.\n"
            "- Treat tool warnings, degraded fallback, and missing evidence as lower confidence, not as facts.\n"
            "- User-facing answers must summarize evidence/results, not raw tool JSON."
        )

    @staticmethod
    def planner_contract() -> str:
        """Return compact advisory-planner rules shared by SEARCH/DOWNLOAD plans."""
        return (
            "Advisory planner rules:\n"
            "- Plans are hints for the live tool-calling agent, not execution authority.\n"
            "- Use only listed tool names and listed parameter names.\n"
            "- Keep plans minimal: one verification/research step plus one discovery/action step when possible.\n"
            "- Do not pre-queue downloads after a fresh search; queue only a previously selected candidate with stable IDs.\n"
            "- Preserve exact user wording in query/objective arguments when research is requested."
        )

    @staticmethod
    def search_task_rules() -> str:
        """Return concise SEARCH intent guidance."""
        return (
            "TASK: Research and report information.\n"
            "- Stable media facts: use metadata_lookup first unless already present in context.\n"
            "- Local/tracked state: use the category context packet or enquire_about_media.\n"
            "- Current public facts/news/rumours/future schedules: use category_web_research for category items, otherwise web_research; metadata-only answers are insufficient.\n"
            "- If metadata is missing/incomplete, continue with web evidence instead of exposing raw failures.\n"
            "- For recurring public updates, use create_web_information_watch, not an ad-hoc reminder.\n"
            "- Cite only tool/source facts from this turn or trusted prior context. If evidence is weak, say so.\n"
            "- Never manufacture a weekly schedule by extrapolation; only state release/episode schedules when a source explicitly supports the cadence and dates."
        )

    @staticmethod
    def download_task_rules() -> str:
        """Return concise DOWNLOAD intent guidance."""
        return (
            "TASK: Find/select torrents or manage existing download queue state.\n"
            "- Queue/status/control requests: call list_downloads first, then manage_downloads or priority tools; do not search torrents.\n"
            "- Fresh media discovery: use category context/enquire_about_media, then search_media_torrents. Use structured category/unit args, not prose-only names.\n"
            "- Untracked requested item: verify with metadata/research first; if the user asks to add/track/follow it, call track_category_item.\n"
            "- Future/next-season download tracking: gather provider/category_web_research evidence, track the item if needed, then create_web_information_watch with allow_download_queueing=true only when the user explicitly asked for future download action.\n"
            "- Candidate choice: queue only by candidate_id/result_set_id when the result is an exact, safe match. Inspect bundles or ask when coverage/language/quality/size/seeders are ambiguous.\n"
            "- Hard filters before preference: requested unit/pack coverage, category language rules, magnet/downloadability, quality/format facets, size/bitrate/storage, and seeders.\n"
            "- Public web evidence never directly authorizes a download; category/download tools must prove release and availability.\n"
            "- Confirm success only from queue tool receipts, not from search results."
        )

    @staticmethod
    def config_task_rules() -> str:
        """Return concise CONFIG guidance."""
        return (
            "TASK: Modify the user's configuration. Includes schedules, providers, categories, or watches.\n"
            "- Read current state with tools before changing it. Confirm tool-reported success only.\n"
            "- Simple reminders/checks: use create_scheduled_task. Public evidence/news/patch/release monitoring: prefer create_web_information_watch.\n"
            "- Category design: use get_category_creation_guide, plan_category_creation, research_category_services, and research_category_download_profile before scaffolding.\n"
            "- Keep category rules category-owned; do not import movie/TV torrent vocabulary into new categories unless researched or user-provided.\n"
            "- Preview scaffolds and apply only after explicit user approval."
        )

    @staticmethod
    def chat_task_rules() -> str:
        """Return concise CHAT guidance."""
        return (
            "TASK: Open-ended conversation.\n"
            "- Be natural and helpful.\n"
            "- For factual media/library questions, use metadata_lookup, enquire_about_media, or web_research unless the answer is already in trusted context.\n"
            "- Do not invent plot, cast, ratings, release dates, local state, or download status.\n"
            "- If the user is brainstorming app/category behavior, discuss design and use read-only planning/research tools when useful."
        )

    @staticmethod
    def clarify_task_rules() -> str:
        """Return CLARIFY guidance."""
        return (
            "TASK: The user's intent was ambiguous.\n"
            "Ask one targeted clarifying question and offer concrete options."
        )

    @staticmethod
    def scheduled_task_context(task_type: str) -> str:
        """Return wrapper guidance for prompts executed by the scheduler."""
        if task_type == "condition_check":
            prefix = (
                "This is a user-created scheduled condition check. Run the check now using registered tools.\n"
                "Notify only when the condition changed, credible new evidence exists, or the user explicitly requested every report.\n"
                "If nothing meaningful changed, reply exactly LJS_NO_NOTIFICATION.\n"
                "Do not queue downloads unless the stored prompt explicitly asks for queueing/download action and current category/download tools prove availability.\n"
            )
        else:
            prefix = (
                "This is a user-created scheduled assistant task. Execute it now and return a concise report.\n"
                "Use tools for factual claims or app actions; do not rely on memory.\n"
            )
        return "\n".join([
            prefix.strip(),
            WebResearchPromptGuidance.runtime_context(),
            TaskPromptGuidance.operating_rules(),
        ])

    @staticmethod
    def for_intent(intent: Intent) -> str:
        """Return task guidance for an intent."""
        if intent == Intent.SEARCH:
            return TaskPromptGuidance.search_task_rules()
        if intent == Intent.DOWNLOAD:
            return TaskPromptGuidance.download_task_rules()
        if intent == Intent.CONFIG:
            return TaskPromptGuidance.config_task_rules()
        if intent == Intent.CHAT:
            return TaskPromptGuidance.chat_task_rules()
        if intent == Intent.CLARIFY:
            return TaskPromptGuidance.clarify_task_rules()
        return "TASK: Help the user with their request."
