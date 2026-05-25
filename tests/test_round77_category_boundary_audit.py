"""Round 77 stabilization guards for category-owned boundary seams."""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """Return the repository root for static source checks."""
    return Path(__file__).resolve().parents[1]


def test_scan_bitrate_estimates_are_category_owned() -> None:
    """The generic scanner must not assume episode/movie runtimes."""
    scanner = (project_root() / "src/utils/library_scanner.py").read_text(encoding="utf-8")
    contract = (project_root() / "src/core/categories/base_contract.py").read_text(encoding="utf-8")
    tv = (project_root() / "src/core/categories/tv.py").read_text(encoding="utf-8")
    movie = (project_root() / "src/core/categories/movie.py").read_text(encoding="utf-8")

    assert "55 * 60" not in scanner
    assert "TV episodes" not in scanner
    assert "scan_average_bitrate_kbps" in scanner
    assert "def scan_average_bitrate_kbps" in contract
    assert "def scan_average_bitrate_kbps" in tv
    assert "def scan_average_bitrate_kbps" in movie


def test_search_aggregator_has_no_builtin_default_category() -> None:
    """Generic provider aggregation should not silently default to TV."""
    source = (project_root() / "src/search/aggregator.py").read_text(encoding="utf-8")

    assert 'category: str = "tv"' not in source
    assert "category: str | None = None" in source
    assert "if not category:" in source


def test_torznab_filtering_is_configured_not_hardcoded() -> None:
    """Torznab provider category filters should be external configuration."""
    source = (project_root() / "src/search/torznab.py").read_text(encoding="utf-8")

    assert 'category == "tv"' not in source
    assert 'category == "movie"' not in source
    assert "category_filters" in source
    assert "_normalize_category_filters" in source


def test_rss_monitor_delegates_unit_labels_to_categories() -> None:
    """RSS matching should not parse every feed title as a TV release."""
    source = (project_root() / "src/search/rss_monitor.py").read_text(encoding="utf-8")
    contract = (project_root() / "src/core/categories/base_contract.py").read_text(encoding="utf-8")
    tv = (project_root() / "src/core/categories/tv.py").read_text(encoding="utf-8")

    assert 'parse(item.title, "tv")' not in source
    assert "rss_unit_label_from_parsed" in source
    assert "def rss_unit_label_from_parsed" in contract
    assert "def rss_unit_label_from_parsed" in tv


def test_frontend_category_actions_do_not_default_to_tv() -> None:
    """Generic dashboard controls should use the selected category ID."""
    library_template = (project_root() / "src/web/templates/library.html").read_text(encoding="utf-8")
    helm_panel = (project_root() / "src/web/static/js/components/helmPanel.js").read_text(encoding="utf-8")
    booty_panel = (project_root() / "src/web/static/js/components/bootyPanel.js").read_text(encoding="utf-8")
    pages_router = (project_root() / "src/web/routers/pages.py").read_text(encoding="utf-8")

    assert "CategoryApiClient.updateItem('tv'" not in library_template
    assert "CategoryApiClient.pauseItem('tv'" not in library_template
    assert "CategoryApiClient.resumeItem('tv'" not in library_template
    assert "CategoryApiClient.listItems('tv'" not in helm_panel
    assert "categoryEl.value : 'tv'" not in booty_panel
    assert 'getattr(item, "item_type", "tv")' not in pages_router
