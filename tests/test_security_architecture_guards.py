"""Tests for shell/filesystem security architecture guard."""

from pathlib import Path

from scripts.check_security_architecture import SecurityArchitectureGuard


def test_security_architecture_guard_finds_no_unsafe_primitives() -> None:
    """Unsafe shell/filesystem primitives stay centralized in src/core/security."""
    root = Path(__file__).resolve().parents[1]
    assert SecurityArchitectureGuard(root).scan() == {}
