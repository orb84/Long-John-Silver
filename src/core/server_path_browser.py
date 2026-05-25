"""Server-side directory browser for setup and settings UIs.

The browser exposes the server machine's filesystem through small, bounded
JSON payloads so a web UI running on another device can still choose library
folders without pretending the browser has local filesystem access.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class DirectoryBrowserEntry:
    """One child directory visible to the path browser UI."""

    name: str
    path: str
    is_symlink: bool = False
    can_read: bool = True
    can_write: bool = False

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation of the entry."""
        return {
            "name": self.name,
            "path": self.path,
            "is_symlink": self.is_symlink,
            "can_read": self.can_read,
            "can_write": self.can_write,
        }


@dataclass(frozen=True)
class DirectoryBrowserRoot:
    """A useful starting point shown in the path browser sidebar."""

    name: str
    path: str
    kind: str = "folder"
    exists: bool = True
    can_read: bool = True
    can_write: bool = False

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation of the root."""
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "exists": self.exists,
            "can_read": self.can_read,
            "can_write": self.can_write,
        }


@dataclass(frozen=True)
class DirectoryBrowserRootGroup:
    """A labelled group of server filesystem starting points."""

    label: str
    entries: tuple[DirectoryBrowserRoot, ...]

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation of the group."""
        return {
            "label": self.label,
            "entries": [entry.to_dict() for entry in self.entries],
        }


class ServerPathBrowser:
    """Browse and create directories on the LJS server host.

    The frontend may be served to a phone, tablet, or another computer, so the
    browser cannot use a native file picker. This class deliberately lists only
    directories, bounds the result size, and keeps filesystem work synchronous
    so callers can run it in a worker thread.
    """

    _INVALID_FOLDER_CHARS = re.compile(r"[\\/\0]")
    _LINUX_MEDIA_ROOTS = (Path("/media"), Path("/mnt"), Path("/run/media"))
    _LINUX_USER_MOUNT_DIR_NAMES = {
        "mounted",
        "mounts",
        "mount",
        "drives",
        "disks",
        "volumes",
    }
    _LINUX_STORAGE_FSTYPES = {
        "apfs",
        "btrfs",
        "cifs",
        "exfat",
        "ext2",
        "ext3",
        "ext4",
        "f2fs",
        "fuseblk",
        "hfsplus",
        "nfs",
        "nfs4",
        "ntfs",
        "smb3",
        "vfat",
        "xfs",
        "zfs",
    }
    _LINUX_PSEUDO_PREFIXES = (
        "/boot",
        "/dev",
        "/proc",
        "/run/credentials",
        "/run/lock",
        "/run/user",
        "/snap",
        "/sys",
        "/tmp",
        "/var/lib/docker",
        "/var/lib/containers",
    )

    def __init__(self, *, max_entries: int = 500, seed_paths: Iterable[str | Path] | None = None) -> None:
        """Create a browser with optional paths to expose as quick roots."""
        self._max_entries = max(25, int(max_entries))
        self._seed_paths = list(seed_paths or [])

    def browse(self, path: str | None = None) -> dict:
        """List child directories for ``path`` or return useful roots.

        Args:
            path: Server-side directory path from the UI. ``None`` and empty
                strings start from the user's home directory when available.

        Returns:
            A dictionary designed for direct FastAPI JSON responses.
        """
        target = self._resolve_target(path)
        root_groups = self._root_groups()
        roots = [root.to_dict() for group in root_groups for root in group.entries]
        if target is None:
            return {
                "ok": True,
                "path": "",
                "display_path": "Choose a starting point",
                "parent": None,
                "exists": False,
                "can_write": False,
                "entries": [],
                "roots": roots,
                "root_groups": [group.to_dict() for group in root_groups],
                "truncated": False,
                "message": "Choose a server folder or mounted drive to browse.",
            }

        if not target.exists():
            return {
                "ok": False,
                "path": str(target),
                "display_path": str(target),
                "parent": str(target.parent) if target.parent != target else None,
                "exists": False,
                "can_write": False,
                "entries": [],
                "roots": roots,
                "root_groups": [group.to_dict() for group in root_groups],
                "truncated": False,
                "message": "Folder does not exist on the server.",
            }
        if not target.is_dir():
            return {
                "ok": False,
                "path": str(target),
                "display_path": str(target),
                "parent": str(target.parent) if target.parent != target else None,
                "exists": True,
                "can_write": False,
                "entries": [],
                "roots": roots,
                "root_groups": [group.to_dict() for group in root_groups],
                "truncated": False,
                "message": "Selected path is not a folder.",
            }

        entries, truncated, error = self._entries_for(target)
        return {
            "ok": error is None,
            "path": str(target),
            "display_path": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "exists": True,
            "can_write": os.access(target, os.W_OK),
            "entries": [entry.to_dict() for entry in entries],
            "roots": roots,
            "root_groups": [group.to_dict() for group in root_groups],
            "truncated": truncated,
            "message": error,
        }

    def create_directory(self, parent: str | None, name: str) -> dict:
        """Create a child directory and return the browser payload for it."""
        clean_name = (name or "").strip()
        if not clean_name:
            return {"ok": False, "message": "Folder name is required."}
        if clean_name in {".", ".."} or self._INVALID_FOLDER_CHARS.search(clean_name):
            return {"ok": False, "message": "Folder name cannot contain path separators."}
        parent_path = self._resolve_target(parent)
        if parent_path is None:
            return {"ok": False, "message": "Parent folder is required."}
        if not parent_path.exists() or not parent_path.is_dir():
            return {"ok": False, "message": "Parent folder does not exist on the server."}
        child = parent_path / clean_name
        try:
            child.mkdir(parents=False, exist_ok=True)
        except Exception as exc:  # pragma: no cover - OS/permission dependent
            return {"ok": False, "message": f"Could not create folder: {exc}"}
        return self.browse(str(child))

    def _resolve_target(self, path: str | None) -> Path | None:
        raw = (path or "").strip()
        if not raw:
            home = Path.home()
            return home if str(home) else None
        try:
            return Path(raw).expanduser().resolve(strict=False)
        except Exception:
            return Path(raw).expanduser().absolute()

    def _entries_for(self, target: Path) -> tuple[list[DirectoryBrowserEntry], bool, str | None]:
        entries: list[DirectoryBrowserEntry] = []
        try:
            children = list(target.iterdir())
        except Exception as exc:
            return [], False, f"Could not read folder: {exc}"

        for child in children:
            try:
                if not child.is_dir():
                    continue
                entries.append(DirectoryBrowserEntry(
                    name=child.name,
                    path=str(child),
                    is_symlink=child.is_symlink(),
                    can_read=os.access(child, os.R_OK),
                    can_write=os.access(child, os.W_OK),
                ))
            except OSError:
                continue
        entries.sort(key=lambda e: e.name.lower())
        truncated = len(entries) > self._max_entries
        return entries[:self._max_entries], truncated, None

    def _root_groups(self) -> list[DirectoryBrowserRootGroup]:
        groups: list[DirectoryBrowserRootGroup] = []

        home = self._root_payload(Path.home(), "Home", "home")
        if home:
            groups.append(DirectoryBrowserRootGroup("Places", (home,)))

        configured = tuple(self._configured_root_payloads())
        if configured:
            groups.append(DirectoryBrowserRootGroup("Configured paths", configured))

        drives = tuple(self._drive_root_payloads())
        if drives:
            groups.append(DirectoryBrowserRootGroup("Drives and mounted media", drives))

        computer = tuple(root for root in self._computer_roots() if root is not None)
        if computer:
            groups.append(DirectoryBrowserRootGroup("Computer", computer))
        return groups

    def _configured_root_payloads(self) -> list[DirectoryBrowserRoot]:
        roots: list[DirectoryBrowserRoot] = []
        seen: set[str] = set()
        for seed in self._seed_paths:
            self._add_unique_root(roots, seen, seed, name=None, kind="configured")
            parent = self._seed_parent(seed)
            if parent is not None:
                self._add_unique_root(roots, seen, parent, name=None, kind="folder")
        return roots

    @staticmethod
    def _seed_parent(seed: str | Path) -> Path | None:
        """Return the parent directory for a configured seed path, if parseable."""
        try:
            return Path(seed).expanduser().parent
        except (OSError, TypeError, ValueError):
            return None

    def _drive_root_payloads(self) -> list[DirectoryBrowserRoot]:
        if sys.platform.startswith("win"):
            return self._windows_drive_roots()
        if sys.platform == "darwin":
            return self._mac_volume_roots()
        return self._linux_drive_roots()

    def _windows_drive_roots(self) -> list[DirectoryBrowserRoot]:
        roots: list[DirectoryBrowserRoot] = []
        seen: set[str] = set()
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            self._add_unique_root(roots, seen, drive, name=f"Drive {letter}:", kind="drive")
        return roots

    def _mac_volume_roots(self) -> list[DirectoryBrowserRoot]:
        roots: list[DirectoryBrowserRoot] = []
        seen: set[str] = set()
        volumes = Path("/Volumes")
        if volumes.exists():
            self._add_unique_root(roots, seen, volumes, name="Volumes", kind="mounts")
            for volume in self._safe_child_dirs(volumes):
                self._add_unique_root(roots, seen, volume, name=volume.name, kind="drive")
        return roots

    def _linux_drive_roots(self) -> list[DirectoryBrowserRoot]:
        roots: list[DirectoryBrowserRoot] = []
        seen: set[str] = set()
        for media_root in self._LINUX_MEDIA_ROOTS:
            if not media_root.exists():
                continue
            self._add_unique_root(roots, seen, media_root, name=str(media_root), kind="mounts")
            for mount in self._discover_media_children(media_root):
                self._add_unique_root(roots, seen, mount, name=mount.name, kind="drive")

        for mount in self._read_linux_mounts():
            self._add_unique_root(roots, seen, mount, name=mount.name or str(mount), kind="drive")

        for container, drive in self._configured_linux_mount_roots():
            self._add_unique_root(roots, seen, container, name=container.name, kind="mounts")
            self._add_unique_root(roots, seen, drive, name=drive.name or str(drive), kind="drive")

        gvfs = Path(f"/run/user/{os.getuid()}/gvfs") if hasattr(os, "getuid") else None
        if gvfs and gvfs.exists():
            self._add_unique_root(roots, seen, gvfs, name="Network mounts", kind="network")
            for mount in self._safe_child_dirs(gvfs):
                self._add_unique_root(roots, seen, mount, name=mount.name, kind="network")
        return roots

    def _computer_roots(self) -> list[DirectoryBrowserRoot | None]:
        if sys.platform.startswith("win"):
            return []
        return [self._root_payload(Path("/"), "Filesystem /", "computer")]

    def _discover_media_children(self, media_root: Path) -> list[Path]:
        mounts: list[Path] = []
        if media_root.name in {"media"} or str(media_root).endswith("/run/media"):
            for user_dir in self._safe_child_dirs(media_root):
                mounts.append(user_dir)
                mounts.extend(self._safe_child_dirs(user_dir))
        else:
            mounts.extend(self._safe_child_dirs(media_root))
        return mounts

    def _configured_linux_mount_roots(self) -> list[tuple[Path, Path]]:
        """Infer user-mounted drive roots from configured deep library paths.

        Linux users often mount external disks below a home folder such as
        ``~/Mounted/Argh/Media/Series`` instead of the distro defaults under
        ``/media`` or ``/mnt``.  When a configured path contains one of those
        mount-container names, expose the immediate child (``Argh`` here) as a
        drive shortcut so users do not have to manually climb the path tree.
        """
        candidates: list[tuple[Path, Path]] = []
        for seed in self._seed_paths:
            try:
                path = Path(seed).expanduser().resolve(strict=False)
            except Exception:
                continue
            parts = path.parts
            for index, part in enumerate(parts[:-1]):
                if part.lower() not in self._LINUX_USER_MOUNT_DIR_NAMES:
                    continue
                if index + 1 >= len(parts):
                    continue
                container = Path(*parts[: index + 1])
                drive = Path(*parts[: index + 2])
                if drive.exists() and drive.is_dir():
                    candidates.append((container, drive))
                break
        candidates.sort(key=lambda pair: str(pair[1]).lower())
        return candidates

    def _read_linux_mounts(self) -> list[Path]:
        mountinfo = Path("/proc/self/mountinfo")
        if not mountinfo.exists():
            return []
        mounts: list[Path] = []
        try:
            lines = mountinfo.read_text(errors="ignore").splitlines()
        except Exception:
            return []
        for line in lines:
            try:
                before, after = line.split(" - ", 1)
                fields = before.split()
                fs_fields = after.split()
                mount_point = fields[4].replace("\\040", " ")
                fs_type = fs_fields[0]
                source = fs_fields[1] if len(fs_fields) > 1 else ""
            except Exception:
                continue
            if self._is_useful_linux_mount(mount_point, fs_type, source):
                mounts.append(Path(mount_point))
        mounts.sort(key=lambda p: str(p).lower())
        return mounts

    def _is_useful_linux_mount(self, mount_point: str, fs_type: str, source: str) -> bool:
        if mount_point == "/":
            return False
        if mount_point.startswith(self._LINUX_PSEUDO_PREFIXES):
            return False
        if fs_type not in self._LINUX_STORAGE_FSTYPES and not source.startswith("/dev/"):
            return False
        path = Path(mount_point)
        return path.exists() and path.is_dir()

    def _safe_child_dirs(self, parent: Path) -> list[Path]:
        try:
            children = list(parent.iterdir())
        except Exception:
            return []
        dirs: list[Path] = []
        for child in children:
            try:
                if child.is_dir():
                    dirs.append(child)
            except OSError:
                continue
        dirs.sort(key=lambda p: p.name.lower())
        return dirs

    def _add_unique_root(
        self,
        roots: list[DirectoryBrowserRoot],
        seen: set[str],
        path: str | Path | None,
        *,
        name: str | None,
        kind: str,
    ) -> None:
        payload = self._root_payload(path, name, kind)
        if not payload or not payload.exists:
            return
        key = payload.path
        if key in seen:
            return
        seen.add(key)
        roots.append(payload)

    def _root_payload(self, path: str | Path | None, name: str | None, kind: str) -> DirectoryBrowserRoot | None:
        if path is None:
            return None
        try:
            resolved = Path(path).expanduser().resolve(strict=False)
        except Exception:
            resolved = Path(path).expanduser().absolute()
        if not str(resolved):
            return None
        label = name or self._friendly_root_name(resolved)
        return DirectoryBrowserRoot(
            name=label,
            path=str(resolved),
            kind=kind,
            exists=resolved.exists(),
            can_read=os.access(resolved, os.R_OK),
            can_write=os.access(resolved, os.W_OK),
        )

    @staticmethod
    def _friendly_root_name(path: Path) -> str:
        if path.name:
            return path.name
        return str(path)
