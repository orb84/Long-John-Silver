"""
Declarative agent tool package for LJS.

This package intentionally avoids importing domain tool modules at package
import time. Tool providers are imported explicitly by the composition root.
"""

from src.ai.tools.base import AgentTool
from src.core.models import ToolExecutionContext

__all__ = ["AgentTool", "ToolExecutionContext"]
