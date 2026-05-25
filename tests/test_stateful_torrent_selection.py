"""Tests for stateful candidate caching and index-based download selection."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from src.ai.tools.downloads import SearchTorrentsTool, QueueDownloadTool
from src.ai.tools.scheduling import SearchMediaTorrentsTool
from src.core.models import ToolExecutionContext
from src.core.models import SearchResult
from src.ai.assistant import AIAssistant

class DummySearchAggregator:
    def __init__(self, results):
        self._results = results

    async def search(self, query):
        return self._results


@pytest.mark.asyncio
async def test_search_torrents_caches_and_strips_magnets(db):
    # Setup mock search results
    results = [
        SearchResult(title="Interstellar.2014.1080p", magnet="magnet:?xt=urn:btih:interstellar1080p", size="2.5 GB", seeders=50, source="mock"),
        SearchResult(title="Interstellar.2014.720p", magnet="magnet:?xt=urn:btih:interstellar720p", size="1.2 GB", seeders=10, source="mock"),
    ]
    aggregator = DummySearchAggregator(results)
    
    tool = SearchTorrentsTool(search_aggregator=aggregator, database=db)
    context = ToolExecutionContext(session_id="test_session_123")
    
    # Execute the search tool
    output = await tool.execute({"query": "Interstellar"}, context)
    
    # 1. Verify output returned to LLM contains stable IDs but NOT magnets
    assert output["result_set_id"]
    candidates = output["candidates"]
    assert len(candidates) == 2
    assert candidates[0]["index"] == 1
    assert candidates[0]["title"] == "Interstellar.2014.1080p"
    assert "magnet" not in candidates[0]
    assert candidates[0]["candidate_id"]
    assert candidates[0]["result_set_id"] == output["result_set_id"]
    assert candidates[0]["seeders"] == 50
    
    assert candidates[1]["index"] == 2
    assert "magnet" not in candidates[1]
    
    # 2. Verify results are cached in the database preferences
    cache_json = await db.system.get_preference("last_options_test_session_123")
    assert cache_json is not None
    cache_data = json.loads(cache_json)
    assert cache_data["name"] == "Interstellar"
    assert len(cache_data["candidates"]) == 2
    assert cache_data["result_set_id"] == output["result_set_id"]
    assert cache_data["candidates"][0]["index"] == 1
    assert cache_data["candidates"][0]["candidate_id"]
    assert cache_data["candidates"][0]["magnet"] == "magnet:?xt=urn:btih:interstellar1080p"


@pytest.mark.asyncio
async def test_queue_download_resolves_option_index(db):
    # Seed the cache in the database manually
    cache_data = {
        "name": "Interstellar",
        "season": None,
        "episode": None,
        "candidates": [
            {"index": 1, "title": "Interstellar.2014.1080p", "magnet": "magnet:?xt=urn:btih:interstellar1080p", "size": "2.5 GB", "seeders": 50, "source": "mock"},
            {"index": 2, "title": "Interstellar.2014.720p", "magnet": "magnet:?xt=urn:btih:interstellar720p", "size": "1.2 GB", "seeders": 10, "source": "mock"},
        ]
    }
    await db.system.set_preference("last_options_test_session_123", json.dumps(cache_data))
    
    # Mock scheduler
    scheduler = MagicMock()
    scheduler.queue_download = AsyncMock(return_value={"status": "queued", "download_id": "dl_123"})
    
    tool = QueueDownloadTool(scheduler=scheduler, database=db)
    context = ToolExecutionContext(session_id="test_session_123")
    
    # Execute the queue tool using option_index
    result = await tool.execute({"option_index": 2, "name": "Interstellar"}, context)
    
    # Verify the scheduler was called with the resolved magnet link
    assert result == {"status": "queued", "download_id": "dl_123"}
    scheduler.queue_download.assert_called_once_with(
        name="Interstellar",
        magnet="magnet:?xt=urn:btih:interstellar720p",
        season=None,
        episode=None,
        category_id="",
        estimated_size_bytes=None,
    )


@pytest.mark.asyncio
async def test_search_media_torrents_caches_and_strips(db):
    # Mock scheduler
    scheduler = MagicMock()
    scheduler._db = db
    scheduler.search_media_torrents = AsyncMock(return_value={
        "query": "Breaking Bad S01E01",
        "language": "English",
        "candidates": [
            {"title": "Breaking.Bad.S01E01.1080p", "magnet": "magnet:?xt=urn:btih:bb1080p", "size": "2 GB", "seeders": 100, "source": "mock", "quality_score": 10},
        ]
    })
    
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    context = ToolExecutionContext(session_id="test_session_456")
    
    # Execute SearchMediaTorrentsTool
    res = await tool.execute({"name": "Breaking Bad", "season": 1, "episode": 1}, context)
    
    # Verify magnet is stripped from the return value
    assert len(res["candidates"]) == 1
    assert res["result_set_id"]
    assert res["candidates"][0]["index"] == 1
    assert res["candidates"][0]["candidate_id"]
    assert res["candidates"][0]["result_set_id"] == res["result_set_id"]
    assert "magnet" not in res["candidates"][0]
    
    # Verify cached data exists in database preference
    cache_json = await db.system.get_preference("last_options_test_session_456")
    assert cache_json is not None
    cache_data = json.loads(cache_json)
    assert cache_data["name"] == "Breaking Bad"
    assert cache_data["season"] == 1
    assert cache_data["episode"] == 1
    assert cache_data["result_set_id"] == res["result_set_id"]
    assert cache_data["candidates"][0]["candidate_id"]
    assert cache_data["candidates"][0]["magnet"] == "magnet:?xt=urn:btih:bb1080p"


def test_assistant_history_planner_formatting():
    assistant = AIAssistant.__new__(AIAssistant)
    
    # Mock messages
    tool_msg_dict = {
        "role": "tool",
        "name": "search_media_torrents",
        "content": json.dumps({
            "query": "Interstellar",
            "candidates": [
                {"index": 1, "title": "Interstellar.2014.1080p", "size": "2.5 GB", "seeders": 50},
                {"index": 2, "title": "Interstellar.2014.720p", "size": "1.2 GB", "seeders": 10},
            ]
        })
    }
    
    tool_list_msg_dict = {
        "role": "tool",
        "name": "search_torrents",
        "content": json.dumps({
            "query": "Inception",
            "result_set_id": "rs123",
            "candidates": [
                {"index": 1, "candidate_id": "cand123", "result_set_id": "rs123", "title": "Inception.1080p", "size": "2.1 GB", "seeders": 45},
            ]
        })
    }
    
    prior_msgs = [
        {"role": "user", "content": "Search Interstellar please"},
        {"role": "assistant", "content": "__TOOL_CALLS__:[{}]"},
        tool_msg_dict,
        {"role": "user", "content": "Ok download option 1"},
    ]
    
    history_parts = assistant._format_history_parts(prior_msgs)
    
    # Verify formatted results for the planner
    assert len(history_parts) == 4
    assert history_parts[0] == "USER: Search Interstellar please"
    assert history_parts[1] == "ASSISTANT: [Tool Calls]"
    assert "TOOL (search_media_torrents) query: Interstellar" in history_parts[2]
    assert "- Option 1: Interstellar.2014.1080p (Size: 2.5 GB, Seeders: 50" in history_parts[2]
    assert "- Option 2: Interstellar.2014.720p (Size: 1.2 GB, Seeders: 10" in history_parts[2]
    assert history_parts[3] == "USER: Ok download option 1"
    
    # Test dict format from search_torrents with stable IDs
    list_parts = assistant._format_history_parts([tool_list_msg_dict])
    assert len(list_parts) == 1
    assert "TOOL (search_torrents) query: Inception" in list_parts[0]
    assert "candidate_id: cand123" in list_parts[0]
    assert "- Option 1: Inception.1080p (Size: 2.1 GB, Seeders: 45" in list_parts[0]
