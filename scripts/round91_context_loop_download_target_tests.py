#!/usr/bin/env python3
"""Round 91 regression traces for LLM context loops and download targets.

These checks cover the two failures seen in the 2026-05-24 continuation logs:
- DOWNLOAD prompts exposing a huge tool surface and retaining raw search payloads.
- Ready-time TV imports producing an unsafe, category-root-invalid target such as
  ``Media/Season 5/For All Mankind.mkv`` instead of the show/season folder.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tool_policy import AgentToolPolicy
from src.ai.tool_result_compactor import ToolResultCompactor
from src.core.categories.registry import CategoryRegistry
from src.core.download_handler import DownloadCompletionHandler
from src.core.models import DownloadFileInfo, DownloadImportContext, DownloadItem, Intent, Settings
from src.core.security.path_policy import SafePathResolver


def test_download_tool_surface_is_small_and_focused() -> None:
    policy = AgentToolPolicy()
    names = policy.allowed_tool_names(Intent.DOWNLOAD)
    assert len(names) <= 16
    assert "search_media_torrents" in names
    assert "queue_download" in names
    assert "browse_page" not in names
    assert "browser_extract" not in names
    assert "research_category_services" not in names
    assert "get_category_creation_guide" not in names


def test_media_search_result_compaction_preserves_queue_ids() -> None:
    result = {
        "query": "For All Mankind S05E04 Italian",
        "category_id": "tv",
        "result_set_id": "rs-episode-5x04",
        "candidates": [
            {
                "index": i,
                "candidate_id": f"cand-{i}",
                "title": f"For.All.Mankind.S05E04.Release.{i}.1080p.h265",
                "size": "2.1 GB",
                "size_bytes": 2_100_000_000,
                "seeders": 100 - i,
                "source": "jackett",
                "quality_score": 80 - i,
                "languages": ["Italian", "English"],
                "resolution": "1080p",
                "codec": "h265",
                "raw_tracker_payload": "x" * 5000,
                "unit_descriptor": {
                    "stable_key": "tv:for-all-mankind:s05e04",
                    "label": "S05E04",
                    "granularity": "episode",
                    "coordinates": {"season": 5, "episode": 4},
                    "large_unused_blob": "y" * 5000,
                },
            }
            for i in range(30)
        ],
        "batch_recommendation": {
            "intent": "queue_missing_units",
            "candidate_ids": ["cand-0", "cand-25"],
            "queue_download_arguments": {
                "result_set_id": "rs-episode-5x04",
                "candidate_ids": ["cand-0", "cand-25"],
            },
            "groups": [
                {"unit": "S05E04", "recommended_candidate_id": "cand-25", "candidate_count": 30}
            ],
        },
    }
    compact_text = ToolResultCompactor().compact_for_message("search_media_torrents", result)
    assert "rs-episode-5x04" in compact_text
    assert "cand-0" in compact_text
    # Recommended candidates outside the visible top slice must still survive.
    assert "cand-25" in compact_text
    assert "raw_tracker_payload" not in compact_text
    assert "large_unused_blob" not in compact_text
    assert len(compact_text) < 9000


def test_tv_fallback_preserves_existing_show_and_season_folders() -> None:
    registry = CategoryRegistry()
    registry.register_defaults()
    tv = registry.get("tv")
    assert tv is not None

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        downloads = base / "downloads"
        series_root = base / "Media" / "Series"
        existing_season = series_root / "ForAllMankind" / "Season 5"
        existing_season.mkdir(parents=True)
        downloads.mkdir(parents=True)
        source = downloads / "For.All.Mankind.S05e04.Ita.Eng.Spa.1080p.h265.10bit.SubS-Me7alh.mkv"
        source.write_bytes(b"episode")

        settings = Settings(
            download_dir=str(downloads),
            library_root=str(base / "Media"),
            library_paths={"tv": str(series_root)},
        )
        item = DownloadItem(
            id="dl-1",
            item_name="For All Mankind",
            item_id="For All Mankind",
            magnet="magnet:?xt=urn:btih:test",
            category_id="tv",
            season=5,
            episode=4,
            save_path=str(downloads),
            import_context=DownloadImportContext(
                category_id="tv",
                item_id="For All Mankind",
                display_title="For All Mankind",
                season=5,
                episode=4,
                unit_descriptor={
                    "stable_key": "tv:for-all-mankind:s05e04",
                    "granularity": "episode",
                    "coordinates": {"season": 5, "episode": 4},
                },
            ),
        )
        df = DownloadFileInfo(
            file_index=0,
            file_path=source.name,
            size=len(b"episode"),
            downloaded_bytes=len(b"episode"),
            season=5,
            episode=4,
            status="complete",
            unit_descriptor={
                "stable_key": "tv:for-all-mankind:s05e04",
                "granularity": "episode",
                "coordinates": {"season": 5, "episode": 4},
            },
        )
        handler = DownloadCompletionHandler(
            downloader=object(),
            librarian=object(),
            notifications=object(),
            category_registry=registry,
            settings=settings,
            download_dir=downloads,
        )
        resolver = SafePathResolver.for_category(tv, settings, extra_roots=[downloads])
        unsafe_target = base / "Media" / "Season 5" / "For All Mankind.mkv"
        destination = handler._resolve_safe_completion_destination(  # noqa: SLF001 - regression seam
            resolver=resolver,
            target=unsafe_target,
            source=source,
            item=item,
            category=tv,
            settings=settings,
            file_info=df,
        )
        assert destination is not None
        safe_target, already_present = destination
        assert already_present is False
        assert safe_target == existing_season / source.name
        assert safe_target.name == source.name


def main() -> None:
    test_download_tool_surface_is_small_and_focused()
    test_media_search_result_compaction_preserves_queue_ids()
    test_tv_fallback_preserves_existing_show_and_season_folders()
    print("Round 91 context loop/download target regression traces passed")


if __name__ == "__main__":
    main()
