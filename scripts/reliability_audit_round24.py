#!/usr/bin/env python3
"""Round 24 audit: seed-in-place library sharing feature surface.

Verifies that the first implementation of opt-in library seeding includes the
required model, downloader, API, setup, Compass, and dedicated status-view
surfaces without depending on optional services.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Round24LibrarySharingAudit:
    """Static invariants for seed-in-place library sharing."""

    def run(self) -> None:
        """Run all Round 24 library-sharing invariants."""
        self.require_architecture_doc()
        self.require_settings_and_storage_model()
        self.require_downloader_seed_in_place_support()
        self.require_api_and_action_surface()
        self.require_setup_and_compass_options()
        self.require_dedicated_sharing_view()
        print("round24 seed-in-place library sharing audit passed")

    def read(self, rel: str) -> str:
        """Read a project file as UTF-8 text."""
        return (ROOT / rel).read_text(encoding="utf-8")

    def require(self, condition: bool, message: str) -> None:
        """Raise an assertion when a required invariant is missing."""
        if not condition:
            raise AssertionError(message)

    def require_architecture_doc(self) -> None:
        """Ensure the feature has a dedicated design/operations document."""
        doc = self.read("docs/LIBRARY_SHARING_SEED_IN_PLACE.md")
        for token in ("Seed-in-place", "separate upload", "TorrentLibraryBinding", "Future work"):
            self.require(token in doc, f"sharing architecture doc missing {token!r}")

    def require_settings_and_storage_model(self) -> None:
        """Ensure policy and telemetry persist in domain models and migrations."""
        settings = self.read("src/core/domain_models/settings.py")
        downloads = self.read("src/core/domain_models/downloads.py")
        migration = self.read("migrations/102_library_sharing_seed_in_place.sql")
        repository = self.read("src/core/repositories/download.py")
        for token in ("class SharingSettings", "LibrarySharingMode", "library_upload_speed_kbps", "category_enabled"):
            self.require(token in settings, f"settings model missing {token!r}")
        for token in ("save_path", "sharing_enabled", "uploaded_bytes", "seed_ratio"):
            self.require(token in downloads and token in migration and token in repository,
                         f"download sharing telemetry missing {token!r}")

    def require_downloader_seed_in_place_support(self) -> None:
        """Ensure torrent placement and bandwidth separation are implemented."""
        mixin = self.read("src/core/downloader_sharing_mixin.py")
        engine = self.read("src/core/torrent_engine.py")
        lifecycle = self.read("src/core/downloader_lifecycle.py")
        handler = self.read("src/core/download_handler.py")
        downloader = self.read("src/core/downloader.py")
        for token in ("DownloadSharingMixin", "_resolve_torrent_save_path", "category.get_root_path", "sharing_enabled"):
            self.require(token in mixin, f"download sharing mixin missing {token!r}")
        for token in ("library_seed_upload_rate_limit", "_handle_modes", "mark_handle_mode", "library_seed"):
            self.require(token in engine, f"torrent engine sharing quota missing {token!r}")
        self.require("mark_handle_mode" in lifecycle and "uploaded_bytes" in lifecycle and "seed_ratio" in lifecycle,
                     "lifecycle must switch completed library torrents to seed quota and track ratios")
        self.require("seed_in_place" in handler and "retaining library payload" in handler,
                     "download handler must keep seed-in-place payloads intact")
        self.require("original_status == DownloadStatus.SEEDING and item.sharing_enabled" in downloader,
                     "startup recovery must restart previously seeding library shares")

    def require_api_and_action_surface(self) -> None:
        """Ensure setup/settings write APIs and sharing read API are wired."""
        app = self.read("src/web/app.py")
        sharing_router = self.read("src/web/routers/sharing.py")
        settings_router = self.read("src/web/routers/settings.py")
        setup_router = self.read("src/web/routers/setup.py")
        actions = self.read("src/core/actions/registration.py")
        service = self.read("src/core/library_sharing.py")
        self.require("SharingRouter" in app, "sharing router must be included in the FastAPI app")
        self.require("/api/sharing/library" in sharing_router and "LibrarySharingService" in sharing_router,
                     "sharing router must expose library sharing status")
        self.require("/api/settings/sharing" in settings_router and "settings_update_sharing" in actions,
                     "Compass sharing settings endpoint/action missing")
        self.require("/api/setup/sharing" in setup_router and "setup_sharing" in actions,
                     "first-run sharing endpoint/action missing")
        self.require("active_upload_bps" in service and "seed_ratio" in service,
                     "sharing read model must expose rates and ratios")

    def require_setup_and_compass_options(self) -> None:
        """Ensure users can choose sharing at first setup and later in Compass."""
        setup_html = self.read("src/web/templates/setup.html")
        setup_js = self.read("src/web/static/js/pages/setup.js")
        settings_panel = self.read("src/web/static/js/components/settingsPanel.js")
        for token in ("Sharing & Seeding", "sharing-mode", "seed_in_place", "setup-sharing-upload"):
            self.require(token in setup_html, f"setup UI missing {token!r}")
        self.require("/api/setup/sharing" in setup_js and "updateSharingHighlight" in setup_js,
                     "setup JS must save and explain the sharing choice")
        for token in ("Sharing & Seeding", "saveSharing", "pref-sharing-upload-speed", "pause_when_downloading"):
            self.require(token in settings_panel, f"Compass sharing panel missing {token!r}")

    def require_dedicated_sharing_view(self) -> None:
        """Ensure the UI has a dedicated library sharing page."""
        base = self.read("src/web/templates/base.html")
        index = self.read("src/web/templates/index.html")
        app_js = self.read("src/web/static/js/app.js")
        view_manager = self.read("src/web/static/js/components/viewManager.js")
        panel = self.read("src/web/static/js/components/sharingPanel.js")
        css = self.read("src/web/static/css/style.css")
        for token in ("data-target=\"sharing\"", "sharingPanel.js"):
            self.require(token in base, f"base template missing {token!r}")
        self.require('id="sharing" class="view"' in index, "index must contain sharing view container")
        self.require("new SharingPanel" in app_js and "window.sharingPanel" in view_manager,
                     "front-end app must instantiate and reload the sharing panel")
        for token in ("Library Sharing", "Ratio", "Uploaded", "Seeds / peers", "_formatCap"):
            self.require(token in panel, f"sharing panel missing {token!r}")
        self.require("sharing-card" in css and "sharing-summary-grid" in css,
                     "sharing panel CSS missing core classes")


if __name__ == "__main__":
    Round24LibrarySharingAudit().run()
