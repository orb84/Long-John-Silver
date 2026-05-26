#!/usr/bin/env python3
"""Round 17 reliability audit: torrent speed, planner placeholders, and file UI.

Validates fixes derived from logs(5) without requiring optional runtime
services such as libtorrent, TMDB, or aiosqlite.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(rel: str) -> str:
    return (ROOT / rel).read_text()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    bandwidth = text('src/core/bandwidth_manager.py')
    downloader = text('src/core/downloader.py') + text('src/core/downloader_sharing_mixin.py')
    require('* 1024' in bandwidth and '* 1000 // 8' not in bandwidth,
            'bandwidth manager must treat UI speed limits as KB/s, not kilobits/s')
    require(('int(quality.max_upload_speed_kbps) * 1024' in downloader)
            or ('int(quality.max_upload_speed_kbps or 0) * 1024' in downloader),
            'manual quality speed limits must also convert KB/s to bytes/s')

    executor = text('src/ai/plan_executor.py')
    require('_resolve_dynamic_arguments' in executor and r'\$\{' in executor,
            'plan executor must resolve ${step.field} placeholders before tool calls')
    require('_extract_latest_season' in executor and 'number_of_seasons' in executor,
            'latest-season placeholder alias must support metadata_lookup payloads')

    scheduler = text('src/core/scheduler.py')
    scheduler_services = text('src/core/scheduler_services.py')
    require(('_safe_tv_unit_int' in scheduler and '_safe_tv_unit_int(season)' in scheduler)
            or ('safe_tv_unit_int' in scheduler_services and 'safe_tv_unit_int(season)' in scheduler_services and 'extract_tv_unit_from_name' in scheduler_services),
            'torrent search must sanitize unresolved season/episode inputs defensively')

    resolver = text('src/core/torrent_resolver.py')
    require('_extract_trackers' in resolver and '&tr=' in resolver and '_extract_url_list' in resolver,
            'HTTP .torrent to magnet resolution must preserve trackers and webseeds')

    engine = text('src/core/torrent_engine.py')
    require('connections_limit", 600' in engine and 'listen_system_port_fallback' in engine,
            'torrent engine should use less restrictive connectivity settings')

    view_model = text('src/web/view_models/download_view_model.py')
    require('Single-file torrents can have reliable aggregate progress' in view_model,
            'single-file expanded rows must fall back to parent torrent progress')

    ui = text('src/web/static/js/components/downloadManagerUI.js')
    stats_patcher = text('src/web/static/js/components/downloadStatsPatcher.js')
    file_rows = text('src/web/static/js/components/downloadFileRows.js')
    cards = text('src/web/static/js/components/downloadCards.js')
    require(('dl.files.length >= 1' in ui and 'fallbackProgress > prog' in ui)
            or ('DownloadStatsPatcher' in stats_patcher and 'downloadedBytes' in stats_patcher and 'progressFor' in file_rows),
            'Hold UI must update single-file rows from live stats/parent progress')
    require('dl.files.length > 0' in cards and 'fallbackProgress > prog' in cards,
            'Download card UI must update single-file rows from live stats/parent progress')

    css = text('src/web/static/css/style.css')
    require('.file-row.no-ep' in css and 'overflow: hidden;' in css,
            'file-row CSS must handle rows without episode badges and avoid border overflow')

    print('round17 torrent speed/planner/file-ui audit passed')


if __name__ == '__main__':
    main()
