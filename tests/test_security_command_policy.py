"""Tests for structured subprocess command policy."""

import sys

import pytest

from src.core.security.command_policy import CommandPolicy, CommandPolicyError


def test_command_policy_blocks_shell_metacharacters() -> None:
    """Shell-style command composition should never pass validation."""
    with pytest.raises(CommandPolicyError):
        CommandPolicy().validate_argv(["python", "-c", "print(1); rm -rf /"])


def test_command_policy_blocks_dangerous_executables() -> None:
    """Dangerous shell and deletion executables are denied outright."""
    with pytest.raises(CommandPolicyError):
        CommandPolicy().validate_argv(["rm", "-rf", "/tmp/example"])


def test_command_policy_requires_approval_for_package_installs() -> None:
    """Package install commands require explicit user/UI approval."""
    with pytest.raises(CommandPolicyError):
        CommandPolicy().validate_argv([sys.executable, "-m", "pip", "install", "playwright"])

    argv = CommandPolicy().validate_argv(
        [sys.executable, "-m", "pip", "install", "playwright"],
        approved=True,
    )
    assert argv[1:4] == ["-m", "pip", "install"]


def test_command_policy_allows_safe_argv_without_shell() -> None:
    """Simple argv execution plans are allowed when no shell features are present."""
    argv = CommandPolicy().validate_argv(["ffprobe", "-version"], purpose="test")
    assert argv == ["ffprobe", "-version"]
