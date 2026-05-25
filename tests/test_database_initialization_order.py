"""Regression tests for fresh-install database startup order."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_initializes_database_before_state_sync() -> None:
    """State synchronization must run only after Database.initialize()."""
    text = (ROOT / "main.py").read_text()
    db_init = text.index("await db.initialize()")
    state_sync = text.index("await state_coordinator.sync_category_items()")
    assert db_init < state_sync


def test_database_creates_schema_before_migrations_and_repository_attachment() -> None:
    """Fresh-install tables must exist before migrations and repository objects."""
    text = (ROOT / "src/core/database.py").read_text()
    init = text[text.index("async def initialize"):text.index("async def _create_base_schema")]
    schema = init.index("await self._create_base_schema()")
    version = init.index("current_version = await self._get_schema_version()")
    migrations = init.index("await self._run_migrations(current_version)")
    repositories = init.index("self.media = MediaRepository(self._db)")
    assert schema < version < migrations < repositories


def test_base_schema_creates_category_tables_before_schema_version_insert() -> None:
    """The base schema script must define category tables before marking version 100."""
    text = (ROOT / "src/core/database.py").read_text()
    create_category_items = text.index("CREATE TABLE IF NOT EXISTS category_items")
    create_schema_version = text.index("CREATE TABLE IF NOT EXISTS schema_version")
    insert_schema_version = text.index("INSERT OR REPLACE INTO schema_version")
    assert create_category_items < create_schema_version < insert_schema_version
