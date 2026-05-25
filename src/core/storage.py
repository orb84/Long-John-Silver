"""
Category-aware disk-space monitoring for LJS.

The storage monitor maps application paths to physical/logical volumes,
aggregates categories that share the same disk, and exposes compact
status summaries for the web UI and assistant prompt context.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from loguru import logger

from src.core.models import (
    Settings,
    StorageCapacityDecision,
    StoragePathUsage,
    StorageReport,
    StorageVolumeUsage,
)

_BYTES_PER_GB = 1024 ** 3
_STATUS_RANK = {"unknown": 0, "ok": 1, "warning": 2, "critical": 3}


@dataclass(frozen=True)
class StoragePathTarget:
    """One configured filesystem path that should be monitored."""

    path: Path
    purpose: str
    category_id: str | None = None
    category_name: str | None = None


class StorageMonitor:
    """Builds category-aware disk-space reports and preflight checks.

    The monitor treats every configured category library path and the global
    download directory as roots. Paths that live on the same device are grouped
    into a single volume so the UI and LLM do not double-count free space.
    """

    def __init__(self, settings_manager: object, category_registry: object | None = None) -> None:
        """Initialize the monitor with settings and optional category registry.

        Args:
            settings_manager: Object exposing a ``settings`` property.
            category_registry: Registry that can list registered categories.
        """
        self._settings_manager = settings_manager
        self._category_registry = category_registry

    @property
    def _settings(self) -> Settings:
        """Return current hot-reloaded settings."""
        return self._settings_manager.settings

    def build_report(self) -> StorageReport:
        """Return current disk usage grouped by volume and category path."""
        settings = self._settings
        if not settings.storage.enabled:
            return StorageReport(ok=True, llm_summary="Disk-space monitoring is disabled.")

        paths = [self._usage_for_target(target) for target in self._iter_targets(settings)]
        volumes = self._group_by_volume(paths)
        warnings = [v.message for v in volumes if v.status == "warning" and v.message]
        critical = [v.message for v in volumes if v.status == "critical" and v.message]
        ok = not critical
        report = StorageReport(
            ok=ok,
            volumes=volumes,
            paths=paths,
            warnings=warnings,
            critical=critical,
        )
        report.llm_summary = self.format_for_llm(report)
        return report

    def format_for_llm(self, report: StorageReport | None = None) -> str:
        """Return a compact prompt-safe storage summary.

        Args:
            report: Optional prebuilt report. When omitted, a fresh report is built.

        Returns:
            Human-readable lines grouped by disk volume.
        """
        settings = self._settings
        if not settings.storage.include_in_llm_context:
            return ""
        report = report or self.build_report()
        if not report.volumes:
            return "STORAGE STATUS: no monitored paths are configured."

        lines = ["STORAGE STATUS (category-aware; same-disk paths are grouped):"]
        max_volumes = max(1, int(settings.storage.context_max_volumes or 5))
        for volume in report.volumes[:max_volumes]:
            categories = ", ".join(volume.category_ids) if volume.category_ids else "downloads/system"
            free_gb = volume.free_bytes / _BYTES_PER_GB if volume.free_bytes else 0.0
            total_gb = volume.total_bytes / _BYTES_PER_GB if volume.total_bytes else 0.0
            lines.append(
                f"- {volume.status.upper()} {volume.mount_point}: {free_gb:.1f} GB free "
                f"of {total_gb:.1f} GB ({volume.free_percent:.1f}%) for {categories}."
            )
            if volume.status in {"warning", "critical"}:
                lines.append("  Before queueing large downloads on this disk, warn the user or choose a smaller release.")
        if len(report.volumes) > max_volumes:
            lines.append(f"- ... {len(report.volumes) - max_volumes} additional volume(s) omitted from prompt context.")
        return "\n".join(lines)

    def check_download_capacity(
        self,
        category_id: str | None = None,
        estimated_bytes: int | None = None,
    ) -> StorageCapacityDecision:
        """Check whether a category/download target has enough free space.

        Args:
            category_id: Target category ID, if known.
            estimated_bytes: Optional estimated download size in bytes.

        Returns:
            Capacity decision. ``ok=False`` only for critical/no-space states;
            warning states are allowed but should be surfaced to the user.
        """
        report = self.build_report()
        target_usage = self._select_target_usage(report, category_id)
        if not target_usage:
            return StorageCapacityDecision(
                ok=True,
                status="unknown",
                category_id=category_id,
                estimated_bytes=estimated_bytes,
                reason="No monitored storage target matched this category; proceeding without a disk-space decision.",
            )

        projected_free = None
        if estimated_bytes is not None:
            projected_free = max(0, target_usage.free_bytes - max(0, estimated_bytes))
        free_gb = target_usage.free_bytes / _BYTES_PER_GB
        projected_gb = projected_free / _BYTES_PER_GB if projected_free is not None else None
        min_after_gb = self._settings.storage.minimum_free_after_download_gb
        ok = target_usage.status != "critical"
        reason = target_usage.message or "Storage target has acceptable free space."
        if projected_gb is not None and projected_gb < min_after_gb:
            ok = False
            reason = (
                f"Estimated download would leave only {projected_gb:.1f} GB free on {target_usage.mount_point}; "
                f"minimum configured reserve is {min_after_gb:.1f} GB."
            )
        elif projected_gb is not None and target_usage.status == "warning":
            reason = (
                f"Estimated download would leave about {projected_gb:.1f} GB free on warning-level disk "
                f"{target_usage.mount_point}."
            )

        return StorageCapacityDecision(
            ok=ok,
            status="critical" if not ok else target_usage.status,
            category_id=category_id,
            estimated_bytes=estimated_bytes,
            target_path=target_usage.path,
            volume_id=target_usage.volume_id,
            free_bytes=target_usage.free_bytes,
            projected_free_bytes=projected_free,
            reason=reason,
        )

    def _iter_targets(self, settings: Settings) -> Iterable[StoragePathTarget]:
        """Yield global and category-specific paths to monitor."""
        yield StoragePathTarget(Path(settings.download_dir), "download_dir")
        seen_categories: set[str] = set()
        if self._category_registry:
            try:
                for category in self._category_registry.list_all():
                    seen_categories.add(category.category_id)
                    yield StoragePathTarget(
                        Path(category.get_root_path(settings)),
                        "category_library",
                        category_id=category.category_id,
                        category_name=category.display_name,
                    )
            except Exception as exc:
                logger.debug(f"Failed to enumerate category roots for storage monitor: {exc}")

        for category_id, values in settings.category_settings.items():
            if category_id in seen_categories:
                continue
            root = str(values.get("library_path") or "").strip()
            if root:
                yield StoragePathTarget(Path(root), "category_library", category_id=category_id, category_name=category_id)

    def _usage_for_target(self, target: StoragePathTarget) -> StoragePathUsage:
        """Resolve and measure one monitored target path."""
        requested = target.path.expanduser()
        resolved = requested.resolve(strict=False)
        anchor = self._existing_anchor(resolved)
        exists = requested.exists()
        try:
            usage = shutil.disk_usage(anchor)
            volume_id, mount_point = self._volume_identity(anchor)
            free_percent = (usage.free / usage.total * 100.0) if usage.total else 0.0
            status, message = self._status_for_usage(free_percent, usage.free, mount_point)
            return StoragePathUsage(
                path=str(resolved),
                purpose=target.purpose,
                category_id=target.category_id,
                category_name=target.category_name,
                exists=exists,
                volume_id=volume_id,
                mount_point=mount_point,
                total_bytes=usage.total,
                used_bytes=usage.used,
                free_bytes=usage.free,
                free_percent=round(free_percent, 2),
                status=status,
                message=message,
            )
        except OSError as exc:
            logger.debug(f"Disk usage lookup failed for {resolved}: {exc}")
            return StoragePathUsage(
                path=str(resolved),
                purpose=target.purpose,
                category_id=target.category_id,
                category_name=target.category_name,
                exists=exists,
                volume_id=f"unknown:{resolved}",
                mount_point=str(anchor),
                status="unknown",
                message=f"Unable to inspect disk usage for {resolved}: {exc}",
            )

    def _group_by_volume(self, paths: list[StoragePathUsage]) -> list[StorageVolumeUsage]:
        """Aggregate path usage by actual disk volume."""
        grouped: dict[str, StorageVolumeUsage] = {}
        for path_usage in paths:
            volume = grouped.get(path_usage.volume_id)
            if not volume:
                volume = StorageVolumeUsage(
                    volume_id=path_usage.volume_id,
                    mount_point=path_usage.mount_point,
                    total_bytes=path_usage.total_bytes,
                    used_bytes=path_usage.used_bytes,
                    free_bytes=path_usage.free_bytes,
                    free_percent=path_usage.free_percent,
                    status=path_usage.status,
                    message=path_usage.message,
                )
                grouped[path_usage.volume_id] = volume
            volume.paths.append(path_usage)
            if path_usage.category_id and path_usage.category_id not in volume.category_ids:
                volume.category_ids.append(path_usage.category_id)
            if _STATUS_RANK.get(path_usage.status, 0) > _STATUS_RANK.get(volume.status, 0):
                volume.status = path_usage.status
                volume.message = path_usage.message

        for volume in grouped.values():
            purposes = []
            for path_usage in volume.paths:
                label = path_usage.category_id or path_usage.purpose
                if label not in purposes:
                    purposes.append(label)
            volume.purpose_summary = ", ".join(purposes)
        return sorted(grouped.values(), key=lambda v: (_STATUS_RANK.get(v.status, 0), v.free_percent))

    def _status_for_usage(self, free_percent: float, free_bytes: int, mount_point: str) -> tuple[str, str]:
        """Classify free-space health using configured thresholds."""
        config = self._settings.storage
        free_gb = free_bytes / _BYTES_PER_GB
        if free_percent <= config.critical_free_percent or free_gb <= config.critical_free_gb:
            return "critical", f"Disk {mount_point} is critically low: {free_gb:.1f} GB free ({free_percent:.1f}%)."
        if free_percent <= config.warning_free_percent or free_gb <= config.warning_free_gb:
            return "warning", f"Disk {mount_point} is running low: {free_gb:.1f} GB free ({free_percent:.1f}%)."
        return "ok", f"Disk {mount_point} has {free_gb:.1f} GB free ({free_percent:.1f}%)."

    def _select_target_usage(self, report: StorageReport, category_id: str | None) -> StoragePathUsage | None:
        """Pick the category path if possible, otherwise the download directory."""
        if category_id:
            for path_usage in report.paths:
                if path_usage.category_id == category_id and path_usage.purpose == "category_library":
                    return path_usage
        for path_usage in report.paths:
            if path_usage.purpose == "download_dir":
                return path_usage
        return report.paths[0] if report.paths else None

    def _existing_anchor(self, path: Path) -> Path:
        """Return the closest existing ancestor for non-created category paths."""
        current = path
        while not current.exists() and current.parent != current:
            current = current.parent
        return current if current.exists() else Path.cwd().anchor and Path(Path.cwd().anchor) or Path.cwd()

    def _volume_identity(self, anchor: Path) -> tuple[str, str]:
        """Return a stable volume ID and mount point for a path."""
        try:
            anchor = anchor.resolve(strict=False)
            if os.name == "nt":
                drive = anchor.drive or str(anchor.anchor)
                return f"drive:{drive.lower()}", drive or str(anchor)
            current = anchor
            current_dev = current.stat().st_dev
            while current.parent != current:
                parent = current.parent
                try:
                    if parent.stat().st_dev != current_dev:
                        break
                except OSError:
                    break
                current = parent
            return f"dev:{current_dev}", str(current)
        except OSError:
            return f"path:{anchor}", str(anchor)
