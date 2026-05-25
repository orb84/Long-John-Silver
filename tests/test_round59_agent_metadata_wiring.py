import pytest

from src.ai.tools.research import MetadataLookupTool, ResearchToolProvider
from src.core.models import ToolExecutionContext


class FakeSettings:
    tmdb_api_key = "live-key"


class FakeSettingsManager:
    settings = FakeSettings()


class FakeHydratedTMDB:
    def __init__(self, api_key):
        self.api_key = api_key
        self.queries = []

    async def search(self, query, media_type="multi"):
        self.queries.append((query, media_type))
        return [{"id": 87917, "title": "For All Mankind", "type": "tv", "year": "2019"}]

    async def get_tv_details(self, tv_id):
        return {
            "id": tv_id,
            "name": "For All Mankind",
            "cast": [{"name": "Joel Kinnaman", "character": "Ed Baldwin"}],
            "number_of_seasons": 5,
        }

    async def get_movie_details(self, movie_id):
        return {}

    async def get_tv_season_details(self, tv_id, season):
        return {"season_number": season, "cast": [{"name": "Joel Kinnaman", "character": "Ed Baldwin"}]}


class FakeTVMaze:
    async def search(self, query):
        return [{"id": 1, "name": "For All Mankind"}]

    async def get_show_details(self, show_id):
        return {"id": show_id, "name": "For All Mankind", "genres": ["Drama"], "status": "Running"}

    async def get_episode_list(self, show_id, season=None):
        return []


@pytest.mark.asyncio
async def test_metadata_lookup_hydrates_tmdb_from_current_settings(monkeypatch):
    import src.integrations.tmdb as tmdb_module

    monkeypatch.setattr(tmdb_module, "TMDBClient", FakeHydratedTMDB)
    tool = MetadataLookupTool(tmdb_client=None, tvmaze_client=None, settings_manager=FakeSettingsManager())

    result = await tool.execute(
        {
            "query": "For All Mankind",
            "media_type": "tv",
            "service": "tmdb",
            "season": 5,
            "question": "Who are the lead actors in season 5?",
        },
        ToolExecutionContext(),
    )

    assert result["ok"] is True
    assert result["best"]["provider"] == "tmdb"
    assert result["answer_hints"]["top_billed_actor"] == "Joel Kinnaman"


@pytest.mark.asyncio
async def test_metadata_lookup_treats_planner_tmdb_choice_as_preference_not_dead_end():
    tool = MetadataLookupTool(tmdb_client=None, tvmaze_client=FakeTVMaze(), settings_manager=None)

    result = await tool.execute(
        {
            "query": "For All Mankind",
            "media_type": "tv",
            "service": "tmdb",
            "season": 5,
        },
        ToolExecutionContext(),
    )

    assert result["ok"] is True
    assert result["best"]["provider"] == "tvmaze"
    assert "tmdb" in result["services_tried"]
    assert "tvmaze" in result["services_tried"]


def test_research_tool_provider_logs_and_wires_settings_and_database(caplog):
    provider = ResearchToolProvider(
        tmdb_client=None,
        tvmaze_client=FakeTVMaze(),
        settings_manager=FakeSettingsManager(),
        database=object(),
    )

    tools = provider.get_tools()

    assert any(tool.name == "metadata_lookup" for tool in tools)


def test_initial_setup_awaits_integration_save_before_advancing():
    setup_js = open("src/web/static/js/pages/setup.js", encoding="utf-8").read()
    assert "return APIClient.post('/api/settings/integrations', intelData);" in setup_js
    assert "APIClient.post('/api/settings/integrations', intelData).catch" not in setup_js
