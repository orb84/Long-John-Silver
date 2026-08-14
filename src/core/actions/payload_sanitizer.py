"""Sanitize and bound payloads before durable operational logging."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class OperationalPayloadSanitizer:
    """Redact credentials and trackers while preserving useful diagnostic structure."""

    _REDACTED = "[REDACTED]"
    _SENSITIVE_KEY_PARTS = (
        "password", "passwd", "api_key", "apikey", "secret", "token",
        "authorization", "cookie", "passkey", "private_key", "credential",
    )
    _MAX_DEPTH = 8
    _MAX_ITEMS = 100
    _MAX_STRING = 4096
    _MAX_JSON_BYTES = 64 * 1024
    _MAGNET_PATTERN = re.compile(r"magnet:\?[^\s\"']+", re.IGNORECASE)
    _URL_PATTERN = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
    _KEY_VALUE_SECRET = re.compile(
        r"(?i)(api[_-]?key|token|secret|password|passkey|authorization)=([^&\s]+)"
    )

    def sanitize(self, value: Any) -> Any:
        """Return a recursively sanitized, size-bounded JSON-compatible value."""
        cleaned = self._sanitize(value, depth=0, key_hint="")
        encoded = json.dumps(cleaned, ensure_ascii=False, default=str, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= self._MAX_JSON_BYTES:
            return cleaned
        return {
            "_truncated": True,
            "_reason": "operational payload exceeded 65536 bytes",
            "preview": encoded[: self._MAX_STRING],
        }

    def sanitize_text(self, value: Any) -> str | None:
        """Redact secrets and URL query strings from an error or free-text field."""
        if value is None:
            return None
        return self._sanitize_string(str(value))

    def _sanitize(self, value: Any, *, depth: int, key_hint: str) -> Any:
        if depth > self._MAX_DEPTH:
            return "[TRUNCATED_DEPTH]"
        if self._is_sensitive_key(key_hint):
            return self._REDACTED
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self._sanitize_string(value)
        if isinstance(value, dict):
            output: dict[str, Any] = {}
            for index, (key, child) in enumerate(value.items()):
                if index >= self._MAX_ITEMS:
                    output["_truncated_items"] = len(value) - self._MAX_ITEMS
                    break
                key_text = str(key)
                output[key_text] = self._sanitize(child, depth=depth + 1, key_hint=key_text)
            return output
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            output = [self._sanitize(child, depth=depth + 1, key_hint=key_hint) for child in items[: self._MAX_ITEMS]]
            if len(items) > self._MAX_ITEMS:
                output.append({"_truncated_items": len(items) - self._MAX_ITEMS})
            return output
        if hasattr(value, "model_dump"):
            return self._sanitize(value.model_dump(), depth=depth + 1, key_hint=key_hint)
        return self._sanitize_string(str(value))

    def _sanitize_string(self, value: str) -> str:
        text = value[: self._MAX_STRING]
        text = self._MAGNET_PATTERN.sub(self._sanitize_magnet_match, text)
        text = self._URL_PATTERN.sub(self._sanitize_url_match, text)
        text = self._KEY_VALUE_SECRET.sub(lambda match: f"{match.group(1)}={self._REDACTED}", text)
        if len(value) > self._MAX_STRING:
            text += "…[TRUNCATED]"
        return text

    def _sanitize_magnet_match(self, match: re.Match[str]) -> str:
        magnet = match.group(0)
        xt_match = re.search(r"(?i)(?:\?|&)xt=urn:btih:([^&]+)", magnet)
        if xt_match:
            return f"magnet:?xt=urn:btih:{xt_match.group(1)[:80]}&tr={self._REDACTED}"
        return f"magnet:?{self._REDACTED}"

    def _sanitize_url_match(self, match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parts = urlsplit(raw)
            host = parts.hostname or ""
            port = f":{parts.port}" if parts.port else ""
            safe_netloc = host + port
            return urlunsplit((parts.scheme, safe_netloc, parts.path, "", ""))
        except ValueError:
            return self._REDACTED

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = str(key or "").lower().replace("-", "_")
        return any(part in normalized for part in self._SENSITIVE_KEY_PARTS)
