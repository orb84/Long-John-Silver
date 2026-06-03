"""Database layer for LJS using aiosqlite.

Manages schema migrations, CRUD operations, and persistent storage
for all media, download, and configuration data.
"""

import json
import aiosqlite
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from typing import Any, Optional
from src.core.repositories.media import MediaRepository
from src.core.repositories.download import DownloadRepository
from src.core.repositories.user import UserRepository
from src.core.repositories.system import SystemRepository
from src.core.repositories.notifications import NotificationRepository
from src.core.repositories.release_watch import ReleaseWatchRepository
from src.core.repositories.base import BaseRepository


class PlanTraceStore(BaseRepository):
    """Stores and retrieves AgentPlan execution traces."""

    async def save_trace(
        self,
        plan: Any,
        result: Any,
        session_id: str | None = None,
    ) -> int:
        """Persist a plan execution trace to the database.

        Args:
            plan: An AgentPlan instance.
            result: A PlanExecutionResult instance.
            session_id: Optional session identifier.

        Returns:
            The auto-incremented trace ID.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        cursor = await self._db.execute(
            """INSERT INTO plan_traces
               (session_id, intent, user_goal, constraints_json,
                all_successful, total_steps, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                plan.intent.value if hasattr(plan.intent, 'value') else str(plan.intent),
                plan.user_goal,
                json.dumps(plan.constraints),
                1 if result.all_successful else 0,
                len(result.steps),
                now,
            ),
        )
        trace_id = cursor.lastrowid

        for step_result in result.steps:
            step = step_result.step
            await self._db.execute(
                """INSERT INTO plan_trace_steps
                   (trace_id, step_id, tool_name, arguments_json,
                    depends_on_json, success_condition, success,
                    result_json, summary, error, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace_id,
                    step.id,
                    step.tool_name,
                    json.dumps(step.arguments),
                    json.dumps(step.depends_on),
                    step.success_condition,
                    1 if step_result.success else 0,
                    json.dumps(step_result.result) if isinstance(step_result.result, dict) else str(step_result.result),
                    step_result.summary,
                    step_result.error,
                    now,
                ),
            )

        await self._db.commit()
        return trace_id


class Database:
    """Async SQLite database for LJS with migration-based schema versioning.

    Migrations are SQL files in the ``migrations/`` directory, named sequentially
    (e.g., ``001_multi_user.sql``). The runner reads the current version from
    the ``schema_version`` table and applies any migrations with a higher number.

    Schema hardening rule: startup must never assume ``CREATE TABLE IF NOT
    EXISTS`` upgraded an existing table.  Before creating indexes or wiring
    repositories, the initializer runs an idempotent legacy-compatibility pass
    that adds missing columns to already-existing tables, then validates a small
    contract of columns/unique surfaces the current app requires.
    """

    MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"
    BASE_SCHEMA_VERSION = 100

    # Columns that may be missing from pre-category or partially migrated DBs.
    # SQLite cannot alter constraints in-place, so these compatibility columns
    # deliberately use safe defaults and keep stricter guarantees in repository
    # code and fresh-schema definitions.
    LEGACY_COMPAT_COLUMNS: dict[str, dict[str, str]] = {
        "sessions": {
            "channel": "TEXT NOT NULL DEFAULT 'web'",
            "channel_user_id": "TEXT DEFAULT ''",
            "last_active_at": "TEXT NOT NULL DEFAULT ''",
        },
        "conversation_history": {
            "tool_call_id": "TEXT",
        },
        "preferences": {
            "user_id": "TEXT",
        },
        "behavior_log": {
            "category_id": "TEXT DEFAULT ''",
            "item_id": "TEXT DEFAULT ''",
            "item_name": "TEXT DEFAULT ''",
            "resolution": "TEXT",
            "codec": "TEXT",
            "release_group": "TEXT",
            "file_size_mb": "REAL",
            "quality_score": "REAL",
        },
        "downloads": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "item_name": "TEXT NOT NULL DEFAULT ''",
            "priority": "TEXT NOT NULL DEFAULT 'normal'",
            "reason": "TEXT NOT NULL DEFAULT ''",
            "season": "INTEGER",
            "episode": "INTEGER",
            "progress": "REAL DEFAULT 0.0",
            "download_rate": "REAL DEFAULT 0.0",
            "upload_rate": "REAL DEFAULT 0.0",
            "num_peers": "INTEGER DEFAULT 0",
            "num_seeds": "INTEGER DEFAULT 0",
            "total_size": "INTEGER NOT NULL DEFAULT 0",
            "downloaded_bytes": "INTEGER NOT NULL DEFAULT 0",
            "eta_seconds": "REAL NOT NULL DEFAULT 0.0",
            "file_path": "TEXT",
            "files": "TEXT NOT NULL DEFAULT '[]'",
            "language": "TEXT NOT NULL DEFAULT ''",
            "torrent_title": "TEXT NOT NULL DEFAULT ''",
            "import_context_json": "TEXT NOT NULL DEFAULT '{}'",
            "save_path": "TEXT NOT NULL DEFAULT ''",
            "sharing_enabled": "INTEGER NOT NULL DEFAULT 0",
            "uploaded_bytes": "INTEGER NOT NULL DEFAULT 0",
            "seed_ratio": "REAL NOT NULL DEFAULT 0.0",
            "source_seeders": "INTEGER",
            "stalled_notified": "INTEGER NOT NULL DEFAULT 0",
            "stalled_cancel_asked": "INTEGER NOT NULL DEFAULT 0",
            "user_id": "TEXT",
            "completed_at": "TEXT",
        },
        "upgrade_candidates": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "item_name": "TEXT NOT NULL DEFAULT ''",
            "current_resolution": "TEXT DEFAULT ''",
            "current_codecs": "TEXT DEFAULT '[]'",
            "best_upgrade_resolution": "TEXT DEFAULT ''",
            "best_upgrade_codecs": "TEXT DEFAULT '[]'",
            "best_upgrade_title": "TEXT DEFAULT ''",
            "best_upgrade_magnet": "TEXT DEFAULT ''",
            "quality_improvement": "TEXT DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "approved_at": "TEXT",
            "denied_at": "TEXT",
        },
        "suggested_actions": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "item_name": "TEXT NOT NULL DEFAULT ''",
            "description": "TEXT NOT NULL DEFAULT ''",
            "endpoint": "TEXT NOT NULL DEFAULT ''",
            "method": "TEXT NOT NULL DEFAULT 'POST'",
            "body": "TEXT NOT NULL DEFAULT '{}'",
            "priority": "INTEGER NOT NULL DEFAULT 0",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "metadata": "TEXT NOT NULL DEFAULT '{}'",
            "approved_at": "TEXT",
            "denied_at": "TEXT",
        },
        "scheduled_tasks": {
            "task_type": "TEXT NOT NULL DEFAULT 'scheduled_prompt'",
            "schedule_type": "TEXT NOT NULL DEFAULT 'recurring'",
            "title": "TEXT NOT NULL DEFAULT ''",
            "due_at": "TEXT",
            "next_run_at": "TEXT",
            "run_count": "INTEGER NOT NULL DEFAULT 0",
            "max_runs": "INTEGER",
            "session_id": "TEXT",
            "last_error": "TEXT NOT NULL DEFAULT ''",
        },
        "deletion_log": {
            "category_id": "TEXT DEFAULT ''",
            "item_id": "TEXT DEFAULT ''",
            "item_name": "TEXT DEFAULT ''",
        },
        "notifications": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "event_type": "TEXT NOT NULL DEFAULT 'general'",
            "status": "TEXT NOT NULL DEFAULT 'unread'",
            "actions_json": "TEXT NOT NULL DEFAULT '[]'",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "dedupe_key": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "read_at": "TEXT",
        },
        "notification_deliveries": {
            "notification_id": "INTEGER NOT NULL DEFAULT 0",
            "bridge_id": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "delivered_at": "TEXT",
            "last_error": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        },
        "release_watches": {
            "expected_air_at": "TEXT NOT NULL DEFAULT ''",
            "watch_start_at": "TEXT NOT NULL DEFAULT ''",
            "expires_at": "TEXT NOT NULL DEFAULT ''",
            "cadence_profile": "TEXT NOT NULL DEFAULT 'unknown'",
            "requirements_json": "TEXT NOT NULL DEFAULT '{}'",
            "last_candidate_summary_json": "TEXT NOT NULL DEFAULT '{}'",
            "last_outcome_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "category_items": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "display_name": "TEXT NOT NULL DEFAULT ''",
            "item_type": "TEXT NOT NULL DEFAULT ''",
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "status": "TEXT NOT NULL DEFAULT ''",
            "properties_json": "TEXT NOT NULL DEFAULT '{}'",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "state_json": "TEXT NOT NULL DEFAULT '{}'",
            "item_json": "TEXT NOT NULL DEFAULT '{}'",
            "last_checked_at": "TEXT",
            "last_download_at": "TEXT",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        },
        "category_item_units": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "unit_key": "TEXT NOT NULL DEFAULT ''",
            "unit_type": "TEXT NOT NULL DEFAULT ''",
            "display_name": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT ''",
            "sort_index": "INTEGER NOT NULL DEFAULT 0",
            "properties_json": "TEXT NOT NULL DEFAULT '{}'",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "state_json": "TEXT NOT NULL DEFAULT '{}'",
            "unit_json": "TEXT NOT NULL DEFAULT '{}'",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        },
        "category_item_processing_state": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "next_check_at": "TEXT",
            "next_check_reason": "TEXT NOT NULL DEFAULT ''",
            "invalidated_by": "TEXT NOT NULL DEFAULT '[]'",
        },
        "category_item_processing_events": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT ''",
        },
        "category_item_suggestion_state": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "suggestion_key": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "valid_until": "TEXT",
        },
        "category_property_index": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "property_name": "TEXT NOT NULL DEFAULT ''",
            "value_text": "TEXT",
            "value_number": "REAL",
            "value_json": "TEXT NOT NULL DEFAULT 'null'",
        },
        "category_item_metadata": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "item_id": "TEXT NOT NULL DEFAULT ''",
            "provider": "TEXT NOT NULL DEFAULT ''",
            "external_id": "TEXT NOT NULL DEFAULT ''",
        },
        "category_metadata_cache": {
            "category_id": "TEXT NOT NULL DEFAULT ''",
            "provider": "TEXT NOT NULL DEFAULT ''",
            "cache_key": "TEXT NOT NULL DEFAULT ''",
            "stable_id": "TEXT NOT NULL DEFAULT ''",
            "expires_at": "TEXT NOT NULL DEFAULT ''",
        },
    }

    SCHEMA_CONTRACT: dict[str, tuple[str, ...]] = {
        "schema_version": ("version",),
        "downloads": ("id", "status", "priority", "created_at", "category_id", "item_id", "import_context_json"),
        "suggested_actions": ("id", "category_id", "item_id", "status"),
        "behavior_log": ("id", "category_id", "item_id", "action"),
        "category_items": ("category_id", "item_id", "display_name", "enabled"),
        "category_item_units": ("category_id", "item_id", "unit_key", "status"),
        "category_item_processing_state": ("category_id", "item_id", "next_check_at"),
        "category_item_suggestion_state": ("category_id", "item_id", "suggestion_key", "status"),
        "notifications": ("id", "status", "dedupe_key"),
        "notification_deliveries": ("notification_id", "bridge_id", "status", "attempts", "updated_at"),
        "release_watches": ("id", "category_id", "item_id", "unit_key", "next_check_at", "expected_air_at", "watch_start_at", "requirements_json"),
    }

    def __init__(self, db_path: str = "data/ljs.db"):
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

        # Repositories
        self.media: Optional[MediaRepository] = None
        self.downloads: Optional[DownloadRepository] = None
        self.users: Optional[UserRepository] = None
        self.system: Optional[SystemRepository] = None
        self.plan_traces: Optional[PlanTraceStore] = None
        self.notifications: Optional[NotificationRepository] = None
        self.release_watches: Optional[ReleaseWatchRepository] = None

    async def initialize(self) -> None:
        """Create the base schema and run any pending migrations.

        Base schema (v1) is created inline for the initial install.
        Migrations in ``migrations/`` are applied sequentially after that.
        """
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row

        await self._create_base_schema()
        current_version = await self._get_schema_version()
        await self._run_migrations(current_version)
        await self._validate_schema_contract()

        # Initialize repositories
        self.media = MediaRepository(self._db)
        self.downloads = DownloadRepository(self._db)
        self.users = UserRepository(self._db)
        self.system = SystemRepository(self._db)
        self.plan_traces = PlanTraceStore(self._db)
        self.notifications = NotificationRepository(self._db)
        self.release_watches = ReleaseWatchRepository(self._db)

        logger.info(
            f"Database initialized at {self._db_path} "
            f"(schema version: {await self._get_schema_version()})"
        )

    async def _create_base_schema(self) -> None:
        """Create the category-first schema for a fresh LJS database.

        The schema keeps app-level columns stable and stores category-specific
        fields in JSON envelopes owned by category manifests. New categories can
        add item properties, units, or metadata without database migrations.
        Optional index rows make selected dynamic properties searchable when a
        category needs fast filtering.
        """
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._prepare_schema_version_table()
        await self._repair_legacy_schema_before_base_schema()

        await self._db.executescript("""
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'web',
                channel_user_id TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_active_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_call_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                user_id TEXT
            );

            CREATE TABLE IF NOT EXISTS behavior_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                category_id TEXT DEFAULT '',
                item_id TEXT DEFAULT '',
                item_name TEXT DEFAULT '',
                resolution TEXT,
                codec TEXT,
                release_group TEXT,
                file_size_mb REAL,
                quality_score REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS category_taste_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT '',
                category_id TEXT NOT NULL,
                item_id TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                signal_type TEXT NOT NULL,
                polarity TEXT NOT NULL DEFAULT 'neutral',
                strength REAL NOT NULL DEFAULT 0.0,
                weight REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL DEFAULT 'conversation',
                confidence REAL NOT NULL DEFAULT 1.0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                interpreted_facets_json TEXT NOT NULL DEFAULT '{}',
                evidence_text TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, category_id, item_id, signal_type, source)
            );

            CREATE TABLE IF NOT EXISTS category_taste_facet_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT '',
                category_id TEXT NOT NULL,
                facet_key TEXT NOT NULL,
                facet_value TEXT NOT NULL,
                affinity REAL NOT NULL DEFAULT 0.0,
                positive_score REAL NOT NULL DEFAULT 0.0,
                negative_score REAL NOT NULL DEFAULT 0.0,
                confidence REAL NOT NULL DEFAULT 0.0,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                source_signal_ids_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, category_id, facet_key, facet_value)
            );

            CREATE TABLE IF NOT EXISTS category_taste_profile_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT '',
                category_id TEXT NOT NULL,
                profile_json TEXT NOT NULL DEFAULT '{}',
                summary TEXT NOT NULL DEFAULT '',
                evidence_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, category_id)
            );

            CREATE TABLE IF NOT EXISTS category_items (
                category_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                item_type TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT '',
                properties_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                state_json TEXT NOT NULL DEFAULT '{}',
                item_json TEXT NOT NULL DEFAULT '{}',
                last_checked_at TEXT,
                last_download_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (category_id, item_id)
            );

            CREATE TABLE IF NOT EXISTS category_item_units (
                category_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                unit_key TEXT NOT NULL,
                unit_type TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                sort_index INTEGER NOT NULL DEFAULT 0,
                properties_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                state_json TEXT NOT NULL DEFAULT '{}',
                unit_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (category_id, item_id, unit_key),
                FOREIGN KEY(category_id, item_id)
                    REFERENCES category_items(category_id, item_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS category_item_metadata (
                category_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                refreshed_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (category_id, item_id, provider, external_id)
            );


            CREATE TABLE IF NOT EXISTS category_item_processing_state (
                category_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                metadata_fingerprint TEXT NOT NULL DEFAULT '',
                library_fingerprint TEXT NOT NULL DEFAULT '',
                taste_fingerprint TEXT NOT NULL DEFAULT '',
                suggestion_fingerprint TEXT NOT NULL DEFAULT '',
                last_processed_at TEXT,
                next_check_at TEXT,
                next_check_reason TEXT NOT NULL DEFAULT '',
                valid_until TEXT,
                policy_version INTEGER NOT NULL DEFAULT 1,
                invalidated_by TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (category_id, item_id),
                FOREIGN KEY(category_id, item_id)
                    REFERENCES category_items(category_id, item_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS category_item_processing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                purpose TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                metadata_fingerprint TEXT NOT NULL DEFAULT '',
                library_fingerprint TEXT NOT NULL DEFAULT '',
                taste_fingerprint TEXT NOT NULL DEFAULT '',
                suggestion_fingerprint TEXT NOT NULL DEFAULT '',
                policy_version INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS category_item_suggestion_state (
                category_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                suggestion_key TEXT NOT NULL,
                suggestion_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                suggestion_fingerprint TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                valid_until TEXT,
                invalidated_by TEXT NOT NULL DEFAULT '[]',
                policy_version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (category_id, item_id, suggestion_key),
                FOREIGN KEY(category_id, item_id)
                    REFERENCES category_items(category_id, item_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS category_property_index (
                category_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                property_name TEXT NOT NULL,
                value_text TEXT,
                value_number REAL,
                value_json TEXT NOT NULL DEFAULT 'null',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (category_id, item_id, property_name),
                FOREIGN KEY(category_id, item_id)
                    REFERENCES category_items(category_id, item_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS downloads (
                id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '',
                item_name TEXT NOT NULL,
                magnet TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                priority TEXT NOT NULL DEFAULT 'normal',
                reason TEXT NOT NULL DEFAULT '',
                season INTEGER,
                episode INTEGER,
                progress REAL DEFAULT 0.0,
                download_rate REAL DEFAULT 0.0,
                upload_rate REAL DEFAULT 0.0,
                num_peers INTEGER DEFAULT 0,
                num_seeds INTEGER DEFAULT 0,
                total_size INTEGER NOT NULL DEFAULT 0,
                downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                eta_seconds REAL NOT NULL DEFAULT 0.0,
                file_path TEXT,
                files TEXT NOT NULL DEFAULT '[]',
                language TEXT NOT NULL DEFAULT '',
                torrent_title TEXT NOT NULL DEFAULT '',
                import_context_json TEXT NOT NULL DEFAULT '{}',
                save_path TEXT NOT NULL DEFAULT '',
                sharing_enabled INTEGER NOT NULL DEFAULT 0,
                uploaded_bytes INTEGER NOT NULL DEFAULT 0,
                seed_ratio REAL NOT NULL DEFAULT 0.0,
                source_seeders INTEGER,
                stalled_notified INTEGER NOT NULL DEFAULT 0,
                stalled_cancel_asked INTEGER NOT NULL DEFAULT 0,
                user_id TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS upgrade_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '',
                item_name TEXT NOT NULL DEFAULT '',
                current_resolution TEXT DEFAULT '',
                current_codecs TEXT DEFAULT '[]',
                best_upgrade_resolution TEXT DEFAULT '',
                best_upgrade_codecs TEXT DEFAULT '[]',
                best_upgrade_title TEXT DEFAULT '',
                best_upgrade_magnet TEXT DEFAULT '',
                quality_improvement TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                found_at TEXT NOT NULL,
                approved_at TEXT,
                denied_at TEXT
            );

            CREATE TABLE IF NOT EXISTS suggested_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '',
                item_name TEXT NOT NULL DEFAULT '',
                action_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                endpoint TEXT NOT NULL DEFAULT '',
                method TEXT NOT NULL DEFAULT 'POST',
                body TEXT NOT NULL DEFAULT '{}',
                priority INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                approved_at TEXT,
                denied_at TEXT
            );

            CREATE TABLE IF NOT EXISTS blacklist (
                pattern TEXT PRIMARY KEY,
                reason TEXT DEFAULT '',
                added_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS release_groups (
                name TEXT PRIMARY KEY,
                download_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                avg_quality REAL NOT NULL DEFAULT 0.0,
                blacklisted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL DEFAULT 10080,
                user_id TEXT,
                channel TEXT NOT NULL DEFAULT 'web',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_at TEXT,
                created_at TEXT NOT NULL,
                task_type TEXT NOT NULL DEFAULT 'scheduled_prompt',
                schedule_type TEXT NOT NULL DEFAULT 'recurring',
                title TEXT NOT NULL DEFAULT '',
                due_at TEXT,
                next_run_at TEXT,
                run_count INTEGER NOT NULL DEFAULT 0,
                max_runs INTEGER,
                session_id TEXT,
                last_error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS deletion_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                media_type TEXT NOT NULL,
                category_id TEXT DEFAULT '',
                item_id TEXT DEFAULT '',
                item_name TEXT DEFAULT '',
                season INTEGER,
                episode INTEGER,
                file_path TEXT NOT NULL,
                deleted_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS action_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_name TEXT NOT NULL,
                source TEXT NOT NULL,
                user_id TEXT,
                session_id TEXT,
                arguments_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plan_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                intent TEXT NOT NULL,
                user_goal TEXT NOT NULL,
                constraints_json TEXT NOT NULL DEFAULT '{}',
                all_successful INTEGER NOT NULL DEFAULT 0,
                total_steps INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plan_trace_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id INTEGER NOT NULL REFERENCES plan_traces(id) ON DELETE CASCADE,
                step_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL DEFAULT '{}',
                depends_on_json TEXT NOT NULL DEFAULT '[]',
                success_condition TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 0,
                result_json TEXT NOT NULL DEFAULT '{}',
                summary TEXT NOT NULL DEFAULT '',
                error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_category_items_category ON category_items(category_id, enabled);
            CREATE INDEX IF NOT EXISTS idx_category_units_status ON category_item_units(category_id, item_id, status);
            CREATE TABLE IF NOT EXISTS category_metadata_cache (
                category_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                stable_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ok',
                payload_json TEXT NOT NULL DEFAULT '{}',
                provider_signature TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_accessed_at TEXT NOT NULL DEFAULT (datetime('now')),
                hit_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (category_id, provider, cache_key)
            );

            CREATE TABLE IF NOT EXISTS provider_rate_limits (
                provider TEXT PRIMARY KEY,
                next_allowed_at TEXT NOT NULL DEFAULT '',
                last_status TEXT NOT NULL DEFAULT '',
                remaining TEXT NOT NULL DEFAULT '',
                reset_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_category_metadata_provider ON category_item_metadata(provider, external_id);
            CREATE INDEX IF NOT EXISTS idx_category_metadata_cache_expiry ON category_metadata_cache(category_id, provider, expires_at);
            CREATE INDEX IF NOT EXISTS idx_category_metadata_cache_stable_id ON category_metadata_cache(category_id, stable_id);
            CREATE INDEX IF NOT EXISTS idx_property_index_lookup ON category_property_index(category_id, property_name, value_text);

            CREATE INDEX IF NOT EXISTS idx_processing_due ON category_item_processing_state(next_check_at, category_id, item_id);
            CREATE INDEX IF NOT EXISTS idx_processing_events_item ON category_item_processing_events(category_id, item_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_suggestion_state_item ON category_item_suggestion_state(category_id, item_id, status, valid_until);
            CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status, priority, created_at);
            CREATE INDEX IF NOT EXISTS idx_downloads_item ON downloads(category_id, item_id);
            CREATE INDEX IF NOT EXISTS idx_downloads_import_context ON downloads(category_id, item_id, season, episode);
            CREATE INDEX IF NOT EXISTS idx_suggestions_item ON suggested_actions(category_id, item_id, status);

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                level TEXT NOT NULL DEFAULT 'info',
                category_id TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT 'general',
                status TEXT NOT NULL DEFAULT 'unread',
                actions_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                dedupe_key TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                read_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedupe
                ON notifications(dedupe_key) WHERE dedupe_key != '';
            CREATE INDEX IF NOT EXISTS idx_notifications_status
                ON notifications(status, created_at);

            CREATE TABLE IF NOT EXISTS notification_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id INTEGER NOT NULL,
                bridge_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                delivered_at TEXT,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(notification_id, bridge_id),
                FOREIGN KEY(notification_id) REFERENCES notifications(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_notification_deliveries_bridge
                ON notification_deliveries(bridge_id, status, updated_at);

            CREATE TABLE IF NOT EXISTS release_watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                unit_key TEXT NOT NULL,
                preferred_language TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                next_check_at TEXT NOT NULL DEFAULT '',
                interval_hours REAL NOT NULL DEFAULT 2.0,
                expected_air_at TEXT NOT NULL DEFAULT '',
                watch_start_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                cadence_profile TEXT NOT NULL DEFAULT 'unknown',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                requirements_json TEXT NOT NULL DEFAULT '{}',
                last_candidate_summary_json TEXT NOT NULL DEFAULT '{}',
                last_outcome_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(category_id, item_id, unit_key)
            );
            CREATE INDEX IF NOT EXISTS idx_release_watches_due
                ON release_watches(status, next_check_at);

            CREATE INDEX IF NOT EXISTS idx_upgrades_item ON upgrade_candidates(category_id, item_id, status);
            CREATE INDEX IF NOT EXISTS idx_behavior_item ON behavior_log(category_id, item_id, action);
            CREATE INDEX IF NOT EXISTS idx_taste_signals_category ON category_taste_signals(user_id, category_id, signal_type);
            CREATE INDEX IF NOT EXISTS idx_taste_signals_item ON category_taste_signals(category_id, item_id);
            CREATE INDEX IF NOT EXISTS idx_taste_facets_category ON category_taste_facet_scores(user_id, category_id, facet_key);
            CREATE INDEX IF NOT EXISTS idx_taste_snapshots_category ON category_taste_profile_snapshots(user_id, category_id);

            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );
        """)
        await self._db.execute(
            "INSERT INTO schema_version (version) "
            "SELECT ? WHERE NOT EXISTS (SELECT 1 FROM schema_version)",
            (self.BASE_SCHEMA_VERSION,),
        )
        await self._db.commit()

    async def _prepare_schema_version_table(self) -> None:
        await self._db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
        await self._db.commit()

    async def _repair_legacy_schema_before_base_schema(self) -> None:
        """Add missing columns before base indexes/migrations reference them.

        This is intentionally run before the inline base schema creates indexes.
        Old DBs may already have tables such as ``downloads`` or
        ``suggested_actions`` without the category-first columns; in SQLite,
        ``CREATE TABLE IF NOT EXISTS`` would leave those tables unchanged and a
        later ``CREATE INDEX ... (category_id, item_id)`` would crash startup.
        """
        repaired: list[str] = []
        for table, columns in self.LEGACY_COMPAT_COLUMNS.items():
            if not await self._table_exists(table):
                continue
            existing = await self._table_columns(table)
            for column, definition in columns.items():
                if column in existing:
                    continue
                await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                repaired.append(f"{table}.{column}")
        if repaired:
            await self._db.commit()
            logger.warning(
                "Database legacy schema repaired before index creation: added {} missing column(s): {}",
                len(repaired),
                ", ".join(repaired[:80]) + (" ..." if len(repaired) > 80 else ""),
            )

    async def _table_exists(self, table: str) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        row = await cursor.fetchone()
        return bool(row)

    async def _table_columns(self, table: str) -> set[str]:
        cursor = await self._db.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        return {str(row[1]) for row in rows}

    async def _validate_schema_contract(self) -> None:
        """Fail with a precise message if startup schema repair was incomplete."""
        missing: list[str] = []
        for table, columns in self.SCHEMA_CONTRACT.items():
            if not await self._table_exists(table):
                missing.append(f"{table}.*")
                continue
            existing = await self._table_columns(table)
            for column in columns:
                if column not in existing:
                    missing.append(f"{table}.{column}")
        if missing:
            raise RuntimeError(
                "Database schema contract is incomplete after compatibility repair/migrations: "
                + ", ".join(missing)
            )

    async def _get_schema_version(self) -> int:
        """Return the current schema version from the database."""
        cursor = await self._db.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        return row[0] if row and row[0] else 1

    @staticmethod
    def _split_sql_migration_statements(sql: str) -> list[str]:
        """Split migration SQL into executable statements.

        A plain semicolon split is not safe enough for migrations because
        semicolons may appear inside SQL comments or string literals. Round 119
        fixed a startup crash where a semicolon in a ``--`` comment was treated
        as a statement boundary, causing the next chunk of comment prose to be
        executed as SQL.

        The migration files are intentionally simple SQLite scripts, so this
        lightweight scanner strips line/block comments outside quoted strings
        and splits only on statement-terminating semicolons.
        """
        statements: list[str] = []
        chars: list[str] = []
        in_single_quote = False
        in_double_quote = False
        in_line_comment = False
        in_block_comment = False
        i = 0

        while i < len(sql):
            char = sql[i]
            next_char = sql[i + 1] if i + 1 < len(sql) else ""

            if in_line_comment:
                if char in "\r\n":
                    in_line_comment = False
                    chars.append(char)
                i += 1
                continue

            if in_block_comment:
                if char == "*" and next_char == "/":
                    in_block_comment = False
                    i += 2
                else:
                    i += 1
                continue

            if not in_single_quote and not in_double_quote:
                if char == "-" and next_char == "-":
                    in_line_comment = True
                    i += 2
                    continue
                if char == "/" and next_char == "*":
                    in_block_comment = True
                    i += 2
                    continue
                if char == ";":
                    statement = "".join(chars).strip()
                    if statement:
                        statements.append(statement)
                    chars = []
                    i += 1
                    continue

            chars.append(char)

            if char == "'" and not in_double_quote:
                if in_single_quote and next_char == "'":
                    chars.append(next_char)
                    i += 2
                    continue
                in_single_quote = not in_single_quote
            elif char == '"' and not in_single_quote:
                if in_double_quote and next_char == '"':
                    chars.append(next_char)
                    i += 2
                    continue
                in_double_quote = not in_double_quote

            i += 1

        statement = "".join(chars).strip()
        if statement:
            statements.append(statement)
        return statements

    async def _run_migrations(self, current_version: int):
        """Apply all migration files with a version higher than current_version."""
        if not self.MIGRATIONS_DIR.exists():
            logger.debug("No migrations directory found, skipping.")
            return

        migrations = []
        for path in sorted(self.MIGRATIONS_DIR.glob("*.sql")):
            try:
                version = int(path.name.split("_")[0])
                migrations.append((version, path))
            except (ValueError, IndexError):
                logger.warning(f"Skipping migration with invalid name: {path.name}")
                continue

        applied = 0
        for version, path in migrations:
            if version <= current_version:
                continue

            logger.info(f"Applying migration {version}: {path.name}")
            sql = path.read_text()

            statements = self._split_sql_migration_statements(sql)
            for stmt in statements:
                try:
                    await self._db.execute(stmt)
                except aiosqlite.OperationalError as e:
                    if "duplicate column name" in str(e).lower():
                        logger.debug(f"Skipping duplicate column: {e}")
                    else:
                        logger.error(f"Migration {version} failed on statement: {stmt[:100]}...")
                        raise

            await self._db.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                (version,),
            )
            await self._db.commit()
            applied += 1
            logger.info(f"Migration {version} applied successfully.")

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            logger.info("Database connection closed.")

    async def get_connection(self) -> Optional[aiosqlite.Connection]:
        """Return the raw aiosqlite connection (async)."""
        return self._db

    @property
    def raw_connection(self) -> Optional[aiosqlite.Connection]:
        """Return the raw aiosqlite connection (sync, for startup wiring).

        Used by create_app() to wire the ActionGateway audit store
        before the event loop is available for async access.
        """
        return self._db