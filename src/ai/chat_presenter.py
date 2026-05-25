"""Persona-aware deterministic chat messages for LJS.

Most final assistant text is produced by the LLM after receiving the active
persona prompt.  Some messages are deterministic though: progress pings from
long-running chat turns and direct queue/plan receipts.  This presenter keeps
those messages useful and in-character instead of leaking sterile tool output
into the Captain's chat window.
"""

from __future__ import annotations

from typing import Any

from src.ai.persona_context import PersonaContext
from src.ai.language import detect_user_language_label


class AgentChatPresenter:
    """Format deterministic non-error chat messages in the active persona.

    The default persona is Long John Silver: warm, direct, competent, lightly
    nautical.  Methods in this presenter intentionally preserve concrete
    operational details while adding just enough voice to feel like the agent,
    not a raw backend receipt.
    """

    def __init__(self, persona_name: str = "default") -> None:
        """Create a presenter bound to the configured persona name."""
        self._persona = PersonaContext(persona_name)

    def progress(self, user_prompt: str, tick: int = 0) -> str:
        """Return a short persona-styled progress update for a long chat turn.

        These messages deliberately avoid hard-coded operational claims such as
        "checking torrent language" unless the backend is actually reporting a
        concrete phase elsewhere.  They are just in-character acknowledgements
        that the turn is still running, so they remain appropriate for weather,
        metadata, downloads, library questions, and future tools.
        """
        language = detect_user_language_label(user_prompt)
        if language == "Italian":
            messages = [
                "Ricevuto, Capitano — controllo le rotte giuste prima di rispondere.",
                "Sto ancora verificando le fonti utili, Capitano.",
                "Ancora un momento: meglio una risposta solida che legno marcio.",
                "Sto chiudendo gli ultimi controlli prima di riferire.",
                "La ricerca è ancora in corso; tengo il timone saldo.",
                "Ho quasi finito di separare il bottino buono dai rottami.",
            ]
        else:
            messages = [
                "Aye Captain — I’m checking the right charts before I answer.",
                "Still working through the useful evidence, Captain.",
                "One more moment; I’d rather be right than toss you driftwood.",
                "I’m tying off the last checks before I report back.",
                "The search is still running; I’m keeping the helm steady.",
                "Nearly done sorting the good cargo from the driftwood.",
            ]
        return messages[tick % len(messages)]

    def batch_queue_result(
        self,
        *,
        item_name: str | None,
        queued: list[dict[str, Any]],
        failed: list[dict[str, Any]],
        fallback_count: int = 0,
    ) -> str:
        """Return a persona-styled summary for automatic batch queueing.

        Args:
            item_name: Media item name from the result set/recommendation.
            queued: Queue receipts returned by ``queue_download``.
            failed: Partial failure receipts returned by ``queue_download``.
            fallback_count: Number of queued receipts that used fallback links.
        """
        queued_units = [self._receipt_label(entry) for entry in queued]
        failed_units = [self._receipt_label(entry) for entry in failed]
        target = f" for **{item_name}**" if item_name else ""

        if queued_units:
            lines = [
                f"Aye Captain — I found the cargo{target} and put it on the download manifest.",
                "",
                f"**Queued now:** {', '.join(queued_units)}",
            ]
            picked_titles = self._picked_titles(queued)
            if picked_titles:
                lines.extend(["", "**Picked releases:**"])
                lines.extend(f"- {title}" for title in picked_titles)
            lines.extend(["", "The downloader has the wheel now; those files are being pulled down."])
        else:
            lines = [
                f"Captain, I found candidates{target}, but none made it safely onto the download manifest.",
            ]

        if failed_units:
            lines.extend([
                "",
                f"**Not queued:** {', '.join(failed_units)}",
                "I did **not** mark those as queued. The link or candidate failed before the downloader accepted it.",
            ])
        if fallback_count:
            noun = "candidate" if fallback_count == 1 else "candidates"
            lines.extend([
                "",
                f"I used **{fallback_count} alternate {noun}** after dead or expired links. Rotten planks, avoided.",
            ])
        return "\n".join(lines)

    @classmethod
    def _receipt_label(cls, entry: dict[str, Any]) -> str:
        season = entry.get("season")
        episode = entry.get("episode")
        if season is not None and episode is not None:
            try:
                return f"S{int(season):02d}E{int(episode):02d}"
            except Exception:
                return f"S{season}E{episode}"
        title = str(entry.get("title") or entry.get("name") or "").strip()
        return title[:80] if title else "one item"

    @staticmethod
    def _picked_titles(queued: list[dict[str, Any]]) -> list[str]:
        titles: list[str] = []
        seen: set[str] = set()
        for entry in queued:
            title = str(entry.get("title") or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            titles.append(title[:160])
            if len(titles) >= 8:
                break
        return titles
