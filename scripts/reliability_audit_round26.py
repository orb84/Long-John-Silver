"""Round 26 audit for boot auto-start and download-completion regressions."""

from __future__ import annotations

from pathlib import Path


class Round26Audit:
    """Static audit for the Round 26 fixes that must not regress."""

    def __init__(self) -> None:
        """Create an audit rooted at the project directory."""
        self.root = Path(__file__).resolve().parents[1]

    def run(self) -> None:
        """Execute all Round 26 checks."""
        self.require_autostart_surface()
        self.require_ready_callback_settings_fix()
        self.require_partial_suffix_safety()
        self.require_plain_sharing_copy()
        print("round26 startup/completion/partial-file audit passed")

    def require_autostart_surface(self) -> None:
        """Ensure boot/login auto-start is wired through core, setup, and Compass."""
        autostart = (self.root / "src/core/autostart.py").read_text()
        settings = (self.root / "src/core/domain_models/settings.py").read_text()
        setup = (self.root / "src/web/static/js/pages/setup.js").read_text()
        panel = (self.root / "src/web/static/js/components/settingsPanel.js").read_text()
        registration = (self.root / "src/core/actions/registration.py").read_text()
        assert "class AutoStartManager" in autostart
        assert "LaunchAgents" in autostart and ".config" in autostart and "winreg" in autostart
        assert "auto_start_at_login" in settings
        assert "/api/setup/startup" in setup
        assert "pref-auto-start" in panel
        assert "settings_update_startup" in registration
        assert "setup_startup" in registration

    def require_ready_callback_settings_fix(self) -> None:
        """Ensure target path planning gets live settings and movie paths are explicit."""
        handler = (self.root / "src/core/download_handler.py").read_text()
        movie = (self.root / "src/core/categories/movie.py").read_text()
        main = (self.root / "main.py").read_text()
        assert "def _current_settings" in handler
        assert "settings=settings" in handler
        assert "category_settings" in handler and "always pass live settings" in handler
        assert "category_id=item.category_id" in handler
        assert "def compute_target_path" in movie
        assert "settings_manager=settings_manager" in main
        assert main.index("downloader.set_ready_callback") < main.index("await downloader.recover_downloads()")
        assert main.index("librarian = Librarian") < main.index("await downloader.recover_downloads()")

    def require_partial_suffix_safety(self) -> None:
        """Ensure pauses/shutdowns no longer make partial files look complete."""
        downloader = (self.root / "src/core/downloader.py").read_text()
        repair = (self.root / "src/core/download_partial_files.py").read_text()
        assert "shutdown now leaves incomplete payload names" in downloader
        assert "Do not remove .downloading while a torrent is merely paused" in downloader
        assert "repair_partial_file_suffixes" in downloader
        assert "PartialDownloadMarkerRepairService" in downloader
        assert "Restored partial-file marker" in repair

    def require_plain_sharing_copy(self) -> None:
        """Ensure the obscure sharing text has been replaced."""
        panel = (self.root / "src/web/static/js/components/settingsPanel.js").read_text()
        setup = (self.root / "src/web/templates/setup.html").read_text()
        forbidden = "Overlay modes are intentionally not enabled yet"
        assert forbidden not in panel
        assert forbidden not in setup
        assert "Seed in place means LJS keeps the original torrent folder as the library copy" in panel
        assert "Choose this if you want to give back to the swarm" in setup


if __name__ == "__main__":
    Round26Audit().run()
