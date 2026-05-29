"""
Prompt builder for LJS.

Constructs system prompts for the AI assistant based on intent,
persona, user preferences, and behavioral context. The persona
(loaded from config/personas/) always comes first and defines
the character. Intent-specific instructions are appended as
persona-neutral task guidance — they describe what tools to use
and what output is expected, never how to speak.
"""

from datetime import datetime

from loguru import logger
from src.ai.persona_context import PersonaContext
from src.core.models import Intent
from src.utils.torrent_knowledge import get_compact_quality_guide


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
        now = datetime.now().astimezone()
        return (
            f"CURRENT RUNTIME DATETIME: {now.isoformat(timespec='seconds')}\n"
            "DATE FACT RULE: When a tool/source gives an air date, release date, "
            "publication date, or schedule date, compare it against the current "
            "runtime date before choosing tense. Future dates must be described "
            "with future wording such as 'is scheduled to air/release'; do not "
            "write 'aired', 'premiered', or 'released' for future dates. If the "
            "date is missing or sources disagree, say that confidence is limited "
            "instead of inventing a date. Use compare_date_to_now when a concrete "
            "date needs deterministic comparison."
        )

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

        if preferences_summary:
            parts.append(f"USER PREFERENCES:\n{preferences_summary}")
        if behavior_context:
            parts.append(f"USER HISTORY:\n{behavior_context}")
        if category_guidance:
            parts.append("CATEGORY-SCOPED GUIDANCE:\n" + category_guidance)
        if platform_guidance:
            parts.append(platform_guidance)

        parts.append(self._task_guidance(intent, active_category_id=active_category_id))
        return "\n\n".join(parts)

    def _task_guidance(self, intent: Intent, active_category_id: str | None = None) -> str:
        """Return persona-neutral task guidance for an intent."""

        if intent == Intent.SEARCH:
            return (
                "TASK: Research and report information.\n"
                "You MUST use your research tools to gather facts — never "
                "rely on your own knowledge or training data.\n"
                "CRITICAL: If the required category metadata, episode lists, "
                "or review summaries are already present in the preceding conversation history, DO NOT "
                "call tools again to fetch the same data. Reuse the existing conversation context.\n"
                "1. For factual media questions (cast, actors, creators, seasons, episodes, air dates, ratings, IDs, artwork), call `metadata_lookup` first with media_type tv/movie/auto unless the answer is already present in context.\n"
                "2. Use the CATEGORY LIBRARY CONTEXT PACKET when present, or `enquire_about_media` for local library/tracked-item state when the packet is absent/stale. Do not call or invent category-specific read-only micro-tools.\n"
                "3. If metadata_lookup returns ok=false, no service, no result, or incomplete cast/season facts, immediately fall back to web_search/read_web_page instead of reporting the tool failure to the user.\n"
                "4. Optionally, use generic review research for critic scores and audience consensus if not already in context.\n"
                "5. If the user has preferences, compare the media's attributes against those preferences and give an opinionated recommendation.\n"
                "6. If tools return no results, say so honestly. Do not invent facts.\n"
                "7. For episode/air-date answers, cite only tool/source facts actually returned in this turn or already present in conversation context. If metadata_lookup returns only season-level data, do not infer a specific episode date from it. "
                "8. For upcoming/future episode dates, prefer official network/streamer pages over community calendars when they disagree. If sources disagree by one day, say so and use the official regional source for the user's locale when available. "
                "9. Follow-up corrections are binding constraints. If the user says a variant of 'released', 'already out', or 'not future' after a latest/newest media answer, exclude future/scheduled titles and answer with the latest title whose release date is not in the future; use compare_date_to_now on candidate dates when needed."
            )

        if intent == Intent.DOWNLOAD:
            return (
                "TASK: Find/select torrents or manage existing download queue state.\n"
                "CATEGORY-DESIGN SAFETY RULE: If the preceding conversation is about creating/configuring a category and the user mentions torrents/downloads only to define that category's capabilities, treat it as category design rather than an immediate download request. Do not search or queue anything; research the new category's own release/download conventions before naming quality rules.\n"
                "CRITICAL DOWNLOAD-CONTROL RULE: If the user asks for a download report, queue status, priority change, pause, resume, cancel, stop, or move/reorder operation, do not search torrents. First call `list_downloads` to inspect current state, then call `manage_downloads` for the requested existing-download mutation. If the user asks what library files are being shared/seeding under Fair Share mode, call `list_library_shares`. For cancellation or any confirmation_required result, ask the user to confirm before retrying with confirmed=true.\n"
                "CRITICAL: If the media is already verified (via category metadata or research evidence in the preceding conversation history), "
                "DO NOT call the verification tools again. Only execute search_torrents if you need to perform a new search.\n"
                "1. TOOL PHILOSOPHY: Use a small generic chain. Read the CATEGORY LIBRARY CONTEXT PACKET first; call `enquire_about_media` only if local/category state is absent or stale; call `search_media_torrents` for category download discovery; it may return both torrent candidates and a companion_soulseek block from a parallel Soulseek search. Also call `search_soulseek` directly when the user explicitly asks for Soulseek or when you need a narrower Soulseek-only retry. For Music source strategy: prefer Soulseek first for single tracks, singles, EPs, and normal albums when configured/ready, but prefer torrents first for full discographies, big catalog packs, and large bundle requests. When searching Soulseek, use concise artist/title text only; never include explanatory words such as 'album', 'track', 'song', 'download', or torrent-style quality filler unless the user made that word part of the title. For Soulseek folder candidates, treat a remote folder name that resembles the requested item as useful evidence that the folder may contain the whole release plus artwork/cue/log sidecars; inspect the returned filenames and active category guidance before deciding whether to enqueue the whole filenames array or only specific files. Call `inspect_torrent_candidate` when a bundle/full-series torrent candidate needs file-list or coverage clarification; call `queue_download` only for torrent candidates and `enqueue_soulseek_download` only for Soulseek candidates after choosing a safe candidate; for Soulseek prefer candidate_id/result_set_id over raw filenames.\n"
                "2. CATEGORY OWNERSHIP: The active category owns the meaning of local units, metadata, release state, bundle/pack rules, language relevance, format facets, and special search terms. Generic prompt code must not hardcode TV/movie/book/music/game decision trees. Follow the active category profile for catalogue/bundle wording, soundtrack naming, language use, source type, narrator/edition/translation, format, bitrate, and quality rules.\n"
                "3. If a matched tracked item is present, use its exact key/name and configured language unless the user explicitly overrides them. Also inspect existing local unit languages/audio_languages: prefer continuity with the configured/existing language and never silently queue a different-language release.\n"
                "5. If the requested item is not tracked, use metadata_lookup/enquire_about_media/research before acting; do not invent metadata from memory.\n"
                "6. For category units (season/episode/chapter/disc/track), put numbers in dedicated tool arguments; do not hide localized unit phrases inside a free-form name field.\n"
                "7. For episodic categories, use category context/provider metadata to avoid future unaired units. For missing/multiple units, search exact units as well as safe bundles/packs returned by the category search hook. If the user asks to prefer a full category-owned bundle/pack, pass search_scope=bundle_preferred to search_media_torrents; this means bundle-first, then category-owned fallback to individual units if no acceptable bundle exists. The search tool returns a compact candidate_picker workspace, result_handle, next_actions, and cached candidate IDs; choose by candidate_id/result_set_id and do not ask the model to ingest raw torrent payloads or invent JSON paths into a result.\n"
                "8. Evaluate results against the user's hard preferences: category-owned language relevance, exact unit/pack coverage, magnet availability, format/bitrate/quality facets that actually belong to the category, seeders, codecs, and release groups. If storage context says WARNING or CRITICAL, call check_storage_capacity with the candidate's estimated size before claiming it cannot fit; do not do free-space arithmetic in prose.\n"
                "9. DECISION RULE: Evaluate the search results returned by search_media_torrents or search_torrents:\n"
                "   - Clear Choice: If there is a single, clear-cut best candidate (exact media unit or safe bundle containing the target units, required/preferred language or acceptable multi-audio, acceptable resolution/size, queueable magnet, good seeders), call `queue_download` with `candidate_id`/`candidate_ids` and `result_set_id`. Confirm success only if the tool result says status=`queued` or returns queued receipts with download IDs; otherwise report the error.\n"
                "   - Ambiguity / Multiple Options: If there are multiple suitable candidates with category-relevant tradeoffs (for example format, edition, bitrate, narrator, translation, coverage, size, or seeders), or if a bundle/pack candidate may not contain the requested units and has not been inspected, call `inspect_torrent_candidate` or ask; DO NOT call `queue_download`. Instead, halt tool execution, present the top 2-3 candidates in a clean markdown list or table (showing index, title, size, and seeders), and conversationally ask the user to select which option they would prefer.\n"
                "   - No Matches: If no acceptable pack was found for a pack-preferred request, say that clearly and then present/queue the category-owned individual-unit fallback only if those candidates are acceptable. If neither path works, report honestly and ask the user how to proceed.\n"
                "   - No Matches: If no acceptable torrent candidates were found and the request fits configured Soulseek categories, call `search_soulseek` before giving up. Soulseek candidates are single-user files; consider queue length/free slot/locked status as well as format, folder context, track count, and size. Never pass a Soulseek result to `queue_download`; use `enqueue_soulseek_download` with `candidate_id` and `result_set_id` from `soulseek_candidate_picker` after user confirmation. If Soulseek is not configured, report that as a recoverable fallback path, not a fatal error.\n\n"
                + self._download_quality_guide(active_category_id)
            )

        if intent == Intent.CONFIG:
            return (
                "TASK: Modify the user's configuration.\n"
                "Available actions: configure category properties, add/remove/pause/resume category items, "
                "create/list/remove scheduled reminders and assistant checks, list library files, check library status, and create new category scaffolds.\n"
                "For reminders or future checks, call create_scheduled_task: use task_type=reminder for simple reminders, task_type=condition_check for future torrent/search existence checks, schedule_type=one_off for 'in 7 days' or 'in 3 weeks', and delay_minutes or due_at to set the first run time. Do not pretend a reminder was scheduled unless the tool returns ok=true. "
                "For new categories, first call get_category_creation_guide and plan_category_creation. "
                "Ask targeted questions when scope, item types, downloads, units, metadata, or taste dimensions are unclear; do not rush to scaffolding from a vague category name. "
                "Before previewing a metadata-capable category, call research_category_services to look for current provider/API/database options comparable to TMDB for that domain, then discuss credible provider tradeoffs with the user. "
                "If the category is downloadable, also call research_category_download_profile and synthesize a category-specific download_profile from researched release/download conventions plus user requirements. "
                "Keep the user's requested scope intact: Audio Books means audiobooks unless the user explicitly broadens it to ebooks/books. "
                "Do not inherit generic movie/TV torrent vocabulary or quality rules by default; only include release terms, file formats, units, and reject rules that are relevant to the new category and supported by research or user instruction. "
                "Only after the design, providers, and download-profile choices are clear, call preview_category_scaffold with a declarative spec. "
                "Show the user the preview and only call apply_category_scaffold after explicit approval. "
                "Do not write arbitrary code or hard-code new category behavior into generic app layers. "
                "Use the appropriate tools to read current state before making "
                "changes. Confirm every change with the user."
            )

        if intent == Intent.CHAT:
            return (
                "TASK: Open-ended conversation.\n"
                "Respond naturally and warmly. Do not demand precise commands "
                "or scold the user — this is casual interaction.\n\n"
                "CRITICAL: If the user asks about a specific category item, film, series, "
                "actor, episode, or any factual media question, you MUST call "
                "metadata_lookup or `enquire_about_media` for local/tracked state BEFORE answering. Prefer metadata_lookup before web_search for media facts, but if metadata_lookup cannot answer, fall back to web_search/read_web_page rather than surfacing a raw tool error. "
                "HOWEVER, if the required category tracking status, metadata details, unit lists, "
                "or library files are ALREADY present in the preceding conversation history, "
                "DO NOT call the tools again — reuse the existing context to keep the "
                "conversation fast, efficient, and natural. Never invent plot summaries, "
                "ratings, cast lists, release dates, or any factual details — only report what "
                "tools actually return (or what is already in context). If tools find nothing, "
                "admit it honestly. If the user is brainstorming a new category, use get_category_creation_guide, "
                "plan_category_creation, research_category_services, and research_category_download_profile as read-only aids, then ask concise design questions instead of pretending a scaffold is ready. "
                "When discussing category downloads, keep download rules category-specific: search or research the domain's own release conventions before naming formats, tags, quality facets, or reject rules."
            )

        if intent == Intent.CLARIFY:
            return (
                "TASK: The user's intent was ambiguous.\n"
                "Ask a clarifying question. Be helpful, not demanding. "
                "Suggest specific options they might mean."
            )

        return "TASK: Help the user with their request."

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
