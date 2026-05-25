"""
Category tool factory for LJS.

Builds LLM tool adapters from category-declared actions and workflows. This
keeps category-specific tool names out of global provider classes while still
registering them with the shared ToolRegistry.
"""

from __future__ import annotations

from typing import Any, Callable

from loguru import logger

from src.core.models import ActionReceipt, CategoryActionDeclaration, CategoryWorkflowDeclaration, Intent, ToolExecutionContext


class CategoryScopedTool:
    """AgentTool adapter around one category action or workflow declaration."""

    def __init__(
        self,
        category: Any,
        declaration: CategoryActionDeclaration | CategoryWorkflowDeclaration,
        is_workflow: bool = False,
        context_factory: Callable[[ToolExecutionContext], Any] | None = None,
    ) -> None:
        """Initialize the adapter for one category-owned callable."""
        self._category = category
        self._declaration = declaration
        self._is_workflow = is_workflow
        self._context_factory = context_factory
        self.name = self._resolve_name(declaration, is_workflow)
        self.description = declaration.description
        self.intents = self._resolve_intents(declaration, is_workflow)
        self.allow_direct = True
        self.requires_confirmation = getattr(declaration, "requires_confirmation", False)
        self.destructive = bool(
            getattr(declaration, "destructive", False)
            or getattr(declaration, "risk_level", "") == "destructive"
        )
        self.required_dependencies = ["category_registry"]

    def parameters(self) -> dict[str, Any]:
        """Return the JSON schema declared by the category."""
        return self._declaration.parameters

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Execute the category action or workflow and return serializable data."""
        runtime_context = self._context_factory(context) if self._context_factory else context
        if self._is_workflow:
            receipt = await self._category.execute_workflow(self._declaration.name, arguments, runtime_context)
        else:
            receipt = await self._category.execute_action(self._declaration.name, arguments, runtime_context)
        if isinstance(receipt, ActionReceipt):
            return receipt.model_dump()
        return receipt

    def _resolve_name(
        self,
        declaration: CategoryActionDeclaration | CategoryWorkflowDeclaration,
        is_workflow: bool,
    ) -> str:
        """Resolve the registry tool name for one declaration."""
        if is_workflow:
            return declaration.tool_name or f"{self._category.category_id}.{declaration.name}"
        return declaration.exposed_tool_name

    def _resolve_intents(
        self,
        declaration: CategoryActionDeclaration | CategoryWorkflowDeclaration,
        is_workflow: bool,
    ) -> set[Intent]:
        """Resolve the tool intent scope for one declaration."""
        if is_workflow:
            return {declaration.intent}
        return {Intent.SEARCH, Intent.DOWNLOAD, Intent.CONFIG, Intent.CHAT}


class CategoryToolFactory:
    """Creates dynamic tools for all registered category declarations."""

    def __init__(self, category_registry: Any | None = None,
                 context_factory: Callable[[ToolExecutionContext], Any] | None = None) -> None:
        """Initialize the factory with an optional category registry."""
        self._registry = category_registry
        self._context_factory = context_factory

    def build_tools(self) -> list[CategoryScopedTool]:
        """Build de-duplicated category tools from action/workflow manifests.

        Workflows are preferred over actions when both declare the same LLM
        tool name because workflows are the concrete domain implementation.
        Actions remain the UI/permission contract and are only exposed directly
        when they have a unique tool name.
        """
        if not self._registry:
            return []
        tools: list[CategoryScopedTool] = []
        seen: dict[str, str] = {}
        for category in self._registry.list_all():
            self._append_category_workflow_tools(category, tools, seen)
            self._append_category_action_tools(category, tools, seen)
        return tools

    def _append_category_workflow_tools(
        self, category: Any, tools: list[CategoryScopedTool], seen: dict[str, str]
    ) -> None:
        """Append unique workflow tools for one category."""
        for workflow in category.declare_workflows():
            if workflow.tool_name:
                self._append_unique_tool(
                    tools, seen,
                    CategoryScopedTool(category, workflow, is_workflow=True, context_factory=self._context_factory),
                    source=f"{category.category_id}.workflow.{workflow.name}",
                )

    def _append_category_action_tools(
        self, category: Any, tools: list[CategoryScopedTool], seen: dict[str, str]
    ) -> None:
        """Append unique action tools for one category, keeping subclass overrides."""
        actions_by_tool_name: dict[str, CategoryActionDeclaration] = {}
        for action in category.declare_actions():
            if action.llm_visible and action.exposed_tool_name:
                actions_by_tool_name[action.exposed_tool_name] = action
        for action in actions_by_tool_name.values():
            self._append_unique_tool(
                tools, seen,
                CategoryScopedTool(category, action, is_workflow=False, context_factory=self._context_factory),
                source=f"{category.category_id}.action.{action.name}",
            )

    def _append_unique_tool(
        self,
        tools: list[CategoryScopedTool],
        seen: dict[str, str],
        tool: CategoryScopedTool,
        source: str,
    ) -> None:
        """Append a category tool unless another declaration already owns its name."""
        if tool.name in seen:
            logger.debug(
                f"Skipping duplicate category tool '{tool.name}' from {source}; "
                f"already provided by {seen[tool.name]}."
            )
            return
        tools.append(tool)
        seen[tool.name] = source
