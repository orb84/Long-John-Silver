"""Static regressions for category-item parent creation before unit inserts."""

from pathlib import Path


def test_scheduler_ensures_scanned_item_parents_before_units() -> None:
    """Scanned units require parent category_items rows because of FK constraints."""
    text = Path("src/core/scheduler.py").read_text()
    assert "await self._ensure_scanned_items_exist(result)" in text
    assert "async def _ensure_scanned_items_exist" in text
    assert text.index("await self._ensure_scanned_items_exist(result)") < text.index("await self._sync_scanned_units(result)")
    assert "await self._db.media.upsert_category_item" in text


def test_startup_state_sync_persists_item_before_progress_unit() -> None:
    """Progress units also need their parent category item row first."""
    text = Path("src/core/state_coordinator.py").read_text()
    assert "await self._ensure_category_item(category_id, item)" in text
    assert text.index("await self._ensure_category_item(category_id, item)") < text.index("progress = await self._db.media.get_item_progress")
    assert "await self._db.media.upsert_category_item" in text


def test_repository_defensively_ensures_parent_before_unit_insert() -> None:
    """Repository unit writes should not crash when a discovered parent is absent."""
    text = Path("src/core/repositories/media.py").read_text()
    assert "async def ensure_category_item" in text
    assert "await self.ensure_category_item" in text
    assert text.index("await self.ensure_category_item") < text.index("INSERT INTO category_item_units")
