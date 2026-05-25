"""
Integration test harness for the LJS server.

Provides a :class:`ServerTestHarness` that connects to a running LJS
instance via WebSocket (chat/agent) and HTTP (REST API). Designed for
end-to-end verification of intent detection, download management,
scheduling, taste profiling, and suggestion workflows.
"""

from tests.harness.server_harness import ServerTestHarness  # noqa: F401
