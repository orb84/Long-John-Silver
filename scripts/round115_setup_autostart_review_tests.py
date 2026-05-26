#!/usr/bin/env python3
"""Round 115 setup/autostart maintenance structural checks.

The checks intentionally use only repository text and synthetic expectations.
They do not embed, compare, or search for real credential values.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    """Raise a clear assertion for shell-friendly execution."""
    if not condition:
        raise AssertionError(message)


def test_setup_writes_category_private_config() -> None:
    """First-run setup should save category-owned values into category config."""
    setup_js = read("src/web/static/js/pages/setup.js")
    setup_router = read("src/web/routers/setup.py")
    setup_handler = read("src/web/action_handlers/setup.py")
    registration = read("src/core/actions/registration.py")

    require("/api/setup/category-config" in setup_js, "setup JS should call the setup category-config endpoint")
    require("function saveSetupMediaServices" in setup_js, "setup should save media services explicitly")
    require("function saveSetupMediaPreferences" in setup_js, "setup should save media download profile explicitly")
    require("category_settings: { media: { services:" in setup_js, "TMDB/Trakt should be saved under media.services")
    require("category_settings: { media: { download_profile:" in setup_js, "language/resolution should be saved under media.download_profile")
    require("APIClient.post('/api/settings/quality'" not in setup_js, "setup should not save media preferences through the old quality endpoint")

    require('router.add_api_route("/api/setup/category-config"' in setup_router, "setup router should expose category-config endpoint")
    require("Depends(verify_auth)" in setup_router, "setup mutation endpoints should use normal auth after setup is complete")
    require("setup_category_config" in registration, "setup category config action should be registered")
    require("def _merge_category_settings" in setup_handler, "setup handler should deep-merge category payloads")
    require("def _deep_merge" in setup_handler, "setup handler should avoid shallow category overwrites")


def test_compass_content_preferences_are_media_category_profile() -> None:
    """Compass Content Selection should save media profile, not global quality."""
    panel = read("src/web/static/js/components/settingsPanel.js")
    require("const mediaProfile" in panel, "Compass should load content controls from media.download_profile")
    require("category_settings: {\n                    media: {\n                        download_profile:" in panel, "Compass content save should target media.download_profile")
    require("Shared Media category content preferences saved" in panel, "toast should describe category-owned save")
    forbidden = "async saveContentPreferences() {\n        try {\n            await APIClient.post('/api/settings', {\n                default_quality:"
    require(forbidden not in panel, "content preferences should not post global default_quality")
    require("max_download_speed_kbps" in panel and "max_upload_speed_kbps" in panel, "bandwidth caps should remain global download settings")


def test_settings_category_merges_are_deep() -> None:
    """Compass category saves should not erase sibling services/preferences."""
    settings_handler = read("src/web/action_handlers/settings.py")
    require("def _merge_category_settings" in settings_handler, "settings handler should share category deep merge helper")
    require("def _deep_merge" in settings_handler, "settings handler should recursively merge category config")
    require("self._merge_category_settings(settings, kwargs.get(\"category_settings\"))" in settings_handler, "library save should deep-merge category_settings")


def test_autostart_reports_current_checkout_not_stale_entry() -> None:
    """Launch-at-login should point at the current source root and detect stale entries."""
    autostart = read("src/core/autostart.py")
    require("def _default_project_root" in autostart, "autostart should derive the source checkout root")
    init_body = autostart.split("def __init__", 1)[1].split("def status", 1)[0]
    require("os.getcwd()" not in init_body, "autostart initializer should not default to the shell current directory")
    require("_file_entry_matches_current_target" in autostart, "file launch entries should be checked for stale paths")
    require("_windows_value_matches_current_target" in autostart, "Windows Run value should be checked against current command")
    is_enabled_head = autostart.split("def is_enabled", 1)[1].split("def set_enabled", 1)[0]
    require("windows" in is_enabled_head and "_windows_value_matches_current_target" in is_enabled_head, "Windows is_enabled branch should be reachable")
    require("working_dir" in autostart and "command" in autostart, "autostart status should expose target details for diagnostics")


def test_docs_cover_setup_and_login_startup_boundaries() -> None:
    """Release docs should explain the reviewed setup/startup contracts."""
    readme = read("README.md")
    maintenance = read("docs/RELEASE_MAINTENANCE_REVIEW.md")
    autostart_doc = read("docs/AUTOSTART_BOOT_INTEGRATION.md")
    history = read("docs/project-history/ROUND115_SETUP_AUTOSTART_MAINTENANCE.md")

    require("Content Selection" in readme and "Media category" in readme, "README should explain category-owned content preferences")
    require("setup_category_config" in maintenance, "maintenance doc should name setup category-config ownership")
    require("verify_auth" in maintenance, "maintenance doc should mention post-setup auth for setup mutations")
    require("stale" in autostart_doc.lower(), "autostart docs should mention stale entry detection")
    require("Round 115" in history, "Round 115 history note should exist")
    require("config/categories/media.yaml" in history, "Round 115 history should mention private media config")


def main() -> None:
    """Run all Round 115 checks."""
    for test in (
        test_setup_writes_category_private_config,
        test_compass_content_preferences_are_media_category_profile,
        test_settings_category_merges_are_deep,
        test_autostart_reports_current_checkout_not_stale_entry,
        test_docs_cover_setup_and_login_startup_boundaries,
    ):
        test()
        print(f"PASS {test.__name__}")
    print("Round 115 setup/autostart maintenance checks passed.")


if __name__ == "__main__":
    main()
