"""Round 25 verification audit for seed-in-place sharing integration.

This audit intentionally avoids importing the database layer so it can run in
minimal sandboxes that do not provide aiosqlite. It focuses on the logic that
regressed during the verification pass: bandwidth separation, Fair Share tool
exposure, and non-hidden seeding completion.
"""

from __future__ import annotations

from pathlib import Path

from src.core.torrent_engine import TorrentEngine


class _Status:
    """Tiny libtorrent status stand-in for active/paused checks."""

    paused = False


class _Handle:
    """Fake libtorrent handle capturing per-handle limits."""

    def __init__(self) -> None:
        self.download_limit: int | None = None
        self.upload_limit: int | None = None

    def is_valid(self) -> bool:
        """Return that the fake handle is usable."""
        return True

    def status(self) -> _Status:
        """Return a non-paused fake status."""
        return _Status()

    def set_download_limit(self, value: int) -> None:
        """Capture the download limit set by TorrentEngine."""
        self.download_limit = value

    def set_upload_limit(self, value: int) -> None:
        """Capture the upload limit set by TorrentEngine."""
        self.upload_limit = value


class _Session:
    """Fake libtorrent session capturing aggregate session settings."""

    def __init__(self) -> None:
        self.settings = {"download_rate_limit": 0, "upload_rate_limit": 0}

    def get_settings(self) -> dict[str, int]:
        """Return a mutable settings copy like libtorrent does."""
        return dict(self.settings)

    def apply_settings(self, settings: dict[str, int]) -> None:
        """Capture the latest aggregate settings."""
        self.settings.update(settings)


class Round25SharingAudit:
    """Runs static and lightweight behavioral checks for Round 25."""

    def __init__(self) -> None:
        """Create the audit with project-root helpers."""
        self.root = Path(__file__).resolve().parents[1]

    def run(self) -> None:
        """Execute all audit checks."""
        self.require_library_share_tool_surface()
        self.require_lifecycle_removes_completed_handles()
        self.require_bandwidth_policy_behaviour()
        print("round25 seed-in-place verification audit passed")

    def require_library_share_tool_surface(self) -> None:
        """Ensure the read-only Fair Share tool is registered and allow-listed."""
        downloads = (self.root / "src/ai/tools/downloads.py").read_text()
        policy = (self.root / "src/ai/tool_policy.py").read_text()
        prompt = (self.root / "src/ai/prompt_builder.py").read_text()
        assert "class ListLibrarySharesTool" in downloads
        assert "LibrarySharingService" in downloads
        assert "ListLibrarySharesTool(downloader=self._downloader" in downloads
        assert policy.count('"list_library_shares"') >= 3
        assert "call `list_library_shares`" in prompt

    def require_lifecycle_removes_completed_handles(self) -> None:
        """Ensure completed seeds cannot keep uploading invisibly."""
        lifecycle = (self.root / "src/core/downloader_lifecycle.py").read_text()
        assert "await self._ctx.engine.remove_torrent(download_id)" in lifecycle
        assert "cannot keep uploading invisibly" in lifecycle
        assert "if item.sharing_enabled" in lifecycle
        assert "return False" in lifecycle

    def require_bandwidth_policy_behaviour(self) -> None:
        """Exercise per-class bandwidth cap decisions with fake handles."""
        engine = TorrentEngine("/tmp/ljs-audit")
        engine._session = _Session()  # type: ignore[attr-defined]

        d1 = _Handle()
        d2 = _Handle()
        engine._handles = {"d1": d1, "d2": d2}  # type: ignore[attr-defined]
        engine._handle_modes = {"d1": "download", "d2": "download"}  # type: ignore[attr-defined]
        engine._rate_limits.update({"upload_rate_limit": 50 * 1024})  # type: ignore[attr-defined]
        engine._rebalance_rate_limits_sync()  # type: ignore[attr-defined]
        assert d1.upload_limit == 25 * 1024
        assert d2.upload_limit == 25 * 1024
        assert engine._session.settings["upload_rate_limit"] == 50 * 1024  # type: ignore[attr-defined]

        seed = _Handle()
        engine._handles = {"d1": d1, "seed": seed}  # type: ignore[attr-defined]
        engine._handle_modes = {"d1": "download", "seed": "library_seed"}  # type: ignore[attr-defined]
        engine._rate_limits.update({  # type: ignore[attr-defined]
            "upload_rate_limit": 0,
            "library_seed_upload_rate_limit": 40 * 1024,
            "pause_library_seeds_when_downloading": 0,
        })
        engine._rebalance_rate_limits_sync()  # type: ignore[attr-defined]
        assert d1.upload_limit == 0
        assert seed.upload_limit == 40 * 1024
        assert engine._session.settings["upload_rate_limit"] == 0  # type: ignore[attr-defined]

        engine._rate_limits.update({"pause_library_seeds_when_downloading": 1, "upload_rate_limit": 30 * 1024})  # type: ignore[attr-defined]
        engine._rebalance_rate_limits_sync()  # type: ignore[attr-defined]
        assert d1.upload_limit == 30 * 1024
        assert seed.upload_limit == 1
        assert engine._session.settings["upload_rate_limit"] == 30 * 1024  # type: ignore[attr-defined]


if __name__ == "__main__":
    Round25SharingAudit().run()
