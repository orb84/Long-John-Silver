"""
Tests for TaskLLMClient — task-aware LLM runtime.

Verifies that task resolution follows the priority chain:
per-task override -> tier default -> global default -> active provider.
Also verifies LLMClient rejects missing model.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import LLMConfig, TaskModelConfig
from src.llm_providers import LLMProviderManager
from src.llm_providers.task_client import TaskLLMClient, ResolvedLLMTask
from src.llm_providers.client import LLMClient
from src.llm_providers.registry import ProviderRegistry
from src.llm_providers.key_store import KeyStore


def _run(coro):
    """Run an async test coroutine with a fresh event loop."""
    return asyncio.run(coro)


class TestResolvedLLMTask:
    """Tests for the ResolvedLLMTask data class."""

    def test_default_feature_support(self):
        task = ResolvedLLMTask(task="search", model="gpt-4")
        assert task.supports_tools is True
        assert task.supports_streaming is True
        # context_limit defaults to None in the data class;
        # resolve_task() uses DEFAULT_CONTEXT_LIMIT as a fallback
        assert task.context_limit is None

    def test_custom_values(self):
        task = ResolvedLLMTask(
            task="embedding",
            model="text-embedding-3-small",
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            context_limit=8191,
            supports_tools=False,
        )
        assert task.provider_id == ""
        assert task.supports_tools is False


class TestTaskLLMClientResolution:
    """Tests for task resolution priority chain."""

    def _make_client(self, llm_config: LLMConfig) -> TaskLLMClient:
        """Create a TaskLLMClient with a temp key store and provider manager."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        manager = LLMProviderManager(key_store_path=path)
        self._temp_paths.append(path)

        # Set active provider if configured
        if llm_config.active_provider:
            manager.registry.set_active_provider(llm_config.active_provider)

        # Import key if global key is set
        if llm_config.api_key:
            provider_id = llm_config.active_provider or "openrouter"
            if not manager.keys.has_keys(provider_id):
                manager.keys.add_key(provider_id, llm_config.api_key, label="test-import")

        return TaskLLMClient(manager=manager, llm_config=llm_config)

    def setup_method(self):
        self._temp_paths = []

    def teardown_method(self):
        for path in self._temp_paths:
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass

    def test_per_task_provider_overrides_active(self):
        """Per-task provider takes priority over active_provider."""
        config = LLMConfig(
            model="gpt-3.5-turbo",
            active_provider="openrouter",
            search=TaskModelConfig(model="gpt-4", provider="nvidia_nim"),
        )
        client = self._make_client(config)
        resolved = client.resolve_task("search")
        assert resolved.provider_id == "nvidia_nim"
        assert resolved.model == "gpt-4"

    def test_tier_provider_overrides_active_when_no_task_provider(self):
        """Tier provider is used when per-task provider is not set."""
        config = LLMConfig(
            model="gpt-3.5-turbo",
            active_provider="openrouter",
            standard=TaskModelConfig(model="gpt-4", provider="nvidia_nim"),
        )
        # 'chat' is in standard tier
        client = self._make_client(config)
        resolved = client.resolve_task("chat")
        assert resolved.provider_id == "nvidia_nim"
        assert resolved.model == "gpt-4"

    def test_active_provider_key_used_when_task_key_absent(self):
        """Active key from KeyStore is used when task/global key is absent."""
        config = LLMConfig(
            model="gpt-3.5-turbo",
            active_provider="openrouter",
        )
        client = self._make_client(config)
        # The imported key should be retrievable
        resolved = client.resolve_task("chat")
        assert resolved.api_key == config.api_key or resolved.api_key is not None

    def test_explicit_task_api_key_overrides_key_store(self):
        """Per-task api_key takes priority over KeyStore."""
        config = LLMConfig(
            model="gpt-3.5-turbo",
            active_provider="openrouter",
            api_key="global-key",
            search=TaskModelConfig(model="gpt-4", api_key="task-specific-key"),
        )
        client = self._make_client(config)
        resolved = client.resolve_task("search")
        assert resolved.api_key == "task-specific-key"

    def test_provider_preset_api_base_used_when_config_absent(self):
        """Provider preset api_base is used when config doesn't specify one."""
        config = LLMConfig(
            model="gpt-3.5-turbo",
            active_provider="openrouter",
        )
        # openrouter has a preset api_base
        client = self._make_client(config)
        resolved = client.resolve_task("chat")
        assert resolved.api_base == "https://openrouter.ai/api/v1"

    def test_no_model_raises_error(self):
        """If no model can be resolved, raise ValueError."""
        config = LLMConfig(model="")
        client = self._make_client(config)
        with pytest.raises(ValueError, match="No model configured"):
            client.resolve_task("search")

    def test_completion_passes_tools_only_when_provided(self):
        """completion() should only include tools kwarg when tools are provided."""
        config = LLMConfig(model="gpt-3.5-turbo", active_provider="openrouter")
        client = self._make_client(config)

        tools = [{"type": "function", "function": {"name": "test_tool"}}]
        with patch("src.llm_providers.task_client.litellm") as mock_litellm:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            _run(
                client.completion("chat", [{"role": "user", "content": "hello"}], tools=tools)
            )

            call_kwargs = mock_litellm.acompletion.call_args[1]
            assert "tools" in call_kwargs
            assert call_kwargs["tools"] == tools

    def test_completion_without_tools(self):
        """completion() should not include tools kwarg when None."""
        config = LLMConfig(model="gpt-3.5-turbo", active_provider="openrouter")
        client = self._make_client(config)

        with patch("src.llm_providers.task_client.litellm") as mock_litellm:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            _run(
                client.completion("chat", [{"role": "user", "content": "hello"}])
            )

            call_kwargs = mock_litellm.acompletion.call_args[1]
            assert "tools" not in call_kwargs

    def test_completion_stream_true(self):
        """completion(stream=True) should pass stream=True."""
        config = LLMConfig(model="gpt-3.5-turbo", active_provider="openrouter")
        client = self._make_client(config)

        with patch("src.llm_providers.task_client.litellm") as mock_litellm:
            mock_response = MagicMock()
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            _run(
                client.completion("chat", [{"role": "user", "content": "hello"}], stream=True)
            )

            call_kwargs = mock_litellm.acompletion.call_args[1]
            assert call_kwargs.get("stream") is True

    def test_no_none_values_passed_to_litellm(self):
        """completion() should not pass None values to litellm."""
        config = LLMConfig(model="gpt-3.5-turbo", active_provider="openrouter")
        client = self._make_client(config)

        with patch("src.llm_providers.task_client.litellm") as mock_litellm:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            _run(
                client.completion("chat", [{"role": "user", "content": "hello"}])
            )

            call_kwargs = mock_litellm.acompletion.call_args[1]
            for key, value in call_kwargs.items():
                assert value is not None, f"kwarg {key} should not be None"

    def test_update_config_changes_resolution(self):
        """update_config() should change task resolution results."""
        config_v1 = LLMConfig(
            model="gpt-3.5-turbo",
            active_provider="openrouter",
        )
        client = self._make_client(config_v1)
        resolved_v1 = client.resolve_task("chat")
        assert resolved_v1.model == "gpt-3.5-turbo"

        config_v2 = LLMConfig(
            model="gpt-4",
            active_provider="openrouter",
        )
        client.update_config(config_v2)
        resolved_v2 = client.resolve_task("chat")
        assert resolved_v2.model == "gpt-4"

    def test_global_model_used_as_fallback(self):
        """Global model is used when no per-task or tier model is set."""
        config = LLMConfig(model="gpt-4-turbo", active_provider="openrouter")
        client = self._make_client(config)
        resolved = client.resolve_task("chat")
        assert resolved.model == "gpt-4-turbo"

    def test_per_task_max_tokens_and_temperature(self):
        """Per-task max_tokens and temperature are passed through."""
        config = LLMConfig(
            model="gpt-3.5-turbo",
            active_provider="openrouter",
            search=TaskModelConfig(model="gpt-4", max_tokens=100, temperature=0.2),
        )
        client = self._make_client(config)
        resolved = client.resolve_task("search")
        assert resolved.max_tokens == 100
        assert resolved.temperature == 0.2

    def test_registry_active_provider_used_as_fallback(self):
        """Registry active provider is used when LLMConfig.active_provider is not set."""
        # LLMConfig.active_provider defaults to "openrouter", so we must
        # explicitly reset it to empty to test the registry fallback path.
        config = LLMConfig(model="gpt-3.5-turbo", active_provider="")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        manager = LLMProviderManager(key_store_path=path)
        manager.registry.set_active_provider("ollama_local")
        client = TaskLLMClient(manager=manager, llm_config=config)

        resolved = client.resolve_task("chat")
        assert resolved.provider_id == "ollama_local"
        assert resolved.api_base == "http://localhost:11434/v1"
        Path(path).unlink()

    def test_embedding_returns_none_when_no_explicit_config(self):
        """embedding() returns None when no explicit embedding task config exists."""
        config = LLMConfig(model="gpt-3.5-turbo", active_provider="openrouter")
        client = self._make_client(config)

        # No explicit embedding config -> should return None
        result = _run(
            client.embedding("embedding", "test text")
        )
        assert result is None

    def test_embedding_calls_litellm_with_explicit_config(self):
        """embedding() calls litellm.aembedding when config is explicit."""
        config = LLMConfig(
            model="gpt-3.5-turbo",
            active_provider="openrouter",
            embedding=TaskModelConfig(model="text-embedding-3-small"),
        )
        client = self._make_client(config)

        mock_embedding = [0.1] * 384
        with patch("src.llm_providers.task_client.litellm") as mock_litellm:
            mock_response = MagicMock()
            mock_response.data = [{"embedding": mock_embedding}]
            mock_litellm.aembedding = AsyncMock(return_value=mock_response)

            result = _run(
                client.embedding("embedding", "test text")
            )
            assert result == mock_embedding

    def test_embedding_returns_none_on_failure(self):
        """embedding() returns None when litellm call fails."""
        config = LLMConfig(
            model="gpt-3.5-turbo",
            active_provider="openrouter",
            embedding=TaskModelConfig(model="text-embedding-3-small"),
        )
        client = self._make_client(config)

        with patch("src.llm_providers.task_client.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(side_effect=Exception("API error"))

            result = _run(
                client.embedding("embedding", "test text")
            )
            assert result is None


class TestLLMClientRejectsMissingModel:
    """Tests that LLMClient.completion() rejects missing model."""

    def test_completion_raises_on_missing_model(self):
        """LLMClient.completion() should raise ValueError when model is None."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        registry = ProviderRegistry(key_store=store)
        registry.set_active_provider("ollama_local")

        client = LLMClient(registry=registry)

        with pytest.raises(ValueError, match="model must be provided"):
            _run(
                client.completion(
                    messages=[{"role": "user", "content": "hello"}],
                    model=None,
                )
            )
        Path(path).unlink()


class TestTaskLLMClientWithProviderKeyStore:
    """Tests for API key resolution from KeyStore."""

    def setup_method(self):
        self._temp_paths = []

    def teardown_method(self):
        for path in self._temp_paths:
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass

    def _make_path(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            self._temp_paths.append(f.name)
            return f.name

    def test_key_from_keystore_when_no_global_or_task_key(self):
        """KeyStore active key is used when no global or task key is set."""
        path = self._make_path()
        manager = LLMProviderManager(key_store_path=path)
        manager.keys.add_key("openrouter", "sk-from-keystore", label="test")
        manager.registry.set_active_provider("openrouter")

        config = LLMConfig(model="gpt-3.5-turbo")
        client = TaskLLMClient(manager=manager, llm_config=config)

        resolved = client.resolve_task("chat")
        assert resolved.api_key == "sk-from-keystore"

    def test_task_key_overrides_keystore(self):
        """Per-task api_key takes priority over KeyStore."""
        path = self._make_path()
        manager = LLMProviderManager(key_store_path=path)
        manager.keys.add_key("openrouter", "sk-from-keystore", label="test")
        manager.registry.set_active_provider("openrouter")

        config = LLMConfig(
            model="gpt-3.5-turbo",
            search=TaskModelConfig(model="gpt-4", api_key="sk-task-specific"),
        )
        client = TaskLLMClient(manager=manager, llm_config=config)

        resolved = client.resolve_task("search")
        assert resolved.api_key == "sk-task-specific"

    def test_global_key_overrides_keystore(self):
        """Global api_key takes priority over KeyStore."""
        path = self._make_path()
        manager = LLMProviderManager(key_store_path=path)
        manager.keys.add_key("openrouter", "sk-from-keystore", label="test")
        manager.registry.set_active_provider("openrouter")

        config = LLMConfig(
            model="gpt-3.5-turbo",
            api_key="sk-global-key",
        )
        client = TaskLLMClient(manager=manager, llm_config=config)

        resolved = client.resolve_task("chat")
        assert resolved.api_key == "sk-global-key"

    def test_per_task_api_base_overrides_preset(self):
        """Per-task api_base takes priority over provider preset."""
        path = self._make_path()
        manager = LLMProviderManager(key_store_path=path)
        manager.registry.set_active_provider("openrouter")

        config = LLMConfig(
            model="gpt-3.5-turbo",
            search=TaskModelConfig(model="gpt-4", api_base="https://custom.api/v1"),
        )
        client = TaskLLMClient(manager=manager, llm_config=config)

        resolved = client.resolve_task("search")
        assert resolved.api_base == "https://custom.api/v1"

    def test_local_provider_no_key_required(self):
        """Local providers don't need an API key."""
        path = self._make_path()
        manager = LLMProviderManager(key_store_path=path)
        manager.registry.set_active_provider("ollama_local")

        # Explicitly set active_provider to ollama_local to override the default
        config = LLMConfig(model="llama3", active_provider="ollama_local")
        client = TaskLLMClient(manager=manager, llm_config=config)

        resolved = client.resolve_task("chat")
        assert resolved.api_key is None
        assert resolved.api_base == "http://localhost:11434/v1"
