#!/usr/bin/env python3
"""Round 107 public-identity and README asset checks."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    """Raise an assertion with a round-specific message when a check fails."""
    if not condition:
        raise AssertionError(message)


def test_readme_assets_exist_and_are_referenced() -> None:
    """Check that README includes the persona avatar and the two UI screenshots."""
    readme = read("README.md")
    assets = [
        "docs/assets/ljs-avatar.png",
        "docs/assets/screenshot-helm-yellowstone.png",
        "docs/assets/screenshot-recommendation-download.png",
    ]
    for rel in assets:
        require((ROOT / rel).exists(), f"Missing README asset {rel}")
        require(rel in readme, f"README should reference {rel}")


def test_public_identity_uses_handle_not_private_name() -> None:
    """Check that release-facing docs use orb84/contact routes, not a private name."""
    checked = ["README.md", "NOTICE", "AUTHORS.md", "SUPPORT.md", "LICENSE"]
    combined = "\n".join(read(path) for path in checked)
    private_tokens = ["T" + "ommaso", "A" + "dani", "gem" + "ma01", "frozen" + "pepper"]
    for private_token in private_tokens:
        require(private_token not in combined, f"Release-facing docs should not contain {private_token}")
    require("orb84" in combined, "Release-facing docs should credit orb84")
    require("github.com/orb84/Long-John-Silver" in combined, "Release-facing docs should link the requested repository")
    require("orblaboratories@gmail.com" in combined, "Release-facing docs should include the requested contact email")


def test_license_advice_documents_are_removed() -> None:
    """Check that old license decision/advice documents are absent."""
    removed_docs = [
        "docs/" + "LICENSE" + "_DECISION.md",
        "docs/" + "OPEN_SOURCE" + "_RELEASE_REVIEW.md",
        "docs/" + "OPEN_SOURCE" + "_BASELINE_REVIEW.md",
    ]
    for rel in removed_docs:
        require(not (ROOT / rel).exists(), f"{rel} should be removed")


if __name__ == "__main__":
    for check in [
        test_readme_assets_exist_and_are_referenced,
        test_public_identity_uses_handle_not_private_name,
        test_license_advice_documents_are_removed,
    ]:
        check()
    print("Round 107 public identity checks passed.")
