"""Tests for the LLM Providers library."""

import tempfile
import json
from pathlib import Path
from src.llm_providers import LLMProviderManager
from src.llm_providers.key_store import KeyStore
from src.llm_providers.registry import ProviderRegistry
from src.llm_providers.catalog import ModelCatalog, _safe_float
from src.llm_providers.models import (
    ProviderType, ModelInfo, PricingInfo, ContextInfo, APIKeyEntry,
)


class TestKeyStore:
    def test_add_and_retrieve_key(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        entry = store.add_key("openrouter", "sk-test-123", label="personal")
        assert entry.provider_id == "openrouter"
        assert entry.label == "personal"
        assert entry.is_active is True
        active = store.get_active_key("openrouter")
        assert active.key == "sk-test-123"
        Path(path).unlink()

    def test_multiple_keys_first_is_active(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        store.add_key("openrouter", "sk-key1", label="personal")
        store.add_key("openrouter", "sk-key2", label="work")
        keys = store.list_keys("openrouter")
        assert len(keys) == 2
        assert keys[0].is_active is True
        Path(path).unlink()

    def test_switch_active_key(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        store.add_key("openrouter", "sk-key1", label="personal")
        entry2 = store.add_key("openrouter", "sk-key2", label="work")
        store.set_active_key("openrouter", entry2.id)
        active = store.get_active_key("openrouter")
        assert active.label == "work"
        Path(path).unlink()

    def test_remove_key_promotes_next(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        e1 = store.add_key("openrouter", "sk-key1")
        e2 = store.add_key("openrouter", "sk-key2")
        store.remove_key("openrouter", e1.id)
        active = store.get_active_key("openrouter")
        assert active.id == e2.id


class TestProviderRegistry:
    def test_builtin_presets(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        registry = ProviderRegistry(key_store=store)
        presets = registry.list_presets()
        ids = [p.id for p in presets]
        assert "openrouter" in ids
        assert "nvidia_nim" in ids
        assert "ollama_cloud" in ids
        assert "ollama_local" in ids
        assert "lm_studio" in ids
        Path(path).unlink()

    def test_is_ready_with_key(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        store.add_key("openrouter", "sk-test")
        registry = ProviderRegistry(key_store=store)
        assert registry.is_provider_ready("openrouter") is True
        assert registry.is_provider_ready("nvidia_nim") is False
        Path(path).unlink()

    def test_local_providers_ready_without_key(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        registry = ProviderRegistry(key_store=store)
        assert registry.is_provider_ready("ollama_local") is True
        assert registry.is_provider_ready("lm_studio") is True
        Path(path).unlink()

    def test_register_custom_provider(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        registry = ProviderRegistry(key_store=store)
        from src.llm_providers.models import ProviderPreset
        custom = ProviderPreset(id="my_hosted", name="My Hosted", api_base="https://llm.example.com/v1")
        registry.register_custom(custom)
        assert registry.get_preset("my_hosted") is not None
        assert registry.get_preset("my_hosted").provider_type == ProviderType.CUSTOM
        Path(path).unlink()


class TestModelCatalog:
    def test_parse_openrouter_models(self):
        data = {
            "data": [
                {
                    "id": "openai/gpt-4o",
                    "name": "GPT-4o",
                    "owned_by": "openai",
                    "pricing": {"prompt": "5", "completion": "15"},
                    "context_length": 128000,
                    "supports_tool_calling": True,
                    "supports_vision": True,
                },
                {
                    "id": "meta-llama/llama-3.1-70b",
                    "name": "Llama 3.1 70B",
                    "pricing": {"prompt": "0.52", "completion": "0.75"},
                    "context_length": 131072,
                },
            ]
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        catalog = ModelCatalog(key_store=store)
        models = catalog._parse_models_response("openrouter", data)
        assert len(models) == 2
        gpt4o = [m for m in models if m.id == "openai/gpt-4o"][0]
        assert gpt4o.pricing.prompt_per_million == 5.0
        assert gpt4o.context.max_context_tokens == 128000
        assert gpt4o.context.supports_tools is True
        assert gpt4o.context.supports_vision is True
        Path(path).unlink()

    def test_parse_models_without_pricing(self):
        data = {
            "data": [
                {"id": "local-model", "name": "Local Model", "context_length": 8192},
            ]
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = KeyStore(store_path=path)
        catalog = ModelCatalog(key_store=store)
        models = catalog._parse_models_response("ollama_local", data)
        assert len(models) == 1
        assert models[0].pricing.prompt_per_million is None
        Path(path).unlink()


class TestSafeFloat:
    def test_valid_numbers(self):
        assert _safe_float("5.0") == 5.0
        assert _safe_float(3) == 3.0
        assert _safe_float("0.52") == 0.52

    def test_invalid_values(self):
        assert _safe_float(None) is None
        assert _safe_float("free") is None
        assert _safe_float("") is None


class TestLLMProviderManager:
    def test_facade_wiring(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        mgr = LLMProviderManager(key_store_path=path)
        assert mgr.keys is not None
        assert mgr.registry is not None
        assert mgr.catalog is not None
        assert mgr.client is not None
        assert len(mgr.list_providers()) == 5
        Path(path).unlink()

    def test_list_ready_providers(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        mgr = LLMProviderManager(key_store_path=path)
        ready = mgr.list_ready_providers()
        ready_ids = [p.id for p in ready]
        assert "ollama_local" in ready_ids
        assert "lm_studio" in ready_ids
        assert "openrouter" not in ready_ids  # no key set
        Path(path).unlink()