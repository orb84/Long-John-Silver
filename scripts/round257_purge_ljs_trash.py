#!/usr/bin/env python3
"""Audit or purge legacy in-place LJS trash folders.

Round 257 stops routine download cleanup from hiding payload files under an
invisible ``.ljs-trash`` folder.  This helper lets a local operator inspect or
permanently remove existing legacy folders after verifying they no longer need
recovery copies.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import yaml

TRASH_FOLDER_NAME = ".ljs-trash"


class LegacyTrashPurgeReport:
    """Builds and optionally executes a purge plan for legacy trash folders."""

    def __init__(self, roots: list[Path], execute: bool) -> None:
        """Create the report builder.

        Args:
            roots: Directories to scan recursively for exact ``.ljs-trash`` folders.
            execute: Whether matching folders should be permanently deleted.
        """
        self._roots = roots
        self._execute = execute

    def run(self) -> dict[str, Any]:
        """Return a JSON-serializable audit or purge report."""
        folders = self._find_trash_folders()
        entries: list[dict[str, Any]] = []
        total_bytes = 0
        deleted_count = 0
        for folder in folders:
            size_bytes = self._folder_size(folder)
            total_bytes += size_bytes
            entry: dict[str, Any] = {
                "path": str(folder),
                "size_bytes": size_bytes,
                "deleted": False,
                "error": None,
            }
            if self._execute:
                try:
                    shutil.rmtree(folder)
                    entry["deleted"] = True
                    deleted_count += 1
                except OSError as exc:
                    entry["error"] = str(exc)
            entries.append(entry)
        return {
            "mode": "execute" if self._execute else "dry_run",
            "roots": [str(root) for root in self._roots],
            "trash_folder_name": TRASH_FOLDER_NAME,
            "folders_found": len(entries),
            "folders_deleted": deleted_count,
            "total_bytes": total_bytes,
            "entries": entries,
            "next_step": "Re-run with --execute to permanently delete these folders." if not self._execute else "Done.",
        }

    def _find_trash_folders(self) -> list[Path]:
        """Find exact legacy trash folders below the configured roots."""
        matches: list[Path] = []
        seen: set[str] = set()
        for root in self._roots:
            resolved = root.expanduser().resolve(strict=False)
            if not resolved.exists() or not resolved.is_dir():
                continue
            for candidate in resolved.rglob(TRASH_FOLDER_NAME):
                if not candidate.is_dir() or candidate.name != TRASH_FOLDER_NAME:
                    continue
                key = str(candidate.resolve(strict=False))
                if key not in seen:
                    seen.add(key)
                    matches.append(candidate)
        return sorted(matches, key=lambda item: str(item))

    def _folder_size(self, folder: Path) -> int:
        """Return the total size of files in a folder tree."""
        total = 0
        for path in folder.rglob("*"):
            try:
                if path.is_file() or path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
        return total


class LegacyTrashPurgeCli:
    """Command-line interface for the legacy trash purge helper."""

    def run(self) -> None:
        """Parse arguments, run the audit/purge, and print JSON."""
        args = self._parser().parse_args()
        roots = self._roots_from_args(args)
        report = LegacyTrashPurgeReport(roots=roots, execute=bool(args.execute)).run()
        print(json.dumps(report, indent=2, sort_keys=True))

    def _parser(self) -> argparse.ArgumentParser:
        """Create the argument parser."""
        parser = argparse.ArgumentParser(description="Audit or delete legacy .ljs-trash folders.")
        parser.add_argument("roots", nargs="*", help="Download/library roots to scan. Defaults to config download_dir.")
        parser.add_argument("--settings", default="config/settings.local.yaml", help="Settings file used when roots are omitted.")
        parser.add_argument("--execute", action="store_true", help="Permanently delete matching .ljs-trash folders.")
        return parser

    def _roots_from_args(self, args: argparse.Namespace) -> list[Path]:
        """Resolve roots from explicit arguments or settings."""
        if args.roots:
            return [Path(root) for root in args.roots]
        settings_path = Path(args.settings)
        if settings_path.exists():
            data = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
            download_dir = data.get("download_dir")
            if download_dir:
                return [Path(str(download_dir))]
        return [Path("./downloads")]


if __name__ == "__main__":
    LegacyTrashPurgeCli().run()
