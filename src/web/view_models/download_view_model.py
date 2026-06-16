"""
DownloadViewModelBuilder for LJS.

Builds enriched download view models by merging live file progress
from the WebSocket cache into model data. Extracted from the
DownloadsRouter to isolate presentation logic from routing.
"""

from typing import Any


class DownloadViewModelBuilder:
    """Builds enriched download dicts with live file-level progress.

    Merges cached progress from the downloader's file-progress store
    into the download model's per-file data. When the model has no
    files (e.g. existing downloads that predate per-file tracking),
    falls back entirely to the cache.

    Dependencies (injected at composition root):
        downloader — object with ``get_file_progress(download_id)``
    """

    def __init__(self, downloader: Any) -> None:
        self._downloader = downloader


    @staticmethod
    def _add_swarm_display_fields(result: dict) -> None:
        """Add stable swarm fields for UI display.

        ``num_seeds`` is connected live seeds and may be zero while the indexer
        snapshot or tracker scrape still proves that a swarm exists.  Keeping a
        separate display value prevents cards from bouncing between "60 seeds"
        and "0 seeds" when the raw connected count changes between polls.
        """
        live = int(result.get('num_seeds') or 0)
        source = result.get('source_seeders')
        try:
            source_int = int(source) if source is not None else 0
        except (TypeError, ValueError):
            source_int = 0
        if live > 0:
            result['display_seeders'] = live
            result['display_seeders_basis'] = 'connected'
        elif source_int > 0:
            result['display_seeders'] = source_int
            result['display_seeders_basis'] = 'source'
        else:
            result['display_seeders'] = 0
            result['display_seeders_basis'] = 'none'

    @staticmethod
    def _apply_parent_progress_estimate(result: dict, result_files: list[dict]) -> None:
        """Fill blank multi-file rows from aggregate torrent progress.

        libtorrent can report a reliable torrent-level percentage before this
        process has received usable per-file byte counters after metadata/restart
        transitions.  In that state the expanded UI must not show every file at
        0% while the parent card shows progress.  The fallback is marked as an
        estimate so exact per-file cache updates can replace it later.
        """
        if len(result_files) <= 1:
            return
        try:
            parent_progress = float(result.get('progress') or 0.0)
        except (TypeError, ValueError):
            parent_progress = 0.0
        if parent_progress <= 0:
            total_size = int(result.get('total_size') or result.get('size_bytes') or 0)
            downloaded = int(result.get('downloaded_bytes') or 0)
            if total_size > 0 and downloaded > 0:
                parent_progress = min(1.0, max(0.0, downloaded / total_size))
        if parent_progress <= 0:
            return
        has_exact_file_progress = any(
            (int(f.get('downloaded_bytes') or 0) > 0 or float(f.get('progress') or 0.0) > 0)
            and not f.get('progress_estimated')
            for f in result_files
        )
        if has_exact_file_progress:
            return
        selected = []
        for f in result_files:
            try:
                priority = int(f.get('priority') or 0)
            except (TypeError, ValueError):
                priority = 0
            if priority > 0:
                selected.append(f)
        target_files = selected or result_files
        for f in target_files:
            if f.get('status') in ('complete', 'organized'):
                continue
            size = int(f.get('size') or 0)
            if size <= 0:
                continue
            estimated_progress = min(1.0, max(0.0, parent_progress))
            f['progress'] = max(float(f.get('progress') or 0.0), estimated_progress)
            f['downloaded_bytes'] = max(int(f.get('downloaded_bytes') or 0), int(size * estimated_progress))
            f['progress_estimated'] = True
            f['progress_basis'] = 'estimated_from_parent_torrent_progress'

    def build(self, item: Any) -> dict:
        """Merge live file progress from cache into the model's per-file data.

        Falls back to cache data when model files are empty (e.g. existing
        downloads that predate per-file tracking). This ensures the UI always
        shows file-level progress even before metadata parsing completes on
        restart.

        Args:
            item: A download model instance with ``model_dump()``, ``id``,
                  and ``files`` attributes.

        Returns:
            A dict representing the enriched download suitable for JSON
            serialisation.
        """
        result = item.model_dump()
        # Non-active rows must not keep the last instantaneous rates.  A pause,
        # queued requeue, or stall means the current transfer rate is zero even
        # if an older WebSocket stat was cached in the browser.
        if str(result.get('status', '')).lower() not in {'downloading', 'seeding'}:
            result['download_rate'] = 0.0
            result['upload_rate'] = 0.0
            result['eta_seconds'] = 0.0
            result['num_peers'] = 0
            result['num_seeds'] = 0
        self._add_swarm_display_fields(result)
        cache_files = self._downloader.get_file_progress(item.id)

        if item.files:
            def _norm(value: str) -> str:
                text = str(value or '')
                return text[:-12] if text.endswith('.downloading') else text

            progress_by_path = {}
            progress_by_index = {}
            for fp in cache_files:
                payload = {
                    'progress': fp.get('progress', 0.0),
                    'downloaded_bytes': int(fp.get('downloaded', fp.get('downloaded_bytes', 0)) or 0),
                }
                if 'path' in fp:
                    progress_by_path[_norm(fp['path'])] = payload
                if fp.get('file_index') is not None:
                    progress_by_index[int(fp['file_index'])] = payload
            result_files = result.get('files', [])
            for f in result_files:
                cached = progress_by_path.get(_norm(f.get('file_path')))
                if cached is None and f.get('file_index') is not None:
                    cached = progress_by_index.get(int(f['file_index']))
                if cached is not None:
                    f['progress'] = cached['progress']
                    f['downloaded_bytes'] = cached['downloaded_bytes']
                elif f.get('status') in ('complete', 'organized'):
                    f['progress'] = 1.0
                    f['downloaded_bytes'] = f.get('size', 0)
                else:
                    downloaded = f.get('downloaded_bytes', 0) or 0
                    size = f.get('size', 0) or 0
                    # Single-file torrents can have reliable aggregate progress
                    # before libtorrent reports a per-file byte count after a
                    # metadata/restart transition. Mirror the parent progress so
                    # the expanded file row does not misleadingly stay at 0 B
                    # while the card itself is moving.
                    if len(result_files) == 1 and not downloaded:
                        parent_downloaded = result.get('downloaded_bytes', 0) or 0
                        if parent_downloaded and size:
                            downloaded = min(int(parent_downloaded), int(size))
                    parent_progress = result.get('progress', 0.0) or 0.0
                    f['progress'] = (downloaded / size) if size else (f.get('progress', parent_progress) or parent_progress)
                    if len(result_files) == 1 and parent_progress > f['progress']:
                        f['progress'] = parent_progress
                    f['downloaded_bytes'] = downloaded
            self._apply_parent_progress_estimate(result, result_files)
        elif cache_files:
            result['files'] = [
                {
                    'file_index': i,
                    'file_path': fp.get('path', ''),
                    'size': fp.get('size', 0),
                    'downloaded_bytes': int(fp.get('downloaded', 0)),
                    'progress': fp.get('progress', 0.0),
                    'priority': 4,
                    'season': None,
                    'episode': None,
                    'status': 'downloading' if fp.get('progress', 0) < 1.0 else 'complete',
                    'organized_path': None,
                }
                for i, fp in enumerate(cache_files)
            ]
        return result
