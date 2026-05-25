import pytest

from src.ai.tools.research import MetadataLookupTool
from src.core.models import ToolExecutionContext


class FakeTMDBClient:
    def __init__(self):
        self.queries = []

    async def search(self, query, media_type="multi"):
        self.queries.append((query, media_type))
        if query.lower() == "twin peaks":
            return [{"id": 123, "title": "Twin Peaks", "type": "tv", "year": "1990"}]
        return []

    async def get_tv_details(self, tv_id):
        return {
            "id": tv_id,
            "name": "Twin Peaks",
            "title": "Twin Peaks",
            "cast": [{"name": "Kyle MacLachlan", "character": "Special Agent Dale Cooper"}],
            "number_of_seasons": 3,
            "number_of_episodes": 48,
            "status": "Ended",
        }

    async def get_movie_details(self, movie_id):
        return {"id": movie_id, "title": "Unused", "cast": []}

    async def get_tv_season_details(self, tv_id, season_number):
        return {"season_number": season_number, "episodes": []}


@pytest.mark.asyncio
async def test_metadata_lookup_answers_media_fact_from_structured_metadata():
    tool = MetadataLookupTool(tmdb_client=FakeTMDBClient(), tvmaze_client=None)

    result = await tool.execute(
        {
            "query": "who is the lead actor in the twin peaks tv series",
            "media_type": "tv",
            "question": "Who is the lead actor in the Twin Peaks TV series?",
        },
        ToolExecutionContext(),
    )

    assert result["ok"] is True
    assert result["best"]["title"] == "Twin Peaks"
    assert result["answer_hints"]["top_billed_actor"] == "Kyle MacLachlan"


def test_metadata_lookup_builds_title_fallback_queries():
    assert "twin peaks" in [q.lower() for q in MetadataLookupTool._candidate_queries(
        "who is the lead actor in the twin peaks tv series"
    )]

class ExplodingTVMazeClient:
    async def search(self, query):
        raise AssertionError("TVMaze should not be called when TMDB already answers the media fact")


@pytest.mark.asyncio
async def test_metadata_lookup_does_not_hit_fallbacks_after_complete_tmdb_answer():
    tool = MetadataLookupTool(tmdb_client=FakeTMDBClient(), tvmaze_client=ExplodingTVMazeClient())

    result = await tool.execute(
        {
            "query": "who is the lead actor in the twin peaks tv series",
            "media_type": "tv",
            "question": "Who is the lead actor in the Twin Peaks TV series?",
        },
        ToolExecutionContext(),
    )

    assert result["ok"] is True
    assert result["services_tried"] == ["tmdb"]
