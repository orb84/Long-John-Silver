"""Regression coverage for suppressing repetitive UI polling access logs."""

from __future__ import annotations

import logging

from src.web.access_logs import QuietPollingAccessLogFilter, install_quiet_polling_access_log_filter


def _record(args: tuple) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=args,
        exc_info=None,
    )


def test_quiet_filter_suppresses_successful_polling_endpoints() -> None:
    access_filter = QuietPollingAccessLogFilter()

    assert not access_filter.filter(_record(("client", "GET", "/api/storage/status", "1.1", 200)))
    assert not access_filter.filter(_record(("client", "GET", "/api/system/logs?lines=160", "1.1", 200)))


def test_quiet_filter_keeps_failures_and_non_polling_requests() -> None:
    access_filter = QuietPollingAccessLogFilter()

    assert access_filter.filter(_record(("client", "GET", "/api/system/logs?lines=160", "1.1", 500)))
    assert access_filter.filter(_record(("client", "POST", "/api/storage/status", "1.1", 200)))
    assert access_filter.filter(_record(("client", "GET", "/api/categories/tv/items", "1.1", 200)))


def test_quiet_filter_parses_formatted_uvicorn_message() -> None:
    access_filter = QuietPollingAccessLogFilter()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='192.168.68.110:50646 - "GET /api/storage/status HTTP/1.1" 200 OK',
        args=(),
        exc_info=None,
    )

    assert not access_filter.filter(record)


def test_filter_installer_is_idempotent() -> None:
    logger = logging.getLogger("uvicorn.access.round68-test")
    logger.filters.clear()

    install_quiet_polling_access_log_filter(logger_name=logger.name)
    install_quiet_polling_access_log_filter(logger_name=logger.name)

    assert sum(isinstance(filter_, QuietPollingAccessLogFilter) for filter_ in logger.filters) == 1
    logger.filters.clear()
