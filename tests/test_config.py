"""Tests for configuration management."""

import tempfile
import warnings

import yaml
from pathlib import Path
from src.core.config import SettingsManager
from src.core.models import Settings, TvShowItem, LLMConfig, QualityProfile


class TestSettingsManager:
    def test_load_defaults(self):
        """Settings should load with default values."""
        manager = SettingsManager(yaml_path="/nonexistent/path")
        settings = manager.load()
        assert settings.llm.model == "gpt-3.5-turbo"
        assert settings.download_dir == "./downloads"

    def test_round_trip(self):
        """Settings should survive save/load cycle without data loss."""
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.dump({"llm": {"model": "test-model"}, "tracked_items": {"items": []}}, f)
            path = f.name

        manager = SettingsManager(yaml_path=path)
        settings = manager.load()
        settings.llm.model = "round-trip-model"
        settings.tracked_items.items.append(TvShowItem(key="Test Show", language="English"))
        manager.save(settings)

        # Reload from disk
        manager2 = SettingsManager(yaml_path=path)
        reloaded = manager2.load()
        assert reloaded.llm.model == "round-trip-model"
        assert len(reloaded.tracked_items.items) == 1
        assert reloaded.tracked_items.items[0].key == "Test Show"

        Path(path).unlink()

    def test_embeddings_yaml_loads_as_model_and_saves_without_serializer_warning(self):
        """Embedding YAML settings remain typed and do not trigger Pydantic serializer warnings."""
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.safe_dump({
                "embeddings": {
                    "enabled": True,
                    "provider": "builtin",
                    "builtin_model": "sentence-transformers/all-MiniLM-L6-v2",
                    "dimension": 384,
                    "cache_dir": "./data/embedding_models",
                    "auto_download": True,
                    "warmup_on_startup": True,
                    "max_model_size_mb": 150,
                }
            }, f)
            path = f.name

        manager = SettingsManager(yaml_path=path)
        settings = manager.load()

        assert hasattr(settings.embeddings, "model_dump")
        assert settings.embeddings.provider == "builtin"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            payload = settings.model_dump(mode="json")
        assert isinstance(payload["embeddings"], dict)
        assert not [warning for warning in caught if "Pydantic serializer warnings" in str(warning.message)]

        manager.save(settings)
        manager2 = SettingsManager(yaml_path=path)
        reloaded = manager2.load()
        assert hasattr(reloaded.embeddings, "model_dump")
        assert reloaded.embeddings.max_model_size_mb == 150

        Path(path).unlink()

    def test_bandwidth_schedules_yaml_loads_as_models(self):
        """Bandwidth schedule YAML entries remain typed for later serialization."""
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.safe_dump({
                "bandwidth_schedules": [
                    {"days": [0, 1], "start_time": "20:00", "end_time": "23:00", "max_download_kbps": 1024}
                ]
            }, f)
            path = f.name

        manager = SettingsManager(yaml_path=path)
        settings = manager.load()

        assert len(settings.bandwidth_schedules) == 1
        assert hasattr(settings.bandwidth_schedules[0], "model_dump")
        assert settings.bandwidth_schedules[0].max_download_kbps == 1024

        Path(path).unlink()

    def test_library_paths_are_category_keyed(self):
        """Library paths are stored in category_settings for registry-driven access."""
        settings = Settings(
            category_settings={"movie": {"library_path": "/mnt/movies"}, "tv": {"library_path": "/mnt/tv"}},
        )
        assert settings.category_settings["movie"]["library_path"] == "/mnt/movies"
        assert settings.category_settings["tv"]["library_path"] == "/mnt/tv"


class TestTvShowItem:
    def test_show_with_quality(self):
        show = TvShowItem(key="Test", quality=QualityProfile(preferred_resolution="4k"))
        assert show.quality.preferred_resolution == "4k"

    def test_show_defaults(self):
        show = TvShowItem(key="Test")
        assert show.language == "English"
        assert show.check_interval_days == 7
        assert show.enabled is True
