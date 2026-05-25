#!/usr/bin/env python3
"""Check public Python and frontend documentation coverage.

The audit enforces the project convention that every public backend class,
public backend method/function, frontend class, and direct public frontend
method/function has a short usage-oriented docstring/JSDoc comment.  It is a
static architecture guard; it does not import application modules and can run in
minimal CI/sandbox environments.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PY_SRC = PROJECT_ROOT / "src"
JS_SRC = PROJECT_ROOT / "src" / "web" / "static" / "js"


class PythonPublicDocAudit:
    """Audit backend modules for public docstrings.

    Public API means module docstrings, every class docstring, top-level public
    functions, and public methods not starting with ``_``.  Keep this class
    import-free so missing optional runtime dependencies do not block the audit.
    """

    def find_missing(self) -> list[str]:
        """Return missing Python documentation violations."""
        missing: list[str] = []
        for path in sorted(PY_SRC.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel = path.relative_to(PROJECT_ROOT)
            if not ast.get_docstring(tree):
                missing.append(f"[PY MODULE] {rel}: missing module docstring")
            missing.extend(self.missing_node_docs(tree, rel))
        return missing

    def missing_node_docs(self, tree: ast.Module, rel: Path) -> list[str]:
        """Return missing class/method/function docstrings for one module."""
        missing: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if ast.get_docstring(node) is None:
                    missing.append(f"[PY CLASS] {rel}:{node.lineno} {node.name}")
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_"):
                        if ast.get_docstring(child) is None:
                            missing.append(f"[PY METHOD] {rel}:{child.lineno} {node.name}.{child.name}")
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                if ast.get_docstring(node) is None:
                    missing.append(f"[PY FUNCTION] {rel}:{node.lineno} {node.name}")
        return missing


class JavaScriptPublicDocAudit:
    """Audit frontend modules for public JSDoc comments.

    The scanner intentionally focuses on direct class methods and top-level
    functions/classes, which are the UI extension points used by templates and
    the composition root.  It ignores nested callbacks and private methods that
    start with ``_``.
    """

    _CLASS_RE = re.compile(r"(?:export\s+default\s+|export\s+)?class\s+([A-Za-z_$][\w$]*)\b")
    _FUNCTION_RE = re.compile(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
    _METHOD_RE = re.compile(r"(?:static\s+)?(?:async\s+)?([A-Za-z_$][\w$]*)\s*\(")

    def find_missing(self) -> list[str]:
        """Return missing frontend JSDoc violations."""
        missing: list[str] = []
        for path in sorted(JS_SRC.rglob("*.js")):
            missing.extend(self.missing_file_docs(path))
        return missing

    def missing_file_docs(self, path: Path) -> list[str]:
        """Return missing JSDoc comments for one JavaScript file."""
        lines = path.read_text(encoding="utf-8").splitlines()
        rel = path.relative_to(PROJECT_ROOT)
        missing: list[str] = []
        in_class = False
        class_depth = 0
        class_name = ""
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not in_class:
                class_match = self._CLASS_RE.match(stripped)
                if class_match:
                    class_name = class_match.group(1)
                    if not self.has_jsdoc(lines, index):
                        missing.append(f"[JS CLASS] {rel}:{index + 1} {class_name}")
                    in_class = True
                    class_depth = self.brace_delta(line) or 1
                    continue
                func_match = self._FUNCTION_RE.match(stripped)
                if func_match and not func_match.group(1).startswith("_"):
                    if not self.has_jsdoc(lines, index):
                        missing.append(f"[JS FUNCTION] {rel}:{index + 1} {func_match.group(1)}")
                continue
            method_match = self._METHOD_RE.match(stripped)
            if class_depth == 1 and method_match and not method_match.group(1).startswith("_"):
                if not self.has_jsdoc(lines, index):
                    missing.append(f"[JS METHOD] {rel}:{index + 1} {class_name}.{method_match.group(1)}")
            class_depth += self.brace_delta(line)
            if class_depth <= 0:
                in_class = False
                class_depth = 0
                class_name = ""
        return missing

    def has_jsdoc(self, lines: list[str], index: int) -> bool:
        """Return whether a declaration is immediately preceded by JSDoc."""
        cursor = index - 1
        while cursor >= 0 and lines[cursor].strip() == "":
            cursor -= 1
        return cursor >= 0 and lines[cursor].strip().endswith("*/")

    def brace_delta(self, line: str) -> int:
        """Return brace delta for a line after removing strings and comments."""
        stripped = self.strip_strings_and_comments(line)
        return stripped.count("{") - stripped.count("}")

    def strip_strings_and_comments(self, line: str) -> str:
        """Remove simple JS strings/comments before counting braces."""
        out: list[str] = []
        quote: str | None = None
        escape = False
        index = 0
        while index < len(line):
            char = line[index]
            if quote:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == quote:
                    quote = None
                out.append(" ")
            else:
                if char in {'"', "'", "`"}:
                    quote = char
                    out.append(" ")
                elif char == "/" and index + 1 < len(line) and line[index + 1] == "/":
                    break
                else:
                    out.append(char)
            index += 1
        return "".join(out)


class PublicDocumentationAudit:
    """Coordinate backend and frontend public documentation checks."""

    def run(self) -> int:
        """Run the audit and return a process exit code."""
        missing = PythonPublicDocAudit().find_missing()
        missing.extend(JavaScriptPublicDocAudit().find_missing())
        if missing:
            print("\n".join(missing))
            print(f"\nPublic documentation audit failed: {len(missing)} missing item(s).")
            return 1
        print("Public documentation audit passed: Python docstrings and frontend JSDoc are complete.")
        return 0


if __name__ == "__main__":
    sys.exit(PublicDocumentationAudit().run())
