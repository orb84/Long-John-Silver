"""Round 76 stabilization tests for descriptor-first download handling."""

from __future__ import annotations

from pathlib import Path

from src.ai.tools.download_control import DownloadFilterPredicates, DownloadFilterResolver
from src.ai.tools.download_support import DownloadSnapshotPresenter
from src.core.models import DownloadImportContext, DownloadItem, DownloadPriority, DownloadStatus


def project_root() -> Path:
    """Return the repository root for static guard checks."""
    return Path(__file__).resolve().parents[1]


def _download(download_id: str, stable_key: str, sort_key: list[object], *, priority: DownloadPriority = DownloadPriority.NORMAL) -> DownloadItem:
    """Build a minimal download row with a category-owned unit descriptor."""
    return DownloadItem(
        id=download_id,
        item_name="Descriptor Demo",
        magnet=f"magnet:?xt=urn:btih:{download_id}",
        status=DownloadStatus.QUEUED,
        priority=priority,
        import_context=DownloadImportContext.from_selection(
            category_id="custom",
            item_id="descriptor-demo",
            item_name="Descriptor Demo",
            unit_descriptor={
                "granularity": "chapter",
                "stable_key": stable_key,
                "label": stable_key,
                "sort_key": sort_key,
            },
        ),
    )


def test_download_item_exposes_descriptor_first_unit_identity() -> None:
    """Queue/download consumers should read the descriptor before legacy fields."""
    item = _download("a", "chapter-02", [2])

    assert item.unit_descriptor["stable_key"] == "chapter-02"
    assert item.unit_label == "chapter-02"
    assert item.unit_sort_key < _download("b", "chapter-10", [10]).unit_sort_key
    assert item.stable_unit_identity.endswith(":chapter-02")


def test_download_presenter_orders_by_descriptor_sort_key() -> None:
    """Agent/UI snapshots must not depend on season/episode coordinates."""
    later = _download("later", "chapter-10", [10])
    earlier = _download("earlier", "chapter-02", [2])

    ordered = sorted([later, earlier], key=DownloadSnapshotPresenter.sort_key)

    assert [item.id for item in ordered] == ["earlier", "later"]
    serialized = DownloadSnapshotPresenter.serialize(earlier)
    assert serialized["unit_label"] == "chapter-02"
    assert serialized["unit_descriptor"]["granularity"] == "chapter"


def test_manage_downloads_filters_and_selection_use_descriptor_units() -> None:
    """The LLM download-control tool should support category-neutral unit filters."""
    resolver = DownloadFilterResolver()
    first = _download("first", "chapter-01", [1])
    second = _download("second", "chapter-02", [2])

    assert DownloadFilterPredicates().matches_fields(first, {"unit_key": "chapter-01"}, set())
    assert not DownloadFilterPredicates().matches_fields(first, {"unit_key": "chapter-02"}, set())
    assert resolver.apply_selection([second, first], "next_unit", None) == [first]
    assert resolver.apply_selection([first, second], "latest_unit", None) == [second]


def test_generic_downloader_no_longer_sorts_directly_by_legacy_coordinates() -> None:
    """Downloader queue enforcement should use the model's descriptor sort seam."""
    source = (project_root() / "src/core/downloader.py").read_text(encoding="utf-8")
    assert "self._download_unit_sort_key" in source
    assert "d.season or 9999" not in source
    assert "-(d.season or 0)" not in source
    assert "-(d.episode or 0)" not in source


def test_download_control_public_schema_prefers_unit_descriptor_filters() -> None:
    """Legacy season/episode fields may remain, but new descriptor filters must exist."""
    source = (project_root() / "src/ai/tools/download_control.py").read_text(encoding="utf-8")
    assert '"unit_key"' in source
    assert '"unit_label"' in source
    assert '"next_unit"' in source
    assert "next_episode/latest_episode remain accepted aliases" in source


def test_torrent_selection_prompt_fallback_has_no_builtin_category_branch() -> None:
    """Torrent selection fallback guidance must not special-case built-in categories."""
    source = (project_root() / "src/ai/torrent_selection_prompt.py").read_text(encoding="utf-8")
    assert 'media_category == "tv"' not in source
    assert '"TV show"' not in source
    assert 'media_category in ("tv", "movie", "")' not in source
    assert "per-unit/file size budget" in source
