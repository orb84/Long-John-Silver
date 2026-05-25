#!/usr/bin/env python3
"""
Security architecture guard for LJS.

Fails when unsafe shell or filesystem mutation primitives are used outside the
central security subsystem. This keeps LLM/tool hardening enforceable in CI.
"""

from __future__ import annotations

from pathlib import Path


class SecurityArchitectureGuard:
    """Scans source files for unsafe shell and filesystem primitives."""

    BANNED_PATTERNS = {
        "shell=True": "Never invoke a shell; use CommandPolicy with argv.",
        "os.system": "Use CommandPolicy instead of os.system.",
        "subprocess.run": "Use CommandPolicy.run_sync instead of raw subprocess.run.",
        "asyncio.create_subprocess_exec": "Use CommandPolicy.create_subprocess_exec.",
        "asyncio.create_subprocess_shell": "Shell subprocesses are forbidden.",
        "shutil.rmtree": "Use SafePathResolver.safe_rmtree.",
        "shutil.move": "Use SafePathResolver.safe_move.",
        "shutil.copy2": "Use SafePathResolver.safe_copy.",
        "os.link": "Use SafePathResolver.safe_hardlink.",
        ".unlink(": "Use SafePathResolver.safe_unlink.",
    }

    ALLOWED_PREFIXES = {
        "src/core/security/",
    }

    ALLOWED_FILES = {
        "src/core/categories/scaffold.py",  # scans generated category text for banned imports/calls
    }

    def __init__(self, root: Path) -> None:
        """Initialize the guard with a repository root."""
        self._root = root.resolve()

    def scan(self) -> dict[str, list[str]]:
        """Return offending files keyed by banned pattern."""
        offenders: dict[str, list[str]] = {}
        for path in sorted((self._root / "src").rglob("*.py")):
            rel = str(path.relative_to(self._root))
            if self._is_allowed(rel):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in self.BANNED_PATTERNS:
                if pattern in text:
                    offenders.setdefault(pattern, []).append(rel)
        return offenders

    def _is_allowed(self, rel_path: str) -> bool:
        """Return whether a path is allowed to contain guarded primitives."""
        if rel_path in self.ALLOWED_FILES:
            return True
        return any(rel_path.startswith(prefix) for prefix in self.ALLOWED_PREFIXES)


def main() -> int:
    """Run the guard from the command line."""
    root = Path(__file__).resolve().parents[1]
    offenders = SecurityArchitectureGuard(root).scan()
    if offenders:
        for pattern, paths in offenders.items():
            print(f"{pattern}: {', '.join(paths)}")
        return 1
    print("Security architecture guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
