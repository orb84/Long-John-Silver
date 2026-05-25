"""Round 50 regressions for web launch responsiveness and supervision."""

import asyncio
from pathlib import Path

import pytest

from src.core.models import TaskCriticality
from src.core import task_supervisor as supervisor_module
from src.core.task_supervisor import TaskSupervisor


def test_web_server_is_bound_before_startup_background_jobs():
    """The UI/API must be reachable before scans or metadata jobs can monopolize startup."""
    source = Path("main.py").read_text(encoding="utf-8")

    web_config = source.index("uvicorn.Config(app, host=web_host, port=port")
    web_ready = source.index("await _wait_for_web_server_ready(web_host, port, web_task)")
    scheduler_initialize = source.index("await scheduler.initialize()")
    deferred_jobs = source.index('"deferred_startup_jobs"')
    comms_bridges = source.index('spawn_one_shot("start_comms_bridges"')

    assert web_config < web_ready
    assert web_ready < scheduler_initialize
    assert web_ready < deferred_jobs
    assert web_ready < comms_bridges
    assert 'spawn_one_shot("initial_library_scan"' not in source
    assert 'spawn_one_shot("suggestion_compilation"' not in source


@pytest.mark.asyncio
async def test_critical_task_regular_exception_is_restartable(monkeypatch):
    """Regular Exceptions must not be swallowed by a broad BaseException handler."""
    monkeypatch.setattr(supervisor_module, "RESTART_BACKOFF_BASE_SECONDS", 0)
    supervisor = TaskSupervisor()
    calls = 0

    async def crash_once_then_wait():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        await asyncio.sleep(60)

    supervisor.spawn_restartable(
        "web_server",
        lambda: crash_once_then_wait(),
        TaskCriticality.CRITICAL,
    )

    for _ in range(20):
        if calls >= 2:
            break
        await asyncio.sleep(0.01)

    try:
        assert calls >= 2
        assert supervisor._restart_counts["web_server"] >= 1
    finally:
        await supervisor.shutdown()
