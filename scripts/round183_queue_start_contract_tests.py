#!/usr/bin/env python3
"""Round 183 regression checks for explicit queue/start behavior."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_user_approved_discovery_is_explicit() -> None:
    downloader = read("src/core/downloader.py")
    pipeline = read("src/core/search_pipeline.py")
    tv = read("src/core/categories/tv_workflows.py")
    require('reason = f"user approved discovery for {query}" if force else f"Auto-discovery for {query}"' in pipeline,
            "forced/user-approved discovery must write an explicit queue reason")
    require('"user approved"' in downloader and 'explicit_prefixes' in downloader,
            "downloader must recognize user-approved queue reasons")
    require('f"user approved TV workflow {workflow_name}"' in tv,
            "direct TV workflow magnets must use an explicit user-approved reason")
    require('f"user approved TV notification candidate for {unit_key}"' in tv,
            "notification candidate approval must use an explicit user-approved reason")


def test_explicit_duplicate_queued_rows_are_promoted() -> None:
    downloader = read("src/core/downloader.py")
    require('explicit_user_request = self._is_explicit_user_reason(reason)' in downloader,
            "add_magnet must classify the current queue request before duplicate handling")
    require('User-approved duplicate magnet' in downloader and 'promoting it for immediate queue processing' in downloader,
            "explicitly approving an already-queued magnet must not silently return the duplicate row")
    require('self._explicit_start_allowed.add(download_id)' in downloader,
            "explicit duplicate approvals must be allowed through the auto-download-off start gate")
    require('if await self._can_start_queued_download(item):' in downloader and 'await self._start_download(item)' in downloader,
            "add_magnet must run the immediate start gate after queueing/promoting")


def test_background_release_auto_does_not_masquerade_as_user_approval() -> None:
    tv = read("src/core/categories/tv_workflows.py")
    require('run_discovery(item, episode_label=unit_key, force=False, language=preferred_language)' in tv,
            "frontier auto-download release events should remain auto-discovery, not user-approved discovery")


def main() -> None:
    test_user_approved_discovery_is_explicit()
    test_explicit_duplicate_queued_rows_are_promoted()
    test_background_release_auto_does_not_masquerade_as_user_approval()
    print("round183 queue start contract tests passed")


if __name__ == "__main__":
    main()
