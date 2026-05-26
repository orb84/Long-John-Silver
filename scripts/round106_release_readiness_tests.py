#!/usr/bin/env python3
"""Round 106/107 release-readiness checks.

These checks intentionally avoid importing application modules so they can run
in a sparse sandbox. They guard the release-facing documentation surface: a
category-first README, an explicit AGPL release license, handle-based public
attribution, and docstrings on every public-ish backend helper.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    """Raise an assertion with a release-specific message when a check fails."""
    if not condition:
        raise AssertionError(message)


class StrictDocstringAudit:
    """Audit every public-ish backend class/function, including nested helpers."""

    def find_missing(self) -> list[str]:
        """Return missing docstring entries under src/."""
        missing: list[str] = []
        for path in sorted((ROOT / "src").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("_"):
                    continue
                if ast.get_docstring(node) is None:
                    rel = path.relative_to(ROOT)
                    missing.append(f"{rel}:{node.lineno} {node.name}")
        return missing


def test_readme_reflects_current_architecture() -> None:
    """Check that README describes the current category-first runtime."""
    readme = read("README.md")
    require("LLM reasons and chooses" in readme, "README should describe the LLM-led/contract-bound principle")
    require("General Files" in readme, "README should document the bundled General Files category")
    require("Advanced Category Contracts" in readme, "README should explain the renamed manifest diagnostics panel")
    require("config/settings.template.yaml" in readme and "config/settings.local.yaml" in readme, "README should document template/live settings split")
    require("config/category-definitions/<category_id>.yaml" in readme and "config/category-config-templates/<category_id>.yaml" in readme and "config/categories/<category_id>.yaml" in readme, "README should document category definition/template/live split")


def test_release_license_and_public_attribution_are_set() -> None:
    """Check that release docs use AGPL and handle-based attribution."""
    readme = read("README.md")
    license_text = read("LICENSE")
    notice = read("NOTICE")
    authors = read("AUTHORS.md")
    support = read("SUPPORT.md")
    require("AGPL-3.0-or-later" in readme, "README should declare AGPL-3.0-or-later")
    require("GNU AFFERO GENERAL PUBLIC LICENSE" in license_text, "LICENSE should contain AGPL license text")
    require("Copyright © 2026 orb84 and contributors" in readme, "README should use handle-based attribution")
    require("github.com/orb84/Long-John-Silver" in readme, "README should link the public repository")
    require("github.com/orb84" in notice and "orblaboratories@gmail.com" in notice, "NOTICE should include public contact routes")
    require("orb84" in authors, "AUTHORS should credit the public maintainer handle")
    require("orblaboratories@gmail.com" in support, "SUPPORT should include the requested contact email")


def test_removed_license_advice_documents_stay_removed() -> None:
    """Check that private decision/advice docs are not left in the public tree."""
    removed = [
        "docs/" + "LICENSE" + "_DECISION.md",
        "docs/" + "OPEN_SOURCE" + "_RELEASE_REVIEW.md",
        "docs/" + "OPEN_SOURCE" + "_BASELINE_REVIEW.md",
    ]
    for rel in removed:
        require(not (ROOT / rel).exists(), f"{rel} should not be present in the public release tree")
    readme = read("README.md")
    forbidden = [
        "final release " + "license has **not** been selected",
        "Recommended " + "decision path",
        "See `docs/" + "LICENSE" + "_DECISION.md`",
    ]
    for phrase in forbidden:
        require(phrase not in readme, f"README should not retain old license-advice phrase: {phrase}")


def test_env_example_only_documents_supported_runtime_env() -> None:
    """Check that .env.example no longer lists settings that the runtime ignores."""
    env_text = read(".env.example")
    unsupported_prefixes = [
        "LJS_LLM_",
        "LJS_MOVIES_PATH",
        "LJS_TV_SHOWS_PATH",
        "LJS_JACKETT_",
        "LJS_OPENSUBTITLES_",
        "LJS_TRAKT_",
    ]
    for token in unsupported_prefixes:
        require(token not in env_text, f".env.example should not advertise unsupported env setting {token}")
    for token in ["LJS_PORT", "LJS_HOST", "LJS_ACCESS_LOGS", "LJS_WEB_SECRET", "LJS_ALLOW_INSECURE_DEV"]:
        require(token in env_text, f".env.example should include supported runtime env setting {token}")


def test_strict_backend_docstrings_are_complete() -> None:
    """Check public-ish backend helpers beyond the normal public-docs audit."""
    missing = StrictDocstringAudit().find_missing()
    require(not missing, "Missing backend docstrings:\n" + "\n".join(missing[:50]))


if __name__ == "__main__":
    for check in [
        test_readme_reflects_current_architecture,
        test_release_license_and_public_attribution_are_set,
        test_removed_license_advice_documents_stay_removed,
        test_env_example_only_documents_supported_runtime_env,
        test_strict_backend_docstrings_are_complete,
    ]:
        check()
    print("Round 106/107 release-readiness checks passed.")
