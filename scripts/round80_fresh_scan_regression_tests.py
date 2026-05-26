#!/usr/bin/env python3
"""Round 80 fresh-install TV scan/detail regression tests.

These are scenario traces for the regression reported from a fresh install: TV
series folders were discovered, but the frontend/detail layer showed no local
episodes.  The tests intentionally avoid the global pytest fixture stack so they
can run in a minimal environment without aiosqlite.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.core.categories.tv import TvShowCategory
from src.core.library_objects import CanonicalLibraryObjectContext


class _FakeMediaRepo:
    def __init__(self, units: list[dict[str, Any]], metadata_rows: list[dict[str, Any]] | None = None) -> None:
        self._units = units
        self._metadata_rows = metadata_rows or []

    async def list_category_units(self, category_id: str, item_id: str, unit_type: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = list(self._units)
        if unit_type is not None:
            rows = [row for row in rows if row.get("unit_type") == unit_type]
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        return rows

    async def get_item_progress(self, category_id: str, item_id: str) -> dict[str, Any] | None:
        return {"last_season": 1, "last_episode": len(self._units)} if self._units else None

    async def get_category_metadata(self, category_id: str, item_id: str) -> list[dict[str, Any]]:
        return self._metadata_rows


class _FakeDB:
    def __init__(self, units: list[dict[str, Any]], metadata_rows: list[dict[str, Any]] | None = None) -> None:
        self.media = _FakeMediaRepo(units, metadata_rows=metadata_rows)


def _touch(path: Path, size: int = 1024) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


async def main() -> None:
    category = TvShowCategory()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Common real-world layouts from already-organized libraries: the show
        # and season folder carry context, while individual files may be just a
        # number or E-number instead of full SxxEyy release names.
        _touch(root / "Chernobyl" / "Season 1" / "01.mkv")
        _touch(root / "Chernobyl" / "Season 1" / "E02 - Please Remain Calm.mkv")
        _touch(root / "Chernobyl" / "Stagione 1" / "Episodio 03.mkv")
        _touch(root / "For All Mankind" / "S01" / "For.All.Mankind.S01E01.1080p.WEB.x264.mkv")
        _touch(root / "The Wire" / "Season 02" / "2x05 - Undertow.mp4")

        scanned = await category.scan(str(root), existing_keys={"Chernobyl", "For All Mankind", "The Wire"})
        by_name = {item.name: item for item in scanned}
        assert set(by_name) == {"Chernobyl", "For All Mankind", "The Wire"}, by_name.keys()
        assert by_name["Chernobyl"].episodes == {1: [1, 2, 3]}, by_name["Chernobyl"].episodes
        assert by_name["For All Mankind"].episodes == {1: [1]}, by_name["For All Mankind"].episodes
        assert by_name["The Wire"].episodes == {2: [5]}, by_name["The Wire"].episodes

        units = category.library_units_from_scan(by_name["Chernobyl"])
        assert len(units) == 3, units
        assert {unit["unit_type"] for unit in units} == {"file"}
        assert {unit["role"] for unit in units} == {"episode_payload"}

        canonical = category.build_library_object(CanonicalLibraryObjectContext(
            category_id="tv",
            item_id="Chernobyl",
            item={"item_id": "Chernobyl", "display_name": "Chernobyl"},
            units=units,
            metadata_rows=[],
            settings_item=None,
        ))
        assert canonical["computed"]["downloaded_episode_count"] == 3, canonical
        assert canonical["seasons"][0]["episode_count"] == 3, canonical["seasons"]

        detail = await category.build_item_detail_payload(
            item_id="Chernobyl",
            item={"item_id": "Chernobyl", "display_name": "Chernobyl"},
            settings=SimpleNamespace(tracked_items=[]),
            db=_FakeDB(units),
            artwork_manager=None,
        )
        assert detail["downloaded_episodes_count"] == 3, detail
        assert detail["seasons"] and detail["seasons"][0]["episode_count"] == 3, detail.get("seasons")
        assert len(detail["episodes"]) == 3, detail.get("episodes")

    print("Round 80 fresh scan/detail regression tests passed")


if __name__ == "__main__":
    asyncio.run(main())
