#!/usr/bin/env python3
"""Round 19 architecture audit: strict-OOP service decomposition.

This audit verifies that the category monoliths, scheduler, download tools, and
Hold UI have been split into focused services/mixins without relying on optional
runtime integrations.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(rel: str) -> str:
    """Read a project file as UTF-8 text."""
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    """Raise an assertion error when an architecture invariant is not met."""
    if not condition:
        raise AssertionError(message)


def line_count(rel: str) -> int:
    """Return the number of lines in a project file."""
    return len(text(rel).splitlines())


def main() -> None:
    """Run static architecture invariants for the Round 19 refactor."""
    models = text("src/core/models.py")
    require("domain_models" in models and "Compatibility facade" in models,
            "core models should be a compatibility facade over domain model modules")
    for rel in [
        "src/core/domain_models/enums.py",
        "src/core/domain_models/media.py",
        "src/core/domain_models/downloads.py",
        "src/core/domain_models/agent.py",
        "src/core/domain_models/settings.py",
    ]:
        require((ROOT / rel).exists(), f"missing domain model module: {rel}")

    require(line_count("src/core/downloader.py") < 800, "downloader should stay below the legacy monolith line limit")
    require("class DownloadDependencies" in text("src/core/download_dependencies.py"),
            "download dependencies should live outside downloader.py")
    scheduler = text("src/core/scheduler.py")
    services = text("src/core/scheduler_services.py")
    require(line_count("src/core/scheduler.py") < 800, "scheduler should stay below the legacy monolith line limit")
    require("SchedulerCatalogService" in scheduler and "SchedulerTorrentSearchService" in scheduler,
            "scheduler should delegate catalog and torrent-search operations")
    require("class SchedulerCatalogService" in services and "class SchedulerTorrentSearchService" in services,
            "scheduler services should be explicit OOP collaborators")

    require(line_count("src/core/categories/base.py") < 800, "base category should not be a monolith")
    require(line_count("src/core/categories/tv.py") < 800, "TV category should not be a monolith")
    require("CategoryContractMixin" in text("src/core/categories/base_contract.py"),
            "category manifest/workflow contract should be isolated in a mixin")
    require("CategoryContextMixin" in text("src/core/categories/base_context.py"),
            "category prompt/detail context should be isolated in a mixin")
    require("TvWorkflowMixin" in text("src/core/categories/tv_workflows.py"),
            "TV workflow behavior should be isolated in a mixin")
    require("TvAgentSearchMixin" in text("src/core/categories/tv_agent.py"),
            "TV agent search behavior should be isolated in a mixin")
    require("TvContextMixin" in text("src/core/categories/tv_context.py"),
            "TV LLM/detail serialization should be isolated in a mixin")
    require("TvMetadataInfoMixin" in text("src/core/categories/tv_metadata_info.py"),
            "TV metadata/enquiry behavior should be isolated in a mixin")

    downloads = text("src/ai/tools/downloads.py")
    require("QueueDownloadService" in downloads and "DownloadListReportService" in downloads and "TorrentSearchToolService" in downloads,
            "download tools should delegate to focused service collaborators")
    require("class QueueDownloadService" in text("src/ai/tools/queue_download_support.py"),
            "queue-download support service should exist")
    require("class TorrentSearchToolService" in text("src/ai/tools/torrent_search_support.py"),
            "torrent-search support service should exist")
    require("class DownloadListReportService" in text("src/ai/tools/download_list_support.py"),
            "download-list report service should exist")

    ui = text("src/web/static/js/components/downloadManagerUI.js")
    base_html = text("src/web/templates/base.html")
    require("DownloadStatsPatcher.patch" in ui and "DownloadFileRowsRenderer" in ui,
            "Hold UI should delegate live patching and file-row rendering")
    require("downloadStatsPatcher.js" in base_html and "downloadFileRows.js" in base_html,
            "new Hold UI components should be loaded before the manager")

    print("round19 strict-OOP service decomposition audit passed")


if __name__ == "__main__":
    main()
