#!/usr/bin/env python3
"""Round 274 regression tests for generic fallback/category-boundary cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.media_title_repair import MediaTitleRepair
from src.ai.tools.scheduling import SearchMediaTorrentsTool
from src.core.categories.tv import TvShowCategory
from src.core.notifications import NotificationService
from src.utils.media_classifier import MediaClassifier


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_generic_title_repair_does_not_strip_category_units() -> None:
    prompt = "Please grab me A Knight of the Seven Kingdoms in Italian, full first season."
    assert MediaTitleRepair.recover_literal_title("A Knight the Seven Kingdoms", prompt) == "A Knight of the Seven Kingdoms"
    # Generic title repair must not know TV season/pack vocabulary. TV trims its
    # own unit suffix before this helper runs.
    assert MediaTitleRepair.recover_literal_title("A Knight the Seven Kingdoms Season 1", prompt) == "A Knight the Seven Kingdoms Season 1"


def test_tv_owns_search_name_unit_suffix_normalization() -> None:
    tv = TvShowCategory()
    prompt = "Please grab me A Knight of the Seven Kingdoms in Italian, full first season."
    normalized = tv.normalize_agent_search_name_argument("A Knight the Seven Kingdoms Season 1", user_prompt=prompt)
    assert normalized == "A Knight the Seven Kingdoms"
    assert MediaTitleRepair.recover_literal_title(normalized, prompt) == "A Knight of the Seven Kingdoms"


def test_search_tool_no_longer_repeats_generic_schema_type_key() -> None:
    schema = SearchMediaTorrentsTool().parameters()
    current = schema["properties"]["current_bitrate_kbps"]
    assert current == {"type": "number", "description": current["description"]}


def test_streaming_loop_has_no_hidden_batch_phrase_parser() -> None:
    source = _read("src/ai/streaming_agent_loop.py")
    assert "_download_goal_requests_batch" not in source
    for leaked in ("episodi", "mancanti", "rimanenti"):
        assert leaked not in source


def test_scheduler_uses_category_routing_without_hidden_unit_phrase_parser() -> None:
    source = _read("src/core/scheduler_services.py")
    assert "from src.utils.media_classifier import MediaClassifier" not in source
    assert "extract_structured_unit_from_name" not in source
    assert "_extract_season_pattern" not in source
    assert "stagione" not in source
    assert "resolve_from_text(normalized_name)" in source
    assert "registry.classify(normalized_name)" in source
    assert "normalize_agent_search_units_from_name" in source


def test_legacy_media_classifier_falls_back_to_neutral_media() -> None:
    classifier = MediaClassifier(settings_manager=None)
    assert classifier._heuristic_classify("Some ambiguous title") == "media"
    assert classifier._heuristic_classify("Some Show S01E02") == "tv"
    assert classifier._heuristic_classify("please download something") == "media"


class _NotificationRepo:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return (len(self.created), True)

    async def unread_count(self) -> int:
        return len(self.created)


class _Db:
    def __init__(self) -> None:
        self.notifications = _NotificationRepo()


async def _send_notification() -> dict:
    db = _Db()
    service = NotificationService(db=db)
    await service.send_download_complete(
        "Example Item",
        season=5,
        episode=15,
        category_id="",
        unit_label="Disc 2",
    )
    return db.notifications.created[0]


def test_download_complete_notification_prefers_category_unit_label() -> None:
    created = asyncio.run(_send_notification())
    assert created["body"] == "Download complete: Example Item Disc 2"
    assert created["category_id"] == "media"


def test_generic_source_no_incident_title_or_tv_suffix_stripping_in_title_repair() -> None:
    source = _read("src/ai/media_title_repair.py")
    assert "A Knight" not in source
    assert "season|series|full|complete|pack" not in source
    assert "S\\d" not in source


def main() -> None:
    test_generic_title_repair_does_not_strip_category_units()
    test_tv_owns_search_name_unit_suffix_normalization()
    test_search_tool_no_longer_repeats_generic_schema_type_key()
    test_streaming_loop_has_no_hidden_batch_phrase_parser()
    test_scheduler_uses_category_routing_without_hidden_unit_phrase_parser()
    test_legacy_media_classifier_falls_back_to_neutral_media()
    test_download_complete_notification_prefers_category_unit_label()
    test_generic_source_no_incident_title_or_tv_suffix_stripping_in_title_repair()
    print("round274 generic fallback drift cleanup tests passed")


if __name__ == "__main__":
    main()
