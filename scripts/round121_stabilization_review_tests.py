#!/usr/bin/env python3
"""Round 121 stabilization review checks.

These checks cover issues found by tracing first-run/category flows after the
Round 116-120 category, migration, and Trakt changes.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    """Raise a clear assertion error for review-script failures."""
    if not condition:
        raise AssertionError(message)


def text(path: str) -> str:
    """Read a repository text file."""
    return (ROOT / path).read_text(encoding="utf-8")


def load_yaml(path: Path) -> dict:
    """Load a YAML mapping from a path."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    require(isinstance(data, dict), f"{path} should contain a YAML mapping")
    return data


def copied_store():
    """Create an isolated CategoryConfigStore with repository definitions/templates."""
    from src.core.category_config import CategoryConfigStore

    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    definitions = root / "category-definitions"
    templates = root / "category-config-templates"
    live = root / "categories"
    shutil.copytree(ROOT / "config/category-definitions", definitions)
    shutil.copytree(ROOT / "config/category-config-templates", templates)
    return tmp, CategoryConfigStore(live, template_directory=templates, definition_directory=definitions)


def test_ebook_preference_ui_uses_persisted_download_profile_field() -> None:
    """Setup/Compass must not save ebook preference under a dropped field name."""
    setup_js = text("src/web/static/js/pages/setup.js")
    panel_js = text("src/web/static/js/components/settingsPanel.js")
    store = text("src/core/category_config.py")
    ebooks_def = load_yaml(ROOT / "config/category-definitions/ebooks.yaml")
    ebooks_template = load_yaml(ROOT / "config/category-config-templates/ebooks.yaml")

    require("preferred_ebook_format" in setup_js, "setup should save selected ebook format to preferred_ebook_format")
    require("preferred_ebook_format" in panel_js, "Compass should edit preferred_ebook_format")
    require("preferred_primary_format" not in setup_js + panel_js, "old dropped ebook preference key should not remain in UI")
    require("'preferred_ebook_format'" in store, "category config save filter should retain preferred_ebook_format")
    require((ebooks_def.get("download_profile") or {}).get("preferred_ebook_format") == "epub", "definition should declare scalar preferred ebook format")
    require((ebooks_template.get("download_profile") or {}).get("preferred_ebook_format") == "epub", "template should seed scalar preferred ebook format")


def test_ebook_preference_survives_category_save_reload() -> None:
    """The config split should persist the ebook format selected in setup/settings."""
    tmp, store = copied_store()
    with tmp:
        loaded = store.load_all()
        profile = loaded["ebooks"].setdefault("download_profile", {})
        profile["preferred_ebook_format"] = "pdf"
        store.save_all({"ebooks": loaded["ebooks"]})
        saved = load_yaml(store.directory / "ebooks.yaml")
        require((saved.get("download_profile") or {}).get("preferred_ebook_format") == "pdf", "ebook scalar preference should be written to private config")
        reloaded = store.load_all()
        require((reloaded["ebooks"].get("download_profile") or {}).get("preferred_ebook_format") == "pdf", "ebook scalar preference should survive reload")


def test_seed_in_place_runs_category_post_import_hooks() -> None:
    """Seed-in-place must still trigger Music/Audiobook conversion sidecar hooks."""
    handler = text("src/core/download_handler.py")
    seed_branch_start = handler.index('settings.sharing.mode == "seed_in_place"')
    seed_branch_end = handler.index('if not category:', seed_branch_start)
    seed_branch = handler[seed_branch_start:seed_branch_end]
    require("_run_category_post_import_hooks" in seed_branch, "seed-in-place branch should run category post-import hooks")
    require("imported=source" in seed_branch, "seed-in-place hooks should receive the library payload as imported path")
    require("download_import_sidecar" in handler, "created sidecars should be reconciled")


def test_round121_review_doc_records_findings() -> None:
    """Review findings and fixes should be captured for the next maintainer/session."""
    doc = ROOT / "docs/project-history/ROUND121_STABILIZATION_REVIEW.md"
    require(doc.exists(), "Round 121 stabilization review doc should exist")
    body = doc.read_text(encoding="utf-8")
    for phrase in (
        "ebook preference",
        "preferred_ebook_format",
        "seed-in-place",
        "post-import hooks",
        "Trakt bundled OOB",
        "migration splitter",
        "two-pass review",
    ):
        require(phrase in body, f"Round 121 review doc should mention {phrase}")


if __name__ == "__main__":
    for name in sorted(k for k in globals() if k.startswith("test_")):
        globals()[name]()
        print(f"PASS {name}")
