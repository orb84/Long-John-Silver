"""
Tests for LLM task tier routing in LLMConfig.

Verifies that task model resolution follows the priority chain:
per-task override -> tier default -> global default.
"""

import pytest
from src.core.models import LLMConfig, TaskModelConfig


class TestLLMConfigTierResolution:
    """Tests for tier-based model resolution priority chain."""

    def test_global_default_when_no_overrides(self):
        config = LLMConfig(model="gpt-3.5-turbo")
        # 'search' maps to 'standard' tier — no tier model set -> global default
        assert config.get_model_for_task("search") == "gpt-3.5-turbo"

    def test_tier_default_overrides_global(self):
        config = LLMConfig(
            model="gpt-3.5-turbo",
            standard=TaskModelConfig(model="gpt-4"),
        )
        # search is in 'standard' tier -> should use gpt-4
        assert config.get_model_for_task("search") == "gpt-4"

    def test_per_task_overrides_tier(self):
        config = LLMConfig(
            model="gpt-3.5-turbo",
            standard=TaskModelConfig(model="gpt-4"),
            search=TaskModelConfig(model="gpt-4-turbo"),
        )
        # Per-task 'search' override takes priority over tier
        assert config.get_model_for_task("search") == "gpt-4-turbo"

    def test_per_task_overrides_global(self):
        config = LLMConfig(
            model="gpt-3.5-turbo",
            chat=TaskModelConfig(model="llama3"),
        )
        assert config.get_model_for_task("chat") == "llama3"

    def test_lightweight_tier(self):
        config = LLMConfig(
            model="gpt-4",
            lightweight=TaskModelConfig(model="gpt-3.5-turbo"),
        )
        # summarization is in lightweight tier
        assert config.get_model_for_task("summarization") == "gpt-3.5-turbo"

    def test_heavy_tier(self):
        config = LLMConfig(
            model="gpt-3.5-turbo",
            heavy=TaskModelConfig(model="gpt-4-turbo"),
        )
        # research is in heavy tier
        assert config.get_model_for_task("research") == "gpt-4-turbo"

    def test_api_base_resolution(self):
        config = LLMConfig(
            api_base="https://default.api",
            lightweight=TaskModelConfig(
                model="small-model",
                api_base="https://fast.api",
            ),
        )
        # intent_routing is lightweight -> uses tier api_base
        assert config.get_api_base_for_task("intent_routing") == "https://fast.api"
        # search is standard -> no tier api_base -> global default
        assert config.get_api_base_for_task("search") == "https://default.api"

    def test_api_key_resolution(self):
        config = LLMConfig(
            api_key="global-key",
            download=TaskModelConfig(api_key="task-specific-key"),
        )
        assert config.get_api_key_for_task("download") == "task-specific-key"
        assert config.get_api_key_for_task("search") == "global-key"

    def test_max_tokens_from_task_config(self):
        config = LLMConfig(
            download=TaskModelConfig(max_tokens=50),
        )
        assert config.get_max_tokens_for_task("download") == 50
        assert config.get_max_tokens_for_task("search") is None

    def test_temperature_from_tier_config(self):
        config = LLMConfig(
            lightweight=TaskModelConfig(temperature=0.0),
        )
        assert config.get_temperature_for_task("intent_routing") == 0.0
        assert config.get_temperature_for_task("search") is None

    def test_unknown_task_gets_global_default(self):
        config = LLMConfig(model="gpt-4")
        assert config.get_model_for_task("unknown_task") == "gpt-4"

    def test_download_is_lightweight(self):
        """download task should map to lightweight tier (short JSON output)."""
        config = LLMConfig(
            model="gpt-4",
            lightweight=TaskModelConfig(model="tiny-model"),
        )
        assert config.get_model_for_task("download") == "tiny-model"

    def test_chat_is_standard(self):
        config = LLMConfig(
            model="gpt-3.5-turbo",
            standard=TaskModelConfig(model="mid-model"),
        )
        assert config.get_model_for_task("chat") == "mid-model"

    def test_embedding_never_maps_to_tier(self):
        """Embedding must NOT map to any tier — requires explicit per-task config."""
        config = LLMConfig(
            model="gpt-4",
            lightweight=TaskModelConfig(model="tiny-model"),
        )
        # Even with lightweight tier set, embedding falls back to global default
        # because embedding is explicitly excluded from tier resolution
        assert config.get_model_for_task("embedding") == "gpt-4"

    def test_embedding_per_task_override(self):
        """Embedding uses its per-task override when explicitly set."""
        config = LLMConfig(
            model="gpt-4",
            embedding=TaskModelConfig(model="text-embedding-3-small"),
        )
        assert config.get_model_for_task("embedding") == "text-embedding-3-small"

    def test_has_explicit_task_config_embedding_false_without_override(self):
        """Without an embedding override, has_explicit_task_config returns False."""
        config = LLMConfig(
            model="gpt-4",
            lightweight=TaskModelConfig(model="tiny-model"),
        )
        # Even though lightweight has values, embedding is not in that tier
        assert config.has_explicit_task_config("embedding") is False

    def test_has_explicit_task_config_embedding_true_with_override(self):
        """With an embedding override, has_explicit_task_config returns True."""
        config = LLMConfig(
            model="gpt-4",
            embedding=TaskModelConfig(model="text-embedding-3-small"),
        )
        assert config.has_explicit_task_config("embedding") is True