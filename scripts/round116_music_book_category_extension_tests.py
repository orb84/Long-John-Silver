#!/usr/bin/env python3
"""Round 116 checks for Music/Audiobook/Ebook category extension stress test."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    """Raise an assertion with a clear Round 116 failure message."""
    if not condition:
        raise AssertionError(message)


def load_yaml(path: Path) -> dict:
    """Load YAML as a mapping."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    require(isinstance(data, dict), f"{path} should contain a YAML mapping")
    return data


def copied_store():
    """Create a temporary category config store using repository definitions."""
    from src.core.category_config import CategoryConfigStore

    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    definitions = root / "category-definitions"
    templates = root / "category-config-templates"
    live = root / "categories"
    shutil.copytree(ROOT / "config/category-definitions", definitions)
    shutil.copytree(ROOT / "config/category-config-templates", templates)
    return tmp, CategoryConfigStore(live, template_directory=templates, definition_directory=definitions)


def fake_settings(category_settings: dict, root: Path) -> SimpleNamespace:
    """Return enough settings surface for category manifests and workflows."""
    from src.core.models import SecurityConfig

    return SimpleNamespace(
        category_settings=category_settings,
        library_root=str(root / "library"),
        download_dir=str(root / "downloads"),
        jackett_url="http://127.0.0.1:9117",
        jackett_api_key="synthetic",
        direct_scraper_fallback=False,
        web_search=SimpleNamespace(enabled=True, provider="brave"),
        security=SecurityConfig(),
    )


def test_definitions_model_inheritance_mixins_and_services() -> None:
    """Definitions should express audio/book reuse without hardcoded category blobs."""
    tmp, store = copied_store()
    with tmp:
        loaded = store.load_all()
        for cid in ("music", "audiobooks", "ebooks", "audio", "book"):
            require(cid in loaded, f"missing category config for {cid}")
        require(loaded["audio"].get("abstract") is True, "audio should be an abstract mixin/base")
        require(loaded["book"].get("abstract") is True, "book should be an abstract mixin/base")
        require(loaded["music"].get("extends") == "audio", "music should extend audio")
        require("ffmpeg" in loaded["music"].get("runtime_dependencies", {}), "music should inherit FFmpeg dependency")
        require(loaded["audiobooks"].get("extends") == "book", "audiobooks should extend book")
        require("audio" in loaded["audiobooks"].get("mixins", []), "audiobooks should mix in audio behavior")
        require("ffmpeg" in loaded["audiobooks"].get("runtime_dependencies", {}), "audiobooks should inherit FFmpeg from audio mixin")
        require("open_library" in loaded["audiobooks"].get("services", {}), "audiobooks should inherit book metadata services")
        require("gutendex" in loaded["ebooks"].get("services", {}), "ebooks should declare Gutendex")
        require("ffmpeg" not in loaded["ebooks"].get("runtime_dependencies", {}), "ebooks should not inherit audio conversion dependencies")


def test_save_compaction_includes_mixins_not_only_extends() -> None:
    """Saving effective settings should not copy mixin defaults into private YAML."""
    tmp, store = copied_store()
    with tmp:
        loaded = store.load_all()
        loaded["audiobooks"]["library_path"] = "./synthetic/Audiobooks"
        loaded["audiobooks"]["services"]["librivox"]["enabled"] = False
        store.save_all(loaded)
        private_text = (store.directory / "audiobooks.yaml").read_text(encoding="utf-8")
        private = yaml.safe_load(private_text) or {}
        require(private["paths"]["library_path"] == "./synthetic/Audiobooks", "audiobooks private path should save")
        require(private["services"]["librivox"]["enabled"] is False, "audiobooks private service toggle should save")
        require("open_library:" not in private_text, "book-inherited Open Library should not duplicate into audiobooks private config")
        require("preferred_lossless_format:" not in private_text, "audio mixin download defaults should not duplicate into audiobooks private config")
        require("runtime_dependencies:" not in private_text, "definition-only runtime dependencies must not leak into private config")


def test_definition_backed_categories_register_and_route() -> None:
    """Concrete YAML definitions should become runtime categories and beat fallback routing."""
    from src.core.categories.registry import CategoryRegistry

    tmp, store = copied_store()
    with tmp:
        registry = CategoryRegistry(config_store=store)
        registry.register_defaults()
        ids = set(registry.list_ids())
        require({"music", "audiobooks", "ebooks"}.issubset(ids), "new definitions should register as concrete categories")
        require("audio" not in ids and "book" not in ids, "abstract shared definitions should not register")
        require(registry.resolve_from_text("Kind of Blue FLAC").category_id == "music", "FLAC album should route to Music")
        require(registry.resolve_from_text("The Left Hand of Darkness EPUB").category_id == "ebooks", "EPUB book should route to Ebooks")
        require(registry.resolve_from_text("Project Hail Mary audiobook unabridged m4b").category_id == "audiobooks", "M4B audiobook should route to Audiobooks")
        require(registry.get("music").router_brief().description.startswith("Music"), "definition-backed router brief should use YAML profile")


def test_manifests_and_workflow_contracts_are_declared_and_adapted() -> None:
    """Manifests should expose dependencies/workflows and metadata adapter receipts."""
    from src.core.categories.registry import CategoryRegistry

    tmp, store = copied_store()
    with tmp:
        registry = CategoryRegistry(config_store=store)
        registry.register_defaults()
        settings = fake_settings(store.load_all(), Path(tmp.name))
        music = registry.get("music")
        manifest = music.manifest(settings=settings)
        workflow_names = {workflow.name for workflow in manifest.workflows}
        require("convert_audio_for_apple" in workflow_names, "music should declare Apple audio conversion workflow")
        require(any(dep.id == "ffmpeg" for dep in manifest.runtime_dependencies), "music manifest should expose FFmpeg dependency status")
        receipt = asyncio.run(music.execute_workflow("resolve_metadata", {"title": "Kind of Blue"}, context=SimpleNamespace(settings=settings)))
        require(receipt.status in {"success", "partial"}, "metadata workflow should now use provider adapters or return a partial lookup receipt")
        require(receipt.data.get("query") == "Kind of Blue", "metadata workflow should preserve the requested query")
        require("services_tried" in receipt.data, "metadata workflow should report provider attempts")


def test_neutral_scanner_respects_category_formats() -> None:
    """Definition-backed scanner should use declared formats, not TV/movie assumptions."""
    from src.core.categories.registry import CategoryRegistry

    tmp, store = copied_store()
    with tmp:
        root = Path(tmp.name)
        music_root = root / "library" / "Music"
        ebook_root = root / "library" / "Ebooks"
        (music_root / "Miles Davis - Kind of Blue").mkdir(parents=True)
        (ebook_root / "Ursula K Le Guin").mkdir(parents=True)
        (music_root / "Miles Davis - Kind of Blue" / "01 So What.flac").write_bytes(b"synthetic")
        (music_root / "Miles Davis - Kind of Blue" / "runme.exe").write_bytes(b"synthetic")
        (ebook_root / "Ursula K Le Guin" / "The Left Hand of Darkness.epub").write_bytes(b"synthetic")
        registry = CategoryRegistry(config_store=store)
        registry.register_defaults()
        music_scan = asyncio.run(registry.get("music").scan(str(music_root)))
        ebook_scan = asyncio.run(registry.get("ebooks").scan(str(ebook_root)))
        require(len(music_scan) == 1 and music_scan[0].file_count == 1, "music scanner should include FLAC and ignore EXE")
        require(music_scan[0].files[0].quality == "flac", "music scanner should report audio extension quality")
        require(len(ebook_scan) == 1 and ebook_scan[0].files[0].quality == "epub", "ebook scanner should include EPUB")


def main() -> None:
    """Run Round 116 checks as a standalone script."""
    for test in (
        test_definitions_model_inheritance_mixins_and_services,
        test_save_compaction_includes_mixins_not_only_extends,
        test_definition_backed_categories_register_and_route,
        test_manifests_and_workflow_contracts_are_declared_and_adapted,
        test_neutral_scanner_respects_category_formats,
    ):
        test()
        print(f"PASS {test.__name__}")
    print("Round 116 music/book category extension checks passed.")


if __name__ == "__main__":
    main()
