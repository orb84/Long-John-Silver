#!/usr/bin/env python3
"""Round 224 regression checks for web router private route handlers.

The Linux startup crash in Round 223 happened because SystemRouter registered
new API routes whose bound methods were never added. This static audit keeps
FastAPI router construction from failing late at runtime for the same class of
mistake.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTERS = ROOT / "src" / "web" / "routers"


class RouteHandlerAudit:
    """Verify that every self._handler passed to add_api_route exists."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def run(self) -> int:
        for path in sorted(ROUTERS.glob("*.py")):
            self._audit_file(path)
        if self.failures:
            print("❌ Missing web route handler methods detected:")
            for failure in self.failures:
                print(f"  - {failure}")
            return 1
        print("✅ All class-based web router add_api_route handlers resolve to class methods.")
        return 0

    def _audit_file(self, path: Path) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._audit_class(path, node)

    def _audit_class(self, path: Path, node: ast.ClassDef) -> None:
        method_names = {
            item.name
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for call in ast.walk(node):
            if not self._is_add_api_route_call(call):
                continue
            for arg in call.args:
                if self._is_self_attribute(arg) and arg.attr not in method_names:
                    self.failures.append(f"{path.relative_to(ROOT)}::{node.name}.{arg.attr}")

    @staticmethod
    def _is_add_api_route_call(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_api_route"
        )

    @staticmethod
    def _is_self_attribute(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        )


if __name__ == "__main__":
    raise SystemExit(RouteHandlerAudit().run())
