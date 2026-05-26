"""Round 114 release-maintenance structural checks.

These checks intentionally avoid embedding or matching any real credentials.
They verify ownership boundaries and stale-path cleanup that are easy to regress
when polishing the release tree.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    """Raise a clear assertion for shell-friendly execution."""
    if not condition:
        raise AssertionError(message)


def text(path: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (ROOT / path).read_text(encoding="utf-8")


def test_startup_uses_category_owned_services() -> None:
    """Ensure startup no longer reads media integration globals."""
    main = text("main.py")
    forbidden = (
        "settings.tmdb_api_key",
        "settings.trakt_client_id",
        "settings.trakt_access_token",
        "settings.plex_url",
        "settings.plex_token",
    )
    for token in forbidden:
        require(token not in main, f"main.py still reads deprecated global integration field {token}")
    require("_category_service_value" in main, "main.py should read media services through category helpers")
    require('"media", "tmdb"' in main, "TMDB client should be initialized from the media category")
    require('"media", "trakt"' in main, "Trakt client should be initialized from the media category")
    require('"tv", "tvmaze"' in main, "TVMaze client should be initialized from the TV category")


def test_runner_and_env_docs_point_to_live_local_settings() -> None:
    """Ensure user-facing launch files reference settings.local.yaml."""
    for path in ("run.sh", "run.bat", ".env.example"):
        body = text(path)
        require("settings.local.yaml" in body, f"{path} should mention ignored live settings.local.yaml")
    require('config/settings.yaml" ]; then' not in text("run.sh"), "run.sh should not probe removed settings.yaml")
    require('config\\settings.yaml"' not in text("run.bat"), "run.bat should not probe removed settings.yaml")


def test_integrations_endpoint_uses_structured_category_payload() -> None:
    """Ensure Compass saves service config by category, not global field names."""
    router = text("src/web/routers/settings.py")
    handler = text("src/web/action_handlers/settings.py")
    saver = text("src/web/static/js/components/settingsSavers.js")
    require("category_services" in router, "settings router should accept category_services payload")
    require("category_services" in handler, "settings action should merge category_services into category config")
    require("category_services" in saver, "settings UI should send category_services payload")
    for token in (
        'args["tmdb_api_key"]',
        'args["trakt_client_id"]',
        'args["plex_url"]',
        'kwargs["tmdb_api_key"]',
        'kwargs["trakt_client_id"]',
        'kwargs["plex_url"]',
    ):
        require(token not in router + handler, f"integration save path still contains stale global mapping {token}")


def test_settings_model_has_single_scan_timestamp() -> None:
    """Catch duplicate fields introduced during fast merge rounds."""
    settings_model = text("src/core/domain_models/settings.py")
    require(settings_model.count("last_library_scan_at: str") == 1, "Settings should define last_library_scan_at once")


def test_release_maintenance_doc_exists() -> None:
    """Keep the current release-review contract discoverable."""
    doc = text("docs/RELEASE_MAINTENANCE_REVIEW.md")
    require("Configuration ownership" in doc, "maintenance doc should explain config ownership")
    require("high-risk areas" in doc, "maintenance doc should list current high-risk areas")
    require("config/category-definitions/*.yaml" in doc, "maintenance doc should explain shareable definitions")
    require("config/categories/*.yaml" in doc, "maintenance doc should explain ignored live category config")


def main() -> None:
    """Run all checks without requiring pytest."""
    test_startup_uses_category_owned_services()
    test_runner_and_env_docs_point_to_live_local_settings()
    test_integrations_endpoint_uses_structured_category_payload()
    test_settings_model_has_single_scan_timestamp()
    test_release_maintenance_doc_exists()
    print("Round 114 maintenance review checks passed")


if __name__ == "__main__":
    main()
