"""
API key store for the LLM Providers library.

Keys are persisted to a local JSON file with restricted file permissions.
On POSIX systems, the file is set to owner-only read/write (0o600) after
every write. Keys are stored in plaintext — anyone with filesystem access
can read them. For stronger protection, use OS keychain integration or
a secrets manager in a future phase.
"""

import json
import os
import stat
from pathlib import Path
from loguru import logger
from typing import Optional, Any
from datetime import datetime

from src.llm_providers.models import APIKeyEntry


class KeyStore:
    """Manages API keys for multiple providers with JSON persistence.

    Keys are stored in plaintext JSON. File permissions are restricted
    to owner-only on POSIX systems. The ``list_keys_masked()``
    method returns key entries with masked values suitable for API
    responses — never expose raw key values to the frontend.
    """

    def __init__(self, store_path: str = "data/api_keys.json"):
        self._path = Path(store_path)
        self._keys: dict[str, list[APIKeyEntry]] = {}
        self._load()

    def _load(self):
        """Load keys from the JSON store."""
        if not self._path.exists():
            self._keys = {}
            return
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            for provider_id, entries in data.items():
                self._keys[provider_id] = [
                    APIKeyEntry(**e) for e in entries
                ]
            logger.debug(f"Key store loaded: {sum(len(v) for v in self._keys.values())} keys")
        except Exception as e:
            logger.error(f"Failed to load key store: {e}")
            self._keys = {}

    def _save(self):
        """Persist keys to the JSON store and restrict file permissions."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {}
            for provider_id, entries in self._keys.items():
                data[provider_id] = [e.model_dump(mode="json") for e in entries]
            with open(self._path, "w") as f:
                json.dump(data, f, indent=2)
            # Restrict file to owner-only on POSIX systems
            self._restrict_permissions()
        except Exception as e:
            logger.error(f"Failed to save key store: {e}")

    def _restrict_permissions(self):
        """Set file permissions to owner-only read/write (0o600) on POSIX."""
        try:
            self._path.chmod(0o600)
            # Also restrict the parent directory to owner-only on POSIX
            parent = self._path.parent
            parent_stat = parent.stat()
            if parent_stat.st_mode & stat.S_IWOTH:
                # Parent is world-writable — log a warning
                logger.warning(
                    f"Key store parent directory {parent} is world-writable. "
                    f"Consider running: chmod o-w {parent}"
                )
        except OSError as e:
            # chmod fails on Windows or read-only filesystems
            logger.debug(f"Could not restrict key store file permissions: {e}")

    @staticmethod
    def mask_key(key: str) -> str:
        """Mask an API key, showing only first 4 and last 4 characters.

        Args:
            key: The raw API key string.

        Returns:
            Masked representation like ``sk-t****1234``.
            Keys shorter than 8 characters are fully masked.
        """
        if len(key) <= 8:
            return "****"
        return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"

    def add_key(self, provider_id: str, key: str, label: str = "default",
                set_active: bool = False) -> APIKeyEntry:
        """Add an API key for a provider.

        If this is the first key for the provider, it automatically becomes active.
        """
        import hashlib
        key_id = hashlib.md5(f"{provider_id}:{key}".encode()).hexdigest()[:10]

        entry = APIKeyEntry(
            id=key_id,
            provider_id=provider_id,
            key=key,
            label=label,
            is_active=set_active,
        )

        if provider_id not in self._keys:
            self._keys[provider_id] = []
            entry.is_active = True
        elif set_active:
            for existing in self._keys[provider_id]:
                existing.is_active = False

        self._keys[provider_id].append(entry)
        self._save()
        logger.info(f"Added key '{label}' for provider '{provider_id}'")
        return entry

    def remove_key(self, provider_id: str, key_id: str) -> None:
        """Remove a specific API key."""
        if provider_id in self._keys:
            self._keys[provider_id] = [
                k for k in self._keys[provider_id] if k.id != key_id
            ]
            if self._keys[provider_id] and not any(k.is_active for k in self._keys[provider_id]):
                self._keys[provider_id][0].is_active = True
            self._save()

    def get_active_key(self, provider_id: str) -> Optional[APIKeyEntry]:
        """Return the currently active key for a provider."""
        entries = self._keys.get(provider_id, [])
        for entry in entries:
            if entry.is_active:
                return entry
        return entries[0] if entries else None

    def set_active_key(self, provider_id: str, key_id: str) -> None:
        """Set a specific key as active for a provider."""
        for entry in self._keys.get(provider_id, []):
            entry.is_active = (entry.id == key_id)
        self._save()

    def list_keys(self, provider_id: str) -> list[APIKeyEntry]:
        """List all key entries for a provider.

        WARNING: Key values are NOT masked. Use ``list_keys_masked()``
        for API responses or any display to the user.
        """
        return list(self._keys.get(provider_id, []))

    def list_keys_masked(self, provider_id: str) -> list[dict[str, Any]]:
        """List all keys for a provider with values masked.

        Returns a list of dicts suitable for API responses. Each
        dict contains ``id``, ``label``, ``is_active``,
        ``created_at``, and ``key_preview`` (masked).

        Args:
            provider_id: The provider to list keys for.

        Returns:
            List of dicts with masked key values.
        """
        entries = self._keys.get(provider_id, [])
        return [
            {
                "id": entry.id,
                "label": entry.label,
                "is_active": entry.is_active,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "key_preview": self.mask_key(entry.key),
            }
            for entry in entries
        ]

    def get_key_value(self, provider_id: str, key_id: str) -> Optional[str]:
        """Get the actual key string for a specific key entry."""
        for entry in self._keys.get(provider_id, []):
            if entry.id == key_id:
                return entry.key
        return None

    def has_keys(self, provider_id: str) -> bool:
        """Check if a provider has any configured keys."""
        return bool(self._keys.get(provider_id, []))

    def storage_security_status(self) -> dict[str, Any]:
        """Return key store security status for diagnostics.

        Checks file permissions and whether the store directory
        is world-writable. Useful for admin UI and health checks.

        Returns:
            Dict with file path, permissions, world-writable status,
            and recommendations.
        """
        status: dict[str, Any] = {
            "store_path": str(self._path),
            "store_exists": self._path.exists(),
            "permissions_ok": False,
            "parent_world_writable": False,
            "recommendations": [],
        }

        if self._path.exists():
            try:
                mode = self._path.stat().st_mode
                status["permissions_octal"] = oct(stat.S_IMODE(mode))
                # Check if file is owner-only (0o600 or more restrictive)
                others_perms = mode & (stat.S_IROTH | stat.S_IWOTH)
                status["permissions_ok"] = others_perms == 0
            except OSError:
                status["permissions_octal"] = "unknown"

            try:
                parent = self._path.parent
                parent_mode = parent.stat().st_mode
                status["parent_world_writable"] = bool(parent_mode & stat.S_IWOTH)
                if status["parent_world_writable"]:
                    status["recommendations"].append(
                        f"Parent directory {parent} is world-writable. "
                        f"Run: chmod o-w {parent}"
                    )
            except OSError:
                status["parent_world_writable"] = "unknown"

        if not status.get("permissions_ok", False):
            # On Windows or read-only filesystems chmod may not work
            if os.name != "nt":
                status["recommendations"].append(
                    "Key store file permissions are not restricted to owner-only. "
                    "This may expose API keys to other users on the system."
                )

        status["key_count"] = sum(len(v) for v in self._keys.values())
        status["provider_count"] = len(self._keys)

        return status