"""Completed Soulseek/slskd import monitor.

This monitor keeps Soulseek semantics separate from torrents while still using
category-owned library import rules.  slskd transfers are read from slskd, then
completed files are handed to the same category planning/import machinery used
by completed torrent payloads.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from src.core.models import DownloadFileInfo, DownloadItem, DownloadPriority, DownloadStatus
from src.integrations.slskd_config import build_slskd_share_plan
from src.integrations.slskd_transfer_view import SlskdTransferReadModel

SLSKD_IMPORTED_PREF_KEY = "soulseek_imported_files_v1"


def _path_probe(path: Path) -> str:
    try:
        exists = path.exists()
    except OSError as exc:
        return f"path={path} exists=ERROR({exc})"
    parts = [f"path={path}", f"exists={exists}"]
    try:
        parent = path.parent
        parts.append(f"parent={parent}")
        parts.append(f"parent_exists={parent.exists()}")
        parts.append(f"parent_writable={os.access(parent, os.W_OK)}")
    except Exception as exc:
        parts.append(f"parent_probe_error={exc}")
    if exists:
        try:
            st = path.stat()
            parts.extend([f"is_file={path.is_file()}", f"is_dir={path.is_dir()}", f"size={st.st_size}", f"mode={oct(st.st_mode & 0o777)}", f"uid={st.st_uid}", f"gid={st.st_gid}"])
        except OSError as exc:
            parts.append(f"stat_error={exc}")
    return " ".join(parts)
_AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".alac"}
_BOOK_EXTENSIONS = {".epub", ".pdf", ".azw3", ".mobi", ".djvu", ".cbz", ".cbr"}


class SlskdImportMonitor:
    """Poll slskd and import completed Soulseek files into category libraries."""

    def __init__(
        self,
        *,
        settings_manager: Any,
        database: Any,
        category_registry: Any,
        completion_handler: Any,
        interval_seconds: float = 60.0,
    ) -> None:
        self._settings_manager = settings_manager
        self._database = database
        self._categories = category_registry
        self._completion_handler = completion_handler
        self._interval_seconds = max(15.0, float(interval_seconds or 60.0))
        # Guard external disks/NAS/autofs mounts from retry storms.  Completed
        # Soulseek rows remain visible in slskd after a failed import; without
        # backoff LJS can hammer the same source/target paths every monitor pass.
        self._failure_backoff: dict[str, tuple[float, int, str]] = {}
        self._storage_circuit_until = 0.0
        self._max_import_attempts_per_pass = 4

    async def run_forever(self) -> None:
        """Run the import loop forever as a supervisor-managed background task."""
        while True:
            try:
                counters = await self.run_once()
                if counters.get("imported"):
                    logger.info(f"Soulseek import monitor: {counters}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Soulseek import monitor pass failed: {exc}")
            await asyncio.sleep(self._interval_seconds)

    async def run_once(self) -> dict[str, int]:
        """Import any completed slskd transfer files that are not yet in the library."""
        settings = self._settings_manager.settings
        cfg = getattr(settings, "soulseek", None)
        if cfg is None or not getattr(cfg, "enabled", False) or not getattr(cfg, "api_configured", False):
            return {"seen": 0, "complete": 0, "imported": 0, "missing": 0, "skipped": 0}
        if getattr(cfg, "managed", True) and str(getattr(cfg, "account_status", "") or "").lower() != "ready":
            logger.info(
                "Soulseek import monitor skipped because managed slskd is not ready/current: "
                f"account_status={getattr(cfg, 'account_status', '')!r} "
                f"message={getattr(cfg, 'account_status_message', '')!r} "
                f"settings.download_dir={getattr(settings, 'download_dir', '')!r} "
                f"soulseek.downloads_dir={getattr(cfg, 'downloads_dir', '')!r} "
                f"soulseek.incomplete_dir={getattr(cfg, 'incomplete_dir', '')!r}"
            )
            return {"seen": 0, "complete": 0, "imported": 0, "missing": 0, "skipped": 0}
        logger.info(
            "Soulseek import monitor roots: "
            f"completed={[str(r) for r in self._download_roots(settings)]} "
            f"incomplete={[str(r) for r in self._incomplete_roots(settings)]}"
        )
        if self._storage_circuit_until > time.monotonic():
            retry_in = self._storage_circuit_until - time.monotonic()
            logger.warning(
                "Soulseek import monitor skipped because storage I/O circuit is open: "
                f"retry_in={retry_in:.0f}s"
            )
            return {"seen": 0, "complete": 0, "imported": 0, "missing": 0, "skipped": 0}

        rows = await SlskdTransferReadModel(self._settings_manager, self._database).active_download_rows(include_completed=True)
        imported_keys = await self._load_imported_keys()
        counters = {"seen": 0, "complete": 0, "imported": 0, "missing": 0, "skipped": 0}
        consumed_sources: list[Path] = []
        attempts_this_pass = 0
        stop_pass = False

        for row in rows:
            if stop_pass:
                break
            if str(row.get("backend") or row.get("source") or "").lower() not in {"soulseek", "slskd"}:
                continue
            counters["seen"] += 1
            files = [f for f in (row.get("files") or []) if isinstance(f, dict)]
            complete_files = [f for f in files if _file_is_complete(f, row)]
            if not complete_files:
                counters["skipped"] += 1
                continue
            counters["complete"] += len(complete_files)
            category_id = str(row.get("category_id") or _guess_category_from_files(files) or "general")
            category = self._categories.get(category_id) if self._categories else None
            if category is None:
                counters["skipped"] += len(complete_files)
                logger.warning(f"Soulseek import skipped row with unknown category {category_id!r}: {row.get('item_name')}")
                continue

            item = self._download_item_for_row(row, category_id=category_id)
            for index, file_row in enumerate(complete_files):
                key = self._import_key(row, file_row)
                if key in imported_keys:
                    counters["skipped"] += 1
                    continue
                if self._should_skip_for_backoff(key):
                    counters["skipped"] += 1
                    continue
                if attempts_this_pass >= self._max_import_attempts_per_pass:
                    counters["skipped"] += 1
                    logger.info(
                        "Soulseek import pass budget reached; leaving remaining completed files for later: "
                        f"budget={self._max_import_attempts_per_pass}"
                    )
                    stop_pass = True
                    break
                attempts_this_pass += 1

                source_row = {
                    **file_row,
                    "username": row.get("slskd_username") or row.get("username") or file_row.get("username"),
                    "item_name": row.get("item_name"),
                    "folder": row.get("slskd_folder") or row.get("folder"),
                }
                source = self._resolve_completed_source(source_row, settings)
                remote_name = str(file_row.get('file_path') or file_row.get('filename') or '')
                if source is None:
                    counters["missing"] += 1
                    logger.info(
                        "Soulseek import unresolved completed file without deep scan: "
                        f"row_id={row.get('id')!r} item={row.get('item_name')!r} "
                        f"remote={remote_name!r} local_hint={file_row.get('local_path')!r} "
                        f"size={file_row.get('size')!r} roots={[str(r) for r in self._download_roots(settings)]}"
                    )
                    continue

                logger.info(
                    "Soulseek import resolved completed file: "
                    f"row_id={row.get('id')!r} item={row.get('item_name')!r} "
                    f"remote={remote_name!r} local_hint={file_row.get('local_path')!r} "
                    f"source_probe=({_path_probe(source)})"
                )

                df = DownloadFileInfo(
                    file_index=index,
                    file_path=str(file_row.get("file_path") or file_row.get("filename") or source.name),
                    size=int(file_row.get("size") or 0),
                    downloaded_bytes=int(file_row.get("downloaded_bytes") or file_row.get("size") or 0),
                    status="complete",
                )
                try:
                    target = await self._completion_handler._link_completed_file_to_library(  # noqa: SLF001 - intentional shared import path.
                        source,
                        item,
                        category,
                        settings,
                        file_info=df,
                        source_name=df.file_path,
                    )
                except Exception as exc:
                    self._record_import_failure(key, exc)
                    counters["skipped"] += 1
                    logger.warning(f"Soulseek category import failed for {source}: {exc}")
                    if self._storage_circuit_until > time.monotonic():
                        stop_pass = True
                        break
                    continue
                if not target:
                    self._record_import_failure(key, "library target was not materialized")
                    counters["skipped"] += 1
                    logger.warning(
                        "Soulseek import did not materialize library target: "
                        f"item={row.get('item_name')!r} remote={remote_name!r} source_probe=({_path_probe(source)})"
                    )
                    continue

                self._clear_import_failure(key)
                logger.info(
                    "Soulseek import materialized library target: "
                    f"item={row.get('item_name')!r} remote={remote_name!r} "
                    f"target_probe=({_path_probe(Path(target))})"
                )

                # Soulseek downloads are not torrent seeds, so once a library copy
                # is materialized we can clean the staging payload and empty parents.
                try:
                    if source.exists():
                        logger.info(f"Soulseek import cleanup removing staging source after successful materialization: {_path_probe(source)}")
                        if self._completion_handler._safe_unlink(source):  # noqa: SLF001
                            consumed_sources.append(source)
                            logger.info(f"Soulseek import cleanup removed staging source: {source}")
                        else:
                            logger.warning(f"Soulseek import cleanup could not remove staging source: {_path_probe(source)}")
                    else:
                        consumed_sources.append(source)
                        logger.info(f"Soulseek import cleanup skipped; source already absent after materialization: {source}")
                except Exception as exc:
                    self._record_import_failure(key, exc)
                imported_keys.add(key)
                counters["imported"] += 1
                logger.info(f"Imported Soulseek file into library: {source} -> {target}")

        if consumed_sources:
            try:
                self._completion_handler._cleanup_empty_download_parents(consumed_sources)  # noqa: SLF001
            except Exception as exc:
                logger.debug(f"Soulseek import cleanup skipped: {exc}")
        if counters["imported"]:
            await self._save_imported_keys(imported_keys)
        return counters

    async def _load_imported_keys(self) -> set[str]:
        try:
            raw = await self._database.system.get_preference(SLSKD_IMPORTED_PREF_KEY, "[]")
            data = json.loads(raw or "[]")
            if isinstance(data, list):
                return {str(item) for item in data if str(item).strip()}
        except Exception:
            pass
        return set()

    async def _save_imported_keys(self, keys: set[str]) -> None:
        try:
            data = sorted(keys)[-5000:]
            await self._database.system.set_preference(SLSKD_IMPORTED_PREF_KEY, json.dumps(data, ensure_ascii=False))
        except Exception as exc:
            logger.debug(f"Could not persist Soulseek imported-file ledger: {exc}")

    def _retry_delay_for_failure(self, key: str) -> float:
        _retry_at, count, _reason = self._failure_backoff.get(key, (0.0, 0, ""))
        return min(3600.0, 300.0 * (2 ** min(count, 3)))

    def _should_skip_for_backoff(self, key: str) -> bool:
        retry_at, count, reason = self._failure_backoff.get(key, (0.0, 0, ""))
        now = time.monotonic()
        if retry_at > now:
            logger.debug(
                "Soulseek import backoff active; skipping file this pass: "
                f"key={key} retry_in={retry_at - now:.0f}s failures={count} reason={reason}"
            )
            return True
        return False

    def _record_import_failure(self, key: str, exc: BaseException | str) -> None:
        reason = str(exc)[:300]
        _retry_at, count, _old_reason = self._failure_backoff.get(key, (0.0, 0, ""))
        count += 1
        retry_at = time.monotonic() + self._retry_delay_for_failure(key)
        self._failure_backoff[key] = (retry_at, count, reason)
        if _is_storage_io_failure(exc):
            self._storage_circuit_until = max(self._storage_circuit_until, time.monotonic() + 600.0)
            logger.error(
                "Soulseek import storage circuit opened after filesystem I/O failure; "
                f"skipping further import attempts for {self._storage_circuit_until - time.monotonic():.0f}s "
                f"key={key} error={reason}"
            )
        else:
            logger.warning(
                "Soulseek import failure recorded with backoff: "
                f"key={key} failures={count} retry_in={retry_at - time.monotonic():.0f}s error={reason}"
            )

    def _clear_import_failure(self, key: str) -> None:
        self._failure_backoff.pop(key, None)

    def _download_item_for_row(self, row: dict[str, Any], *, category_id: str) -> DownloadItem:
        item_name = str(row.get("item_name") or row.get("slskd_folder") or "Soulseek transfer")
        files = row.get("files") if isinstance(row.get("files"), list) else []
        total_size = sum(_int_or_zero(f.get("size")) for f in files if isinstance(f, dict))
        return DownloadItem(
            id=str(row.get("id") or self._import_key(row, {})),
            item_name=item_name,
            magnet=str(row.get("magnet") or f"slskd://{row.get('slskd_username') or 'unknown'}/{item_name}"),
            status=DownloadStatus.COMPLETE,
            priority=DownloadPriority.NORMAL,
            progress=1.0,
            total_size=total_size,
            downloaded_bytes=total_size,
            file_path="",
            category_id=category_id,
            item_id=item_name,
            torrent_title=str(row.get("torrent_title") or f"Soulseek · {row.get('slskd_username') or 'unknown'} · {item_name}"),
            save_path=self._download_root(),
        )

    def _download_root(self) -> str:
        try:
            return build_slskd_share_plan(self._settings_manager.settings).downloads_dir
        except Exception:
            return str(Path(getattr(self._settings_manager.settings, "download_dir", "./downloads")).resolve(strict=False))

    def _download_roots(self, settings: Any) -> list[Path]:
        """Return completed-download roots only.

        slskd remote filenames are not local paths.  A remote filename can start
        with arbitrary share folders such as ``Music/`` or ``Albums/`` from the
        uploader.  Import resolution must therefore prefer observed local paths
        and filesystem discovery under the configured completed download root,
        not blindly join the remote path onto the LJS downloads directory.
        """
        return self._unique_roots(self._download_root_values(settings, include_incomplete=False))

    def _incomplete_roots(self, settings: Any) -> list[Path]:
        """Return slskd incomplete roots, used only to avoid premature imports."""
        return self._unique_roots(self._download_root_values(settings, include_incomplete=True, only_incomplete=True))

    def _download_root_values(self, settings: Any, *, include_incomplete: bool, only_incomplete: bool = False) -> list[Path]:
        roots: list[Path] = []
        plan_downloads: Path | None = None
        plan_incomplete: Path | None = None
        try:
            plan = build_slskd_share_plan(settings)
            plan_downloads = Path(plan.downloads_dir)
            plan_incomplete = Path(plan.incomplete_dir)
            if not only_incomplete:
                roots.append(plan_downloads)
            if include_incomplete or only_incomplete:
                roots.append(plan_incomplete)
        except Exception as exc:
            logger.debug(f"Soulseek import could not build slskd share plan: {exc}")
        soulseek = getattr(settings, "soulseek", None)
        managed = bool(getattr(soulseek, "managed", True)) if soulseek is not None else True
        if managed:
            raw_downloads = str(getattr(soulseek, "downloads_dir", "") or "") if soulseek is not None else ""
            raw_incomplete = str(getattr(soulseek, "incomplete_dir", "") or "") if soulseek is not None else ""
            if raw_downloads and plan_downloads is not None and self._same_resolved_path(Path(raw_downloads), plan_downloads) is False:
                logger.warning(
                    "Ignoring stale managed soulseek.downloads_dir during import scan: "
                    f"raw={raw_downloads!r} effective={plan_downloads} settings.download_dir={getattr(settings, 'download_dir', '')!r}"
                )
            if raw_incomplete and plan_incomplete is not None and self._same_resolved_path(Path(raw_incomplete), plan_incomplete) is False:
                logger.warning(
                    "Ignoring stale managed soulseek.incomplete_dir during import scan: "
                    f"raw={raw_incomplete!r} effective={plan_incomplete} settings.download_dir={getattr(settings, 'download_dir', '')!r}"
                )
            return roots

        if not only_incomplete:
            raw = getattr(soulseek, "downloads_dir", "")
            if raw:
                roots.append(Path(str(raw)))
        if include_incomplete or only_incomplete:
            raw = getattr(soulseek, "incomplete_dir", "")
            if raw:
                roots.append(Path(str(raw)))
        return roots

    @staticmethod
    def _same_resolved_path(left: Path, right: Path) -> bool:
        try:
            return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)
        except Exception:
            return str(left) == str(right)

    @staticmethod
    def _unique_roots(roots: list[Path]) -> list[Path]:
        result: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            try:
                resolved = root.expanduser().resolve(strict=False)
            except Exception:
                continue
            key = str(resolved)
            if key not in seen:
                seen.add(key)
                result.append(resolved)
        return result

    def _resolve_completed_source(self, file_row: dict[str, Any], settings: Any) -> Path | None:
        """Resolve a completed slskd transfer to an actual local file.

        The transfer API generally exposes the remote Soulseek filename.  That
        string is *not* the completed local pathname: uploaders often share files
        below top-level folders such as ``Music`` that do not exist in our LJS
        downloads folder.  Treat remote paths as identity hints and locate the
        materialized file under slskd's completed downloads directory.
        """
        completed_roots = self._download_roots(settings)
        incomplete_roots = self._incomplete_roots(settings)
        remote = str(file_row.get("file_path") or file_row.get("filename") or "").replace("\\", "/").strip("/")
        local = str(file_row.get("local_path") or "").strip()
        username = str(file_row.get("username") or file_row.get("slskd_username") or "").strip()
        expected_size = _int_or_zero(file_row.get("size"))
        candidates: list[Path] = []

        # 1) Use a real local path from slskd when it exists.  Relative local
        # paths are rooted at the configured completed download directory.
        if local:
            local_path = Path(local).expanduser()
            if local_path.is_absolute():
                candidates.append(local_path)
            else:
                for root in completed_roots:
                    candidates.append(root / local_path)

        # 2) Probe plausible completed-layout paths.  Avoid broad recursive
        # scans: completed rows remain in slskd after an import failure and a
        # basename rglob of the whole download root every minute can hammer USB
        # or autofs/NAS storage.  Build deterministic candidates from the remote
        # folder and the LJS-visible row metadata instead.
        remote_parts = [part for part in remote.split("/") if part]
        basename = remote_parts[-1] if remote_parts else Path(local).name
        relative_candidates = _relative_source_candidates(
            basename=basename,
            remote_parts=remote_parts,
            item_name=str(file_row.get("item_name") or ""),
            folder=str(file_row.get("folder") or ""),
            username=username,
        )
        if basename:
            for root in completed_roots:
                for relative in relative_candidates:
                    candidates.append(root / relative)

        resolved = self._first_existing_completed_candidate(candidates, completed_roots, incomplete_roots, expected_size)
        if resolved is not None:
            return resolved

        if basename:
            found = self._discover_completed_by_basename(
                basename=basename,
                remote=remote,
                username=username,
                expected_size=expected_size,
                completed_roots=completed_roots,
                incomplete_roots=incomplete_roots,
            )
            if found is not None:
                return found
        return None

    def _first_existing_completed_candidate(
        self,
        candidates: list[Path],
        completed_roots: list[Path],
        incomplete_roots: list[Path],
        expected_size: int,
    ) -> Path | None:
        seen: set[str] = set()
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve(strict=False)
            except Exception:
                continue
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            try:
                if not resolved.exists() or not resolved.is_file():
                    continue
            except OSError as exc:
                logger.warning(f"Soulseek import cannot inspect completed candidate {resolved}: {exc}")
                continue
            if any(_path_within_root(resolved, root) for root in incomplete_roots):
                logger.debug(f"Soulseek import waits for slskd to move completed file out of incomplete folder: {resolved}")
                continue
            if not any(_path_within_root(resolved, root) for root in completed_roots):
                continue
            if expected_size and _safe_size(resolved) not in {0, expected_size}:
                continue
            return resolved
        return None

    def _discover_completed_by_basename(
        self,
        *,
        basename: str,
        remote: str,
        username: str,
        expected_size: int,
        completed_roots: list[Path],
        incomplete_roots: list[Path],
    ) -> Path | None:
        if os.environ.get("LJS_SLSKD_IMPORT_DEEP_SCAN", "").strip().lower() not in {"1", "true", "yes"}:
            logger.debug(
                "Soulseek import deep basename scan disabled: "
                f"basename={basename!r} roots={[str(r) for r in completed_roots]} "
                "set LJS_SLSKD_IMPORT_DEEP_SCAN=1 for a capped diagnostic scan"
            )
            return None
        matches: list[tuple[int, Path]] = []
        remote_tokens = _path_tokens(remote)
        username_text = username.casefold()
        max_scan = 200
        for root in completed_roots:
            try:
                if not root.exists() or not root.is_dir():
                    continue
            except OSError as exc:
                logger.warning(f"Soulseek import cannot inspect completed root {root}: {exc}")
                continue
            try:
                iterator = root.rglob(basename)
            except Exception:
                continue
            scanned = 0
            try:
                for path in iterator:
                    scanned += 1
                    if scanned > max_scan:
                        logger.warning(f"Soulseek import diagnostic basename scan capped under {root} for {basename!r} at {max_scan} entries")
                        break
                    try:
                        resolved = path.resolve(strict=False)
                    except Exception:
                        continue
                    try:
                        if not resolved.is_file():
                            continue
                    except OSError as exc:
                        logger.warning(f"Soulseek import cannot inspect discovered candidate {resolved}: {exc}")
                        continue
                    if any(_path_within_root(resolved, inc) for inc in incomplete_roots):
                        continue
                    if expected_size and _safe_size(resolved) not in {0, expected_size}:
                        continue
                    rel_text = ""
                    try:
                        rel_text = str(resolved.relative_to(root)).replace("\\", "/").casefold()
                    except Exception:
                        rel_text = str(resolved).replace("\\", "/").casefold()
                    score = 0
                    if username_text and username_text in rel_text:
                        score += 20
                    score += sum(1 for token in remote_tokens if token and token in rel_text)
                    if expected_size:
                        score += 5
                    matches.append((score, resolved))
            except OSError as exc:
                logger.warning(f"Soulseek import basename scan failed under {root} for {basename!r}: {exc}")
                continue
        if not matches:
            return None
        matches.sort(key=lambda item: (item[0], len(str(item[1]))), reverse=True)
        return matches[0][1]

    @staticmethod
    def _import_key(row: dict[str, Any], file_row: dict[str, Any]) -> str:
        username = str(row.get("slskd_username") or row.get("username") or "")
        filename = str(file_row.get("file_path") or file_row.get("filename") or file_row.get("local_path") or "")
        size = str(file_row.get("size") or "")
        payload = f"{username}|{filename}|{size}"
        return "slskd-import:" + hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:24]



def _relative_source_candidates(
    *,
    basename: str,
    remote_parts: list[str],
    item_name: str,
    folder: str,
    username: str,
) -> list[Path]:
    """Return deterministic likely slskd local layouts without walking roots."""
    seen: set[str] = set()
    result: list[Path] = []

    def add(*parts: str) -> None:
        cleaned = [_safe_local_segment(part) for part in parts if str(part or "").strip()]
        if not cleaned:
            return
        rel = Path(*cleaned)
        key = str(rel).replace("\\", "/").casefold()
        if key in seen:
            return
        seen.add(key)
        result.append(rel)

    if not basename:
        return result
    add(basename)
    if username:
        add(username, basename)
    if remote_parts:
        add(*remote_parts)
    if len(remote_parts) >= 2:
        album_dir = remote_parts[-2]
        add(album_dir, basename)
        if username:
            add(username, album_dir, basename)
    for text in (folder, item_name):
        parts = [part for part in str(text or "").replace("\\", "/").split("/") if part]
        if parts:
            add(parts[-1], basename)
            if username:
                add(username, parts[-1], basename)
    return result


def _safe_local_segment(value: str) -> str:
    """Clean one untrusted remote path segment enough for local probing."""
    text = str(value or "").replace("\x00", "").strip().strip("/")
    text = text.replace("\\", "_").replace("/", "_")
    text = "".join("_" if ch in ':*?<>|"' or ord(ch) < 32 else ch for ch in text)
    text = text.rstrip(" .") or "_"
    return text


def _is_storage_io_failure(exc: BaseException | str) -> bool:
    """Return true for errors that should stop a pass to protect storage."""
    if isinstance(exc, BaseException):
        current: BaseException | None = exc
        while current is not None:
            err_no = getattr(current, "errno", None)
            if err_no in {errno.EIO, errno.EROFS, errno.ENOSPC, errno.ENODEV, errno.ESTALE}:
                return True
            current = current.__cause__ if isinstance(current.__cause__, BaseException) else None
        text = repr(exc)
    else:
        text = str(exc)
    lowered = text.lower()
    return any(token in lowered for token in (
        "input/output error",
        "read-only file system",
        "no space left on device",
        "stale file handle",
        "no such device",
    ))

def _file_is_complete(file_row: dict[str, Any], row: dict[str, Any]) -> bool:
    status = str(file_row.get("status") or row.get("status") or "").lower()
    if any(token in status for token in ("complete", "completed", "finished", "succeeded", "organized")):
        return True
    size = _int_or_zero(file_row.get("size"))
    done = _int_or_zero(file_row.get("downloaded_bytes"))
    return bool(size and done >= size)


def _guess_category_from_files(files: list[dict[str, Any]]) -> str:
    suffixes = {Path(str(f.get("file_path") or f.get("filename") or "")).suffix.lower() for f in files if isinstance(f, dict)}
    if suffixes & _AUDIO_EXTENSIONS:
        return "music"
    if suffixes & _BOOK_EXTENSIONS:
        return "ebooks"
    return "general"


def _path_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except Exception:
        return False


def _safe_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return 0


def _path_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for part in str(value or "").replace("\\", "/").split("/"):
        cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in part)
        for token in cleaned.split():
            if len(token) >= 3 and token not in {"music", "album", "albums", "download", "downloads", "media"}:
                tokens.add(token)
    return tokens


def _int_or_zero(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0
