"""Soulseek/slskd transfer read model for UI and agent reports.

This module is deliberately a read-model adapter, not a fake torrent layer.
It lets the LJS download UI show slskd transfers next to torrent downloads
without routing Soulseek state through libtorrent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from loguru import logger

from src.integrations.slskd_client import SlskdClient

SHADOW_PREF_KEY = "soulseek_transfer_shadow_v1"
_AUDIO_EXTENSIONS = {"mp3", "flac", "m4a", "m4b", "aac", "ogg", "opus", "wav", "aiff", "alac"}
_BOOK_EXTENSIONS = {"epub", "pdf", "azw3", "mobi", "djvu", "cbz", "cbr"}


@dataclass(frozen=True)
class SlskdTransferReadModel:
    """Build LJS-compatible transfer cards from slskd and shadow cache state."""

    settings_manager: Any
    database: Any | None = None

    async def active_download_rows(self, *, include_completed: bool = True) -> list[dict[str, Any]]:
        """Return Soulseek transfer rows in the same broad shape as DownloadItem JSON."""
        settings = getattr(self.settings_manager, "settings", None)
        cfg = getattr(settings, "soulseek", None)
        if cfg is None or not getattr(cfg, "api_configured", False):
            return []
        live_rows: list[dict[str, Any]] = []
        try:
            payload = await SlskdClient(cfg).download_transfers()
            if isinstance(payload, dict) and payload.get("ok") is not False:
                live_rows = self._rows_from_transfer_payload(payload, include_completed=include_completed)
        except Exception as exc:
            logger.debug(f"Soulseek transfer read model skipped live slskd query: {exc}")
        shadow_rows = await self._shadow_rows(include_completed=include_completed)
        return self._merge_live_and_shadow(live_rows, shadow_rows)

    async def add_shadow_transfer(
        self,
        *,
        username: str,
        filename: str = "",
        filenames: list[str] | None = None,
        file_requests: list[dict[str, Any]] | None = None,
        category_id: str = "",
        candidate: dict[str, Any] | None = None,
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Persist a local read-model row immediately after queueing in slskd.

        slskd transfer polling may lag behind a successful queue request, and
        old UI code only knows LJS download rows.  This shadow row makes the
        transfer visible immediately; live slskd rows replace it once available.
        """
        if not self.database or not getattr(self.database, "system", None):
            return None
        files = self._file_requests_to_files(filename=filename, filenames=filenames, file_requests=file_requests)
        if not username or not files:
            return None
        candidate = dict(candidate or {})
        folder = str(candidate.get("folder") or _common_remote_parent([f["filename"] for f in files]) or "").strip()
        item_name = _clean_item_name(candidate.get("folder") or folder or files[0]["filename"])
        row = {
            "shadow_id": _stable_id("shadow", username, folder or files[0]["filename"]),
            "username": username,
            "category_id": category_id or str(candidate.get("category_id") or _guess_category(files)),
            "item_name": item_name,
            "folder": folder,
            "files": files,
            "candidate_id": candidate.get("candidate_id"),
            "candidate_type": candidate.get("candidate_type"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "queued",
            "receipt": self._compact_receipt(receipt),
        }
        rows = await self._load_shadow_cache()
        rows = [existing for existing in rows if existing.get("shadow_id") != row["shadow_id"]]
        rows.insert(0, row)
        rows = rows[:200]
        await self.database.system.set_preference(SHADOW_PREF_KEY, json.dumps(rows, ensure_ascii=False, default=str))
        return self._row_from_group(row, source_payload="shadow")

    async def _shadow_rows(self, *, include_completed: bool) -> list[dict[str, Any]]:
        rows = await self._load_shadow_cache()
        result = [self._row_from_group(row, source_payload="shadow") for row in rows]
        if include_completed:
            return result
        return [row for row in result if row.get("status") not in {"complete", "failed", "cancelled"}]

    async def _load_shadow_cache(self) -> list[dict[str, Any]]:
        if not self.database or not getattr(self.database, "system", None):
            return []
        try:
            raw = await self.database.system.get_preference(SHADOW_PREF_KEY, "[]")
            data = json.loads(raw or "[]")
            return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
        except Exception:
            return []

    async def remove_shadow_transfers(self, download_ids: list[str]) -> int:
        """Remove queued shadow rows matching LJS-visible slskd row ids."""
        if not self.database or not getattr(self.database, "system", None):
            return 0
        wanted = {str(item or "") for item in download_ids if str(item or "").strip()}
        if not wanted:
            return 0
        rows = await self._load_shadow_cache()
        kept: list[dict[str, Any]] = []
        removed = 0
        for row in rows:
            try:
                public = self._row_from_group(row, source_payload="shadow")
                if str(public.get("id") or "") in wanted or str(row.get("shadow_id") or "") in wanted:
                    removed += 1
                    continue
            except Exception:
                pass
            kept.append(row)
        if removed:
            await self.database.system.set_preference(SHADOW_PREF_KEY, json.dumps(kept, ensure_ascii=False, default=str))
        return removed

    def _rows_from_transfer_payload(self, payload: Any, *, include_completed: bool) -> list[dict[str, Any]]:
        transfers = self._collect_transfer_rows(payload)
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for transfer in transfers:
            # Prefer explicit remote filename fields.  slskd variants may also
            # expose a generic ``path`` field; treat that as a local path hint
            # unless no remote filename exists, because remote Soulseek paths are
            # not safe local filesystem assumptions.
            filename = _first_text(transfer, "filename", "fileName", "fullName", "remoteFilename", "remoteFileName")
            local_path = _first_text(transfer, "localFilename", "localFileName", "localPath", "local_path", "filePath", "pathOnDisk", "path")
            if not filename:
                filename = _first_text(transfer, "path")
            if not filename:
                continue
            username = _first_text(transfer, "username", "user", "remoteUsername") or "unknown"
            folder = _remote_parent(filename)
            key = (username.casefold(), folder.casefold() or filename.casefold())
            group = groups.setdefault(key, {
                "username": username,
                "folder": folder,
                "item_name": _clean_item_name(folder or filename),
                "files": [],
                "created_at": _first_text(transfer, "requestedAt", "createdAt") or datetime.now(timezone.utc).isoformat(),
                "category_id": "",
                "states": [],
            })
            size = _int_or_zero(_first_present(transfer, "size", "bytes", "length"))
            transferred = _int_or_zero(_first_present(transfer, "bytesTransferred", "bytes_transferred", "downloadedBytes", "downloaded"))
            average_speed = _float_or_zero(_first_present(transfer, "averageSpeed", "speed", "downloadSpeed"))
            state = _first_text(transfer, "state", "stateDescription", "status") or "queued"
            group["states"].append(state)
            group["files"].append({
                "filename": filename,
                "local_path": local_path,
                "username": username,
                "size": size,
                "bytes_transferred": transferred,
                "average_speed": average_speed,
                "state": state,
                "id": _first_text(transfer, "id", "transferId", "token") or filename,
                "bytes_remaining": _int_or_zero(_first_present(transfer, "bytesRemaining", "bytes_remaining")),
                "percent_complete": _float_or_zero(_first_present(transfer, "percentComplete", "percent_complete")),
            })
        rows = [self._row_from_group(group, source_payload="live") for group in groups.values()]
        if include_completed:
            return rows
        return [row for row in rows if row.get("status") not in {"complete", "failed", "cancelled"}]

    @staticmethod
    def _collect_transfer_rows(payload: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        def walk(value: Any, username_hint: str = "", directory_hint: str = "") -> None:
            if isinstance(value, list):
                for item in value:
                    walk(item, username_hint=username_hint, directory_hint=directory_hint)
                return
            if not isinstance(value, dict):
                return

            current_username = _first_text(value, "username", "user", "remoteUsername") or username_hint
            current_directory = _first_text(value, "directory", "folder", "path", "remoteDirectory") or directory_hint

            # slskd's documented transfer shape is grouped as:
            # {username, directories:[{directory, files:[{id, filename, state, ...}]}]}.
            # Flatten those file rows while carrying the parent username/folder.
            file_list = value.get("files")
            if isinstance(file_list, list) and (current_username or current_directory):
                for file_item in file_list:
                    if not isinstance(file_item, dict):
                        continue
                    row = dict(file_item)
                    if current_username and not _first_text(row, "username", "user", "remoteUsername"):
                        row["username"] = current_username
                    if current_directory and not _remote_parent(_first_text(row, "filename", "fileName", "fullName", "remoteFilename", "remoteFileName")):
                        filename = _first_text(row, "filename", "fileName", "fullName", "remoteFilename", "remoteFileName")
                        if filename and "/" not in filename.replace("\\", "/"):
                            directory_prefix = current_directory.rstrip("/\\")
                            row["filename"] = f"{directory_prefix}/{filename}"
                    rows.append(row)

            if _looks_like_transfer(value):
                row = dict(value)
                if current_username and not _first_text(row, "username", "user", "remoteUsername"):
                    row["username"] = current_username
                rows.append(row)
                return

            for key, child in value.items():
                if key == "files":
                    continue
                next_username = current_username
                if isinstance(key, str) and _looks_like_username_key(key) and isinstance(child, (list, dict)):
                    next_username = key
                walk(child, username_hint=next_username, directory_hint=current_directory)

        walk(payload)
        return rows

    @staticmethod
    def _file_requests_to_files(
        *,
        filename: str = "",
        filenames: list[str] | None = None,
        file_requests: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(name: Any, size: Any = None) -> None:
            text = str(name or "").strip()
            if not text or text.casefold() in seen:
                return
            seen.add(text.casefold())
            files.append({"filename": text, "size": _int_or_zero(size)})

        for item in file_requests or []:
            if isinstance(item, dict):
                add(item.get("filename") or item.get("name") or item.get("fullName"), item.get("size") or item.get("size_bytes") or item.get("length"))
        for item in filenames or []:
            add(item)
        if not files and filename:
            add(filename)
        return files

    def _row_from_group(self, group: dict[str, Any], *, source_payload: str) -> dict[str, Any]:
        files = [f for f in (group.get("files") or []) if isinstance(f, dict) and f.get("filename")]
        total_size = sum(_int_or_zero(f.get("size")) for f in files)
        downloaded = sum(_int_or_zero(f.get("bytes_transferred")) for f in files)
        rate = sum(_float_or_zero(f.get("average_speed")) for f in files)
        status = self._group_status(files, group.get("states") or [group.get("status")])
        if status not in {"downloading", "queued"}:
            rate = 0.0
        category_id = str(group.get("category_id") or _guess_category(files) or "music")
        folder = str(group.get("folder") or _common_remote_parent([f["filename"] for f in files]) or "")
        item_name = str(group.get("item_name") or _clean_item_name(folder or (files[0]["filename"] if files else "Soulseek transfer")))
        row_id = _stable_id("slskd", group.get("username"), folder or item_name)
        return {
            "id": row_id,
            "source": "slskd",
            "backend": "soulseek",
            "item_name": item_name,
            "torrent_title": f"Soulseek · {group.get('username') or 'unknown'} · {folder or item_name}",
            "magnet": f"slskd://{group.get('username') or 'unknown'}/{folder or item_name}",
            "status": status,
            "priority": "normal",
            "reason": "Soulseek/slskd transfer",
            "category_id": category_id,
            "item_id": item_name,
            "progress": (downloaded / total_size) if total_size else (1.0 if status == "complete" else 0.0),
            "download_rate": rate,
            "upload_rate": 0.0,
            "num_peers": 1 if group.get("username") else 0,
            "num_seeds": 0,
            "total_size": total_size,
            "downloaded_bytes": downloaded,
            "eta_seconds": _eta_seconds(total_size, downloaded, rate),
            "created_at": group.get("created_at") or datetime.now(timezone.utc).isoformat(),
            "files": [
                {
                    "file_index": index,
                    "file_path": f.get("filename") or "",
                    "local_path": f.get("local_path") or "",
                    "slskd_id": f.get("id") or f.get("filename") or "",
                    "size": _int_or_zero(f.get("size")),
                    "downloaded_bytes": _int_or_zero(f.get("bytes_transferred")),
                    "progress": self._file_progress(f),
                    "priority": 4,
                    "status": self._file_status(f),
                    "organized_path": None,
                }
                for index, f in enumerate(files)
            ],
            "slskd_username": group.get("username"),
            "slskd_folder": folder,
            "slskd_shadow": source_payload == "shadow",
        }

    @staticmethod
    def _file_status(file_row: dict[str, Any]) -> str:
        state = str(file_row.get("state") or "").lower()
        if any(token in state for token in ("completed", "complete", "succeeded", "finished")):
            return "complete"
        if any(token in state for token in ("failed", "errored", "rejected")):
            return "failed"
        if any(token in state for token in ("in progress", "inprogress", "downloading", "transferring")):
            return "downloading"
        return "queued"

    @staticmethod
    def _file_progress(file_row: dict[str, Any]) -> float:
        percent = _float_or_zero(file_row.get("percent_complete"))
        if percent > 1.0:
            return min(1.0, percent / 100.0)
        if 0.0 < percent <= 1.0:
            return percent
        size = _int_or_zero(file_row.get("size"))
        done = _int_or_zero(file_row.get("bytes_transferred"))
        if size:
            return max(0.0, min(1.0, done / size))
        return 1.0 if SlskdTransferReadModel._file_status(file_row) == "complete" else 0.0

    @classmethod
    def _group_status(cls, files: list[dict[str, Any]], states: list[Any]) -> str:
        file_statuses = [cls._file_status(f) for f in files]
        state_text = " ".join(str(s or "").lower() for s in states)
        if file_statuses and all(s == "complete" for s in file_statuses):
            return "complete"
        if any(s == "downloading" for s in file_statuses) or any(token in state_text for token in ("in progress", "inprogress", "downloading", "transferring")):
            return "downloading"
        if any(s == "failed" for s in file_statuses) or any(token in state_text for token in ("failed", "errored", "rejected")):
            return "failed"
        return "queued"

    @staticmethod
    def _compact_receipt(receipt: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(receipt, dict):
            return {}
        enqueued = receipt.get("enqueued") if isinstance(receipt.get("enqueued"), list) else []
        failed = receipt.get("failed") if isinstance(receipt.get("failed"), list) else []
        return {"enqueued_count": len(enqueued), "failed_count": len(failed)}

    @staticmethod
    def _merge_live_and_shadow(live_rows: list[dict[str, Any]], shadow_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        for row in live_rows + shadow_rows:
            key = (str(row.get("slskd_username") or "").casefold(), str(row.get("slskd_folder") or row.get("item_name") or "").casefold())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(row)
        return sorted(merged, key=lambda r: (str(r.get("status") or ""), str(r.get("created_at") or "")), reverse=True)


def _looks_like_transfer(row: dict[str, Any]) -> bool:
    if not any(key in row for key in ("filename", "fileName", "fullName", "path")):
        return False
    return any(key in row for key in ("state", "stateDescription", "status", "bytesTransferred", "percentComplete", "averageSpeed", "direction"))


def _looks_like_username_key(key: str) -> bool:
    text = key.strip()
    if not text or "/" in text or "\\" in text or text.lower() in {"downloads", "transfers", "items", "data", "directories", "files", "responses"}:
        return False
    return len(text) <= 80


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _first_text(row: dict[str, Any], *keys: str) -> str:
    value = _first_present(row, *keys)
    return str(value or "").strip()


def _int_or_zero(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float_or_zero(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _eta_seconds(total: int, done: int, rate: float) -> float:
    if total <= 0 or done >= total or rate <= 0:
        return 0.0
    return max(0.0, float(total - done) / float(rate))


def _remote_parent(filename: str) -> str:
    text = str(filename or "").replace("\\", "/").strip("/")
    if "/" not in text:
        return ""
    return text.rsplit("/", 1)[0]


def _common_remote_parent(filenames: list[str]) -> str:
    parents = [_remote_parent(f) for f in filenames if _remote_parent(f)]
    if not parents:
        return ""
    first = parents[0]
    return first if all(p == first for p in parents) else ""


def _clean_item_name(value: Any) -> str:
    text = str(value or "Soulseek transfer").replace("\\", "/").strip("/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text or "Soulseek transfer"


def _guess_category(files: list[dict[str, Any]]) -> str:
    suffixes = {Path(str(f.get("filename") or "")).suffix.lower().lstrip(".") for f in files}
    if suffixes & _AUDIO_EXTENSIONS:
        return "music"
    if suffixes & _BOOK_EXTENSIONS:
        return "ebooks"
    return "general"


def _stable_id(*parts: Any) -> str:
    text = "|".join(str(p or "") for p in parts)
    return "slskd:" + hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
