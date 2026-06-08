"""Planning and agent runtime models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from src.core.domain_models.enums import Intent
from src.core.domain_models.categories import AgentRunContext

# --- Agent / Planning Models ---


class PlanStep(BaseModel):
    """A single step in a structured agent plan.

    Describes which tool to call, with what arguments, what it
    depends on, and a human-readable success condition.
    """

    id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    success_condition: str = ""


class AgentPlan(BaseModel):
    """A structured, typed plan for complex agent tasks.

    Produced by ReasoningPlanner for SEARCH and DOWNLOAD intents.
    Steps are executed deterministically by PlanExecutor.
    """

    intent: Intent
    user_goal: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    steps: list[PlanStep] = Field(default_factory=list)


class PlanExecutionStep(BaseModel):
    """Result of executing a single PlanStep.

    Tracks whether the step succeeded, its tool result message,
    a human-readable summary, and any error that occurred.
    """

    step: PlanStep
    success: bool = False
    result: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    error: str | None = None


class PlanExecutionResult(BaseModel):
    """Overall result of executing an AgentPlan.

    Contains the original plan, per-step results, and a
    boolean indicating whether all steps completed successfully.
    """

    plan: AgentPlan
    steps: list[PlanExecutionStep] = Field(default_factory=list)
    all_successful: bool = False

    def format_for_prompt(self) -> str:
        """Format the plan execution results as a prompt-friendly string."""
        lines = []
        for s in self.steps:
            if s.success:
                lines.append(f"  [{s.step.id}] {s.step.tool_name} — OK")
                if s.summary:
                    lines.append(f"    Result: {s.summary}")
            else:
                lines.append(f"  [{s.step.id}] {s.step.tool_name} — FAILED: {s.error}")
        return "\n".join(lines)


class ToolExecutionContext(BaseModel):
    """Runtime context passed to AgentTool.execute().

    Carries information about who invoked the tool and from where,
    enabling audit logging and permission checks.
    """

    user_id: str | None = None
    session_id: str | None = None
    source: str = "chat"
    actor: str = "user"
    category_id: str | None = None
    user_prompt: str | None = None


class AgentStreamEvent(BaseModel):
    """A typed event during agent streaming.

    type: text → regular text token from the model.
    type: status → persona progress update while a long turn is running.
    type: tool_start → a tool is being executed.
    type: tool_end → a tool execution completed.
    type: error → an error occurred.
    """

    type: Literal["text", "status", "tool_start", "tool_end", "error"]
    content: str = ""
    tool_name: str | None = None


class AssembledToolCall(BaseModel):
    """A fully assembled tool call from streaming deltas.

    Contains the complete function name, arguments JSON, and ID
    after all streaming chunks have been received.
    """

    model_config = {"arbitrary_types_allowed": True}

    id: str = ""
    name: str = ""
    arguments: str = ""


class AgentLoopState(BaseModel):
    """Mutable state for one agent loop execution.

    Tracks tool results accumulated across iterations and whether
    reflection has determined that sufficient evidence exists.
    """

    model_config = {"arbitrary_types_allowed": True}

    tool_results: list[str] = Field(default_factory=list)
    force_final_response: bool = False


class PreparedAgentRun(BaseModel):
    """Fully prepared state for one assistant run.

    Contains all resolved configuration, context, and tool
    definitions needed to execute the agentic loop.
    """

    model_config = {"arbitrary_types_allowed": True}

    intent: Intent
    task: str
    messages: list = Field(default_factory=list)
    tool_definitions: list | None = None
    allowed_tool_names: set = Field(default_factory=set)
    max_iterations: int = 4
    should_record_download_behavior: bool = False
    system_prompt: str = ""
    agent_plan: AgentPlan | None = None
    agent_context: AgentRunContext | None = None

