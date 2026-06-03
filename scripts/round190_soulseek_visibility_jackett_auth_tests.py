#!/usr/bin/env python3
"""Round 190 regression tests for Soulseek result visibility and Jackett admin auth repair."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.tool_result_compactor import ToolResultCompactor
from src.search.jackett_manager import JackettManager


def test_soulseek_only_media_search_is_not_compacted_as_empty() -> None:
    result = {
        "query": "Project Hail Mary",
        "category_id": "movie",
        "name": "Project Hail Mary",
        "result_set_id": "rs1",
        "candidates": [],
        "next_actions": [{"action": "evaluate_soulseek_candidates", "tool": "enqueue_soulseek_download"}],
        "source_result_status": "soulseek_only_candidates_found",
        "torrent_candidate_count": 0,
        "soulseek_candidate_count": 10,
        "downloadable_candidate_count": 10,
        "agent_instruction": "No torrent candidate matched, but Soulseek returned queueable candidates.",
        "soulseek_summary": {
            "enabled": True,
            "status": "ready",
            "candidate_count": 10,
            "queries": ["Project Hail Mary"],
            "recommended_candidate_id": "slskd_1",
            "queue_tool": "enqueue_soulseek_download",
        },
        "soulseek_candidate_picker": [
            {"candidate_id": "slskd_1", "result_set_id": "rs1", "username": "user", "sample_filenames": ["Project Hail Mary.mkv"]}
        ],
        "companion_soulseek": {
            "status": "ready",
            "candidate_count": 10,
            "queries": ["Project Hail Mary"],
            "recommended_candidate_id": "slskd_1",
            "queueing_note": "Use enqueue_soulseek_download",
            "candidates": [
                {"candidate_id": "slskd_1", "result_set_id": "rs1", "username": "user", "filename": "Project Hail Mary.mkv"}
            ],
        },
    }
    compact = ToolResultCompactor().compact("search_media_torrents", result)
    assert compact["candidate_count"] == 0, compact
    assert compact["torrent_candidate_count"] == 0, compact
    assert compact["soulseek_candidate_count"] == 10, compact
    assert compact["downloadable_candidate_count"] == 10, compact
    assert compact["source_result_status"] == "soulseek_only_candidates_found", compact
    assert compact["agent_instruction"], compact
    assert compact["soulseek_candidate_picker"][0]["candidate_id"] == "slskd_1", compact
    assert compact["companion_soulseek"]["recommended_candidate_id"] == "slskd_1", compact


def test_managed_jackett_admin_auth_repair_clears_local_login_gate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp)
        cfg = cfg_dir / "ServerConfig.json"
        cfg.write_text(json.dumps({"APIKey": "abc", "AdminPassword": "hash", "AllowExternal": True}))
        manager = JackettManager()
        manager._server_config_paths = lambda: [cfg]  # type: ignore[method-assign]
        changed = manager._repair_managed_admin_auth_config()
        data = json.loads(cfg.read_text())
        assert changed is True
        assert data["AdminPassword"] is None, data
        assert data["AllowExternal"] is False, data
        assert data["APIKey"] == "abc", data


def main() -> None:
    test_soulseek_only_media_search_is_not_compacted_as_empty()
    test_managed_jackett_admin_auth_repair_clears_local_login_gate()
    print("round190 soulseek visibility / jackett auth repair tests passed")


if __name__ == "__main__":
    main()
