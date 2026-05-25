"""
Architecture review tests for LJS.

These tests are regression caps, not line-count laws. Long cohesive classes,
state machines, adapters, declarative category manifests, and UI builders may
be acceptable. The caps below track only high-risk structural smoke alarms so
new architecture debt does not grow silently.

Hard correctness/security boundaries live in the dedicated security/category
architecture guards. This file keeps review pressure on risky size, helper, and
private-access patterns without forcing mechanical class/method mutilation.
"""

import ast
import re
from pathlib import Path
import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
WAIVERS_FILE = PROJECT_ROOT / "config" / "architecture_waivers.yaml"

FILE_RISK_LINES = 1500
CLASS_RISK_LINES = 500
METHOD_RISK_LINES = 120
STANDALONE_FN_EXEMPT_FILES = {"models.py", "types.py", "__init__.py", "vector_reindexer.py"}
STANDALONE_FN_EXEMPT_NAMES = {
    "create_app",
    "create_registry",
    "load_settings",
    "verify_auth",
    "verify_ws_auth",
}

# Baseline: maximum allowed unwaived violations of each type.
# When a violation is removed from the codebase, decrement the baseline.
# Never increment without justification — this is the architecture debt cap.
_DEBT_BASELINE: dict[str, int] = {
    # These are high-risk review caps, not arbitrary style limits.
    # Lower them when genuine refactors reduce risk. Do not raise casually.
    "files_risk": 0,
    "classes_risk": 9,
    "methods_risk": 16,
    "standalone_function_advisories": 24,
    "private_access": 21,
    "missing_module_docstrings": 0,
    "missing_class_docstrings": 0,
    "missing_type_hints": 0,
}


@pytest.fixture(scope="session")
def waivers() -> list[dict]:
    """Load architecture waivers from config/architecture_waivers.yaml."""
    with open(WAIVERS_FILE) as f:
        data = yaml.safe_load(f)
    return data.get("waivers", [])


def _collect_py_files() -> list[Path]:
    """Recursively collect all .py files under src/, excluding __pycache__."""
    return sorted(SRC_DIR.rglob("*.py"))


def _count_lines(filepath: Path) -> int:
    with open(filepath, encoding="utf-8") as f:
        return sum(1 for _ in f)


def _node_lines(node: ast.AST) -> int:
    """Return line count (end_lineno - lineno + 1) for an AST node."""
    if hasattr(node, "end_lineno") and hasattr(node, "lineno"):
        return node.end_lineno - node.lineno + 1
    return 0


def _rel_path(filepath: Path) -> str:
    """Return path relative to project root for waiver matching."""
    return str(filepath.relative_to(PROJECT_ROOT))


def _is_waived(
    waivers: list[dict], file_path: str, rule: str, symbol: str | None = None
) -> bool:
    """Check if a specific violation is covered by an active waiver."""
    for w in waivers:
        if w["file"] != file_path:
            continue
        if w["rule"] != rule:
            continue
        if symbol is not None and w.get("symbol") != symbol:
            continue
        return True
    return False


# ── File Line Count ────────────────────────────────────────────────


class TestFileLineCount:
    """Extremely large source files are review risks, not automatic defects."""

    def test_file_size_risk_does_not_increase(self, waivers: list[dict]) -> None:
        violations: list[str] = []
        for fp in _collect_py_files():
            lines = _count_lines(fp)
            if lines > FILE_RISK_LINES:
                rel = _rel_path(fp)
                if not _is_waived(waivers, rel, f"file_size_risk ({FILE_RISK_LINES})"):
                    violations.append(
                        f"[FILE-RISK] {rel}  {lines} lines "
                        f"(risk threshold {FILE_RISK_LINES})"
                    )
        msg = "\n".join(violations)
        assert len(violations) <= _DEBT_BASELINE["files_risk"], (
            f"File size risk count increased ({len(violations)} > "
            f"baseline {_DEBT_BASELINE['files_risk']}):\n{msg}"
        )


# ── Class Line Count ────────────────────────────────────────────────


class TestClassLineCount:
    """Very large classes are capped as review risks, not split mandates."""

    def test_class_size_risk_does_not_increase(self, waivers: list[dict]) -> None:
        violations: list[str] = []
        for fp in _collect_py_files():
            try:
                with open(fp, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(fp))
            except SyntaxError:
                continue
            rel = _rel_path(fp)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    class_lines = _node_lines(node)
                    if class_lines > CLASS_RISK_LINES:
                        if not _is_waived(
                            waivers, rel, f"class_size_risk ({CLASS_RISK_LINES})", symbol=node.name
                        ):
                            violations.append(
                                f"[CLASS-RISK] {rel}:{node.lineno} "
                                f"{node.name}  {class_lines} lines "
                                f"(risk threshold {CLASS_RISK_LINES})"
                            )
        msg = "\n".join(violations)
        assert len(violations) <= _DEBT_BASELINE["classes_risk"], (
            f"Class size risk count increased ({len(violations)} > "
            f"baseline {_DEBT_BASELINE['classes_risk']}):\n{msg}"
        )


# ── Method Line Count ──────────────────────────────────────────────


class TestMethodLineCount:
    """Very long methods are review risks, not automatic refactor orders."""

    def test_method_size_risk_does_not_increase(self, waivers: list[dict]) -> None:
        violations: list[str] = []
        for fp in _collect_py_files():
            try:
                with open(fp, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(fp))
            except SyntaxError:
                continue
            rel = _rel_path(fp)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for child in ast.iter_child_nodes(node):
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            method_lines = _node_lines(child)
                            if method_lines > METHOD_RISK_LINES:
                                symbol = f"{node.name}.{child.name}"
                                if not _is_waived(
                                    waivers,
                                    rel,
                                    f"method_size_risk ({METHOD_RISK_LINES})",
                                    symbol=symbol,
                                ):
                                    violations.append(
                                        f"[METHOD-RISK] {rel}:{child.lineno} "
                                        f"{node.name}.{child.name}  "
                                        f"{method_lines} lines "
                                        f"(risk threshold {METHOD_RISK_LINES})"
                                    )
        msg = "\n".join(violations)
        assert len(violations) <= _DEBT_BASELINE["methods_risk"], (
            f"Method size risk count increased ({len(violations)} > "
            f"baseline {_DEBT_BASELINE['methods_risk']}):\n{msg}"
        )


# ── Standalone Functions ───────────────────────────────────────────


class TestStandaloneFunctions:
    """Standalone functions are advisory unless they grow architecture state."""

    def test_standalone_function_advisories_do_not_increase(self) -> None:
        violations: list[str] = []
        for fp in _collect_py_files():
            if any(exempt in fp.name for exempt in STANDALONE_FN_EXEMPT_FILES):
                continue
            try:
                with open(fp, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(fp))
            except SyntaxError:
                continue
            rel = _rel_path(fp)
            for node in ast.iter_child_nodes(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("_") or node.name in STANDALONE_FN_EXEMPT_NAMES:
                    continue
                violations.append(
                    f"[FN-ADVISORY] {rel}:{node.lineno} "
                    f"standalone function '{node.name}'"
                )
        msg = "\n".join(violations)
        assert len(violations) <= _DEBT_BASELINE["standalone_function_advisories"], (
            f"Standalone function advisory count increased ({len(violations)} > "
            f"baseline {_DEBT_BASELINE['standalone_function_advisories']}):\n{msg}"
        )


# ── Cross-Module Private Access ────────────────────────────────────


class TestCrossModulePrivateAccess:
    """No cross-module access to private attributes (_attr)."""

    _PRIVATE_ACCESS_RE = re.compile(r"(?<!self)(?<!cls)\._([a-z]\w*)\b")
    _FALSE_POSITIVES = {"._json_serialize", "._d", "._s"}

    def test_no_private_access(self) -> None:
        violations: list[str] = []
        for fp in _collect_py_files():
            rel = _rel_path(fp)
            with open(fp, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    stripped = line.split("#")[0]
                    for m in self._PRIVATE_ACCESS_RE.finditer(stripped):
                        attr = m.group(0)
                        if attr in self._FALSE_POSITIVES:
                            continue
                        violations.append(
                            f"[PRIVATE] {rel}:{lineno} "
                            f"cross-module private access '{attr}'"
                        )
        msg = "\n".join(violations)
        assert len(violations) <= _DEBT_BASELINE["private_access"], (
            f"Cross-module private access violations ({len(violations)} > "
            f"baseline {_DEBT_BASELINE['private_access']}):\n{msg}"
        )


# ── Module Docstrings ──────────────────────────────────────────────


class TestModuleDocstrings:
    """Every .py file in src/ must have a module-level docstring."""

    def test_module_docstrings(self, waivers: list[dict]) -> None:
        violations: list[str] = []
        for fp in _collect_py_files():
            try:
                with open(fp, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(fp))
            except SyntaxError:
                continue
            if not tree.body:
                violations.append(f"[MODULE] {_rel_path(fp)}: empty file")
                continue
            first = tree.body[0]
            has_doc = (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            )
            if not has_doc:
                rel = _rel_path(fp)
                if not _is_waived(waivers, rel, "module_docstring"):
                    violations.append(f"[MODULE] {rel}: missing module docstring")
        msg = "\n".join(violations)
        assert len(violations) <= _DEBT_BASELINE.get("missing_module_docstrings", 0), (
            f"Missing module docstrings ({len(violations)} > "
            f"baseline {_DEBT_BASELINE['missing_module_docstrings']}):\n{msg}"
        )


class TestClassDocstrings:
    """Every public class in src/ must have a docstring."""

    def test_class_docstrings(self, waivers: list[dict]) -> None:
        violations: list[str] = []
        for fp in _collect_py_files():
            try:
                with open(fp, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(fp))
            except SyntaxError:
                continue
            rel = _rel_path(fp)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    has_doc = (
                        node.body
                        and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)
                    )
                    if not has_doc:
                        if not _is_waived(
                            waivers, rel, "class_docstring", symbol=node.name
                        ):
                            violations.append(
                                f"[CLASS] {rel}:{node.lineno} "
                                f"{node.name}: missing class docstring"
                            )
        msg = "\n".join(violations)
        assert len(violations) <= _DEBT_BASELINE.get("missing_class_docstrings", 0), (
            f"Missing class docstrings ({len(violations)} > "
            f"baseline {_DEBT_BASELINE['missing_class_docstrings']}):\n{msg}"
        )


# ── Type Hints on Public Methods ────────────────────────────────────


_RETURN_EXEMPT_METHODS = frozenset({"__init__", "__str__", "__repr__"})


class TestTypeHints:
    """Every public method in src/ must have full type hints on all params and return."""

    def test_missing_param_type_hints(self, waivers: list[dict]) -> None:
        violations: list[str] = []
        for fp in _collect_py_files():
            try:
                with open(fp, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(fp))
            except SyntaxError:
                continue
            rel = _rel_path(fp)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for child in ast.iter_child_nodes(node):
                    if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if child.name.startswith("_"):
                        continue
                    args = child.args
                    all_params = (
                        args.args
                        + args.posonlyargs
                        + args.kwonlyargs
                    )
                    if args.vararg:
                        all_params = all_params + [args.vararg]
                    if args.kwarg:
                        all_params = all_params + [args.kwarg]
                    for arg in all_params:
                        if arg.arg in ("self", "cls"):
                            continue
                        if arg.annotation is None:
                            symbol = f"{node.name}.{child.name}"
                            if not _is_waived(
                                waivers, rel, "missing_param_type_hint", symbol=symbol
                            ):
                                violations.append(
                                    f"[PARAM] {rel}:{child.lineno} "
                                    f"{node.name}.{child.name} "
                                    f"missing type hint for '{arg.arg}'"
                                )
        msg = "\n".join(violations)
        assert len(violations) <= _DEBT_BASELINE.get("missing_type_hints", 0), (
            f"Missing param type hints ({len(violations)} > "
            f"baseline {_DEBT_BASELINE['missing_type_hints']}):\n{msg}"
        )

    def test_missing_return_type_hints(self, waivers: list[dict]) -> None:
        violations: list[str] = []
        for fp in _collect_py_files():
            try:
                with open(fp, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(fp))
            except SyntaxError:
                continue
            rel = _rel_path(fp)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for child in ast.iter_child_nodes(node):
                    if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if child.name.startswith("_"):
                        continue
                    if child.name in _RETURN_EXEMPT_METHODS:
                        continue
                    if child.returns is None:
                        symbol = f"{node.name}.{child.name}"
                        if not _is_waived(
                            waivers, rel, "missing_return_type_hint", symbol=symbol
                        ):
                            violations.append(
                                f"[RETURN] {rel}:{child.lineno} "
                                f"{node.name}.{child.name} "
                                f"missing return type hint"
                            )
        msg = "\n".join(violations)
        assert len(violations) <= _DEBT_BASELINE.get("missing_type_hints", 0), (
            f"Missing return type hints ({len(violations)} > "
            f"baseline {_DEBT_BASELINE['missing_type_hints']}):\n{msg}"
        )



