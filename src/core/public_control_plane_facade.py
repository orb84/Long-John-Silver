"""Aggregate protocol-neutral public services without exposing internal registries."""

from __future__ import annotations

from src.ai.agent_delegation import AgentDelegationService
from src.core.public_control_plane import (
    PublicDiagnosticsService,
    PublicDownloadService,
    PublicLibraryService,
    PublicLLMConfigurationService,
    PublicStatusService,
)


class PublicControlPlane:
    """Small facade consumed by external protocol adapters such as MCP."""

    def __init__(
        self,
        *,
        agent: AgentDelegationService,
        status: PublicStatusService,
        library: PublicLibraryService,
        downloads: PublicDownloadService,
        llm: PublicLLMConfigurationService,
        diagnostics: PublicDiagnosticsService,
    ) -> None:
        self.agent = agent
        self.status = status
        self.library = library
        self.downloads = downloads
        self.llm = llm
        self.diagnostics = diagnostics
