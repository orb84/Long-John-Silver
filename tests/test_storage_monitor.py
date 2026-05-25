"""Tests for category-aware disk-space monitoring."""

from pathlib import Path

from src.core.models import Settings
from src.core.storage import StorageMonitor


class _SettingsManager:
    """Small settings-manager test double."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class _Category:
    """Small category test double."""

    category_id = "tv"
    display_name = "TV"

    def get_root_path(self, settings: Settings) -> str:
        """Return configured test category root."""
        return settings.category_settings["tv"]["library_path"]


class _Registry:
    """Small category-registry test double."""

    def list_all(self):
        """Return test categories."""
        return [_Category()]


def test_storage_monitor_groups_download_and_category_paths(tmp_path: Path) -> None:
    """Paths on the same temp filesystem should be grouped into one volume."""
    settings = Settings(
        download_dir=str(tmp_path / "downloads"),
        category_settings={"tv": {"library_path": str(tmp_path / "tv")}},
    )
    monitor = StorageMonitor(_SettingsManager(settings), _Registry())

    report = monitor.build_report()

    assert report.volumes
    assert any(path.purpose == "download_dir" for path in report.paths)
    assert any(path.category_id == "tv" for path in report.paths)
    assert "STORAGE STATUS" in report.llm_summary


def test_storage_capacity_decision_blocks_projected_low_space(tmp_path: Path) -> None:
    """A huge projected download should fail the reserve-space preflight."""
    settings = Settings(
        download_dir=str(tmp_path / "downloads"),
        category_settings={"tv": {"library_path": str(tmp_path / "tv")}},
    )
    monitor = StorageMonitor(_SettingsManager(settings), _Registry())
    report = monitor.build_report()
    target = next(path for path in report.paths if path.category_id == "tv")

    decision = monitor.check_download_capacity(
        category_id="tv",
        estimated_bytes=target.free_bytes,
    )

    assert decision.ok is False
    assert "minimum configured reserve" in decision.reason
