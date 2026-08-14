"""TV-owned cleanup and file-listing semantics."""

from __future__ import annotations

from typing import Any


class TvCleanupMixin:
    """Expose episodic file coordinates without teaching generic cleanup code TV rules."""

    def library_file_records_from_scan(self, scanned: Any) -> list[dict[str, Any]]:
        """Return local TV file records with explicit season/episode selectors."""
        records: list[dict[str, Any]] = []
        for scanned_file in list(getattr(scanned, "files", []) or []):
            size = int(getattr(scanned_file, "size_bytes", 0) or 0)
            season = getattr(scanned_file, "season", None)
            episode = getattr(scanned_file, "episode", None)
            records.append({
                "name": getattr(scanned, "name", ""),
                "category_id": self.category_id,
                "season": int(season) if season not in (None, "") else None,
                "episode": int(episode) if episode not in (None, "") else None,
                "path": getattr(scanned_file, "file_path", ""),
                "size_mb": round(size / (1024 * 1024), 1),
                "quality": getattr(scanned_file, "quality", ""),
            })
        return records

    def file_record_matches_selector(
        self,
        file_info: dict[str, Any],
        *,
        season: int | None = None,
        episode: int | None = None,
        year: int | None = None,
    ) -> bool:
        """Match a TV cleanup record only when requested coordinates agree."""
        if season is not None and file_info.get("season") != season:
            return False
        if episode is not None and file_info.get("episode") != episode:
            return False
        return True
