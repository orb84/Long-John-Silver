"""Import smoke test for LJS.

Verifies that all core modules can be imported without circular
dependency errors. Each test imports a single top-level module
and checks for ImportError.

Run with:
    python -m pytest tests/test_import_smoke.py -q
"""

import importlib
import pkgutil
import pytest
from pathlib import Path


# ── Core domain modules ──────────────────────────────────────────

CORE_MODULES = [
    "src.core.models",
    "src.core.config",
    "src.core.database",
    "src.core.downloader",
    "src.core.downloader_progress_cache",
    "src.core.downloader_start_coordinator",
    "src.core.downloader_lifecycle",
    "src.core.downloader_monitor_registry",
    "src.core.queue_manager",
    "src.core.scheduler",
    "src.core.torrent_engine",
    "src.core.librarian",
    "src.core.search_pipeline",
    "src.core.preferences",
    "src.core.behavior_tracker",
    "src.core.conversation",
    "src.core.smart_quality",
    "src.core.upgrade_detector",
    "src.core.recommender",
    "src.core.taste_profiler",
    "src.core.vector_store",
    "src.core.categories.metadata.enricher",
    "src.core.content_cleanup",
    "src.core.bandwidth_manager",
    "src.core.release_groups",
    "src.core.prompt_scheduler",
    "src.core.notifications",
    "src.core.task_supervisor",
    "src.core.state_coordinator",
    "src.core.bundle_download",
    "src.core.torrent_racer",
    "src.core.torrent_engine",
    "src.core.suggestion_compiler",
    "src.core.download_handler",
]

# ── Action sub-package ───────────────────────────────────────────

ACTION_MODULES = [
    "src.core.actions",
    "src.core.actions.gateway",
    "src.core.actions.audit",
    "src.core.actions.registration",
]

# ── Category sub-package ─────────────────────────────────────────

CATEGORY_MODULES = [
    "src.core.categories",
    "src.core.categories.base",
    "src.core.categories.tv",
    "src.core.categories.movie",
    "src.core.categories.language",
    "src.core.categories.verifier",
    "src.core.categories.path_planner",
    "src.core.categories.consolidator",
]

# ── AI modules ───────────────────────────────────────────────────

AI_MODULES = [
    "src.ai",
    "src.ai.assistant",
    "src.ai.intent_router",
    "src.ai.tool_registry",
    "src.ai.tool_executor",
    "src.ai.tool_catalog",
    "src.ai.tool_policy",
    "src.ai.agent_loop",
    "src.ai.agent_loop_state",
    "src.ai.streaming_agent_loop",
    "src.ai.streaming_tool_calls",
    "src.ai.conversation_binding",
    "src.ai.llm_task_runtime",
    "src.ai.plan_coordinator",
    "src.ai.plan_executor",
    "src.ai.reasoning",
    "src.ai.memory_composer",
    "src.ai.run_preparer",
    "src.ai.prompt_builder",
    "src.ai.behavior_recorder",
    "src.ai.token_budget",
    "src.ai.web_researcher",
    "src.ai.web_reader",
    "src.ai.browser_tools",
    "src.ai.browser_session",
    "src.ai.torrent_selection",
    "src.ai.stream_events",
]

# ── AI tool providers ────────────────────────────────────────────

TOOL_MODULES = [
    "src.ai.tools",
    "src.ai.tools.base",
    "src.ai.tools.downloads",
    "src.ai.tools.library",
    "src.ai.tools.preferences",
    "src.ai.tools.research",
    "src.ai.tools.scheduling",
    "src.ai.tools.web",
]

# ── Integration modules ──────────────────────────────────────────

INTEGRATION_MODULES = [
    "src.integrations",
    "src.integrations.tmdb",
    "src.integrations.tvmaze",
    "src.integrations.trakt",
]

# ── LLM provider modules ─────────────────────────────────────────

LLM_MODULES = [
    "src.llm_providers",
    "src.llm_providers.client",
    "src.llm_providers.catalog",
    "src.llm_providers.registry",
    "src.llm_providers.key_store",
    "src.llm_providers.presets",
    "src.llm_providers.models",
]

# ── Search modules ───────────────────────────────────────────────

SEARCH_MODULES = [
    "src.search",
    "src.search.base",
    "src.search.btdigg",
    "src.search.nyaa",
    "src.search.torznab",
    "src.search.search_1337x",
    "src.search.torrentgalaxy",
    "src.search.aggregator",
    "src.search.rss_monitor",
]

# ── Subtitle modules ─────────────────────────────────────────────

SUBTITLE_MODULES = [
    "src.subtitles",
    "src.subtitles.opensubtitles",
]

# ── Utility modules ──────────────────────────────────────────────

UTILITY_MODULES = [
    "src.utils",
    "src.utils.auth",
    "src.utils.blacklist",
    "src.utils.quality",
    "src.utils.library_scanner",
    "src.utils.circuit_breaker",
    "src.utils.torrent_knowledge",
    "src.utils.scheduler",
]

# ── Web modules (no side effects) ────────────────────────────────

WEB_MODULES = [
    "src.web",
    "src.web.dependencies",
    "src.web.app",
    "src.web.comms",
]

ALL_MODULES = (
    CORE_MODULES
    + ACTION_MODULES
    + CATEGORY_MODULES
    + AI_MODULES
    + TOOL_MODULES
    + INTEGRATION_MODULES
    + LLM_MODULES
    + SEARCH_MODULES
    + SUBTITLE_MODULES
    + UTILITY_MODULES
    + WEB_MODULES
)


@pytest.mark.parametrize("module_name", ALL_MODULES, ids=lambda m: m.split(".")[-1])
def test_module_imports_cleanly(module_name: str) -> None:
    """Verify that a module imports without circular dependency errors.

    Args:
        module_name: Fully qualified module path (e.g. 'src.core.models').
    """
    try:
        importlib.import_module(module_name)
    except ImportError as exc:
        pytest.fail(f"ImportError for {module_name}: {exc}")
    except Exception as exc:
        pytest.fail(f"Unexpected {type(exc).__name__} importing {module_name}: {exc}")


def test_tool_init_has_no_eager_imports() -> None:
    """Verify src.ai.tools.__init__ does not import domain tool providers.

    Eager imports in __init__.py cause circular dependency chains.
    Only AgentTool and ToolExecutionContext should be re-exported.
    """
    import src.ai.tools  # noqa: F811

    expected = {"AgentTool", "ToolExecutionContext"}
    exported = set(getattr(src.ai.tools, "__all__", []))
    assert exported == expected, (
        f"src.ai.tools.__init__ exports {exported}, expected {expected}. "
        "Domain tool providers must be imported explicitly by the composition root."
    )


def test_all_tool_providers_have_get_tools() -> None:
    """Verify every module in src.ai.tools exposes a class with get_tools()."""
    import src.ai.tools

    pkg_path = Path(src.ai.tools.__file__).parent
    for importer, modname, ispkg in pkgutil.walk_packages(
        [str(pkg_path)], prefix="src.ai.tools."
    ):
        if ispkg or modname == "src.ai.tools.base":
            continue
        module = importlib.import_module(modname)
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and hasattr(attr, "get_tools") and callable(attr.get_tools):
                break
        else:
            pytest.fail(
                f"No class with get_tools() found in {modname}. "
                "Every tool provider module must expose a class implementing get_tools()."
            )
