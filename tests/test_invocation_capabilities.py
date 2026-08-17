"""Protocol-neutral invocation capability enforcement."""

from __future__ import annotations

import pytest

from src.ai.tool_registry import ToolRegistry
from src.ai.tool_context import ToolExecutionContextFactory
from src.core.models import InvocationCapability, InvocationContext, InvocationPrincipal


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read(self, **_: object) -> dict[str, object]:
        self.calls.append("read")
        return {"ok": True}

    async def write(self, **_: object) -> dict[str, object]:
        self.calls.append("write")
        return {"ok": True}


def _principal(*caps: InvocationCapability) -> InvocationPrincipal:
    return InvocationPrincipal(
        principal_id="external:test",
        client_id="test-client",
        source="mcp",
        capabilities=set(caps),
    )


def _context(principal: InvocationPrincipal, *, allow_actions: bool) -> object:
    return ToolExecutionContextFactory.create(
        invocation=InvocationContext(principal=principal, allow_actions=allow_actions),
        session_id="external-session",
        user_id=None,
        category_id=None,
        user_prompt="test",
    )


def _registry(recorder: _Recorder) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("web_search", "read", {}, recorder.read)
    registry.register("queue_download", "write", {}, recorder.write)
    registry.register("configure_category_property", "config", {}, recorder.write)
    return registry


@pytest.mark.asyncio
async def test_read_only_delegation_hides_and_blocks_write_tools() -> None:
    recorder = _Recorder()
    registry = _registry(recorder)
    context = _context(
        _principal(InvocationCapability.AGENT_DELEGATE, InvocationCapability.AGENT_READ),
        allow_actions=False,
    )

    visible = registry.filter_names_for_context(
        {"web_search", "queue_download", "configure_category_property"}, context
    )
    assert visible == {"web_search"}
    denied = await registry.execute("queue_download", {}, context=context)
    assert denied["ok"] is False
    assert denied["error_code"] in {"CAPABILITY_DENIED", "ACTIONS_DISABLED"}
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_admin_is_still_read_only_when_allow_actions_is_false() -> None:
    recorder = _Recorder()
    registry = _registry(recorder)
    context = _context(_principal(InvocationCapability.ADMIN), allow_actions=False)

    visible = registry.filter_names_for_context(
        {"web_search", "queue_download", "configure_category_property"}, context
    )
    assert visible == {"web_search"}
    denied = await registry.execute("queue_download", {}, context=context)
    assert denied["ok"] is False
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_write_capabilities_are_scoped_by_domain() -> None:
    recorder = _Recorder()
    registry = _registry(recorder)
    context = _context(
        _principal(
            InvocationCapability.AGENT_READ,
            InvocationCapability.DOWNLOADS_WRITE,
        ),
        allow_actions=True,
    )

    visible = registry.filter_names_for_context(
        {"web_search", "queue_download", "configure_category_property"}, context
    )
    assert visible == {"web_search", "queue_download"}
    allowed = await registry.execute("queue_download", {}, context=context)
    denied = await registry.execute("configure_category_property", {}, context=context)
    assert allowed["ok"] is True
    assert denied["error_code"] == "CAPABILITY_DENIED"
    assert recorder.calls == ["write"]


def test_trusted_first_party_context_preserves_existing_tool_surface() -> None:
    recorder = _Recorder()
    registry = _registry(recorder)
    context = ToolExecutionContextFactory.create(
        invocation=None,
        session_id="web_default",
        user_id="local-user",
        category_id=None,
        user_prompt="test",
    )

    names = {"web_search", "queue_download", "configure_category_property"}
    assert context.trusted is True
    assert registry.filter_names_for_context(names, context) == names
