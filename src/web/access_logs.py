"""Helpers for keeping web-server access logs useful instead of noisy.

The web UI deliberately polls a few lightweight endpoints for live status and
log tails. Uvicorn logs every HTTP request by default, which means those polling
requests can bury the application warnings and errors users actually need to
see.  This module installs a narrow standard-logging filter on ``uvicorn.access``
so repetitive status/log-tail reads are quiet while normal application logging,
errors, and non-polling access lines remain available.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from urllib.parse import urlsplit

# Endpoints that are expected to be called frequently by the browser or startup
# probes. They are intentionally cheap and successful calls are not actionable.
DEFAULT_QUIET_ACCESS_PATHS: frozenset[str] = frozenset(
    {
        "/api/live",
        "/api/health",
        "/api/storage/status",
        "/api/system/logs",
        "/api/suggestions",
        "/api/downloads",
        "/api/categories",
    }
)

# We only suppress safe polling-style methods. If a mutating request somehow
# targets one of the paths above, keep the access line because it may indicate a
# client bug or suspicious traffic.
DEFAULT_QUIET_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})


class QuietPollingAccessLogFilter(logging.Filter):
    """Suppress successful, high-frequency polling endpoint access lines.

    Uvicorn's access records normally carry structured ``record.args`` shaped
    like ``(client_addr, method, path, http_version, status_code)``. The filter
    also falls back to parsing the final formatted message so tests and future
    uvicorn format tweaks degrade safely instead of leaking spam again.
    """

    def __init__(
        self,
        quiet_paths: Iterable[str] | None = None,
        quiet_methods: Iterable[str] | None = None,
    ) -> None:
        super().__init__()
        self.quiet_paths = {self._normalize_path(path) for path in (quiet_paths or DEFAULT_QUIET_ACCESS_PATHS)}
        self.quiet_methods = {method.upper() for method in (quiet_methods or DEFAULT_QUIET_METHODS)}

    def filter(self, record: logging.LogRecord) -> bool:
        """Return ``False`` for known polling access records."""
        method, path, status_code = self._extract_access_fields(record)
        if not method or not path:
            return True
        if method.upper() not in self.quiet_methods:
            return True
        if self._normalize_path(path) not in self.quiet_paths:
            return True
        # Keep failed poll requests visible. A repeated 4xx/5xx on a status/log
        # endpoint is real signal; only the boring 2xx/3xx heartbeat lines go.
        if status_code is not None and status_code >= 400:
            return True
        return False

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Strip query strings and fragments before comparing endpoints."""
        if not path:
            return ""
        parsed = urlsplit(path)
        return parsed.path or path.split("?", 1)[0].split("#", 1)[0]

    @staticmethod
    def _extract_access_fields(record: logging.LogRecord) -> tuple[str | None, str | None, int | None]:
        """Extract method/path/status from uvicorn records or formatted text."""
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            method = str(args[1]) if args[1] is not None else None
            path = str(args[2]) if args[2] is not None else None
            try:
                status_code = int(args[4]) if args[4] is not None else None
            except (TypeError, ValueError):
                status_code = None
            return method, path, status_code

        message = record.getMessage()
        # Example: 192.168.1.2:50646 - "GET /api/storage/status HTTP/1.1" 200 OK
        quote_start = message.find('"')
        quote_end = message.find('"', quote_start + 1)
        if quote_start == -1 or quote_end == -1:
            return None, None, None
        request_line = message[quote_start + 1 : quote_end]
        parts = request_line.split()
        method = parts[0] if len(parts) >= 2 else None
        path = parts[1] if len(parts) >= 2 else None
        status_code = None
        trailing = message[quote_end + 1 :].strip().split()
        if trailing:
            try:
                status_code = int(trailing[0])
            except ValueError:
                status_code = None
        return method, path, status_code


def install_quiet_polling_access_log_filter(
    quiet_paths: Iterable[str] | None = None,
    logger_name: str = "uvicorn.access",
) -> None:
    """Install the quiet polling filter once on uvicorn's access logger."""
    access_logger = logging.getLogger(logger_name)
    if any(isinstance(existing, QuietPollingAccessLogFilter) for existing in access_logger.filters):
        return
    access_logger.addFilter(QuietPollingAccessLogFilter(quiet_paths=quiet_paths))
