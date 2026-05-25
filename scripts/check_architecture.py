#!/usr/bin/env python3
"""
Architecture review helper for LJS.

This script is a smoke alarm, not a line-count law. It separates:
  - HARD findings: concrete structural hazards that should fail CI when enabled.
  - RISK findings: high-value refactor candidates that need human review.
  - ADVISORY findings: size/shape signals that may be perfectly acceptable.

Long cohesive classes, declarative category manifests, state machines, adapters,
and UI composition files are allowed when they have one clear reason to change.
Use this report to guide careful review, not to mechanically split code.

Usage:
    python scripts/check_architecture.py
    python scripts/check_architecture.py --summary
    python scripts/check_architecture.py --ci          # fails only on HARD findings
    python scripts/check_architecture.py --fail-on-risk
    python scripts/check_architecture.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
PROJECT_ROOT = SRC_DIR.parent

# Advisory thresholds. These are intentionally higher than the old rule set.
# They flag code worth reviewing, without implying it must be split.
FILE_REVIEW_LINES = 1000
FILE_RISK_LINES = 1500
CLASS_REVIEW_LINES = 300
CLASS_RISK_LINES = 500
METHOD_REVIEW_LINES = 50
METHOD_RISK_LINES = 120

# Standalone functions are common and reasonable for factories, pure utilities,
# compatibility adapters, and small helpers. We report them as advisory signals
# unless a narrower guard explicitly flags a concrete architecture violation.
STANDALONE_FN_EXEMPT_FILES = {
    "models.py",
    "types.py",
    "__init__.py",
    "vector_reindexer.py",
}
STANDALONE_FN_EXEMPT_PREFIXES = ("_",)
STANDALONE_FN_EXEMPT_NAMES = {
    "create_app",
    "create_registry",
    "load_settings",
    "verify_auth",
    "verify_ws_auth",
}

_PRIVATE_ACCESS_RE = re.compile(r"(?<!self)(?<!cls)\._([a-z]\w*)\b")
_PRIVATE_ACCESS_FALSE_POSITIVES = {"._json_serialize", "._d", "._s"}


class Severity(str, Enum):
    """Severity levels used by the architecture report."""

    HARD = "hard"
    RISK = "risk"
    ADVISORY = "advisory"


@dataclass(frozen=True)
class Finding:
    """One architecture review finding."""

    severity: str
    rule: str
    path: str
    line: int | None
    symbol: str | None
    message: str


@dataclass(frozen=True)
class ArchitectureSummary:
    """Aggregate architecture review counts."""

    files_scanned: int
    hard_findings: int
    risk_findings: int
    advisory_findings: int
    files_review: int
    files_risk: int
    classes_review: int
    classes_risk: int
    methods_review: int
    methods_risk: int
    standalone_functions: int
    private_access_refs: int


def _count_lines(filepath: Path) -> int:
    with open(filepath, "r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def _node_lines(node: ast.AST) -> int:
    if hasattr(node, "end_lineno") and hasattr(node, "lineno"):
        return int(node.end_lineno) - int(node.lineno) + 1
    return 0


def _is_test_file(filepath: Path) -> bool:
    return "test_" in filepath.name or filepath.parent.name == "tests"


def _collect_py_files(src_dir: Path) -> list[Path]:
    files: list[Path] = []
    for root, _dirs, filenames in os.walk(src_dir):
        for filename in filenames:
            if filename.endswith(".py"):
                files.append(Path(root) / filename)
    return sorted(files)


def _is_standalone_function_advisory(filepath: Path, name: str) -> bool:
    if filepath.name in STANDALONE_FN_EXEMPT_FILES:
        return False
    if name in STANDALONE_FN_EXEMPT_NAMES:
        return False
    if name.startswith(STANDALONE_FN_EXEMPT_PREFIXES):
        return False
    return True


def _private_accesses(filepath: Path) -> Iterable[tuple[int, str]]:
    with open(filepath, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            stripped = line.split("#")[0]
            for match in _PRIVATE_ACCESS_RE.finditer(stripped):
                attr = match.group(0)
                if attr in _PRIVATE_ACCESS_FALSE_POSITIVES:
                    continue
                yield line_no, attr


class ArchitectureChecker:
    """Collects architecture review findings without enforcing arbitrary style."""

    def __init__(self, summary_only: bool = False) -> None:
        self._summary_only = summary_only
        self._findings: list[Finding] = []
        self._files_scanned = 0
        self._counts = {
            "files_review": 0,
            "files_risk": 0,
            "classes_review": 0,
            "classes_risk": 0,
            "methods_review": 0,
            "methods_risk": 0,
            "standalone_functions": 0,
            "private_access_refs": 0,
        }

    @property
    def findings(self) -> list[Finding]:
        """Return all collected findings."""
        return list(self._findings)

    def check(self) -> ArchitectureSummary:
        """Scan src/ and return the aggregate summary."""
        for filepath in _collect_py_files(SRC_DIR):
            if _is_test_file(filepath):
                continue
            self._files_scanned += 1
            self._check_file(filepath)
        return self.summary()

    def summary(self) -> ArchitectureSummary:
        """Return current aggregate counts."""
        hard = sum(1 for f in self._findings if f.severity == Severity.HARD.value)
        risk = sum(1 for f in self._findings if f.severity == Severity.RISK.value)
        advisory = sum(1 for f in self._findings if f.severity == Severity.ADVISORY.value)
        return ArchitectureSummary(
            files_scanned=self._files_scanned,
            hard_findings=hard,
            risk_findings=risk,
            advisory_findings=advisory,
            files_review=self._counts["files_review"],
            files_risk=self._counts["files_risk"],
            classes_review=self._counts["classes_review"],
            classes_risk=self._counts["classes_risk"],
            methods_review=self._counts["methods_review"],
            methods_risk=self._counts["methods_risk"],
            standalone_functions=self._counts["standalone_functions"],
            private_access_refs=self._counts["private_access_refs"],
        )

    def _add(
        self,
        severity: Severity,
        rule: str,
        path: Path,
        line: int | None,
        symbol: str | None,
        message: str,
    ) -> None:
        self._findings.append(Finding(
            severity=severity.value,
            rule=rule,
            path=str(path.relative_to(PROJECT_ROOT)),
            line=line,
            symbol=symbol,
            message=message,
        ))

    def _check_file(self, filepath: Path) -> None:
        rel_path = filepath.relative_to(PROJECT_ROOT)
        line_count = _count_lines(filepath)
        if line_count > FILE_RISK_LINES:
            self._counts["files_risk"] += 1
            self._add(
                Severity.RISK,
                "file_size_review",
                filepath,
                None,
                None,
                f"{rel_path} has {line_count} lines; review for multiple responsibilities.",
            )
        elif line_count > FILE_REVIEW_LINES:
            self._counts["files_review"] += 1
            self._add(
                Severity.ADVISORY,
                "file_size_advisory",
                filepath,
                None,
                None,
                f"{rel_path} has {line_count} lines; acceptable if cohesive.",
            )

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(filepath))
        except SyntaxError as exc:
            self._add(
                Severity.HARD,
                "syntax_error",
                filepath,
                getattr(exc, "lineno", None),
                None,
                f"Syntax error while parsing {rel_path}: {exc}",
            )
            return

        self._check_classes(filepath, tree)
        self._check_standalone_functions(filepath, tree)
        self._check_private_access(filepath)

    def _check_classes(self, filepath: Path, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            class_lines = _node_lines(node)
            if class_lines > CLASS_RISK_LINES:
                self._counts["classes_risk"] += 1
                self._add(
                    Severity.RISK,
                    "class_size_review",
                    filepath,
                    node.lineno,
                    node.name,
                    f"{node.name} is {class_lines} lines; review for multiple reasons to change.",
                )
            elif class_lines > CLASS_REVIEW_LINES:
                self._counts["classes_review"] += 1
                self._add(
                    Severity.ADVISORY,
                    "class_size_advisory",
                    filepath,
                    node.lineno,
                    node.name,
                    f"{node.name} is {class_lines} lines; acceptable if cohesive.",
                )

            for child in ast.iter_child_nodes(node):
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                method_lines = _node_lines(child)
                symbol = f"{node.name}.{child.name}"
                if method_lines > METHOD_RISK_LINES:
                    self._counts["methods_risk"] += 1
                    self._add(
                        Severity.RISK,
                        "method_size_review",
                        filepath,
                        child.lineno,
                        symbol,
                        f"{symbol} is {method_lines} lines; review for mixed abstraction levels.",
                    )
                elif method_lines > METHOD_REVIEW_LINES:
                    self._counts["methods_review"] += 1
                    self._add(
                        Severity.ADVISORY,
                        "method_size_advisory",
                        filepath,
                        child.lineno,
                        symbol,
                        f"{symbol} is {method_lines} lines; acceptable if linear and readable.",
                    )

    def _check_standalone_functions(self, filepath: Path, tree: ast.AST) -> None:
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_standalone_function_advisory(filepath, node.name):
                continue
            self._counts["standalone_functions"] += 1
            self._add(
                Severity.ADVISORY,
                "standalone_function_advisory",
                filepath,
                node.lineno,
                node.name,
                f"Top-level function {node.name} exists; fine for utility/factory code, review if it owns domain state.",
            )

    def _check_private_access(self, filepath: Path) -> None:
        for line_no, attr in _private_accesses(filepath):
            self._counts["private_access_refs"] += 1
            self._add(
                Severity.RISK,
                "private_access_review",
                filepath,
                line_no,
                attr,
                f"Cross-object private access {attr}; review for missing public seam or false positive.",
            )

    def print_report(self) -> None:
        """Print findings and summary in a human-readable format."""
        if not self._summary_only:
            for finding in self._findings:
                location = finding.path
                if finding.line is not None:
                    location += f":{finding.line}"
                symbol = f" {finding.symbol}" if finding.symbol else ""
                print(f"[{finding.severity.upper()}] {finding.rule} {location}{symbol} — {finding.message}")
        self.print_summary()

    def print_summary(self) -> None:
        """Print aggregate counts."""
        summary = self.summary()
        print(f"\n{'=' * 68}")
        print("Architecture Review Summary")
        print(f"{'=' * 68}")
        print(f"  Files scanned:             {summary.files_scanned}")
        print(f"  HARD findings:             {summary.hard_findings}")
        print(f"  RISK findings:             {summary.risk_findings}")
        print(f"  ADVISORY findings:         {summary.advisory_findings}")
        print("  Size smoke alarms:")
        print(f"    Files >{FILE_REVIEW_LINES}/{FILE_RISK_LINES}:       {summary.files_review}/{summary.files_risk}")
        print(f"    Classes >{CLASS_REVIEW_LINES}/{CLASS_RISK_LINES}:    {summary.classes_review}/{summary.classes_risk}")
        print(f"    Methods >{METHOD_REVIEW_LINES}/{METHOD_RISK_LINES}:      {summary.methods_review}/{summary.methods_risk}")
        print(f"  Standalone function notes: {summary.standalone_functions}")
        print(f"  Private access reviews:    {summary.private_access_refs}")
        print("\n  Policy: size findings are review prompts, not automatic defects.")
        print(f"{'=' * 68}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review LJS architecture signals.")
    parser.add_argument("--ci", action="store_true", help="Exit non-zero only on HARD findings.")
    parser.add_argument("--fail-on-risk", action="store_true", help="Also fail on RISK findings.")
    parser.add_argument("--summary", action="store_true", help="Only show aggregate summary.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    checker = ArchitectureChecker(summary_only=args.summary)
    summary = checker.check()
    if args.json:
        print(json.dumps({"summary": asdict(summary), "findings": [asdict(f) for f in checker.findings]}, indent=2))
    else:
        checker.print_report()
    if args.fail_on_risk and (summary.hard_findings or summary.risk_findings):
        return 1
    if args.ci and summary.hard_findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
