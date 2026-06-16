"""Runtime checks for configured writable storage paths.

LJS often points downloads or category libraries at removable drives.  A
missing external volume must be reported as unavailable instead of being
created accidentally under a mount parent such as ``/Volumes``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoragePathAvailability:
    """Writable-directory probe result for one configured storage path."""

    path: Path
    requested: Path
    exists: bool
    is_directory: bool
    writable: bool
    can_create: bool
    status: str
    reason: str
    existing_anchor: Path
    missing_root: Path | None = None

    @property
    def available_for_writes(self) -> bool:
        """Return whether the path can be used as a write target now."""
        return self.status == "ok" and (self.writable or self.can_create)


class StoragePathUnavailableError(RuntimeError):
    """Raised when a configured storage directory cannot be used safely."""

    def __init__(self, availability: StoragePathAvailability) -> None:
        self.availability = availability
        super().__init__(availability.reason)


class StoragePathGuard:
    """Validate and prepare configured storage directories without mount hacks.

    The guard deliberately distinguishes a missing leaf directory on a writable
    disk from a missing removable-drive mount.  The former may be created.  The
    latter must be surfaced to the UI/user because creating ``/Volumes/Drive``
    or ``/mnt/Drive`` as a normal folder would send payload bytes to the wrong
    disk and hide the real configuration problem.
    """

    @classmethod
    def inspect(cls, path: str | Path) -> StoragePathAvailability:
        """Return the current write availability for ``path`` without creating it."""
        requested = Path(path).expanduser()
        resolved = requested.resolve(strict=False)
        if requested.exists():
            is_directory = requested.is_dir()
            writable = bool(is_directory and os.access(requested, os.W_OK))
            status = "ok" if writable else "unavailable"
            reason = (
                f"Storage path is writable: {resolved}"
                if writable
                else cls._existing_path_reason(resolved, is_directory)
            )
            return StoragePathAvailability(
                path=resolved,
                requested=requested,
                exists=True,
                is_directory=is_directory,
                writable=writable,
                can_create=False,
                status=status,
                reason=reason,
                existing_anchor=requested,
            )

        anchor = cls._existing_anchor(requested)
        missing_root = cls._first_missing_component(anchor, requested)
        synthetic_mount = cls._synthetic_platform_mount_root(requested)
        if synthetic_mount is not None:
            anchor = synthetic_mount.parent
            missing_root = synthetic_mount
        if cls._looks_like_missing_mount(anchor, missing_root):
            return StoragePathAvailability(
                path=resolved,
                requested=requested,
                exists=False,
                is_directory=False,
                writable=False,
                can_create=False,
                status="unavailable",
                reason=(
                    f"Configured storage path appears to be on a missing or unplugged volume: "
                    f"{missing_root}. Reconnect the drive or change the configured path before starting downloads."
                ),
                existing_anchor=anchor,
                missing_root=missing_root,
            )

        if not os.access(anchor, os.W_OK):
            return StoragePathAvailability(
                path=resolved,
                requested=requested,
                exists=False,
                is_directory=False,
                writable=False,
                can_create=False,
                status="unavailable",
                reason=(
                    f"Configured storage path cannot be created because the nearest existing parent "
                    f"is not writable: {anchor}. Target: {resolved}"
                ),
                existing_anchor=anchor,
                missing_root=missing_root,
            )

        return StoragePathAvailability(
            path=resolved,
            requested=requested,
            exists=False,
            is_directory=False,
            writable=False,
            can_create=True,
            status="ok",
            reason=f"Storage path can be created on the current volume: {resolved}",
            existing_anchor=anchor,
            missing_root=missing_root,
        )

    @classmethod
    def ensure_directory(cls, path: str | Path) -> Path:
        """Create ``path`` only when it is safe, otherwise raise a typed error."""
        availability = cls.inspect(path)
        if not availability.available_for_writes:
            raise StoragePathUnavailableError(availability)
        try:
            availability.path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            retry = cls.inspect(path)
            reason = f"{retry.reason}; mkdir failed with {exc.__class__.__name__}: {exc}"
            raise StoragePathUnavailableError(
                StoragePathAvailability(
                    path=retry.path,
                    requested=retry.requested,
                    exists=retry.exists,
                    is_directory=retry.is_directory,
                    writable=False,
                    can_create=False,
                    status="unavailable",
                    reason=reason,
                    existing_anchor=retry.existing_anchor,
                    missing_root=retry.missing_root,
                )
            ) from exc
        return availability.path

    @classmethod
    def try_prepare_directory(cls, path: str | Path) -> StoragePathAvailability:
        """Best-effort startup preparation that never raises for missing media drives."""
        availability = cls.inspect(path)
        if not availability.available_for_writes:
            return availability
        try:
            availability.path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            retry = cls.inspect(path)
            return StoragePathAvailability(
                path=retry.path,
                requested=retry.requested,
                exists=retry.exists,
                is_directory=retry.is_directory,
                writable=False,
                can_create=False,
                status="unavailable",
                reason=f"{retry.reason}; mkdir failed with {exc.__class__.__name__}: {exc}",
                existing_anchor=retry.existing_anchor,
                missing_root=retry.missing_root,
            )
        return cls.inspect(path)

    @staticmethod
    def _existing_path_reason(path: Path, is_directory: bool) -> str:
        """Return a human-readable reason for an unusable existing path."""
        if not is_directory:
            return f"Configured storage path exists but is not a directory: {path}"
        return f"Configured storage path is not writable by the current process: {path}"

    @classmethod
    def _existing_anchor(cls, path: Path) -> Path:
        """Return the nearest existing ancestor for ``path``."""
        current = path
        while not current.exists() and current.parent != current:
            current = current.parent
        if current.exists():
            return current
        return Path(current.anchor or os.sep)

    @staticmethod
    def _first_missing_component(anchor: Path, path: Path) -> Path | None:
        """Return the first missing descendant below ``anchor``."""
        try:
            relative_parts = path.relative_to(anchor).parts
        except ValueError:
            relative_parts = path.parts[len(anchor.parts):]
        if not relative_parts:
            return None
        return anchor / relative_parts[0]

    @staticmethod
    def _synthetic_platform_mount_root(path: Path) -> Path | None:
        """Return a platform mount root even when the mount parent is absent in tests."""
        parts = path.parts
        if len(parts) >= 3 and parts[0] == os.sep and parts[1] == "Volumes":
            volume_root = Path(os.sep) / "Volumes" / parts[2]
            if not volume_root.exists():
                return volume_root
        if len(parts) >= 4 and parts[0] == os.sep and parts[1] in {"mnt", "media"}:
            mount_root = Path(os.sep) / parts[1] / parts[2]
            if not mount_root.exists():
                return mount_root
        if len(parts) >= 5 and parts[0] == os.sep and parts[1] == "run" and parts[2] == "media":
            mount_root = Path(os.sep) / "run" / "media" / parts[3] / parts[4]
            if not mount_root.exists():
                return mount_root
        return None

    @classmethod
    def _looks_like_missing_mount(cls, anchor: Path, missing_root: Path | None) -> bool:
        """Return whether creating the first missing component would fake a mount."""
        if missing_root is None:
            return False
        anchor_name = anchor.name
        if anchor_name in {"Volumes", "mnt", "media"}:
            return True
        # Linux removable media is commonly /run/media/<user>/<drive>.  Creating
        # the user component can be legitimate, but creating the final drive
        # component under an existing user mount parent is not.
        if anchor.parent.name == "media" and anchor.parent.parent.name == "run":
            return True
        return False
