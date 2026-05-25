"""
Command execution policy for LJS.

LJS should not expose a generic shell to the assistant. This module allows only
structured argv execution, blocks shell metacharacters, and requires explicit
approval for package-install style commands.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any, Sequence

from src.core.security.audit import SecurityAuditLogger


class CommandPolicyError(RuntimeError):
    """Raised when a command violates LJS command-execution policy."""


class CommandPolicy:
    """Validates and runs subprocess commands without a shell."""

    DANGEROUS_EXECUTABLES = {
        "bash",
        "sh",
        "zsh",
        "fish",
        "powershell",
        "pwsh",
        "cmd",
        "cmd.exe",
        "sudo",
        "su",
        "rm",
        "rmdir",
        "del",
        "erase",
        "format",
        "mkfs",
        "chmod",
        "chown",
        "curl",
        "wget",
    }
    SHELL_METACHARS = (";", "&&", "||", "|", ">", "<", "`", "$(", "${")
    INSTALL_MODULES = {"pip", "playwright"}

    def __init__(self, audit_logger: SecurityAuditLogger | None = None) -> None:
        """Initialize command policy with an optional audit logger."""
        self._audit = audit_logger or SecurityAuditLogger()

    def validate_argv(self, argv: Sequence[str], approved: bool = False, purpose: str = "subprocess") -> list[str]:
        """Validate argv and return a normalized list suitable for subprocess.

        Args:
            argv: Argument vector. Command strings are not accepted.
            approved: Whether a user or admin explicitly approved privileged install behavior.
            purpose: Logical operation name for audit messages.

        Returns:
            A list copy of argv.

        Raises:
            CommandPolicyError: If argv is empty, shell-like, or dangerous.
        """
        if not argv:
            raise CommandPolicyError("Command argv cannot be empty")
        normalized = [str(part) for part in argv]
        executable = Path(normalized[0]).name.lower()
        if executable in self.DANGEROUS_EXECUTABLES:
            raise CommandPolicyError(f"Executable is blocked: {executable}")
        for part in normalized:
            if any(token in part for token in self.SHELL_METACHARS):
                raise CommandPolicyError(f"Shell metacharacter blocked in argv for {purpose}")
        if self._looks_like_package_install(normalized) and not approved:
            raise CommandPolicyError("Package-install command requires explicit approval")
        return normalized

    def run_sync(
        self,
        argv: Sequence[str],
        purpose: str,
        approved: bool = False,
        timeout: int | float | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Run a validated subprocess with ``shell=False``.

        Args:
            argv: Structured argument vector.
            purpose: Logical operation name.
            approved: Whether privileged install behavior was approved.
            timeout: Optional subprocess timeout.
            kwargs: Additional subprocess.run keyword arguments, except shell.

        Returns:
            Completed process object.
        """
        if kwargs.pop("shell", False):
            raise CommandPolicyError("shell=True is never allowed")
        normalized = self.validate_argv(argv, approved=approved, purpose=purpose)
        self._audit.record(purpose, "subprocess", "allowed", "write", paths=[normalized[0]])
        return subprocess.run(normalized, shell=False, timeout=timeout, **kwargs)

    async def create_subprocess_exec(
        self,
        argv: Sequence[str],
        purpose: str,
        approved: bool = False,
        **kwargs: Any,
    ) -> asyncio.subprocess.Process:
        """Create a validated asyncio subprocess without invoking a shell."""
        normalized = self.validate_argv(argv, approved=approved, purpose=purpose)
        self._audit.record(purpose, "async_subprocess", "allowed", "write", paths=[normalized[0]])
        return await asyncio.create_subprocess_exec(*normalized, **kwargs)

    def _looks_like_package_install(self, argv: Sequence[str]) -> bool:
        """Detect pip/playwright package installation commands."""
        lowered = [part.lower() for part in argv]
        if "install" not in lowered:
            return False
        if len(lowered) >= 4 and lowered[1:3] == ["-m", "pip"]:
            return True
        if len(lowered) >= 4 and lowered[1:3] == ["-m", "playwright"]:
            return True
        return bool(lowered and Path(lowered[0]).name in self.INSTALL_MODULES)
