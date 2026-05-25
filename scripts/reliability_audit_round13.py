"""Round 13 reliability audit.

Covers the regressions reported after round 12:
- max_concurrent enforcement must pause/requeue excess active downloads;
- inactive downloads must not retain stale speed/ETA in UI view models;
- per-file progress must be computed from persisted downloaded bytes even when
  live cache is empty or paths changed;
- metadata lookup must use release year as a disambiguator instead of first-hit
  name similarity.
"""
from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

# The sandbox used for artifact generation may not have optional runtime DB
# dependencies installed.  The audit exercises pure logic with fakes, so stub
# aiosqlite before importing DownloadManager.
sys.modules.setdefault('aiosqlite', types.SimpleNamespace(Connection=object, Cursor=object, Row=dict))

from src.core.models import DownloadItem, DownloadStatus, DownloadPriority
from src.core.downloader import DownloadManager, DownloadDependencies
from src.web.view_models.download_view_model import DownloadViewModelBuilder
from src.core.models import DownloadFileInfo
from src.core.categories.metadata.enricher import TMDBMetadataEnricher


class FakeDownloads:
    def __init__(self):
        self.rows: dict[str, DownloadItem] = {}

    async def get_active_downloads(self):
        return [d for d in self.rows.values() if d.status not in (DownloadStatus.COMPLETE, DownloadStatus.CANCELLED, DownloadStatus.FAILED)]

    async def get_download(self, did):
        return self.rows.get(did)

    async def upsert_download(self, item):
        self.rows[item.id] = item


class FakeDB:
    def __init__(self):
        self.downloads = FakeDownloads()


class FakeQueue:
    def __init__(self):
        self.active = set()
        self.limit = 99
    def register_active(self, did): self.active.add(did)
    def deregister_active(self, did): self.active.discard(did)
    def active_count(self): return len(self.active)
    def sync_active(self, ids): self.active = set(ids)
    def set_max_concurrent(self, n): self.limit = n


class FakeEngine:
    def __init__(self):
        self.handles = {}
        self.limit = None
    async def initialize(self): pass
    async def close(self): pass
    async def set_max_concurrent(self, n): self.limit = n
    def get_handle(self, did): return self.handles.get(did)
    async def remove_torrent(self, did): self.handles.pop(did, None)


class FakeSupervisor:
    def spawn_restartable(self, *a, **k): return object()
    def cancel(self, *a, **k): pass
    def cancel_by_prefix(self, *a, **k): pass


class FakeBandwidth:
    async def run_loop(self): pass


class FakeSettings:
    settings = SimpleNamespace(auto_download=True, security=SimpleNamespace())


async def audit_concurrency():
    db = FakeDB()
    queue = FakeQueue()
    manager = DownloadManager(DownloadDependencies(
        download_dir='/tmp/ljs-audit', db=db, supervisor=FakeSupervisor(),
        engine=FakeEngine(), queue=queue, bandwidth=FakeBandwidth(),
        settings_manager=FakeSettings(), max_concurrent=2,
    ))
    priorities = [DownloadPriority.HIGH, DownloadPriority.HIGH, DownloadPriority.NORMAL, DownloadPriority.LOW]
    for idx, prio in enumerate(priorities, start=1):
        item = DownloadItem(id=f'd{idx}', item_name='Show', magnet=f'magnet:{idx}', status=DownloadStatus.DOWNLOADING, priority=prio, episode=idx, download_rate=1234, upload_rate=1, eta_seconds=99)
        db.downloads.rows[item.id] = item
    await manager._enforce_concurrency_limit()
    rows = db.downloads.rows
    active = [d for d in rows.values() if d.status == DownloadStatus.DOWNLOADING]
    assert len(active) == 2, f'expected 2 active, got {[(d.id,d.status) for d in rows.values()]}'
    queued = [d for d in rows.values() if d.status == DownloadStatus.QUEUED]
    assert queued and all(d.download_rate == 0 and d.eta_seconds == 0 for d in queued), 'queued rows must have zero stale rates'


def audit_view_model_progress():
    class D:
        def get_file_progress(self, _): return []
    item = DownloadItem(id='x', item_name='Movie', magnet='m', status=DownloadStatus.QUEUED, download_rate=999, eta_seconds=50)
    item.files = [DownloadFileInfo(file_index=0, file_path='f.mkv', size=1000, downloaded_bytes=250)]
    vm = DownloadViewModelBuilder(D()).build(item)
    assert vm['download_rate'] == 0 and vm['eta_seconds'] == 0
    assert vm['files'][0]['downloaded_bytes'] == 250
    assert abs(vm['files'][0]['progress'] - 0.25) < 0.001


def audit_metadata_scoring():
    enricher = TMDBMetadataEnricher(None, None)
    title, year = enricher._extract_lookup_identity('DrStrangelove_(1964).1080p.BluRay.x265')
    assert year == 1964 and 'DrStrangelove' in title.replace(' ', '')
    results = [
        {'id': 1, 'title': 'Dr. Strangelove Jr', 'year': '2019', 'vote_count': 9000},
        {'id': 2, 'title': 'Dr. Strangelove', 'year': '1964', 'vote_count': 500},
    ]
    best = enricher._choose_best_search_result(results, 'Dr Strangelove', 1964)
    assert best['id'] == 2, best


async def main():
    await audit_concurrency()
    audit_view_model_progress()
    audit_metadata_scoring()
    print('round13 audit passed')


if __name__ == '__main__':
    asyncio.run(main())
