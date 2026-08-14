"""Deterministic identity for the code bundle currently serving LJS.

The browser asset hash alone cannot prove which backend is running.  This
resolver hashes the shipped runtime sources and category definitions so startup
can reject a stale process that happens to own the configured port.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class RuntimeBuildIdentityResolver:
    """Compute a stable, content-derived identity for one installed LJS tree."""

    _ROOT_FILES = ("main.py",)
    _SOURCE_ROOTS = ("src", "config/category-definitions")
    _SUFFIXES = {".py", ".html", ".js", ".css", ".yaml", ".yml", ".json"}

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve()
        self.version = self._compute()

    def _compute(self) -> str:
        digest = hashlib.sha256()
        files = list(self._iter_files())
        for path in files:
            relative = path.relative_to(self._project_root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()[:16]

    def _iter_files(self):
        for name in self._ROOT_FILES:
            path = self._project_root / name
            if path.is_file():
                yield path
        for root_name in self._SOURCE_ROOTS:
            root = self._project_root / root_name
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.suffix.casefold() in self._SUFFIXES:
                    yield path
