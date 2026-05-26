"""Round 112 regression checks for category inheritance and setup scan status.

These tests are structural and use only synthetic temporary settings. They do
not contain credentials or inspect user-private config files.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.category_config import CategoryConfigStore
from src.core.config import SettingsManager
from src.core.models import Settings
from src.web.routers.setup import SetupRouter



def test_media_template_is_inherited_without_duplication() -> None:
    """TV/Movie effective configs inherit media services but save only overrides."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        templates = tmp_path / "category-config-templates"
        live = tmp_path / "categories"
        shutil.copytree(ROOT / "config" / "category-config-templates", templates)

        store = CategoryConfigStore(live, template_directory=templates, definition_directory=ROOT / "config" / "category-definitions")
        configs = store.load_all()

        assert "media" in configs
        assert configs["media"].get("abstract") is True
        assert configs["tv"].get("extends") == "media"
        assert configs["movie"].get("extends") == "media"
        assert "tmdb" in configs["tv"].get("services", {})
        assert "trakt" in configs["movie"].get("services", {})
        assert "tvmaze" in configs["tv"].get("services", {})
        assert "tvmaze" not in configs["movie"].get("services", {})

        configs["media"]["services"]["tmdb"]["api_key"] = "synthetic-test-key"
        configs["tv"]["services"]["tvmaze"]["enabled"] = False
        store.save_all(configs)

        tv_payload = yaml.safe_load((live / "tv.yaml").read_text())
        media_payload = yaml.safe_load((live / "media.yaml").read_text())
        assert "extends" not in tv_payload, "inheritance lives in category-definitions, not private config"
        assert "tmdb" not in (tv_payload.get("services") or {})
        assert (tv_payload.get("services") or {}).get("tvmaze", {}).get("enabled") is False
        assert media_payload["services"]["tmdb"]["api_key"] == "synthetic-test-key"


def test_settings_model_has_no_global_media_service_fields() -> None:
    """Media service credentials are no longer global Settings fields."""
    fields = set(Settings.model_fields)
    forbidden = {
        "tmdb_api_key",
        "trakt_client_id",
        "trakt_access_token",
        "trakt_refresh_token",
        "plex_url",
        "plex_token",
        "opensubtitles_api_key",
    }
    assert not (fields & forbidden)


def test_settings_manager_does_not_migrate_old_settings_yaml() -> None:
    """Fresh-install mode must not rename/read old config/settings.yaml."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / "config"
        cfg.mkdir()
        (cfg / "settings.template.yaml").write_text("setup_complete: false\n", encoding="utf-8")
        (cfg / "settings.yaml").write_text("setup_complete: true\n", encoding="utf-8")
        shutil.copytree(ROOT / "config" / "category-config-templates", cfg / "category-config-templates")

        manager = SettingsManager(
            yaml_path=str(cfg / "settings.local.yaml"),
            template_path=str(cfg / "settings.template.yaml"),
            category_config_dir=str(cfg / "categories"),
            category_template_dir=str(cfg / "category-config-templates"),
            category_definition_dir=str(ROOT / "config" / "category-definitions"),
        )
        settings = manager.load()
        assert settings.setup_complete is False
        assert (cfg / "settings.yaml").exists(), "old settings.yaml should not be moved or consumed"


def test_post_setup_uses_nonblocking_scan_request() -> None:
    """Closing setup should queue a scan immediately so UI status hydrates."""
    class FakeScheduler:
        def __init__(self) -> None:
            self.requested: dict | None = None

        def request_library_scan(self, **kwargs):
            self.requested = kwargs
            return {"status": "queued", "scan_in_progress": True}

    class FakeSupervisor:
        def __init__(self) -> None:
            self.spawned = []

        def spawn_one_shot(self, name, coro):
            self.spawned.append(name)
            if asyncio.iscoroutine(coro):
                coro.close()

    scheduler = FakeScheduler()
    deps = SimpleNamespace(
        supervisor=FakeSupervisor(),
        scheduler=scheduler,
        comms_registry=None,
        settings_manager=None,
        assistant=None,
        notifications=None,
    )
    router = SetupRouter(deps)
    router._start_post_setup_tasks()
    assert scheduler.requested == {"force": True, "refresh_metadata": True, "reason": "post_setup"}


def main() -> None:
    test_media_template_is_inherited_without_duplication()
    test_settings_model_has_no_global_media_service_fields()
    test_settings_manager_does_not_migrate_old_settings_yaml()
    test_post_setup_uses_nonblocking_scan_request()
    print("Round 112 category inheritance and scan-status tests passed.")


if __name__ == "__main__":
    main()
