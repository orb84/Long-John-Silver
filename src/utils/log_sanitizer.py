"""Small helpers for keeping operational logs useful and non-sensitive."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_QUERY_KEYS = {
    "apikey", "api_key", "key", "token", "access_token", "refresh_token",
    "password", "pass", "secret", "auth", "authorization",
}

_SECRET_PATTERNS = [
    re.compile(r"(?i)(apikey|api_key|token|access_token|refresh_token|password|secret)=([^&\s]+)"),
    re.compile(r"(?i)(Authorization:\s*Bearer\s+)[A-Za-z0-9._\-]+"),
]


def redact_secrets(text: object) -> str:
    """Return ``text`` with obvious API keys/tokens redacted.

    This is deliberately conservative and side-effect free so it can be used
    both before writing logs and when serving existing logs back to the UI.
    """
    value = str(text or "")
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(lambda m: f"{m.group(1)}=<redacted>", value)
    return value


def redact_url(url: object) -> str:
    """Redact credential-bearing query parameters in a URL-like string."""
    raw = str(url or "")
    if not raw:
        return raw
    try:
        parts = urlsplit(raw)
        if not parts.query:
            return redact_secrets(raw)
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            query.append((key, "<redacted>" if key.lower() in _SECRET_QUERY_KEYS else value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))
    except Exception:
        return redact_secrets(raw)
