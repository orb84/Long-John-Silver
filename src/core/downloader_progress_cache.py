"""DownloadFileProgressCache for LJS.

Stores per-download per-file progress snapshots so that the
WebSocket endpoint can serve them without reaching into the
monitor's internal state.
"""


class DownloadFileProgressCache:
    """Cache of per-file download progress for WebSocket streaming.

    The DownloadLifecycleMonitor pushes file progress updates via callbacks;
    this cache stores them so that ``get_file_progress()`` can return them
    on demand for WebSocket polling.
    """

    def __init__(self) -> None:
        self._progress: dict[str, list[dict]] = {}
        self._renamed: set[str] = set()

    def get_file_progress(self, download_id: str) -> list[dict]:
        """Return cached per-file progress for a download."""
        return self._progress.get(download_id, [])

    def update(self, download_id: str, files: list[dict]) -> None:
        """Store a file progress snapshot for the given download."""
        self._progress[download_id] = files

    def mark_renamed(self, download_id: str) -> None:
        """Record that .downloading extensions were applied."""
        self._renamed.add(download_id)

    def mark_restored(self, download_id: str) -> None:
        """Record that .downloading extensions were removed."""
        self._renamed.discard(download_id)

    def is_renamed(self, download_id: str) -> bool:
        """Check whether a download currently has .downloading extensions."""
        return download_id in self._renamed

    def clear_renamed(self) -> None:
        """Clear all renamed download tracking."""
        self._renamed.clear()

    def clear(self, download_id: str) -> None:
        """Remove all cached state for a download (cancel / cleanup)."""
        self._progress.pop(download_id, None)
        self._renamed.discard(download_id)

    @property
    def renamed_downloads(self) -> set[str]:
        """Set of all download IDs currently with .downloading extensions."""
        return set(self._renamed)
