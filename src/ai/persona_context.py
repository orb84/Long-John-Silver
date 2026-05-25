"""Persona loading and prompt snippets for user-facing assistant text.

Assistant identity now comes from a persona package under ``config/personas``.
A package can include prompt text, display metadata, an avatar, and bounded UI
style hints.  This module remains the small prompt-facing adapter so existing
assistant code does not need to know how assets and package metadata are stored.
"""

from __future__ import annotations

from pathlib import Path

from src.ai.persona_registry import PersonaPackage, PersonaRegistry


class PersonaContext:
    """Load and expose the active assistant persona from disk.

    Use this class when code outside ``PromptBuilder`` needs the same persona
    source, for example deterministic error messages that bypass the LLM.  The
    class is intentionally lightweight and read-only; callers should reload by
    creating a new instance when settings change.
    """

    def __init__(self, persona_name: str = "default", root: Path | None = None) -> None:
        """Create a persona context for the requested persona package.

        Args:
            persona_name: Persona package id, matching a folder under
                ``config/personas``.
            root: Optional project root override for tests or embedded usage.
        """
        self.persona_name = persona_name or "default"
        self._root = root or Path.cwd()
        self._registry = PersonaRegistry(self._root)
        self.package: PersonaPackage = self._registry.load(self.persona_name)
        self.text = self._load_text()

    def prompt_preamble(self) -> str:
        """Return the exact persona text to place at the top of system prompts."""
        return self.text

    def response_contract(self) -> str:
        """Return guidance that binds final replies and errors to the persona.

        The contract is appended to user-facing system prompts.  It gives the
        LLM permission to add persona flavor while preserving clarity, technical
        accuracy, and explicit error labels.
        """
        return (
            "USER-FACING VOICE CONTRACT:\n"
            "- The persona above comes from the active persona package and is the authority for tone.\n"
            "- Apply that voice to every message the user will see, including confirmations, clarifications, and failures.\n"
            "- For errors, always start with a clear marker such as `⚠️ **Error — ...**`.\n"
            "- Keep the useful technical detail: operation, tool/step name when known, and the exact actionable message.\n"
            "- Use persona flavor lightly; never hide the cause behind jokes or theatrics.\n"
            "- Respect any address/title rule from the persona file when it feels natural, especially for bad news or decisions."
        )

    def _load_text(self) -> str:
        """Read active persona text through the registry with safe fallback."""
        return self._registry.prompt_text(self.persona_name)
