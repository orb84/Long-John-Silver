"""
Agent loop state for LJS.

Tracks tool results and reflection decisions during the agentic loop,
enabling the plan-execute-reflect cycle to decide when enough
evidence has been gathered.
"""

from src.core.models import AgentLoopState

# Constants for reflection policy
MIN_TOOL_RESULTS_BEFORE_REFLECT = 1
"""Minimum tool results before reflection is called."""

MIN_ITERATIONS_BETWEEN_REFLECTIONS = 2
"""Minimum iterations between reflection calls."""

INTENTS_ELIGIBLE_FOR_REFLECTION = {"SEARCH", "DOWNLOAD"}
"""Intents that use the reflect step."""