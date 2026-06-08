"""Download context freshness helpers.

These helpers are deliberately category-neutral.  They do not decide what a TV
show, episode, book, or game is; they only decide whether stale candidate
handles are safe to show to the LLM for the current turn.
"""

from __future__ import annotations

import re
from src.core.models import Intent


class DownloadContextPolicy:
    """Classify whether previous candidate context should be used.

    Pending candidate/result-set context is powerful for follow-ups like
    "queue the second one".  It is dangerous for fresh acquisition requests like
    "grab <title> in Italian": the model may answer from stale candidates instead
    of running a new search.  Keep this policy intentionally small and generic.
    """

    _ACQUIRE_RE = re.compile(
        r"\b(download|grab|get|fetch|find|search|look\s+for|queue|add|scarica|prendi|cerca|trova|metti)\b",
        re.I,
    )
    _FOLLOWUP_RE = re.compile(
        r"\b(that|this|those|these|it|one|ones|first|second|third|option|candidate|result|pack|same|yes|ok|okay|queue\s+it|download\s+it|quello|questa|questo|prima|seconda|terza)\b",
        re.I,
    )
    _CANDIDATE_ID_RE = re.compile(r"\b[a-f0-9]{12,24}\b", re.I)
    _QUALITY_SELECTOR_RE = re.compile(
        r"\b(2160p?|1080p?|720p?|480p?|4k|uhd|hdr|dv|x264|x265|h264|h265|hevc|av1|remux|web[- ]?dl|webrip|bluray|bitrate|kbps|mbps)\b",
        re.I,
    )

    @classmethod
    def should_suppress_pending_candidates(cls, user_prompt: str | None, intent: Intent | str | None = None) -> bool:
        """Return true when old candidate context is risky for this turn."""
        text = str(user_prompt or "").strip()
        if not text:
            return False
        intent_value = intent.value if isinstance(intent, Intent) else str(intent or "").upper()
        if intent_value and intent_value not in {"DOWNLOAD", "SEARCH", "CONFIG", "NONE"}:
            return False
        if cls._is_candidate_followup(text):
            return False
        if not cls._ACQUIRE_RE.search(text):
            return False
        # Fresh media requests usually include a concrete target, not just a bare
        # acknowledgement.  Do not require English title casing; Italian/lowercase
        # titles still have several content words after the action verb.
        content_words = re.findall(r"[\wÀ-ÿ']+", text, flags=re.UNICODE)
        return len(content_words) >= 4

    @classmethod
    def should_start_fresh_goal(cls, user_prompt: str | None, intent: Intent | str | None = None) -> bool:
        """Return true when active goal result sets should not be inherited."""
        intent_value = intent.value if isinstance(intent, Intent) else str(intent or "").upper()
        if intent_value != "DOWNLOAD":
            return False
        return cls.should_suppress_pending_candidates(user_prompt, intent)

    @classmethod
    def download_turn_requires_tool(cls, task: str | None, allowed_tool_names: set[str] | None) -> bool:
        """Return true when a DOWNLOAD turn must not answer before a tool call."""
        if str(task or "").lower() != "download":
            return False
        tools = set(allowed_tool_names or set())
        return bool(tools.intersection({
            "search_media_torrents",
            "queue_download",
            "list_downloads",
            "manage_downloads",
            "inspect_torrent_candidate",
        }))

    @classmethod
    def reprompt_after_toolless_download_answer(cls, user_prompt: str | None) -> str:
        """Instruction injected when the model tries to answer a download task without tools."""
        return (
            "The previous assistant text was not sent because this DOWNLOAD turn needs tool evidence. "
            "Do not answer from pending candidates or memory. If the user is making a fresh media request, "
            "call search_media_torrents with the literal title and explicit constraints. If the user is selecting "
            "a previous result, call the appropriate queue/inspect tool using result_set_id and candidate_id. "
            f"Current user request: {str(user_prompt or '').strip()}"
        )

    @classmethod
    def _is_candidate_followup(cls, text: str) -> bool:
        if cls._CANDIDATE_ID_RE.search(text):
            return True
        words = re.findall(r"[\wÀ-ÿ']+", text, flags=re.UNICODE)
        if len(words) <= 5 and cls._FOLLOWUP_RE.search(text):
            return True
        # Short quality/resolution refinements such as "get the 720 version"
        # are selections against the visible candidate workspace, not fresh
        # media discovery.  This test is intentionally format/token based
        # rather than title/category specific.
        if len(words) <= 7 and cls._QUALITY_SELECTOR_RE.search(text):
            return True
        # Longer messages can still be follow-ups if they explicitly mention a
        # candidate/result handle rather than a fresh title request.
        return bool(re.search(r"\b(candidate|result_set|result set|candidate_id|option\s+\d+)\b", text, re.I))
