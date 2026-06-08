"""Runtime date/time context for every LJS LLM prompt.

The application has many prompt builders: chat/system prompts, planners,
routers, candidate adjudicators, summarizers, and legacy provider calls.  This
utility provides one compact, category-neutral block so every model call can
anchor relative dates consistently without duplicating date wording across the
codebase.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class RuntimePromptContext:
    """Build and inject current runtime date/time guidance for LLM calls."""

    _MARKER = 'CURRENT RUNTIME DATETIME:'

    @classmethod
    def payload(cls) -> dict[str, Any]:
        """Return the current local runtime date/time as structured context."""
        now = datetime.now().astimezone()
        return {
            'current_datetime': now.isoformat(timespec='seconds'),
            'current_date': now.date().isoformat(),
            'current_year': now.year,
            'timezone': str(now.tzinfo or ''),
            'rule': (
                'Compare every air/release/source/schedule date to current_date before saying '
                'upcoming, future, aired, released, current, latest, recent, or stale.'
            ),
        }

    @classmethod
    def llm_guidance_block(cls) -> str:
        """Return concise instructions for using the runtime date/time."""
        runtime = cls.payload()
        timezone = runtime.get('timezone') or 'local runtime timezone'
        return (
            f"CURRENT RUNTIME DATETIME: {runtime['current_datetime']}\n"
            f"CURRENT DATE: {runtime['current_date']}\n"
            f"CURRENT YEAR: {runtime['current_year']}\n"
            f"CURRENT TIMEZONE: {timezone}\n"
            "RUNTIME DATE/TIME RULES:\n"
            "- Anchor today, tomorrow, yesterday, next, upcoming, future, latest, "
            "current, and recent to this runtime date/time.\n"
            "- Compare every air date, release date, publication/update date, "
            "source date, schedule date, and deadline against CURRENT DATE before "
            "choosing past/future/current wording.\n"
            "- A source that calls an old year/date upcoming or future is stale "
            "background, not current evidence.\n"
            "- For current/future public claims, prefer fresh dated evidence; lower "
            "confidence for stale, undated, degraded, or snippet-only sources.\n"
            "- Use deterministic date-comparison tools when available instead of guessing relative tense."
        )

    @classmethod
    def has_runtime_guidance(cls, messages: list[dict[str, Any]]) -> bool:
        """Return whether a message list already carries runtime date guidance."""
        for message in messages:
            content = message.get('content')
            if isinstance(content, str) and cls._MARKER in content:
                return True
        return False

    @classmethod
    def ensure_messages(cls, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return messages with exactly one runtime date/time guidance block.

        Existing system prompts stay first and protected by token budgeting.  If
        a prompt has no system message, the guidance is inserted as a compact
        system message before the user prompt.  When callers already included
        the shared runtime block, the original messages are copied unchanged.
        """
        copied = [dict(message) for message in messages]
        if cls.has_runtime_guidance(copied):
            return copied
        guidance = cls.llm_guidance_block()
        for index, message in enumerate(copied):
            if message.get('role') != 'system':
                continue
            content = str(message.get('content') or '').strip()
            copied[index]['content'] = f'{content}\n\n{guidance}' if content else guidance
            return copied
        return [{'role': 'system', 'content': guidance}, *copied]
