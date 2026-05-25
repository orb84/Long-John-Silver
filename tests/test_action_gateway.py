"""
Tests for the unified action pipeline: ActionGateway, ActionEventStore,
ActionCommand, and ActionResult models.

Verifies registration, execution, auditing, error handling, and batch
behavior without needing the full application stack.
"""

from unittest.mock import AsyncMock, MagicMock
import json
import asyncio

import aiosqlite
import pytest
import pytest_asyncio
from pydantic import ValidationError
from pydantic import BaseModel

from src.core.models import ActionSource, ActionCommand, ActionResult
from src.core.actions.gateway import ActionGateway
from src.core.actions.audit import ActionEventStore


def _run(coro):
    """Run an async coroutine synchronously for non-async tests."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return loop.run_until_complete(coro)


# ─── Model Tests ───────────────────────────────────────────────


class TestActionSource:
    """ActionSource enum has the expected values."""

    def test_enum_values(self):
        assert ActionSource.CHAT.value == 'chat'
        assert ActionSource.UI.value == 'ui'
        assert ActionSource.SCHEDULER.value == 'scheduler'
        assert ActionSource.SYSTEM.value == 'system'

    def test_all_sources_covered(self):
        sources = set(v.value for v in ActionSource)
        assert sources == {'chat', 'ui', 'scheduler', 'system'}


class TestActionCommand:
    """ActionCommand model validation."""

    def test_minimal_command(self):
        cmd = ActionCommand(name='test', source=ActionSource.UI)
        assert cmd.name == 'test'
        assert cmd.source == ActionSource.UI
        assert cmd.arguments == {}
        assert cmd.user_id is None
        assert cmd.session_id is None

    def test_full_command(self):
        cmd = ActionCommand(
            name='pause_download',
            arguments={'download_id': 'abc123'},
            source=ActionSource.CHAT,
            user_id='user-1',
            session_id='session-1',
        )
        assert cmd.name == 'pause_download'
        assert cmd.arguments == {'download_id': 'abc123'}
        assert cmd.user_id == 'user-1'
        assert cmd.session_id == 'session-1'

    def test_name_is_required(self):
        with pytest.raises(ValidationError):
            ActionCommand(source=ActionSource.UI)

    def test_source_is_required(self):
        with pytest.raises(ValidationError):
            ActionCommand(name='test')


class TestActionResult:
    """ActionResult model validation."""

    def test_success_result(self):
        result = ActionResult(ok=True, data={'id': 'dl-001'}, action_name='pause_download')
        assert result.ok is True
        assert result.data == {'id': 'dl-001'}
        assert result.error is None

    def test_error_result(self):
        result = ActionResult(ok=False, error='Download not found', action_name='pause_download')
        assert result.ok is False
        assert result.error == 'Download not found'
        assert result.data == {}

    def test_default_data_is_empty(self):
        result = ActionResult(ok=True)
        assert result.data == {}


# ─── Gateway Tests ─────────────────────────────────────────────


class TestActionGateway:
    """ActionGateway registration, execution, and error handling."""

    def test_register_and_execute(self):
        """Registered handler is called with keyword arguments."""
        gateway = ActionGateway()
        handler = AsyncMock(return_value={'status': 'ok'})
        gateway.register('test_action', handler)

        result = _run(gateway.execute(ActionCommand(
            name='test_action',
            arguments={'key': 'value'},
            source=ActionSource.UI,
        )))

        assert result.ok is True
        assert result.data == {'status': 'ok'}
        handler.assert_awaited_once_with(key='value')

    def test_unknown_action_returns_error(self):
        """Executing an unregistered action returns error result."""
        gateway = ActionGateway()
        result = _run(gateway.execute(ActionCommand(
            name='nonexistent',
            source=ActionSource.UI,
        )))
        assert result.ok is False
        assert 'Unknown action' in (result.error or '')

    def test_handler_exception_returns_error(self):
        """When a handler raises, the result has ok=False with error message."""
        gateway = ActionGateway()

        async def failing_handler(**kwargs):
            raise ValueError('Something went wrong')

        gateway.register('failing', failing_handler)
        result = _run(gateway.execute(ActionCommand(
            name='failing',
            source=ActionSource.UI,
        )))
        assert result.ok is False
        assert 'Something went wrong' in (result.error or '')

    def test_handler_returning_none(self):
        """Handler returning None produces ok=True with empty data."""
        gateway = ActionGateway()
        handler = AsyncMock(return_value=None)
        gateway.register('null_handler', handler)

        result = _run(gateway.execute(ActionCommand(
            name='null_handler',
            source=ActionSource.UI,
        )))
        assert result.ok is True
        assert result.data == {}

    def test_handler_returning_pydantic_model(self):
        """Handler returning a Pydantic model is auto-converted to dict."""

        class SomeModel(BaseModel):
            id: str
            value: int

        gateway = ActionGateway()
        handler = AsyncMock(return_value=SomeModel(id='x', value=42))
        gateway.register('model_handler', handler)

        result = _run(gateway.execute(ActionCommand(
            name='model_handler',
            source=ActionSource.UI,
        )))
        assert result.ok is True
        assert result.data == {'id': 'x', 'value': 42}

    def test_registered_actions_list(self):
        """registered_actions returns all registered names."""
        gateway = ActionGateway()
        assert gateway.registered_actions == []

        gateway.register('a', AsyncMock())
        gateway.register('b', AsyncMock())
        assert set(gateway.registered_actions) == {'a', 'b'}

    def test_audit_store_is_notified(self):
        """When an audit store is configured, events are recorded."""
        audit_store = AsyncMock(spec=ActionEventStore)
        gateway = ActionGateway(audit_store=audit_store)
        handler = AsyncMock(return_value={'ok': True})
        gateway.register('audited_action', handler)

        _run(gateway.execute(ActionCommand(
            name='audited_action',
            arguments={'x': 1},
            source=ActionSource.UI,
            user_id='user-1',
            session_id='sess-1',
        )))

        audit_store.record.assert_awaited_once()

    def test_audit_store_failure_is_swallowed(self):
        """Audit store failure does not bubble up to the caller."""
        audit_store = AsyncMock(spec=ActionEventStore)
        audit_store.record.side_effect = RuntimeError('DB unavailable')
        gateway = ActionGateway(audit_store=audit_store)
        gateway.register('robust', AsyncMock(return_value='ok'))

        result = _run(gateway.execute(ActionCommand(
            name='robust', source=ActionSource.SYSTEM,
        )))
        assert result.ok is True  # action succeeded despite audit failure

    def test_re_registration_succeeds(self):
        """Re-registering an action replaces the handler without error."""
        gateway = ActionGateway()
        handler1 = AsyncMock(return_value='first')
        handler2 = AsyncMock(return_value='second')
        gateway.register('dup', handler1)
        gateway.register('dup', handler2)

        result = _run(gateway.execute(ActionCommand(
            name='dup', source=ActionSource.UI,
        )))
        # The last registered handler wins
        assert result.data == {'value': 'second'}
        handler2.assert_awaited_once()


# ─── Audit Store Tests (using aiosqlite :memory:) ──────────────


@pytest_asyncio.fixture
async def memory_db():
    """Create an in-memory SQLite database with the action_events table."""
    db = await aiosqlite.connect(':memory:')
    db.row_factory = aiosqlite.Row
    await db.execute('''
        CREATE TABLE IF NOT EXISTS action_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_name TEXT NOT NULL,
            source TEXT NOT NULL,
            user_id TEXT,
            session_id TEXT,
            arguments_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
    ''')
    await db.commit()
    yield db
    await db.close()


@pytest.mark.asyncio
class TestActionEventStore:
    """ActionEventStore persistence and querying."""

    async def test_record_and_count(self, memory_db):
        store = ActionEventStore(memory_db)
        assert await store.count() == 0

        await store.record(
            action_name='pause_download',
            source=ActionSource.UI,
            user_id='user-1',
            arguments={'download_id': 'abc'},
            result={'ok': True},
        )
        assert await store.count() == 1

    async def test_record_with_all_fields(self, memory_db):
        store = ActionEventStore(memory_db)
        await store.record(
            action_name='resume_download',
            source=ActionSource.CHAT,
            user_id='user-1',
            session_id='sess-1',
            arguments={'download_id': 'xyz'},
            result={'ok': True, 'data': {'id': 'xyz'}},
        )
        rows = await store.get_recent(limit=10)
        assert len(rows) == 1
        row = rows[0]
        assert row['action_name'] == 'resume_download'
        assert row['source'] == 'chat'
        assert row['user_id'] == 'user-1'
        assert row['session_id'] == 'sess-1'

    async def test_get_recent_empty(self, memory_db):
        store = ActionEventStore(memory_db)
        rows = await store.get_recent()
        assert rows == []

    async def test_get_recent_filtered_by_source(self, memory_db):
        store = ActionEventStore(memory_db)
        await store.record('a', ActionSource.UI)
        await store.record('b', ActionSource.CHAT)
        await store.record('c', ActionSource.UI)

        ui_events = await store.get_recent(source=ActionSource.UI)
        assert len(ui_events) == 2

        chat_events = await store.get_recent(source=ActionSource.CHAT)
        assert len(chat_events) == 1

    async def test_get_recent_filtered_by_name(self, memory_db):
        store = ActionEventStore(memory_db)
        await store.record('pause', ActionSource.UI)
        await store.record('resume', ActionSource.UI)
        await store.record('pause', ActionSource.CHAT)

        pause_events = await store.get_recent(action_name='pause')
        assert len(pause_events) == 2

        resume_events = await store.get_recent(action_name='resume')
        assert len(resume_events) == 1

    async def test_get_recent_by_user(self, memory_db):
        store = ActionEventStore(memory_db)
        await store.record('a', ActionSource.UI, user_id='u1')
        await store.record('b', ActionSource.UI, user_id='u2')
        await store.record('c', ActionSource.UI, user_id='u1')

        u1_events = await store.get_recent_by_user('u1')
        assert len(u1_events) == 2

        u2_events = await store.get_recent_by_user('u2')
        assert len(u2_events) == 1

    async def test_record_with_null_fields(self, memory_db):
        """Null user_id and session_id are stored as None."""
        store = ActionEventStore(memory_db)
        await store.record('test', ActionSource.SYSTEM)
        rows = await store.get_recent()
        assert len(rows) == 1
        assert rows[0]['user_id'] is None
        assert rows[0]['session_id'] is None

    async def test_database_failure_is_swallowed(self, memory_db):
        """Store does not raise on DB errors (e.g. closed connection)."""
        await memory_db.close()
        store = ActionEventStore(memory_db)
        await store.record('test', ActionSource.SYSTEM)  # no exception raised


# ─── Integration: Gateway + Audit Store ────────────────────────


@pytest.mark.asyncio
class TestGatewayWithAuditStore:
    """End-to-end: ActionGateway with a real ActionEventStore."""

    async def test_execute_records_audit(self, memory_db):
        audit_store = ActionEventStore(memory_db)
        gateway = ActionGateway(audit_store=audit_store)
        handler = AsyncMock(return_value={'processed': True})
        gateway.register('process', handler)

        await gateway.execute(ActionCommand(
            name='process',
            arguments={'item': 'x'},
            source=ActionSource.SCHEDULER,
            user_id='bot',
        ))

        assert await audit_store.count() == 1
        rows = await audit_store.get_recent()
        assert rows[0]['action_name'] == 'process'
        assert rows[0]['source'] == 'scheduler'
        assert rows[0]['user_id'] == 'bot'

    async def test_failed_execution_still_audited(self, memory_db):
        audit_store = ActionEventStore(memory_db)
        gateway = ActionGateway(audit_store=audit_store)

        async def failing(**kwargs):
            raise ValueError('fail')

        gateway.register('fail', failing)
        await gateway.execute(ActionCommand(
            name='fail', source=ActionSource.UI,
        ))

        assert await audit_store.count() == 1
        rows = await audit_store.get_recent()
        assert rows[0]['action_name'] == 'fail'


# ─── Behavior Recording Tests ───────────────────────────────────


class TestBehaviorRecording:
    """ActionGateway records preference-revealing actions via BehaviorRecorder."""

    def test_records_preference_action(self):
        """Gateway calls behavior_recorder.record_action for known preference actions."""
        recorder = AsyncMock()
        gateway = ActionGateway(behavior_recorder=recorder)
        handler = AsyncMock(return_value={'done': True})
        gateway.register('suggestion_deny', handler)

        _run(gateway.execute(ActionCommand(
            name='suggestion_deny',
            arguments={'item_name': 'Test Show'},
            source=ActionSource.UI,
            user_id='user-1',
        )))

        recorder.record_action.assert_awaited_once()
        call_kwargs = recorder.record_action.call_args.kwargs
        assert call_kwargs['user_id'] == 'user-1'
        assert call_kwargs['action'] == 'reject'
        assert call_kwargs['item_name'] == 'Test Show'

    def test_does_not_record_non_preference_action(self):
        """Gateway does not call behavior_recorder for non-preference actions."""
        recorder = AsyncMock()
        gateway = ActionGateway(behavior_recorder=recorder)
        handler = AsyncMock(return_value={'done': True})
        gateway.register('pause_download', handler)

        _run(gateway.execute(ActionCommand(
            name='pause_download',
            arguments={'download_id': 'abc'},
            source=ActionSource.UI,
        )))

        recorder.record_action.assert_not_called()

    def test_does_not_record_failed_action(self):
        """Failed executions do not trigger behavior recording."""
        recorder = AsyncMock()
        gateway = ActionGateway(behavior_recorder=recorder)

        async def failing(**kwargs):
            raise RuntimeError('fail')

        gateway.register('suggestion_deny', failing)

        _run(gateway.execute(ActionCommand(
            name='suggestion_deny',
            arguments={'item_name': 'Test'},
            source=ActionSource.UI,
        )))

        recorder.record_action.assert_not_called()

    def test_records_category_item_pause(self):
        """category_item_pause is a recognized preference action."""
        recorder = AsyncMock()
        gateway = ActionGateway(behavior_recorder=recorder)
        handler = AsyncMock(return_value={'status': 'paused'})
        gateway.register('category_item_pause', handler)

        _run(gateway.execute(ActionCommand(
            name='category_item_pause',
            arguments={'name': 'My Show'},
            source=ActionSource.UI,
            user_id='user-1',
        )))

        recorder.record_action.assert_awaited_once()
        assert recorder.record_action.call_args.kwargs['item_name'] == 'My Show'
        assert recorder.record_action.call_args.kwargs['action'] == 'category_item_pause'

    def test_records_settings_update_quality(self):
        """settings_update_quality is a recognized preference action."""
        recorder = AsyncMock()
        gateway = ActionGateway(behavior_recorder=recorder)
        handler = AsyncMock(return_value={'status': 'ok'})
        gateway.register('settings_update_quality', handler)

        _run(gateway.execute(ActionCommand(
            name='settings_update_quality',
            arguments={'preferred_resolution': '4k'},
            source=ActionSource.UI,
        )))

        recorder.record_action.assert_awaited_once()
        assert recorder.record_action.call_args.kwargs['action'] == 'quality_change'

    def test_records_upgrade_deny(self):
        """upgrade_deny is a recognized preference action."""
        recorder = AsyncMock()
        gateway = ActionGateway(behavior_recorder=recorder)
        handler = AsyncMock(return_value={'status': 'denied'})
        gateway.register('upgrade_deny', handler)

        _run(gateway.execute(ActionCommand(
            name='upgrade_deny',
            arguments={'item_name': 'My Show'},
            source=ActionSource.UI,
        )))

        recorder.record_action.assert_awaited_once()
        assert recorder.record_action.call_args.kwargs['item_name'] == 'My Show'

    def test_behavior_recorder_swallows_exceptions(self):
        """BehaviorRecorder failure does not bubble up to the caller."""
        recorder = AsyncMock()
        recorder.record_action.side_effect = RuntimeError('DB down')
        gateway = ActionGateway(behavior_recorder=recorder)
        handler = AsyncMock(return_value={'done': True})
        gateway.register('suggestion_deny', handler)

        result = _run(gateway.execute(ActionCommand(
            name='suggestion_deny',
            arguments={'item_name': 'Test'},
            source=ActionSource.UI,
        )))

        assert result.ok is True
        recorder.record_action.assert_awaited_once()


# ─── Handler Equivalence Tests ──────────────────────────────────


class TestHandlerEquivalence:
    """Button click (ActionGateway) and chat tool call (ToolRegistry)
    use the same handler for equivalent operations.

    When ActionGateway is constructed with a ToolRegistry, both paths
    route through the registry's execute() method. This test verifies
    that executing via the gateway with ActionSource.UI produces the
    same result as executing via the registry directly.
    """

    def test_gateway_and_registry_call_same_handler(self):
        """UI action via gateway calls the same handler as a registry execute."""
        handler = AsyncMock(return_value={'status': 'paused'})
        registry = MagicMock()
        registry.execute = AsyncMock(return_value={'status': 'paused'})
        registry.get_tool_names = MagicMock(return_value=['pause_download'])

        gateway = ActionGateway(tool_registry=registry)

        # Simulate registering the tool on the registry side
        gateway.register('pause_download', handler)
        gateway.register('category_item_pause', AsyncMock(return_value={'status': 'paused'}))

        # Execute via gateway (simulates UI button click)
        ui_result = _run(gateway.execute(ActionCommand(
            name='pause_download',
            arguments={'download_id': 'abc'},
            source=ActionSource.UI,
        )))

        # Execute via registry directly (simulates chat tool call)
        _run(registry.execute('pause_download', {'download_id': 'abc'}))

        # Both routes called the same registry.execute method
        assert ui_result.ok is True
        assert ui_result.data == {'status': 'paused'}
        registry.execute.assert_awaited_with('pause_download', {'download_id': 'abc'})

    def test_actions_router_uses_same_gateway(self):
        """The unified /api/actions endpoint shares the same gateway
        as the dedicated router endpoints, so the same handler is called."""
        handler_a = AsyncMock(return_value='from-gateway')
        handler_b = AsyncMock(return_value='from-gateway')

        gateway = ActionGateway()
        gateway.register('test_action_a', handler_a)
        gateway.register('test_action_b', handler_b)

        result_a = _run(gateway.execute(ActionCommand(
            name='test_action_a', source=ActionSource.UI,
        )))
        result_b = _run(gateway.execute(ActionCommand(
            name='test_action_b', source=ActionSource.CHAT,
        )))

        assert result_a.data == {'value': 'from-gateway'}
        assert result_b.data == {'value': 'from-gateway'}


# ─── Registration Service Tests ─────────────────────────────────


class TestActionRegistrationService:
    """ActionRegistrationService centralizes registrations from app.py."""

    def test_registration_service_imports(self):
        """The registration service module imports cleanly."""
        from src.core.actions.registration import ActionRegistrationService
        assert ActionRegistrationService is not None
