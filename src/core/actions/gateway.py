"""
Action gateway for LJS.

ActionGateway is the single entry point for all deterministic mutations
from UI buttons, chat tool calls, scheduler jobs, and automation.
It validates actions, executes handlers, records audit events, and
returns typed results.

When a ToolCallExecutor is provided, UI ActionCommands route through
the exact same tool execution pipeline as LLM tool calls — the UI
and the agent share one unified handler registry (ToolRegistry).

Preference-revealing actions (rejecting suggestions, pausing category items,
changing quality) are forwarded to an optional ``BehaviorRecorder``
so the system learns from button clicks, not just chat commands.
"""

from typing import Any, Callable, Coroutine

from loguru import logger

from src.core.models import ActionCommand, ActionResult, ActionSource
from src.core.actions.audit import ActionEventStore


# Type alias for an async action handler
ActionHandler = Callable[..., Coroutine[Any, Any, Any]]

# Actions whose execution should also be recorded as behavior events.
# Mapping: action_name -> (behavior_action_label, extract_item_name_fn)
_PREFERENCE_ACTIONS: dict[str, tuple[str, Callable[[dict], str | None]]] = {
    'suggestion_deny':    ('reject', lambda a: a.get('item_name') or a.get('item_id') or a.get('name')),
    'suggestion_approve': ('download', lambda a: a.get('item_name') or a.get('item_id') or a.get('name')),
    'category_item_pause':  ('category_item_pause', lambda a: a.get('item_name') or a.get('item_id') or a.get('name')),
    'category_item_resume': ('category_item_resume', lambda a: a.get('item_name') or a.get('item_id') or a.get('name')),
    'settings_update_quality': ('quality_change', lambda a: None),
    'upgrade_deny':       ('reject', lambda a: a.get('item_name') or a.get('item_id') or a.get('name')),
    'download_cancel':    ('cancel', lambda a: a.get('item_name') or a.get('item_id') or a.get('name')),
}


class ActionGateway:
    """Unified gateway for executing deterministic action commands.

    When a ToolCallExecutor is provided at construction, all actions
    route through the shared ToolRegistry via ToolCallExecutor —
    the UI and LLM use the exact same tool execution pipeline.
    Without an executor, the gateway falls back to its own legacy
    handler registry for backward compatibility.

    Typical usage:
        gateway = ActionGateway(event_bus=bus, tool_registry=registry)
        gateway.register('pause_download', downloader.pause_download)
        result = await gateway.execute(ActionCommand(name='pause_download', ...))
    """

    def __init__(self, audit_store: ActionEventStore | None = None,
                 event_bus: Any = None,
                 tool_registry: Any = None,
                 behavior_recorder: Any = None) -> None:
        self._registry = tool_registry
        self._legacy_handlers: dict[str, ActionHandler] = {}
        self._audit_store = audit_store
        self._event_bus = event_bus
        self._behavior_recorder = behavior_recorder

    def register(self, name: str, handler: ActionHandler,
                 description: str = "", parameters: dict | None = None,
                 intents: Any = None,
                 requires_confirmation: bool = False,
                 destructive: bool = False) -> None:
        """Register an action handler.

        When a ToolRegistry is available, registers the handler as a
        direct-callable tool in the shared registry so UI actions and
        LLM tool calls use the same handler. Otherwise falls back to
        the legacy handler dict.

        Args:
            name: Unique action name (e.g. 'pause_download').
            handler: Async callable that accepts **kwargs from ActionCommand.arguments.
            description: Human-readable description for the tool definition.
            parameters: JSON Schema for the tool's arguments.
            intents: Optional set of intents this action is available for.
            requires_confirmation: Whether the action needs user confirmation.
            destructive: Whether the action can delete or remove data.
        """
        if self._registry:
            self._registry.register(
                name=name,
                description=description or f"Action: {name}",
                parameters=parameters or {"type": "object", "properties": {}, "required": []},
                handler=handler,
                allow_direct=True,
                intents=intents,
                requires_confirmation=requires_confirmation,
                destructive=destructive,
            )
            logger.debug(f'Registered action as tool: {name}')
        else:
            if name in self._legacy_handlers:
                logger.warning(f'Action {name!r} is being re-registered')
            self._legacy_handlers[name] = handler
            logger.debug(f'Registered action handler: {name}')

    async def execute(self, command: ActionCommand) -> ActionResult:
        """Execute an action command through the pipeline.

        Pipeline order:
          1. Validate action exists (via ToolRegistry or legacy handlers).
          2. Execute via the shared tool pipeline.
          3. Record the audit event.
          4. Emit a WebSocket action event.
          5. Return the typed result.

        Args:
            command: The action command to execute.

        Returns:
            ActionResult with ok=True on success, ok=False with error on failure.
        """
        if self._registry:
            raw = await self._registry.execute(command.name, command.arguments)
            if isinstance(raw, dict) and raw.get("error"):
                result = ActionResult(
                    ok=False, error=raw["error"], action_name=command.name,
                )
            else:
                result = ActionResult(
                    ok=True, data=_ensure_dict(raw), action_name=command.name,
                )
        else:
            handler = self._legacy_handlers.get(command.name)
            if not handler:
                msg = f'Unknown action: {command.name!r}'
                logger.warning(msg)
                return ActionResult(
                    ok=False, error=msg, action_name=command.name,
                )

            try:
                data = await handler(**command.arguments)
                result = ActionResult(
                    ok=True,
                    data=_ensure_dict(data),
                    action_name=command.name,
                )
            except Exception as exc:
                logger.error(f'Action {command.name!r} failed: {exc}')
                result = ActionResult(
                    ok=False, error=str(exc), action_name=command.name,
                )

        await self._audit(command, result)
        await self._record_behavior(command, result)

        if self._event_bus:
            self._event_bus.emit('action_executed', {
                'action': command.name,
                'source': command.source.value,
                'ok': result.ok,
                'user_id': command.user_id,
            })

        return result

    async def _record_behavior(self, command: ActionCommand, result: ActionResult) -> None:
        """Record preference-revealing actions in the behavior tracker.

        Only records successful actions (result.ok) that match known
        preference-revealing action names in ``_PREFERENCE_ACTIONS``.
        """
        if not self._behavior_recorder or not result.ok:
            return
        mapping = _PREFERENCE_ACTIONS.get(command.name)
        if not mapping:
            return
        behavior_action, extract_item = mapping
        item_name = extract_item(command.arguments)
        try:
            await self._behavior_recorder.record_action(
                user_id=command.user_id or 'system',
                action=behavior_action,
                item_name=item_name,
                action_name=command.name,
            )
        except Exception as exc:
            logger.warning(f'Failed to record behavior for {command.name!r}: {exc}')

    async def _audit(self, command: ActionCommand, result: ActionResult) -> None:
        """Record an action event in the audit store, if configured."""
        if not self._audit_store:
            return
        try:
            await self._audit_store.record(
                action_name=command.name,
                source=command.source,
                user_id=command.user_id,
                session_id=command.session_id,
                arguments=command.arguments,
                result={'ok': result.ok, 'error': result.error, 'data': result.data},
            )
        except Exception as exc:
            logger.warning(f'Failed to record audit event: {exc}')

    @property
    def registered_actions(self) -> list[str]:
        """Return the list of registered action names."""
        if self._registry:
            return self._registry.get_tool_names()
        return list(self._legacy_handlers.keys())


def _ensure_dict(data: Any) -> dict[str, Any]:
    """Convert a handler's return value to a dict for ActionResult.data."""
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    if hasattr(data, 'model_dump'):
        return data.model_dump()
    return {'value': data}
