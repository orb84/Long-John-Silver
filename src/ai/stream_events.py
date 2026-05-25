"""
Agent stream events for LJS.

Typed event model for streaming responses from the agent loop.
Provides structured events (text, tool_start, tool_end, error)
instead of raw string chunks, enabling the UI to show tool
execution status while tools run.
"""

from src.core.models import AgentStreamEvent