"""Dependency container for the download subsystem.

DownloadManager accepts this typed object rather than a long constructor
signature.  Keep new collaborators optional unless every test/composition root
can provide them, then promote them to required fields in a dedicated migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.bandwidth_manager import BandwidthManager
    from src.core.config import SettingsManager
    from src.core.database import Database
    from src.core.queue_manager import QueueManager
    from src.core.task_supervisor import TaskSupervisor
    from src.core.torrent_engine import TorrentEngine
    from src.core.torrent_resolver import TorrentUrlResolver


@dataclass
class DownloadDependencies:
    """Typed dependency container for DownloadManager construction."""

    download_dir: str
    db: "Database"
    supervisor: "TaskSupervisor"
    engine: "TorrentEngine"
    queue: "QueueManager"
    bandwidth: "BandwidthManager"
    settings_manager: "SettingsManager"
    max_concurrent: int = 3
    seed_ratio_target: float = 2.0
    seed_duration_hours: int = 48
    category_registry: object | None = None
    torrent_resolver: "TorrentUrlResolver" | None = None
    blacklist: object | None = None
    storage_monitor: object | None = None
