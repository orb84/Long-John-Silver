"""
Download repository for LJS.

Download records are category-aware and category-neutral: the repository stores
``category_id``, ``item_id``, and ``item_name`` without embedding TV-specific
semantics into the database schema.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from src.core.models import (
    BlacklistEntry,
    DownloadFileInfo,
    DownloadImportContext,
    DownloadItem,
    DownloadPriority,
    DownloadStatus,
    SuggestedActionRecord,
    UpgradeRecord,
)
from src.core.repositories.base import BaseRepository


def _dump_import_context(context: DownloadImportContext | dict | None) -> str:
    """Serialize a stable download import context for SQLite."""
    if not context:
        return "{}"
    if isinstance(context, DownloadImportContext):
        return json.dumps(context.model_dump(mode="json"), default=str)
    return json.dumps(context, default=str)


def _load_import_context(value: str | None) -> DownloadImportContext | None:
    """Deserialize persisted provider identity without failing old rows."""
    data = DownloadRepository._load(value, {})
    if not isinstance(data, dict) or not data:
        return None
    try:
        return DownloadImportContext(**data)
    except Exception:
        return None


def _import_contexts_overlap(wanted: DownloadImportContext, other: DownloadImportContext) -> bool:
    """Return True when two provider contexts represent the same library unit.

    Prefer category-owned ``unit_descriptor`` keys. The legacy season/episode
    comparison remains only for old rows that predate descriptors. This keeps
    the repository generic: it compares opaque stable keys instead of deciding
    what an episode, chapter, DLC, or edition means.
    """
    if other.stable_provider_key != wanted.stable_provider_key:
        return False
    if (other.season_order_type or "official") != (wanted.season_order_type or "official"):
        return False
    wanted_has_descriptor = bool((wanted.unit_descriptor or {}).get("stable_key"))
    other_has_descriptor = bool((other.unit_descriptor or {}).get("stable_key"))
    if wanted_has_descriptor or other_has_descriptor:
        if not (wanted_has_descriptor and other_has_descriptor):
            return False
        return wanted.stable_unit_key == other.stable_unit_key
    if wanted.season is None or other.season is None:
        # Item-level contexts are only duplicate-safe against item-level rows.
        return wanted.season is None and other.season is None
    if wanted.season != other.season:
        return False
    if wanted.episode is None or other.episode is None:
        # A season context overlaps all episodes in that same season.
        return True
    return wanted.episode == other.episode


class DownloadRepository(BaseRepository):
    """Repository for download queue, release group, suggestion, and upgrade data."""

    @staticmethod
    def _now() -> str:
        """Return the current UTC timestamp for persisted records."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _dump(value) -> str:
        """Serialize a value as JSON with a deterministic fallback."""
        return json.dumps(value if value is not None else [], default=str)

    @staticmethod
    def _load(value: str | None, default):
        """Load a JSON value from SQLite."""
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default


    async def upsert_download(self, item: DownloadItem) -> None:
        """Insert or update a category-aware download record."""
        files_json = self._dump([f.model_dump() for f in item.files]) if item.files else "[]"
        await self._db.execute(
            """INSERT INTO downloads
               (id, category_id, item_id, item_name, magnet, status, priority, reason,
                season, episode, progress, download_rate, upload_rate, num_peers, num_seeds,
                total_size, downloaded_bytes, eta_seconds, file_path, files, language,
                torrent_title, import_context_json, save_path, sharing_enabled, uploaded_bytes, seed_ratio,
                source_seeders, stalled_notified, stalled_cancel_asked, user_id,
                created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   category_id = excluded.category_id,
                   item_id = excluded.item_id,
                   item_name = excluded.item_name,
                   magnet = excluded.magnet,
                   status = excluded.status,
                   priority = excluded.priority,
                   reason = excluded.reason,
                   season = excluded.season,
                   episode = excluded.episode,
                   progress = excluded.progress,
                   download_rate = excluded.download_rate,
                   upload_rate = excluded.upload_rate,
                   num_peers = excluded.num_peers,
                   num_seeds = excluded.num_seeds,
                   total_size = excluded.total_size,
                   downloaded_bytes = excluded.downloaded_bytes,
                   eta_seconds = excluded.eta_seconds,
                   file_path = excluded.file_path,
                   files = excluded.files,
                   language = excluded.language,
                   torrent_title = excluded.torrent_title,
                   import_context_json = excluded.import_context_json,
                   save_path = excluded.save_path,
                   sharing_enabled = excluded.sharing_enabled,
                   uploaded_bytes = excluded.uploaded_bytes,
                   seed_ratio = excluded.seed_ratio,
                   source_seeders = excluded.source_seeders,
                   stalled_notified = excluded.stalled_notified,
                   stalled_cancel_asked = excluded.stalled_cancel_asked,
                   user_id = excluded.user_id,
                   completed_at = excluded.completed_at""",
            (
                item.id,
                item.category_id,
                item.item_id or item.item_name,
                item.item_name,
                item.magnet,
                item.status.value,
                item.priority.value,
                item.reason,
                item.season,
                item.episode,
                item.progress,
                item.download_rate,
                item.upload_rate,
                item.num_peers,
                item.num_seeds,
                item.total_size,
                item.downloaded_bytes,
                item.eta_seconds,
                item.file_path,
                files_json,
                item.language,
                item.torrent_title,
                _dump_import_context(item.import_context),
                item.save_path,
                1 if item.sharing_enabled else 0,
                item.uploaded_bytes,
                item.seed_ratio,
                item.source_seeders,
                1 if item.stalled_notified else 0,
                1 if item.stalled_cancel_asked else 0,
                item.user_id,
                item.created_at.isoformat() if hasattr(item.created_at, "isoformat") else item.created_at,
                item.completed_at.isoformat() if item.completed_at and hasattr(item.completed_at, "isoformat") else item.completed_at,
            ),
        )
        await self._db.commit()

    async def get_download(self, download_id: str) -> Optional[DownloadItem]:
        """Retrieve a download by its ID."""
        cursor = await self._db.execute("SELECT * FROM downloads WHERE id = ?", (download_id,))
        row = await cursor.fetchone()
        return self._row_to_download(row) if row else None

    async def get_active_downloads(self) -> list[DownloadItem]:
        """Get downloads that are not terminal."""
        cursor = await self._db.execute(
            "SELECT * FROM downloads WHERE status IN ('queued', 'downloading', 'paused', 'seeding', 'stalled')"
        )
        rows = await cursor.fetchall()
        return [self._row_to_download(row) for row in rows]

    async def get_recent_downloads(self, limit: int = 20) -> list[DownloadItem]:
        """Get recent downloads ordered newest-first."""
        cursor = await self._db.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = await cursor.fetchall()
        return [self._row_to_download(row) for row in rows]

    async def get_queued_downloads(self) -> list[DownloadItem]:
        """Get queued downloads ordered by priority then creation time."""
        priority_order = {"high": 0, "normal": 1, "low": 2}
        cursor = await self._db.execute("SELECT * FROM downloads WHERE status = 'queued'")
        rows = await cursor.fetchall()
        items = [self._row_to_download(row) for row in rows]
        items.sort(key=lambda download: (priority_order.get(download.priority.value, 1), download.created_at))
        return items

    async def find_existing_by_import_context(
        self,
        context: DownloadImportContext,
        *,
        statuses: set[str] | None = None,
        limit: int = 500,
    ) -> list[DownloadItem]:
        """Find downloads with the same stable provider/unit identity.

        SQLite JSON support is not guaranteed in all target environments, so this
        method scans a bounded set of rows and compares normalized context fields
        in Python.  Provider IDs win over title/year fallbacks.
        """
        if not context or not context.stable_provider_key:
            return []
        wanted_statuses = statuses or {"queued", "downloading", "paused", "seeding", "stalled", "complete"}
        cursor = await self._db.execute(
            "SELECT * FROM downloads ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        )
        rows = await cursor.fetchall()
        matches: list[DownloadItem] = []
        for row in rows:
            item = self._row_to_download(row)
            status_value = item.status.value if hasattr(item.status, "value") else str(item.status)
            if status_value not in wanted_statuses:
                continue
            other = item.import_context
            if other and _import_contexts_overlap(context, other):
                matches.append(item)
        return matches

    async def find_active_by_import_context(self, context: DownloadImportContext) -> list[DownloadItem]:
        """Compatibility wrapper for non-terminal import-context matches."""
        return await self.find_existing_by_import_context(
            context,
            statuses={"queued", "downloading", "paused", "seeding", "stalled"},
        )

    async def add_blacklist_entry(self, entry: BlacklistEntry) -> None:
        """Add or update a blacklist pattern."""
        await self._db.execute(
            "INSERT OR REPLACE INTO blacklist (pattern, reason, added_at) VALUES (?, ?, ?)",
            (entry.pattern, entry.reason, entry.added_at.isoformat()),
        )
        await self._db.commit()

    async def get_blacklist(self) -> list[BlacklistEntry]:
        """Return all blacklist entries."""
        cursor = await self._db.execute("SELECT * FROM blacklist ORDER BY pattern")
        rows = await cursor.fetchall()
        return [BlacklistEntry(pattern=row["pattern"], reason=row["reason"], added_at=row["added_at"]) for row in rows]

    async def remove_blacklist_entry(self, pattern: str) -> None:
        """Remove one blacklist pattern."""
        await self._db.execute("DELETE FROM blacklist WHERE pattern = ?", (pattern,))
        await self._db.commit()

    async def update_release_group(self, name: str, success: bool, quality_score: float | None = None) -> None:
        """Update release group reputation based on download outcome."""
        cursor = await self._db.execute("SELECT * FROM release_groups WHERE name = ?", (name,))
        existing = await cursor.fetchone()
        if existing:
            old_avg = existing["avg_quality"] or 0.0
            old_count = existing["download_count"] or 0
            new_count = old_count + 1
            new_avg = old_avg + (quality_score - old_avg) / new_count if quality_score is not None else old_avg
        else:
            new_avg = quality_score if quality_score is not None else 0.0
        await self._db.execute(
            """INSERT INTO release_groups
               (name, download_count, success_count, fail_count, avg_quality, updated_at)
               VALUES (?, 1, ?, ?, ?, datetime('now'))
               ON CONFLICT(name) DO UPDATE SET
                   download_count = download_count + 1,
                   success_count = success_count + ?,
                   fail_count = fail_count + ?,
                   avg_quality = ?,
                   updated_at = datetime('now')""",
            (name, 1 if success else 0, 0 if success else 1, new_avg, 1 if success else 0, 0 if success else 1, new_avg),
        )
        await self._db.commit()

    async def get_release_group(self, name: str) -> Optional[dict]:
        """Get release group reputation."""
        cursor = await self._db.execute("SELECT * FROM release_groups WHERE name = ?", (name,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_blacklisted_release_groups(self) -> list[str]:
        """Return release groups marked as blacklisted."""
        cursor = await self._db.execute("SELECT name FROM release_groups WHERE blacklisted = 1")
        rows = await cursor.fetchall()
        return [row["name"] for row in rows]

    async def upsert_upgrade_candidate(self, candidate: UpgradeRecord) -> None:
        """Store or update an upgrade candidate for any category item."""
        now = self._now()
        item_id = candidate.item_id or candidate.item_name
        item_name = candidate.item_name or item_id
        category_id = candidate.category_id or "media"
        await self._db.execute(
            """INSERT INTO upgrade_candidates
               (category_id, item_id, item_name, current_resolution, current_codecs,
                best_upgrade_resolution, best_upgrade_codecs, best_upgrade_title,
                best_upgrade_magnet, quality_improvement, status, found_at, approved_at, denied_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                category_id,
                item_id,
                item_name,
                candidate.current_resolution,
                self._dump(candidate.current_codecs),
                candidate.best_upgrade_resolution,
                self._dump(candidate.best_upgrade_codecs),
                candidate.best_upgrade_title,
                candidate.best_upgrade_magnet,
                candidate.quality_improvement,
                candidate.status,
                candidate.found_at or now,
                None,
                None,
            ),
        )
        await self._db.commit()

    async def get_upgrade_candidates(
        self,
        category_id: str | None = None,
        item_id: str | None = None,
        status: str | None = None,
    ) -> list[UpgradeRecord]:
        """Fetch upgrade candidates, optionally filtered by item and status."""
        query = "SELECT * FROM upgrade_candidates WHERE 1=1"
        params: list[str] = []
        resolved_item_id = item_id
        if category_id:
            query += " AND category_id = ?"
            params.append(category_id)
        if resolved_item_id:
            query += " AND item_id = ?"
            params.append(resolved_item_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY found_at DESC"
        cursor = await self._db.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return [
            UpgradeRecord(
                id=row["id"],
                category_id=row["category_id"],
                item_id=row["item_id"],
                item_name=row["item_name"],
                current_resolution=row["current_resolution"],
                current_codecs=self._load(row["current_codecs"], []),
                best_upgrade_resolution=row["best_upgrade_resolution"],
                best_upgrade_codecs=self._load(row["best_upgrade_codecs"], []),
                best_upgrade_title=row["best_upgrade_title"],
                best_upgrade_magnet=row["best_upgrade_magnet"],
                quality_improvement=row["quality_improvement"],
                status=row["status"],
                found_at=row["found_at"],
            )
            for row in rows
        ]

    async def set_upgrade_status(self, upgrade_id: int, status: str) -> None:
        """Approve or deny an upgrade candidate."""
        column = "approved_at" if status == "approved" else "denied_at"
        await self._db.execute(
            f"UPDATE upgrade_candidates SET status = ?, {column} = ? WHERE id = ?",
            (status, self._now(), upgrade_id),
        )
        await self._db.commit()

    async def upsert_suggested_action(self, action: SuggestedActionRecord) -> None:
        """Insert or update a suggested action for any category item."""
        now = self._now()
        item_id = action.item_id or action.item_name
        item_name = action.item_name or item_id
        category_id = action.category_id or "media"
        await self._db.execute(
            """INSERT INTO suggested_actions
               (category_id, item_id, item_name, action_type, title, description,
                endpoint, method, body, priority, status, metadata, created_at,
                approved_at, denied_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                category_id,
                item_id,
                item_name,
                action.action_type,
                action.title,
                action.description,
                action.endpoint,
                action.method,
                action.body_json,
                action.priority,
                action.status,
                action.metadata_json,
                action.created_at or now,
                action.approved_at,
                action.denied_at,
            ),
        )
        await self._db.commit()

    async def get_suggested_actions(
        self,
        category_id: str | None = None,
        item_id: str | None = None,
        status: str | None = None,
    ) -> list[SuggestedActionRecord]:
        """Retrieve suggested actions, optionally filtered by item and status."""
        query = "SELECT * FROM suggested_actions WHERE 1=1"
        params: list[str] = []
        resolved_item_id = item_id
        if category_id:
            query += " AND category_id = ?"
            params.append(category_id)
        if resolved_item_id:
            query += " AND item_id = ?"
            params.append(resolved_item_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY category_id, item_id, priority DESC, created_at DESC"
        cursor = await self._db.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return [
            SuggestedActionRecord(
                id=row["id"],
                category_id=row["category_id"],
                item_id=row["item_id"],
                item_name=row["item_name"],
                action_type=row["action_type"],
                title=row["title"],
                description=row["description"],
                endpoint=row["endpoint"],
                method=row["method"],
                body_json=row["body"],
                priority=row["priority"],
                status=row["status"],
                metadata_json=row["metadata"],
                created_at=row["created_at"],
                approved_at=row["approved_at"],
                denied_at=row["denied_at"],
            )
            for row in rows
        ]

    async def set_suggested_action_status(self, action_id: int, status: str) -> None:
        """Approve or deny a suggested action."""
        column = "approved_at" if status == "approved" else "denied_at"
        await self._db.execute(
            f"UPDATE suggested_actions SET status = ?, {column} = ? WHERE id = ?",
            (status, self._now(), action_id),
        )
        await self._db.commit()

    async def clear_suggestions_for_item(self, category_id: str, item_id: str) -> None:
        """Remove suggested actions for one category item."""
        await self._db.execute(
            "DELETE FROM suggested_actions WHERE category_id = ? AND item_id = ?",
            (category_id, item_id),
        )
        await self._db.commit()

    async def get_suggestion_summary(self) -> dict:
        """Return pending/approved/denied suggestion counts per category item."""
        cursor = await self._db.execute(
            """SELECT category_id, item_id, item_name, status, COUNT(*) as cnt
               FROM suggested_actions
               GROUP BY category_id, item_id, item_name, status
               ORDER BY category_id, item_name"""
        )
        rows = await cursor.fetchall()
        result: dict[str, dict[str, int | str]] = {}
        for row in rows:
            key = f"{row['category_id']}:{row['item_id']}"
            if key not in result:
                result[key] = {
                    "category_id": row["category_id"],
                    "item_id": row["item_id"],
                    "item_name": row["item_name"],
                    "pending": 0,
                    "approved": 0,
                    "denied": 0,
                }
            result[key][row["status"]] = row["cnt"]
        return result

    @staticmethod
    def _row_to_download(row) -> DownloadItem:
        """Convert a SQLite row to a DownloadItem."""
        row_keys = set(row.keys()) if hasattr(row, "keys") else set()
        files_list = []
        if "files" in row_keys and row["files"]:
            try:
                data = json.loads(row["files"])
                if isinstance(data, list):
                    files_list = [DownloadFileInfo(**file_info) for file_info in data]
            except (TypeError, json.JSONDecodeError, ValueError):
                files_list = []

        return DownloadItem(
            id=row["id"],
            category_id=row["category_id"] if "category_id" in row_keys else "",
            item_id=row["item_id"] if "item_id" in row_keys else "",
            item_name=row["item_name"],
            magnet=row["magnet"],
            status=DownloadStatus(row["status"]),
            progress=row["progress"],
            download_rate=row["download_rate"],
            upload_rate=row["upload_rate"],
            num_peers=row["num_peers"],
            num_seeds=row["num_seeds"] if "num_seeds" in row_keys else 0,
            total_size=row["total_size"] if "total_size" in row_keys else 0,
            downloaded_bytes=row["downloaded_bytes"] if "downloaded_bytes" in row_keys else 0,
            eta_seconds=row["eta_seconds"] if "eta_seconds" in row_keys else 0.0,
            file_path=row["file_path"],
            priority=DownloadPriority(row["priority"]) if "priority" in row_keys else DownloadPriority.NORMAL,
            user_id=row["user_id"] if "user_id" in row_keys else None,
            reason=row["reason"] if "reason" in row_keys else "",
            season=row["season"] if "season" in row_keys else None,
            episode=row["episode"] if "episode" in row_keys else None,
            language=row["language"] if "language" in row_keys else "",
            torrent_title=row["torrent_title"] if "torrent_title" in row_keys else "",
            import_context=_load_import_context(row["import_context_json"]) if "import_context_json" in row_keys else None,
            save_path=row["save_path"] if "save_path" in row_keys else "",
            sharing_enabled=bool(row["sharing_enabled"]) if "sharing_enabled" in row_keys else False,
            uploaded_bytes=row["uploaded_bytes"] if "uploaded_bytes" in row_keys else 0,
            seed_ratio=row["seed_ratio"] if "seed_ratio" in row_keys else 0.0,
            source_seeders=row["source_seeders"] if "source_seeders" in row_keys else None,
            stalled_notified=bool(row["stalled_notified"]) if "stalled_notified" in row_keys else False,
            stalled_cancel_asked=bool(row["stalled_cancel_asked"]) if "stalled_cancel_asked" in row_keys else False,
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            files=files_list,
        )
