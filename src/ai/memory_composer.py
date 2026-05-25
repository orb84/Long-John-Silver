"""
Prompt memory composer for LJS.

Automatically injects intent-specific application state into the
prompt context so the AI assistant has up-to-date awareness of
active downloads, recent failures, rejected releases, library
state, preferences, and behavioral history — without manual
context-building in the assistant layer.
"""

import json
from typing import Optional

from loguru import logger

from src.core.actions.audit import ActionEventStore
from src.core.downloader import DownloadManager
from src.core.database import Database
from src.core.behavior_tracker import BehaviorTracker
from src.core.preferences import PreferenceManager
from src.core.config import SettingsManager
from src.core.models import ActionSource
from src.core.models import Intent, DownloadStatus
from src.core.suggestion_support import summarize_suggestion_for_agent


class PromptMemoryComposer:
    """Composes intent-specific context from live application state.

    Collects active downloads, recent failures, blacklist entries,
    library tracked items, user preferences, and behavioral profiles
    into a single formatted string for injection into the LLM system
    prompt. Each intent gets only the context relevant to its task.
    """

    def __init__(
        self,
        downloader: Optional[DownloadManager] = None,
        database: Optional[Database] = None,
        behavior_tracker: Optional[BehaviorTracker] = None,
        preference_manager: Optional[PreferenceManager] = None,
        settings_manager: Optional[SettingsManager] = None,
        action_event_store: Optional[ActionEventStore] = None,
        storage_monitor: Optional[object] = None,
        taste_profiler: Optional[object] = None,
    ) -> None:
        """Initialize with optional service dependencies.

        Args:
            downloader: DownloadManager for active download state.
            database: Database for blacklist and failure queries.
            behavior_tracker: BehaviorTracker for behavioral profiles.
            preference_manager: PreferenceManager for user preferences.
            settings_manager: SettingsManager for library/tracked items.
            action_event_store: ActionEventStore for recent UI action queries.
            storage_monitor: StorageMonitor for category-aware free-space context.
            taste_profiler: Category-scoped taste profile builder.
        """
        self._downloader = downloader
        self._db = database
        self._behavior_tracker = behavior_tracker
        self._preference_manager = preference_manager
        self._settings_manager = settings_manager
        self._action_event_store = action_event_store
        self._storage_monitor = storage_monitor
        self._taste_profiler = taste_profiler

    async def compose(
        self, user_id: str | None = None, intent: Intent | None = None,
        category_id: str | None = None,
    ) -> str:
        """Build an intent-specific context string from live state.

        Collects and formats application state relevant to the given
        intent. Returns an empty string when no state is available.

        Args:
            user_id: Optional user ID for per-user state.
            intent: The routed intent; determines which state to include.
            category_id: Optional active category id for category taste memory.

        Returns:
            Formatted context string, or empty string if nothing to report.
        """
        sections: list[str] = []

        intent = intent or Intent.CHAT

        storage_text = self._get_storage_text()
        if storage_text:
            sections.append(storage_text)

        # Active downloads — relevant for SEARCH, DOWNLOAD, CONFIG
        if intent in (Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG):
            downloads_text = await self._get_active_downloads_text()
            if downloads_text:
                sections.append(downloads_text)

        # Recent failures — relevant for DOWNLOAD
        if intent in (Intent.DOWNLOAD, Intent.CONFIG):
            failures_text = await self._get_recent_failures_text()
            if failures_text:
                sections.append(failures_text)

        # Recent UI actions — relevant for DOWNLOAD and CONFIG
        if intent in (Intent.DOWNLOAD, Intent.CONFIG):
            ui_actions_text = await self._get_recent_ui_actions_text()
            if ui_actions_text:
                sections.append(ui_actions_text)

        # Blacklist / rejected releases — relevant for DOWNLOAD
        if intent in (Intent.DOWNLOAD, Intent.SEARCH):
            blacklist_text = await self._get_blacklist_text()
            if blacklist_text:
                sections.append(blacklist_text)

        # Library state — relevant for CONFIG, SEARCH
        if intent in (Intent.CONFIG, Intent.SEARCH, Intent.DOWNLOAD):
            library_text = self._get_library_text()
            if library_text:
                sections.append(library_text)

        # Suggestions are actionable assistant state. Keep the prompt packet
        # compact, but include enough explanation for chat to answer "why did
        # you suggest this?" without inventing reasoning.
        if intent in (Intent.CHAT, Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG):
            suggestions_text = await self._get_suggestions_text(category_id)
            if suggestions_text:
                sections.append(suggestions_text)

        # Combined preferences + behavior — relevant for everything
        pref_text = await self._get_preference_text(user_id)
        if pref_text:
            sections.append(pref_text)

        category_taste_text = await self._get_category_taste_text(user_id, category_id)
        if category_taste_text:
            sections.append(category_taste_text)

        if not sections:
            return ""

        return "\n\n".join(sections)


    def _get_storage_text(self) -> str:
        """Return category-aware disk-space status for every assistant run."""
        if not self._storage_monitor:
            return ""
        try:
            return self._storage_monitor.format_for_llm()
        except Exception as exc:
            logger.debug(f"Failed to build storage context: {exc}")
            return ""

    async def _get_active_downloads_text(self) -> str:
        """Return formatted active download state, or empty string."""
        if not self._downloader:
            return ""
        try:
            active = await self._downloader.get_active_downloads()
        except Exception as exc:
            logger.debug(f"Failed to fetch active downloads: {exc}")
            return ""

        if not active:
            return ""

        lines = ["Active downloads:"]
        for d in active[:10]:
            progress_pct = round(d.progress * 100) if hasattr(d, "progress") and d.progress else 0
            lines.append(
                f"  - {d.item_name or '?'} [{d.status.value}] "
                f"{progress_pct}% | {d.priority.value}"
            )
        if len(active) > 10:
            lines.append(f"  ... and {len(active) - 10} more")
        return "\n".join(lines)

    async def _get_recent_failures_text(self) -> str:
        """Return formatted recent download failures, or empty string."""
        if not self._db:
            return ""
        try:
            recent = await self._db.downloads.get_recent_downloads(limit=10)
        except Exception as exc:
            logger.debug(f"Failed to fetch recent downloads: {exc}")
            return ""

        failures = [d for d in recent if d.status == DownloadStatus.FAILED]
        if not failures:
            return ""

        lines = ["Recent download failures:"]
        for d in failures[:5]:
            lines.append(f"  - {d.item_name or '?'} (id={d.id})")
        return "\n".join(lines)

    async def _get_recent_ui_actions_text(self) -> str:
        """Return formatted recent UI action events, or empty string."""
        if not self._action_event_store:
            return ""
        try:
            events = await self._action_event_store.get_recent(
                limit=10, source=ActionSource.UI,
            )
        except Exception as exc:
            logger.debug(f'Failed to fetch recent UI actions: {exc}')
            return ""

        if not events:
            return ""

        lines = ['Recent UI actions:']
        for event in events[:5]:
            name = event.get('action_name', '?')
            created = event.get('created_at', '?')[:19]
            args = event.get('arguments_json', '{}') or '{}'
            try:
                args_parsed = json.loads(args) if isinstance(args, str) else args
            except (json.JSONDecodeError, TypeError):
                args_parsed = {}
            summary = ', '.join(f'{k}={v}' for k, v in list(args_parsed.items())[:3])
            lines.append(f'  - [{created}] {name}({summary})')
        return '\n'.join(lines)

    async def _get_blacklist_text(self) -> str:
        """Return formatted blacklist / rejected releases, or empty string."""
        if not self._db:
            return ""
        try:
            entries = await self._db.downloads.get_blacklist()
        except Exception as exc:
            logger.debug(f"Failed to fetch blacklist: {exc}")
            return ""

        if not entries:
            return ""

        patterns = [e.pattern for e in entries[:10]]
        lines = ["Rejected / blacklisted patterns:"]
        lines.append(f"  - {', '.join(patterns)}")
        if len(entries) > 10:
            lines[-1] += f" (... and {len(entries) - 10} more)"
        return "\n".join(lines)

    def _get_library_text(self) -> str:
        """Return formatted library state, or empty string."""
        if not self._settings_manager:
            return ""
        try:
            items = self._settings_manager.settings.tracked_items
        except Exception as exc:
            logger.debug(f"Failed to fetch library state: {exc}")
            return ""

        if not items:
            return ""

        enabled = [i for i in items if i.enabled]
        disabled = [i for i in items if not i.enabled]
        lines = ["Library state:"]
        if enabled:
            names = ", ".join(i.key for i in enabled[:15])
            lines.append(f"  Tracked items: {names}")
        if disabled:
            names = ", ".join(i.key for i in disabled[:5])
            lines.append(f"  Paused items: {names}")
        if len(enabled) > 15 or len(disabled) > 5:
            lines[-1] += " ..."
        return "\n".join(lines)



    async def _get_suggestions_text(self, category_id: str | None = None) -> str:
        """Return compact pending suggestion context for the assistant."""
        if not self._db:
            return ""
        try:
            suggestions = await self._db.downloads.get_suggested_actions(
                category_id=category_id or None,
                status="pending",
            )
        except Exception as exc:
            logger.debug(f"Failed to fetch pending suggestions for prompt context: {exc}")
            return ""
        if not suggestions:
            return ""
        lines = ["Pending suggestions:"]
        for row in suggestions[:8]:
            payload = summarize_suggestion_for_agent(row)
            title = payload.get("title") or payload.get("action_type") or "suggestion"
            item = payload.get("item_name") or payload.get("item_id") or "item"
            explanation = (payload.get("explanation") or payload.get("description") or "").strip()
            if len(explanation) > 180:
                explanation = explanation[:177].rstrip() + "…"
            lines.append(f"  - {item}: {title}. Why: {explanation}")
        if len(suggestions) > 8:
            lines.append(f"  ... and {len(suggestions) - 8} more; call suggestions_list for full evidence.")
        return "\n".join(lines)

    async def _get_category_taste_text(self, user_id: str | None, category_id: str | None) -> str:
        """Return category-scoped taste profile text for the active category."""
        if not self._taste_profiler or not category_id:
            return ""
        try:
            profile = None
            if hasattr(self._taste_profiler, "load_category_profile_snapshot"):
                # Normal chat turns should read the compact persisted snapshot.
                # Rebuilding the full profile can touch many metadata/taste rows,
                # so keep it as a fallback for first run or stale installations.
                profile = await self._taste_profiler.load_category_profile_snapshot(category_id=category_id, user_id=user_id)
            if profile is None:
                profile = await self._taste_profiler.build_category_profile(
                    category_id=category_id, user_id=user_id, include_library=True, limit=80,
                )
            return self._taste_profiler.format_category_profile_for_prompt(category_id, profile)
        except Exception as exc:
            logger.debug(f"Failed to build category taste context for {category_id}: {exc}")
            return ""

    async def _get_preference_text(self, user_id: str | None) -> str:
        """Return formatted preferences + behavioral profile, or empty string."""
        if not self._preference_manager:
            return ""
        try:
            summary = await self._preference_manager.get_summary(user_id=user_id)
        except Exception as exc:
            logger.debug(f"Failed to fetch preference summary: {exc}")
            return ""

        return summary
