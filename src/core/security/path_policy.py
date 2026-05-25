"""
Filesystem path safety policy for LJS.

The LLM, user input, downloaded filenames, metadata, and persisted paths are all
untrusted. This module resolves every path through explicit category or app
roots before a filesystem mutation can execute.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Iterable

from loguru import logger

from src.core.models import SafeFileOperation, SafePathDecision, SecurityConfig
from src.core.security.audit import SecurityAuditLogger


class SecurityPolicyError(RuntimeError):
    """Raised when a filesystem operation violates the configured safety policy."""


class SafePathResolver:
    """Resolves and mutates paths only inside explicit allowed roots.

    The resolver deliberately returns or accepts :class:`pathlib.Path` objects,
    not shell strings. Callers cannot use it to construct arbitrary shell
    commands; it is only for scoped filesystem operations.
    """

    def __init__(
        self,
        allowed_roots: Iterable[Path | str],
        category_id: str | None = None,
        config: SecurityConfig | None = None,
        audit_logger: SecurityAuditLogger | None = None,
    ) -> None:
        """Initialize the resolver with category or application roots.

        Args:
            allowed_roots: Directories this resolver may read or mutate.
            category_id: Optional category that owns these roots.
            config: Security behavior settings.
            audit_logger: Optional audit sink.
        """
        self._category_id = category_id
        self._config = config or SecurityConfig()
        self._audit = audit_logger or SecurityAuditLogger(self._config.audit_log_path)
        self._allowed_roots = self._normalize_roots(allowed_roots)
        if not self._allowed_roots:
            raise SecurityPolicyError("At least one allowed root is required")

    @classmethod
    def for_category(
        cls,
        category: object,
        settings: object,
        extra_roots: Iterable[Path | str] | None = None,
    ) -> "SafePathResolver":
        """Create a resolver scoped to a category's library and download roots.

        Args:
            category: MediaCategory-like object exposing ``category_id`` and ``get_root_path``.
            settings: Settings-like object with download and security fields.
            extra_roots: Additional trusted roots, typically staging directories.

        Returns:
            Resolver scoped to the category roots.
        """
        roots: list[Path | str] = []
        if hasattr(category, "get_root_path"):
            roots.append(category.get_root_path(settings))
        download_dir = getattr(settings, "download_dir", "./downloads")
        category_id = getattr(category, "category_id", None)
        roots.append(Path(download_dir) / str(category_id or ""))
        roots.append(download_dir)
        roots.extend(extra_roots or [])
        return cls(
            allowed_roots=roots,
            category_id=category_id,
            config=getattr(settings, "security", SecurityConfig()),
        )

    @classmethod
    def for_application(
        cls,
        settings: object | None = None,
        extra_roots: Iterable[Path | str] | None = None,
    ) -> "SafePathResolver":
        """Create a resolver for app-controlled data, logs, downloads, and library roots."""
        roots: list[Path | str] = ["./data", "./logs", "./cache", "./config"]
        if settings is not None:
            roots.extend([getattr(settings, "download_dir", "./downloads"), getattr(settings, "library_root", "./library")])
        roots.extend(extra_roots or [])
        return cls(allowed_roots=roots, category_id=None, config=getattr(settings, "security", SecurityConfig()))

    def resolve(self, requested_path: Path | str, purpose: str = "generic", must_exist: bool = False) -> SafePathDecision:
        """Resolve a requested path and report whether it is inside an allowed root.

        Args:
            requested_path: User, agent, database, or metadata supplied path.
            purpose: Human-readable reason for the resolution.
            must_exist: Whether the path must already exist.

        Returns:
            A decision object explaining allow/block status.
        """
        raw_path = str(requested_path)
        roots = [str(root) for root in self._allowed_roots]
        if "\x00" in raw_path:
            return self._decision(False, raw_path, "", roots, purpose, "Path contains a null byte")

        candidate = Path(requested_path).expanduser()
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            return self._decision(False, raw_path, "", roots, purpose, f"Path could not be resolved: {exc}")

        if must_exist and not resolved.exists():
            return self._decision(False, raw_path, str(resolved), roots, purpose, "Path does not exist")

        if not self._is_inside_allowed_root(resolved):
            return self._decision(False, raw_path, str(resolved), roots, purpose, "Path is outside the allowed roots")

        return self._decision(True, raw_path, str(resolved), roots, purpose, None)

    def require(self, requested_path: Path | str, purpose: str = "generic", must_exist: bool = False) -> Path:
        """Return a resolved safe path or raise a security policy error."""
        decision = self.resolve(requested_path, purpose=purpose, must_exist=must_exist)
        if not decision.ok:
            self._audit.record(
                action_name=purpose,
                operation="resolve_path",
                status="blocked",
                risk_level="write",
                category_id=self._category_id,
                paths=[decision.resolved_path or decision.requested_path],
                reason=decision.reason,
            )
            raise SecurityPolicyError(decision.reason or "Unsafe path")
        return Path(decision.resolved_path)

    def ensure_destination(self, requested_path: Path | str, purpose: str, allow_overwrite: bool = False) -> Path:
        """Validate a destination path and its parent before creating or moving to it."""
        target = self.require(requested_path, purpose=purpose, must_exist=False)
        parent = self.require(target.parent, purpose=f"{purpose}:parent", must_exist=False)
        if target.exists() and not allow_overwrite:
            raise SecurityPolicyError(f"Destination already exists: {target}")
        if not self._is_inside_allowed_root(parent):
            raise SecurityPolicyError(f"Destination parent outside allowed roots: {parent}")
        return target

    def safe_mkdir(self, requested_path: Path | str, purpose: str = "mkdir") -> Path:
        """Create a directory only after destination path validation."""
        target = self.ensure_destination(requested_path, purpose=purpose, allow_overwrite=True)
        target.mkdir(parents=True, exist_ok=True)
        self._audit.record(purpose, "mkdir", "success", "write", self._category_id, [str(target)])
        return target

    def safe_move(self, source: Path | str, target: Path | str, purpose: str = "move") -> SafeFileOperation:
        """Move a file or directory within the resolver's allowed roots."""
        safe_source = self.require(source, purpose=f"{purpose}:source", must_exist=True)
        safe_target = self.ensure_destination(target, purpose=f"{purpose}:target")
        safe_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(safe_source), str(safe_target))
        operation = self._operation("move", safe_source, safe_target, destructive=False, allowed=True)
        self._audit.record(purpose, "move", "success", "write", self._category_id, [str(safe_source), str(safe_target)])
        return operation

    def safe_rename(self, source: Path | str, target: Path | str, purpose: str = "rename") -> SafeFileOperation:
        """Rename a file within the resolver's allowed roots."""
        safe_source = self.require(source, purpose=f"{purpose}:source", must_exist=True)
        safe_target = self.ensure_destination(target, purpose=f"{purpose}:target")
        safe_target.parent.mkdir(parents=True, exist_ok=True)
        safe_source.rename(safe_target)
        operation = self._operation("rename", safe_source, safe_target, destructive=False, allowed=True)
        self._audit.record(purpose, "rename", "success", "write", self._category_id, [str(safe_source), str(safe_target)])
        return operation

    def safe_copy(self, source: Path | str, target: Path | str, purpose: str = "copy") -> SafeFileOperation:
        """Copy a file within allowed roots using metadata-preserving copy2."""
        safe_source = self.require(source, purpose=f"{purpose}:source", must_exist=True)
        safe_target = self.ensure_destination(target, purpose=f"{purpose}:target")
        safe_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(safe_source), str(safe_target))
        operation = self._operation("copy", safe_source, safe_target, destructive=False, allowed=True)
        self._audit.record(purpose, "copy", "success", "write", self._category_id, [str(safe_source), str(safe_target)])
        return operation

    def safe_hardlink(self, source: Path | str, target: Path | str, purpose: str = "hardlink") -> SafeFileOperation:
        """Create a hardlink only when both source and destination are scoped."""
        safe_source = self.require(source, purpose=f"{purpose}:source", must_exist=True)
        safe_target = self.ensure_destination(target, purpose=f"{purpose}:target")
        safe_target.parent.mkdir(parents=True, exist_ok=True)
        os.link(str(safe_source), str(safe_target))
        operation = self._operation("hardlink", safe_source, safe_target, destructive=False, allowed=True)
        self._audit.record(purpose, "hardlink", "success", "write", self._category_id, [str(safe_source), str(safe_target)])
        return operation

    def safe_unlink(self, path: Path | str, purpose: str = "unlink", move_to_trash: bool | None = None) -> SafeFileOperation:
        """Delete a file safely, moving to trash by default when configured."""
        safe_path = self.require(path, purpose=f"{purpose}:path", must_exist=True)
        if safe_path.is_dir():
            raise SecurityPolicyError(f"Refusing to unlink a directory: {safe_path}")
        use_trash = self._config.use_trash_for_deletes if move_to_trash is None else move_to_trash
        if use_trash:
            trash_target = self._trash_target(safe_path)
            trash_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(safe_path), str(trash_target))
            operation = SafeFileOperation(
                operation="trash_file",
                category_id=self._category_id,
                source_path=str(safe_path),
                trash_path=str(trash_target),
                destructive=True,
                allowed=True,
            )
            self._audit.record(purpose, "trash_file", "success", "destructive", self._category_id, [str(safe_path), str(trash_target)])
            return operation
        safe_path.unlink()
        operation = self._operation("unlink", safe_path, None, destructive=True, allowed=True)
        self._audit.record(purpose, "unlink", "success", "destructive", self._category_id, [str(safe_path)])
        return operation

    def safe_rmtree(self, path: Path | str, purpose: str = "rmtree", move_to_trash: bool | None = None) -> SafeFileOperation:
        """Remove a directory tree safely, preferring quarantine/trash over permanent deletion."""
        safe_path = self.require(path, purpose=f"{purpose}:path", must_exist=True)
        if not safe_path.is_dir():
            raise SecurityPolicyError(f"Refusing to recursively delete a non-directory: {safe_path}")
        use_trash = self._config.use_trash_for_deletes if move_to_trash is None else move_to_trash
        if use_trash:
            trash_target = self._trash_target(safe_path)
            trash_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(safe_path), str(trash_target))
            operation = SafeFileOperation(
                operation="trash_tree",
                category_id=self._category_id,
                source_path=str(safe_path),
                trash_path=str(trash_target),
                destructive=True,
                allowed=True,
            )
            self._audit.record(purpose, "trash_tree", "success", "destructive", self._category_id, [str(safe_path), str(trash_target)])
            return operation
        shutil.rmtree(safe_path)
        operation = self._operation("rmtree", safe_path, None, destructive=True, allowed=True)
        self._audit.record(purpose, "rmtree", "success", "destructive", self._category_id, [str(safe_path)])
        return operation

    def _normalize_roots(self, roots: Iterable[Path | str]) -> list[Path]:
        """Resolve allowed roots and de-duplicate them."""
        normalized: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            if root is None or str(root).strip() == "":
                continue
            resolved = Path(root).expanduser().resolve(strict=False)
            key = str(resolved)
            if key not in seen:
                seen.add(key)
                normalized.append(resolved)
        return normalized

    def _is_inside_allowed_root(self, path: Path) -> bool:
        """Return whether a resolved path is inside any configured root."""
        for root in self._allowed_roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _trash_target(self, source: Path) -> Path:
        """Build a unique trash path inside the nearest allowed root."""
        root = self._nearest_root(source)
        trash_dir = root / self._config.trash_folder_name / (self._category_id or "app")
        return trash_dir / f"{source.name}.{uuid.uuid4().hex}"

    def _nearest_root(self, path: Path) -> Path:
        """Return the longest allowed root that contains the path."""
        candidates = [root for root in self._allowed_roots if self._contains(root, path)]
        if not candidates:
            raise SecurityPolicyError(f"No allowed root contains {path}")
        return max(candidates, key=lambda item: len(str(item)))

    def _contains(self, root: Path, path: Path) -> bool:
        """Return whether root contains path."""
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _decision(
        self,
        ok: bool,
        requested: str,
        resolved: str,
        roots: list[str],
        purpose: str,
        reason: str | None,
    ) -> SafePathDecision:
        """Build a path decision model."""
        return SafePathDecision(
            ok=ok,
            requested_path=requested,
            resolved_path=resolved,
            allowed_roots=roots,
            category_id=self._category_id,
            purpose=purpose,
            reason=reason,
        )

    def _operation(
        self,
        operation: str,
        source: Path | None,
        target: Path | None,
        destructive: bool,
        allowed: bool,
    ) -> SafeFileOperation:
        """Build a file operation model for callers and receipts."""
        return SafeFileOperation(
            operation=operation,
            category_id=self._category_id,
            source_path=str(source) if source else None,
            target_path=str(target) if target else None,
            destructive=destructive,
            allowed=allowed,
        )
