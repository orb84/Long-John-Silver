#!/usr/bin/env python3
"""Round 16 reliability audit: torrent recovery and swarm visibility.

Validates the log-derived fixes without requiring optional runtime dependencies
like libtorrent or aiosqlite.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(rel: str) -> str:
    return (ROOT / rel).read_text()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    downloader = text('src/core/downloader.py')
    require('if self._is_explicit_user_reason(getattr(item, "reason", None)):' in downloader,
            'manual/user-approved queued downloads must be startable after restart')
    require('original_status == DownloadStatus.DOWNLOADING' in downloader,
            'actively downloading rows must be allowed to resume after restart')
    require('it was not user-approved' in downloader,
            'held recovery log should distinguish background rows from user-approved work')

    lifecycle = text('src/core/downloader_lifecycle.py')
    require('"num_seeds": getattr(s, "num_seeds", 0)' in lifecycle,
            'runtime libtorrent seed count must be extracted')
    require('item.num_seeds = stats.get("num_seeds", 0) or 0' in lifecycle,
            'runtime seed count must be persisted on DownloadItem')

    ui = text('src/web/static/js/components/downloadManagerUI.js')
    require('seeds ${liveSeeds} · peers ${peers}' in ui,
            'Hold UI should label live seeds and peers separately')
    require('src ${sourceSeeders}' in ui,
            'Hold UI should expose search-time source seeders only as a snapshot')

    repo = text('src/core/repositories/download.py')
    require('num_peers, num_seeds' in repo and 'source_seeders' in repo,
            'download repository must persist live and source seed counts')

    migration = text('migrations/101_download_seed_counts.sql')
    require('ADD COLUMN num_seeds' in migration and 'ADD COLUMN source_seeders' in migration,
            'migration must add seed visibility columns to existing DBs')

    engine = text('src/core/torrent_engine.py')
    require('connections_limit"' in engine and 'enable_upnp", True' in engine and 'listen_system_port_fallback' in engine,
            'torrent engine should use desktop-like connectivity defaults and port fallback')
    require('Torrent alert:' in engine,
            'torrent engine should log tracker/DHT/port diagnostics')

    print('round16 torrent recovery/swarm audit passed')


if __name__ == '__main__':
    main()
