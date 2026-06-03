#!/usr/bin/env python3
"""Round 201 regression tests for Jackett bootstrap result reporting.

When Jackett's admin indexer endpoint is login-gated, LJS may configure public
indexers through runtime-file bootstrap and verify them through Torznab.  The
startup result must not continue to report the admin-attempt failures as the
effective result once Jackett reports configured indexers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search.jackett_indexer_config import JackettIndexerInfo
from src.search.jackett_manager import JackettManager


class _LoginGatedConfigurer:
    async def configure_profile(self, profile: str):
        return {
            "status": "ok",
            "profile": profile,
            "requested": 2,
            "missing_ids": [],
            "added": 0,
            "skipped": 0,
            "failed": 2,
            "diagnostics": {
                "status": "ok",
                "admin_error": "admin endpoint redirected to UI login",
                "summary": {"configured_indexers": 0},
            },
        }


async def test_file_bootstrap_success_rewrites_effective_result() -> None:
    manager = JackettManager()
    manager._running = True  # type: ignore[attr-defined]
    manager._api_key = "accepted-key"  # type: ignore[attr-defined]
    manager._public_file_bootstrap_allowed = lambda: True  # type: ignore[method-assign]
    manager.configured_indexer_count = lambda: asyncio.sleep(0, result=0)  # type: ignore[method-assign]

    async def fake_bootstrap(profile="all_open_public", catalogue_configurer=None):
        return {"status": "ok", "requested": 2, "written": 2, "failed": 0}

    async def fake_start():
        return True

    async def fake_stop():
        return None

    async def fake_diagnostics():
        return {
            "status": "ok",
            "admin_error": "admin endpoint redirected to UI login",
            "summary": {"configured_indexers": 2},
        }

    manager.bootstrap_public_indexer_files = fake_bootstrap  # type: ignore[method-assign]
    manager.start = fake_start  # type: ignore[method-assign]
    manager.stop = fake_stop  # type: ignore[method-assign]
    manager.indexer_diagnostics = fake_diagnostics  # type: ignore[method-assign]

    with patch("src.search.jackett_indexer_config.JackettIndexerConfigurer", lambda url, key: _LoginGatedConfigurer()):
        result = await manager.configure_indexer_profile("all_open_public")

    assert result["failed"] == 0
    assert result["added"] == 2
    assert result["effective_configured_indexers"] == 2
    assert result["admin_config_attempt"]["failed"] == 2
    assert "verified" in result["note"]


async def main() -> None:
    await test_file_bootstrap_success_rewrites_effective_result()
    print("round201 Jackett bootstrap result-clarity tests passed")


if __name__ == "__main__":
    asyncio.run(main())
