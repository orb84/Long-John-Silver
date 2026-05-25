"""
Unit tests for LJS media category agent tools.
"""

import pytest

from types import SimpleNamespace

from src.ai.tools.categories import (
    GetCategoryDefinitionsTool,
    ConfigureCategoryPropertyTool,
    PreviewCategoryScaffoldTool,
    ApplyCategoryScaffoldTool,
    GetCategoryCreationGuideTool,
    PlanCategoryCreationTool,
    ResearchCategoryServicesTool,
    ResearchCategoryDownloadProfileTool,
)
from src.core.categories.registry import CategoryRegistry
from src.core.categories.tv import TvShowCategory
from src.core.categories.movie import MovieCategory
from src.core.config import SettingsManager
from src.core.models import Settings, ToolExecutionContext


@pytest.fixture
def test_registry() -> CategoryRegistry:
    registry = CategoryRegistry()
    registry.register(TvShowCategory())
    registry.register(MovieCategory())
    return registry


@pytest.fixture
def test_settings_manager(tmp_path) -> SettingsManager:
    config_file = tmp_path / "settings.yaml"
    # Create empty settings file
    config_file.write_text("category_settings: {}")
    sm = SettingsManager(yaml_path=str(config_file))
    return sm


@pytest.mark.asyncio
async def test_get_category_definitions_tool(test_registry, test_settings_manager) -> None:
    tool = GetCategoryDefinitionsTool(
        category_registry=test_registry,
        settings_manager=test_settings_manager
    )
    
    # Ensure parameter schema is correct
    assert tool.parameters() == {"type": "object", "properties": {}, "required": []}
    
    context = ToolExecutionContext(user="test_user", session_id="123", source="chat")
    result = await tool.execute({}, context)
    
    assert "categories" in result
    categories = result["categories"]
    assert len(categories) == 2
    
    # Verify TV category properties are present
    tv_cat = next(c for c in categories if c["category_id"] == "tv")
    assert tv_cat["display_name"] == "TV Shows"
    
    props = {p["name"]: p for p in tv_cat["properties"]}
    assert "library_path" in props
    assert "naming_template" in props
    assert "ended_update_interval_days" in props
    assert props["ended_update_interval_days"]["current_value"] == 180  # Default value


@pytest.mark.asyncio
async def test_configure_category_property_tool(test_registry, test_settings_manager) -> None:
    tool = ConfigureCategoryPropertyTool(
        category_registry=test_registry,
        settings_manager=test_settings_manager
    )
    
    # Ensure parameter schema is correct
    params = tool.parameters()
    assert "category_id" in params["properties"]
    assert "property_name" in params["properties"]
    assert "value" in params["properties"]
    
    context = ToolExecutionContext(user="test_user", session_id="123", source="chat")
    
    # Try updating Ended Update Interval days to 45
    result = await tool.execute({
        "category_id": "tv",
        "property_name": "ended_update_interval_days",
        "value": "45"
    }, context)
    
    assert result.get("status") == "ok"
    
    # Verify setting updated in SettingsManager
    settings = test_settings_manager.settings
    assert settings.category_settings["tv"]["ended_update_interval_days"] == 45
    
    # Try updating library_path
    result = await tool.execute({
        "category_id": "movie",
        "property_name": "library_path",
        "value": "/custom/movies"
    }, context)
    
    assert result.get("status") == "ok"
    assert settings.category_settings["movie"]["library_path"] == "/custom/movies"
    
    # Try configuring an invalid property
    err_result = await tool.execute({
        "category_id": "tv",
        "property_name": "invalid_property",
        "value": "123"
    }, context)
    assert "error" in err_result


@pytest.mark.asyncio
async def test_category_creation_tools_preview_and_approval_gate(test_registry) -> None:
    """Agent-facing category tools preview safely and require explicit apply approval."""
    preview_tool = PreviewCategoryScaffoldTool()
    apply_tool = ApplyCategoryScaffoldTool(category_registry=test_registry)
    context = ToolExecutionContext(user="test_user", session_id="123", source="chat")
    spec = {
        "category_id": "video_games",
        "display_name": "Video Games",
        "description": "Interactive games tracked with platforms, mechanics, and studios.",
        "metadata_providers": ["igdb"],
        "item_types": ["game", "dlc"],
        "taste_dimensions": {"mechanics": 0.75, "platforms": 0.2, "developers": 0.55},
    }

    preview = await preview_tool.execute({"spec": spec}, context)
    assert "src/core/categories/custom/video_games.py" in preview["files"]
    assert "config/category-templates/video_games.yaml" in preview["files"]
    assert preview["validation_issues"] == []
    assert "taste_dimension_weights" in preview["files"]["src/core/categories/custom/video_games.py"]

    receipt = await apply_tool.execute({"spec": spec, "approved": False}, context)
    assert receipt["status"] == "needs_confirmation"


@pytest.mark.asyncio
async def test_category_creation_guide_tool_reads_skill_file() -> None:
    """The agent can read the category creation skill before scaffolding."""
    tool = GetCategoryCreationGuideTool()
    result = await tool.execute({}, ToolExecutionContext(user="test_user", session_id="123", source="chat"))
    assert "Category Creation Guide" in result["content"]
    assert "taste_dimensions" in result["content"]


@pytest.mark.asyncio
async def test_plan_category_creation_returns_questions_and_provider_queries() -> None:
    """Agent category creation starts with guided design questions, not blind scaffolding."""
    tool = PlanCategoryCreationTool()
    result = await tool.execute(
        {"category_name": "Video Games", "known_item_types": ["game", "dlc"]},
        ToolExecutionContext(user="test_user", session_id="123", source="chat"),
    )

    assert result["readiness"] in {"needs_user_answers", "ready_for_provider_research"}
    assert any(q["id"] == "metadata" for q in result["questions"])
    assert any("metadata API" in query for query in result["provider_research_queries"])
    assert result["download_profile_research_queries"] == []
    assert result["minimum_spec_outline"]["category_id"] == "video_games"


class FakeCategorySearchService:
    """Tiny async search stub for category provider research."""

    async def search(self, query: str, max_results: int = 4):
        """Return a stable API-looking result."""
        return SimpleNamespace(
            query=query,
            provider="fake",
            ok=True,
            error=None,
            hits=[
                SimpleNamespace(
                    title="RAWG Video Games Database API",
                    url="https://rawg.io/apidocs",
                    snippet="Video games metadata database API with genres, developers, platforms, and tags.",
                    source="fake",
                )
            ],
        )


@pytest.mark.asyncio
async def test_research_category_services_returns_discovery_source_leads() -> None:
    """Provider research converts web-search hits into reviewable discovery-source leads."""
    tool = ResearchCategoryServicesTool(search_service=FakeCategorySearchService(), settings_manager=object())
    result = await tool.execute(
        {
            "category_name": "Video Games",
            "item_types": ["game", "dlc"],
            "metadata_needs": ["genres", "developers", "platforms", "mechanics"],
        },
        ToolExecutionContext(user="test_user", session_id="123", source="chat"),
    )

    assert result["ok"] is True
    assert result["candidate_discovery_sources"]
    lead = result["candidate_discovery_sources"][0]
    assert lead["provider"] == "rawg"
    assert lead["purpose"] == "metadata_enrichment"
    assert "genres" in lead["taste_metadata_keys"]
    assert lead["requires_review"] is True


class FakeDownloadConventionSearchService:
    """Tiny async search stub for category download-profile research."""

    async def search(self, query: str, max_results: int = 4):
        """Return stable convention-looking results without assuming video rules."""
        return SimpleNamespace(
            query=query,
            provider="fake",
            ok=True,
            error=None,
            hits=[
                SimpleNamespace(
                    title="Audio Book Naming",
                    url="https://example.org/audiobook-naming",
                    snippet="Audiobook filename conventions use author, book name, chapter files, language, format, bitrate and narrator metadata.",
                    source="fake",
                )
            ],
        )


@pytest.mark.asyncio
async def test_research_category_download_profile_returns_convention_leads() -> None:
    """Download-profile research gives the LLM category-specific convention evidence."""
    tool = ResearchCategoryDownloadProfileTool(search_service=FakeDownloadConventionSearchService(), settings_manager=object())
    result = await tool.execute(
        {
            "category_name": "Audio Books",
            "item_types": ["audiobook", "chapter"],
        },
        ToolExecutionContext(user="test_user", session_id="123", source="chat"),
    )

    assert result["ok"] is True
    assert any("torrent naming conventions" in query for query in result["queries"])
    assert result["download_profile_research_leads"]
    lead = result["download_profile_research_leads"][0]
    assert lead["source_domain"] == "example.org"
    assert "bitrate" in lead["evidence_snippet"]
    assert "download_profile_schema_hint" in result
