#!/usr/bin/env python3
"""Round 117 checks for Music/Books review and library-root defaults."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    """Raise a clear assertion for this review pass."""
    if not condition:
        raise AssertionError(message)


def load_yaml(path: Path) -> dict:
    """Load one YAML file as a mapping."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    require(isinstance(data, dict), f"{path} should contain a YAML mapping")
    return data


def copied_store():
    """Create an isolated category store using repository definitions/templates."""
    from src.core.category_config import CategoryConfigStore

    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    definitions = root / "category-definitions"
    templates = root / "category-config-templates"
    live = root / "categories"
    shutil.copytree(ROOT / "config/category-definitions", definitions)
    shutil.copytree(ROOT / "config/category-config-templates", templates)
    return tmp, CategoryConfigStore(live, template_directory=templates, definition_directory=definitions)


def test_library_root_is_the_path_authority() -> None:
    """Category paths should be optional overrides over settings.library_root."""
    from src.core.categories.registry import CategoryRegistry
    from src.core.models import Settings

    tmp, store = copied_store()
    with tmp:
        root = Path(tmp.name)
        settings = Settings(library_root=str(root / "Library Root"), download_dir=str(root / "Downloads"))
        registry = CategoryRegistry(config_store=store)
        registry.register_defaults()
        music = registry.get("music")
        ebooks = registry.get("ebooks")
        require(music is not None and ebooks is not None, "definition-backed Music/Ebooks categories should register")
        require(music.default_root_path(settings).endswith("Library Root/Music"), "Music default should be under library_root/Music")
        require(ebooks.get_root_path(settings).endswith("Library Root/Ebooks"), "Ebooks should fall back to library_root/Ebooks")

        settings.category_settings["music"] = {"library_path": str(root / "External Music")}
        require(music.get_root_path(settings).endswith("External Music"), "explicit category library_path should override root default")

        settings.category_settings["music"] = {"library_path": ""}
        settings.library_paths["music"] = str(root / "Legacy Music")
        require(music.get_root_path(settings).endswith("Legacy Music"), "legacy settings.library_paths should remain a migration fallback")


def test_setup_requirements_and_manifests_explain_optional_paths() -> None:
    """UI manifests should expose default/effective paths and optional override guidance."""
    from src.core.categories.registry import CategoryRegistry
    from src.core.models import Settings

    tmp, store = copied_store()
    with tmp:
        settings = Settings(library_root=str(Path(tmp.name) / "Library"))
        registry = CategoryRegistry(config_store=store)
        registry.register_defaults()
        audiobook = registry.get("audiobooks")
        require(audiobook is not None, "Audiobooks category should register")

        manifest = audiobook.manifest(settings=settings)
        require(manifest.default_library_path.endswith("Library/Audiobooks"), "manifest should expose root-derived default path")
        require(manifest.effective_library_path.endswith("Library/Audiobooks"), "manifest should expose effective path")
        path_req = next((req for req in manifest.setup_requirements if req.id == "library_path"), None)
        require(path_req is not None, "library path setup requirement should be present")
        require(path_req.required is False, "category library path must be optional, not a setup blocker")
        require("Leave blank" in path_req.description, "path requirement should explain root fallback")


def test_templates_do_not_preseed_category_paths() -> None:
    """Blank templates must not hardcode ./library/<Category> overrides."""
    for category_id in ("tv", "movie", "general", "music", "audiobooks", "ebooks"):
        payload = load_yaml(ROOT / "config/category-config-templates" / f"{category_id}.yaml")
        paths = payload.get("paths") or {}
        require("library_path" not in paths, f"{category_id} template should not preseed library_path")


def test_blank_paths_do_not_dirty_new_private_configs_but_can_clear_old_overrides() -> None:
    """Save compaction should avoid meaningless blanks while supporting clearing overrides."""
    tmp, store = copied_store()
    with tmp:
        loaded = store.load_all()
        loaded["music"]["library_path"] = ""
        store.save_all({"music": loaded["music"]})
        clean_music = load_yaml(store.directory / "music.yaml")
        require("library_path" not in (clean_music.get("paths") or {}), "new blank music path should not be persisted")

        old = load_yaml(store.directory / "music.yaml")
        old.setdefault("paths", {})["library_path"] = "/old/music"
        store.save_file("music", old)
        loaded = store.load_all()
        loaded["music"]["library_path"] = ""
        store.save_all({"music": loaded["music"]})
        cleared_music = load_yaml(store.directory / "music.yaml")
        require((cleared_music.get("paths") or {}).get("library_path") == "", "blank should persist when clearing an existing override")


def test_setup_and_settings_ui_expose_library_root_and_optional_overrides() -> None:
    """First-run setup and settings panels should surface the root+override model."""
    setup_template = (ROOT / "src/web/templates/setup.html").read_text(encoding="utf-8")
    settings_template = (ROOT / "src/web/templates/settings.html").read_text(encoding="utf-8")
    setup_js = (ROOT / "src/web/static/js/pages/setup.js").read_text(encoding="utf-8")
    settings_js = (ROOT / "src/web/static/js/components/settingsSavers.js").read_text(encoding="utf-8")
    panel_js = (ROOT / "src/web/static/js/components/settingsPanel.js").read_text(encoding="utf-8")

    require("setup-library-root" in setup_template, "setup wizard should include main library root input")
    require("Main Library Root" in setup_template, "setup wizard should label the library root")
    require("optional" in setup_template.lower(), "setup wizard should mark category paths as optional")
    require("category.default_library_path" in setup_template, "setup category placeholders should show root-derived defaults")
    require("req.help_url" in setup_template, "setup requirements should expose provider help links")
    require("library_root" in setup_js, "setup path save should post library_root")
    require("var value = input.value.trim();" in setup_js and "if (value) paths[input.dataset.categoryId] = value;" in setup_js, "setup should omit blank category overrides")

    require("id=\"library_root\"" in settings_template, "classic settings page should include library_root")
    require("pref-library-root" in panel_js, "dynamic settings panel should include library_root")
    require("Default library folder" in panel_js, "category panel should display root-derived default folder")
    require("library_root" in settings_js, "settings save should post library_root")


def test_setup_and_settings_handlers_prepare_effective_roots_without_hardcoding() -> None:
    """Path saves should enumerate category manifests and create effective roots."""
    from src.core.categories.registry import CategoryRegistry
    from src.core.models import Settings

    tmp, store = copied_store()
    with tmp:
        settings = Settings(library_root=str(Path(tmp.name) / "Library"))
        registry = CategoryRegistry(config_store=store)
        registry.register_defaults()
        music = registry.get("music")
        require(music is not None, "Music should register for root creation check")
        created = Path(music.ensure_root_path(settings))
        require(created.exists() and created.name == "Music", "category ensure_root_path should create root-derived folder")

    setup_handler = (ROOT / "src/web/action_handlers/setup.py").read_text(encoding="utf-8")
    settings_handler = (ROOT / "src/web/action_handlers/settings.py").read_text(encoding="utf-8")
    for handler_text, label in ((setup_handler, "setup"), (settings_handler, "settings")):
        require("def _prepare_library_directories" in handler_text, f"{label} handler should prepare library directories")
        require("Path(category.get_root_path(settings))" in handler_text, f"{label} handler should use category effective roots")
        require("mkdir(parents=True, exist_ok=True)" in handler_text, f"{label} handler should create directories best-effort")


def test_review_docs_and_additional_metadata_services_are_declared() -> None:
    """The review should document torrent forms and optional provider suggestions."""
    history = ROOT / "docs/project-history/ROUND117_MUSIC_BOOK_REVIEW_AND_LIBRARY_ROOT.md"
    require(history.exists(), "Round 117 project-history review should exist")
    history_text = history.read_text(encoding="utf-8")
    for phrase in (
        "Music torrents usually appear",
        "Audiobook torrents usually appear",
        "Ebook torrents usually appear",
        "Root plus optional overrides",
        "Apple iTunes / Apple Books Search API",
        "Discogs",
        "AcoustID",
        "Comic Vine",
    ):
        require(phrase in history_text, f"Round 117 review doc should mention {phrase}")

    music = load_yaml(ROOT / "config/category-definitions/music.yaml")
    book = load_yaml(ROOT / "config/category-definitions/book.yaml")
    ebooks = load_yaml(ROOT / "config/category-definitions/ebooks.yaml")
    require("discogs" in music.get("services", {}), "Music should suggest Discogs as optional service")
    require("acoustid" in music.get("services", {}), "Music should suggest AcoustID as optional service")
    require("apple_itunes_search" in book.get("services", {}), "Books should suggest Apple iTunes/Books Search API")
    require("comic_vine" in ebooks.get("services", {}), "Ebooks should suggest Comic Vine for comics")
    for service in (music["services"]["discogs"], music["services"]["acoustid"], book["services"]["apple_itunes_search"], ebooks["services"]["comic_vine"]):
        require(service.get("help_url"), "optional provider suggestions should include help_url links")


if __name__ == "__main__":
    tests = [name for name in globals() if name.startswith("test_")]
    for name in sorted(tests):
        globals()[name]()
        print(f"PASS {name}")
