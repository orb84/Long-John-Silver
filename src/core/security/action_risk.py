"""
Action risk classification for LJS category and system actions.

This module keeps risk decisions outside prompts so assistant text cannot lower
runtime safety requirements.
"""

from __future__ import annotations

from src.core.models import CategoryActionDeclaration


class ActionRiskPolicy:
    """Classifies category action declarations by runtime risk."""

    def requires_confirmation(self, declaration: CategoryActionDeclaration) -> bool:
        """Return whether a declared action must use a two-phase confirmation."""
        return declaration.requires_confirmation or declaration.destructive or declaration.risk_level == "destructive"

    def risk_label(self, declaration: CategoryActionDeclaration) -> str:
        """Return the effective risk label for an action declaration."""
        if declaration.destructive:
            return "destructive"
        return declaration.risk_level
