#!/usr/bin/env python3
"""Round 191 regression tests for macOS Jackett isolation and category-owned Soulseek ranking."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.categories.movie import MovieCategory
from src.search.jackett_manager import JackettManager


def test_managed_jackett_uses_ljs_isolated_config_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager = JackettManager()
        manager._managed_state_dir = Path(tmp) / "managed_jackett"  # type: ignore[attr-defined]
        manager._prepare_managed_state_home()  # type: ignore[attr-defined]
        config_dir = manager._config_dir()  # type: ignore[attr-defined]
        cfg = config_dir / "ServerConfig.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"APIKey": "abc", "AdminPassword": "hashed", "AllowExternal": True}))

        changed = manager._repair_managed_admin_auth_config()  # type: ignore[attr-defined]
        repaired = json.loads(cfg.read_text())

        assert changed is True
        assert str(config_dir).startswith(str(manager._managed_state_dir))  # type: ignore[attr-defined]
        assert repaired["AdminPassword"] is None
        assert repaired["AllowExternal"] is False
        assert repaired["APIKey"] == "abc"
        # Do not mutate the user's global/manual Jackett profile while managing LJS's own runtime.
        assert all(str(path).startswith(str(manager._managed_state_dir)) or "data/jackett" in str(path) for path in manager._server_config_paths())  # type: ignore[attr-defined]


async def test_movie_soulseek_filters_audio_book_rows_and_keeps_video_candidates() -> None:
    movie = MovieCategory()
    item = movie.create_item("Project Hail Mary", year=2026, language="English")
    raw = [
        {
            "candidate_id": "audio_1",
            "filename": "Project Hail Mary Andy Weir Audiobook mp3",
            "folder": "Audiobooks/Andy Weir/Project Hail Mary",
            "extension": "mp3",
            "size": 900_000_000,
            "queue_length": 0,
        },
        {
            "candidate_id": "ebook_1",
            "filename": "Project Hail Mary.epub",
            "folder": "Books/Andy Weir",
            "extension": "epub",
            "size": 3_000_000,
            "queue_length": 0,
        },
        {
            "candidate_id": "movie_1",
            "filename": "Project.Hail.Mary.2026.1080p.WEB-DL.ENG.mkv",
            "folder": "Movies/Project Hail Mary 2026",
            "extension": "mkv",
            "size": 4_400_000_000,
            "queue_length": 1,
        },
    ]

    ranked = await movie.rank_soulseek_search_results(raw, item=item, language="English")

    assert [entry["candidate_id"] for entry in ranked] == ["movie_1"], ranked
    assert ranked[0]["category_id"] == "movie"
    assert ranked[0]["resolution"] == "1080p"
    assert ranked[0]["language_status"] in {"preferred", "unknown"}
    assert ranked[0]["category_evidence"]["accepted_video_extensions"] == ["mkv"]


def test_generic_soulseek_tooling_contains_no_category_specific_query_hacks() -> None:
    scheduler = (ROOT / "src/core/scheduler_services.py").read_text()
    tool = (ROOT / "src/ai/tools/soulseek.py").read_text()
    prompt = (ROOT / "src/ai/prompt_builder.py").read_text()

    forbidden = [
        "For Music source strategy",
        "category == \"music\"",
        "category_id == \"music\"",
        "album|track|song",
        "flac|mp3|aac",
        "_soulseek_query_variants",
    ]
    haystack = "\n".join([scheduler, tool, prompt])
    missing = [token for token in forbidden if token in haystack]
    assert not missing, missing
    assert "The active category owns Soulseek query wording" in prompt


def main() -> None:
    test_managed_jackett_uses_ljs_isolated_config_state()
    asyncio.run(test_movie_soulseek_filters_audio_book_rows_and_keeps_video_candidates())
    test_generic_soulseek_tooling_contains_no_category_specific_query_hacks()
    print("round191 macOS Jackett/category Soulseek tests passed")


if __name__ == "__main__":
    main()
