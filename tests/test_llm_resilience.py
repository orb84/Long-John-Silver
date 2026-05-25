"""
Unit tests verifying LLM response parsing and execution resilience under malformed inputs.
"""

import json
import pytest
from src.core.models import Intent, SearchResult
from src.utils.json_parser import LLMResponseParser
from src.ai.intent_router import IntentRouter
from src.ai.torrent_selection import TorrentSelectionService


class MockMessage:
    """Mock for LLM choices[0].message."""
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class MockChoice:
    """Mock for LLM choices[0]."""
    def __init__(self, content=None, tool_calls=None):
        self.message = MockMessage(content=content, tool_calls=tool_calls)


class MockResponse:
    """Mock for LLM completion response object."""
    def __init__(self, content=None, tool_calls=None):
        self.choices = [MockChoice(content=content, tool_calls=tool_calls)]


class MockLLMClient:
    """Mock LLM client returning configurable responses."""
    def __init__(self, response_val):
        self.response_val = response_val

    async def completion(self, **kwargs):
        return self.response_val


class TestLLMResponseParser:
    """Tests the resilience of the LLMResponseParser utility class."""

    def test_safe_extract_content_object(self):
        # 1. Object style response
        resp = MockResponse(content="  hello world  ")
        assert LLMResponseParser.safe_extract_content(resp) == "hello world"

    def test_safe_extract_content_dict(self):
        # 2. Dict style response
        resp = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "  hello dict  "
                    }
                }
            ]
        }
        assert LLMResponseParser.safe_extract_content(resp) == "hello dict"

    def test_safe_extract_content_simple(self):
        # 3. Simple string response
        assert LLMResponseParser.safe_extract_content("  hello str  ") == "hello str"
        # 4. None response
        assert LLMResponseParser.safe_extract_content(None) == ""

    def test_safe_extract_tool_calls_object(self):
        # 1. Object style tool calls
        mock_calls = [{"id": "call_1", "type": "function"}]
        resp = MockResponse(tool_calls=mock_calls)
        assert LLMResponseParser.safe_extract_tool_calls(resp) == mock_calls

    def test_safe_extract_tool_calls_dict(self):
        # 2. Dict style tool calls
        mock_calls = [{"id": "call_2", "type": "function"}]
        resp = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": mock_calls
                    }
                }
            ]
        }
        assert LLMResponseParser.safe_extract_tool_calls(resp) == mock_calls

    def test_safe_extract_tool_calls_none(self):
        assert LLMResponseParser.safe_extract_tool_calls(None) == []

    def test_extract_json_resilient_fences(self):
        # Markdown block fencing with json label
        raw = "```json\n{\n  \"index\": 2\n}\n```"
        assert LLMResponseParser.extract_json_resilient(raw) == {"index": 2}

        # Plain markdown fencing
        raw = "```\n{\n  \"index\": 3\n}\n```"
        assert LLMResponseParser.extract_json_resilient(raw) == {"index": 3}

    def test_extract_json_resilient_conversational(self):
        # Conversational filler text around JSON
        raw = "Sure, I can help! The selection is:\n{\n  \"index\": 1\n}\nLet me know if you need more!"
        assert LLMResponseParser.extract_json_resilient(raw) == {"index": 1}

    def test_extract_json_resilient_single_quotes(self):
        # JSON with single quotes
        raw = "{'index': 4, 'name': 'test'}"
        assert LLMResponseParser.extract_json_resilient(raw) == {"index": 4, "name": "test"}


class TestIntentRoutingResilience:
    """Tests the resilience of LLM-based intent routing classification."""

    @pytest.mark.asyncio
    async def test_route_with_llm_quotes_and_dots(self):
        # LLM returning intent wrapped in quotes/dots should match correctly
        mock_resp = MockResponse(content='  "DOWNLOAD."  ')
        client = MockLLMClient(mock_resp)
        router = IntentRouter(llm_client=client)
        
        intent, confidence = await router._route_with_llm("download stranger things")
        assert intent == Intent.DOWNLOAD
        assert confidence == 0.8

    @pytest.mark.asyncio
    async def test_route_with_llm_conversational(self):
        # LLM returning intent within conversational wrapper should match correctly
        mock_resp = MockResponse(content='The category classified is SEARCH because ratings are queried.')
        client = MockLLMClient(mock_resp)
        router = IntentRouter(llm_client=client)
        
        intent, confidence = await router._route_with_llm("what are ratings")
        assert intent == Intent.SEARCH
        assert confidence == 0.8

    @pytest.mark.asyncio
    async def test_route_with_llm_dict_format(self):
        # Dict response style should be parsed correctly without crashing
        mock_resp = {
            "choices": [
                {
                    "message": {
                        "content": "CONFIG."
                    }
                }
            ]
        }
        client = MockLLMClient(mock_resp)
        router = IntentRouter(llm_client=client)
        
        intent, confidence = await router._route_with_llm("change setting")
        assert intent == Intent.CONFIG
        assert confidence == 0.8


class TestTorrentSelectionResilience:
    """Tests the resilience of the TorrentSelectionService."""

    @pytest.mark.asyncio
    async def test_select_best_markdown_json(self):
        # LLM returning markdown fenced JSON
        mock_resp = MockResponse(content="```json\n{\n  \"index\": 1\n}\n```")
        client = MockLLMClient(mock_resp)
        from src.utils.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker("torrent_select_test")
        
        service = TorrentSelectionService(llm_client=client, circuit_breaker=breaker)
        candidates = [
            SearchResult(title="Show.S01E01.1080p", magnet="magnet:?1", size="1.0 GB", seeders=10, source="BTDigg", url="http://url1", quality_score=0.9),
            SearchResult(title="Show.S01E01.720p", magnet="magnet:?2", size="500 MB", seeders=5, source="BTDigg", url="http://url2", quality_score=0.8),
        ]
        
        res = await service.select_best(
            item_name="Show",
            episodes="S01E01",
            results=candidates,
            preferred_language="english",
            media_category="tv",
        )
        assert res is not None
        assert res["title"] == "Show.S01E01.720p"

    @pytest.mark.asyncio
    async def test_select_best_regex_index_fallback(self):
        # LLM returning invalid JSON but containing "index": 0 in raw text
        mock_resp = MockResponse(content="Invalid JSON output: index: 0 is the best one.")
        client = MockLLMClient(mock_resp)
        from src.utils.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker("torrent_select_test")
        
        service = TorrentSelectionService(llm_client=client, circuit_breaker=breaker)
        candidates = [
            SearchResult(title="Show.S01E01.1080p", magnet="magnet:?1", size="1.0 GB", seeders=10, source="BTDigg", url="http://url1", quality_score=0.9),
            SearchResult(title="Show.S01E01.720p", magnet="magnet:?2", size="500 MB", seeders=5, source="BTDigg", url="http://url2", quality_score=0.8),
        ]
        
        res = await service.select_best(
            item_name="Show",
            episodes="S01E01",
            results=candidates,
            preferred_language="english",
            media_category="tv",
        )
        assert res is not None
        assert res["title"] == "Show.S01E01.1080p"

    @pytest.mark.asyncio
    async def test_select_best_unparsable_safe_default(self):
        # LLM returning completely garbage content with no index whatsoever
        mock_resp = MockResponse(content="This torrent has excellent seeders.")
        client = MockLLMClient(mock_resp)
        from src.utils.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker("torrent_select_test")
        
        service = TorrentSelectionService(llm_client=client, circuit_breaker=breaker)
        candidates = [
            SearchResult(title="Show.S01E01.1080p", magnet="magnet:?1", size="1.0 GB", seeders=10, source="BTDigg", url="http://url1", quality_score=0.9),
            SearchResult(title="Show.S01E01.720p", magnet="magnet:?2", size="500 MB", seeders=5, source="BTDigg", url="http://url2", quality_score=0.8),
        ]
        
        res = await service.select_best(
            item_name="Show",
            episodes="S01E01",
            results=candidates,
            preferred_language="english",
            media_category="tv",
        )
        # Should safely default to candidate 0 (Show.S01E01.1080p)
        assert res is not None
        assert res["title"] == "Show.S01E01.1080p"


class TestToolExecutorResilience:
    """Tests the resilience of ToolCallExecutor when tools return complex or un-serializable objects."""

    @pytest.mark.asyncio
    async def test_tool_call_executor_datetime_resilience(self):
        from datetime import datetime
        from src.ai.tool_executor import ToolCallExecutor
        from unittest.mock import AsyncMock, MagicMock
        
        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value={
            "status": "success",
            "timestamp": datetime(2026, 5, 18, 4, 2, 2)
        })
        
        executor = ToolCallExecutor(mock_registry)
        
        msg, summary = await executor.execute_tool_call(
            name="test_tool",
            arguments_raw='{"arg": 1}',
            tool_call_id="call_123",
            allowed_tool_names={"test_tool"}
        )
        
        assert msg is not None
        assert msg["tool_call_id"] == "call_123"
        # Verify it serialized successfully and contains the datetime string representation
        assert "2026-05-18" in msg["content"]


@pytest.mark.asyncio
async def test_task_llm_client_litellm_retry_on_502():
    from unittest.mock import MagicMock, patch
    from src.llm_providers.task_client import TaskLLMClient
    from src.core.models import LLMConfig
    
    # Mock LLMConfig and LLMProviderManager
    manager = MagicMock()
    config = LLMConfig()
    config.model = "test-model"
    config.active_provider = "test-provider"
    
    client = TaskLLMClient(manager, config)
    
    call_count = 0
    async def mock_acompleter(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("API error (502 Bad Gateway)")
        return "Success response"
    
    # We patch litellm.acompletion
    with patch("litellm.acompletion", new=mock_acompleter):
        # We also mock resolve_task to return a dummy ResolvedLLMTask
        dummy_task = MagicMock()
        dummy_task.provider_id = "test-provider"
        dummy_task.model = "test-model"
        dummy_task.api_base = None
        dummy_task.api_key = "test-key"
        dummy_task.temperature = 0.5
        dummy_task.max_tokens = 100
        client.resolve_task = MagicMock(return_value=dummy_task)
        
        # Execute the completion
        result = await client.completion("chat", messages=[{"role": "user", "content": "hi"}])
        
        # Assert the mock completer was called twice!
        assert call_count == 2
        assert result == "Success response"


@pytest.mark.asyncio
async def test_task_llm_client_nvidia_nim_retry_on_502():
    from unittest.mock import MagicMock, AsyncMock, patch
    from src.llm_providers.task_client import TaskLLMClient
    from src.core.models import LLMConfig
    import httpx
    
    # Mock LLMConfig and LLMProviderManager
    manager = MagicMock()
    config = LLMConfig()
    config.model = "openai/test-model"
    config.active_provider = "nvidia_nim"
    
    client = TaskLLMClient(manager, config)
    
    # Mock resolve_task to return a dummy ResolvedLLMTask
    dummy_task = MagicMock()
    dummy_task.provider_id = "nvidia_nim"
    dummy_task.model = "openai/test-model"
    dummy_task.api_base = "http://nvidia.nim"
    dummy_task.api_key = "test-key"
    dummy_task.temperature = 0.5
    dummy_task.max_tokens = 100
    client.resolve_task = MagicMock(return_value=dummy_task)
    
    # We patch httpx.AsyncClient.post
    with patch("httpx.AsyncClient.post") as mock_post:
        # Create a mock 502 response and a mock 200 response
        mock_502_resp = MagicMock()
        mock_502_resp.status_code = 502
        mock_502_resp.text = "Bad Gateway"
        
        mock_200_resp = MagicMock()
        mock_200_resp.status_code = 200
        mock_200_resp.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Success response content",
                    "tool_calls": []
                },
                "finish_reason": "stop"
            }]
        }
        
        mock_post.side_effect = [
            mock_502_resp,
            mock_200_resp
        ]
        
        # Execute direct nvidia completion
        result = await client.completion("chat", messages=[{"role": "user", "content": "hi"}], stream=False)
        
        # Assert post was called twice!
        assert mock_post.call_count == 2
        assert result.choices[0].message.content == "Success response content"
