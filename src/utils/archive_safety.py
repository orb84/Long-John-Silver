"""Safe archive extraction helpers.

Archive entry names are untrusted path strings.  Normalize both POSIX and
Windows separators before joining under the destination so extraction behaves the
same on Linux, macOS, and Windows and cannot write outside the install folder.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from pathlib import Path


class UnsafeArchivePath(ValueError):
    """Raised when an archive member would escape the destination."""


def _safe_member_parts(name: str) -> list[str]:
    raw = str(name or "")
    if "\x00" in raw:
        raise UnsafeArchivePath(f"Archive member contains a null byte: {name!r}")
    normalized = raw.replace("\\", "/")
    # Reject absolute POSIX paths and Windows drive/device-looking roots before
    # removing empty components.
    if normalized.startswith("/") or normalized.startswith("//"):
        raise UnsafeArchivePath(f"Archive member is absolute: {name!r}")
    first = normalized.split("/", 1)[0]
    if first.endswith(":") or ":" in first:
        raise UnsafeArchivePath(f"Archive member has a drive/device prefix: {name!r}")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts:
        raise UnsafeArchivePath(f"Archive member has no safe path components: {name!r}")
    if any(part == ".." for part in parts):
        raise UnsafeArchivePath(f"Archive member contains parent traversal: {name!r}")
    return parts


def safe_archive_target(dest: Path, member_name: str) -> Path:
    """Return the destination path for an archive member, or raise."""
    base = Path(dest).resolve(strict=False)
    target = base.joinpath(*_safe_member_parts(member_name)).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise UnsafeArchivePath(f"Archive member escapes destination: {member_name!r}") from exc
    return target


def safe_extract_zip(archive: Path, dest: Path) -> None:
    """Extract a zip archive without Zip Slip or separator-dependent paths."""
    base = Path(dest).resolve(strict=False)
    base.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(archive), "r") as zf:
        for member in zf.infolist():
            target = safe_archive_target(base, member.filename)
            is_dir = member.is_dir() or str(member.filename).replace("\\", "/").endswith("/")
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            mode = (member.external_attr >> 16) & 0o777
            if mode:
                try:
                    os.chmod(target, mode)
                except OSError:
                    pass


def safe_extract_tar(archive: Path, dest: Path) -> None:
    """Extract a tar archive without traversal, links, or platform surprises."""
    base = Path(dest).resolve(strict=False)
    base.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(archive), "r:gz") as tf:
        for member in tf.getmembers():
            target = safe_archive_target(base, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                # Do not materialize symlinks/hardlinks/devices from installer
                # archives; they can escape the destination or behave
                # differently across platforms.
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            try:
                os.chmod(target, member.mode & 0o777)
            except OSError:
                pass
