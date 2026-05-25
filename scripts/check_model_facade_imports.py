#!/usr/bin/env python3
"""Validate that the ``src.core.models`` compatibility facade exports callers' imports.

The Round 19 domain-model split intentionally kept ``src.core.models`` as the
stable import surface for existing code.  This guard parses project imports and
fails fast if any symbol imported from that facade is missing, including legacy
private helpers that star imports intentionally skip.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SEARCH_ROOTS = (ROOT / "src", ROOT / "tests", ROOT / "scripts")


class ModelFacadeImportAudit:
    """Check the model facade against every explicit project import.

    The audit is intentionally import-light: it imports only the facade module,
    then statically inspects callers.  Add new explicit re-exports to
    ``src.core.models`` before moving symbols into narrower modules.
    """

    def run(self) -> int:
        """Return ``0`` when every imported facade symbol is available."""
        missing = self.find_missing_imports()
        if missing:
            for rel, name in missing:
                print(f"{rel}: src.core.models does not export {name}")
            print(f"Model facade import audit failed: {len(missing)} missing export(s).")
            return 1
        print("Model facade import audit passed: all explicit imports are exported.")
        return 0

    def find_missing_imports(self) -> list[tuple[str, str]]:
        """Return ``(relative_path, symbol)`` pairs missing from the facade."""
        module = importlib.import_module("src.core.models")
        missing: list[tuple[str, str]] = []
        for path in self.iter_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module != "src.core.models":
                    continue
                for alias in node.names:
                    if alias.name != "*" and not hasattr(module, alias.name):
                        missing.append((str(path.relative_to(ROOT)), alias.name))
        return missing

    def iter_python_files(self) -> list[Path]:
        """Return Python files under source, scripts, and tests directories."""
        files: list[Path] = []
        for root in SEARCH_ROOTS:
            if root.exists():
                files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
        return sorted(files)


if __name__ == "__main__":
    sys.exit(ModelFacadeImportAudit().run())
