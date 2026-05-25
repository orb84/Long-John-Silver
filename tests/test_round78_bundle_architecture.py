"""Round 78 bundle/pack architecture guards."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.core.bundle_download import BundleDownloadHandler
from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory
from src.core.models import SearchResult


def project_root() -> Path:
    """Return the repository root for static source checks."""
    return Path(__file__).resolve().parents[1]


def test_retired_season_pack_core_service_is_removed() -> None:
    """Selective bundled torrents must be handled by the generic bundle service."""
    assert not (project_root() / "src/core/season_pack.py").exists()
    smoke = (project_root() / "tests/test_import_smoke.py").read_text(encoding="utf-8")
    assert "src.core.bundle_download" in smoke
    assert "src.core.season_pack" not in smoke


def test_bundle_download_handler_has_no_builtin_tv_parser_branch() -> None:
    """The generic handler must never parse files as a fixed built-in category."""
    source = (project_root() / "src/core/bundle_download.py").read_text(encoding="utf-8")
    assert 'parse(file_name, "tv")' not in source
    assert 'parse_name(Path(file_path).stem)' not in source or 'category.parse_name' in source
    assert "torrent_file_matches_target" in source
    assert "unit_descriptor_from_file" in source


def test_tv_episode_request_can_accept_containing_season_bundle() -> None:
    """A requested episode can be satisfied by a same-season pack/range candidate."""
    tv = TvShowCategory()
    item = SimpleNamespace(key="Example Show", last_season=None, last_episode=None)
    same_season_pack = SearchResult(
        title="Example.Show.S01.Complete.1080p.WEB-DL-GROUP",
        magnet="magnet:?xt=urn:btih:abc",
        size_bytes=20 * 1024 * 1024 * 1024,
    )
    wrong_season_pack = SearchResult(
        title="Example.Show.S02.Complete.1080p.WEB-DL-GROUP",
        magnet="magnet:?xt=urn:btih:def",
    )

    assert tv.validate_search_result_for_request(same_season_pack, item, "S01E05") is True
    assert tv.validate_search_result_for_request(wrong_season_pack, item, "S01E05") is False


def test_bundle_handler_uses_category_context_for_tv_size_estimates() -> None:
    """Per-unit estimates come from category hooks, not generic season math."""
    handler = BundleDownloadHandler()
    context = handler.describe_candidate("Example.Show.S01.Complete.1080p.WEB-DL", category_id="tv")
    assert context and context["bundle_type"] == "tv_bundle"
    estimate = handler.compute_per_unit_limit_mb(
        24 * 1024 * 1024 * 1024,
        "Example.Show.S01.Complete.1080p.WEB-DL",
        category_id="tv",
        bundle_context=context,
    )
    assert estimate is not None and estimate > 0


def test_movie_category_can_select_requested_file_from_collection() -> None:
    """Flat categories can still select one useful payload from a collection."""
    movie = MovieCategory()
    item = SimpleNamespace(key="The Matrix", display_name="The Matrix", year=1999)
    result = SearchResult(title="The.Matrix.Collection.1999.2003.1080p.BluRay", magnet="m:1")
    target = movie.unit_descriptor_from_search_result(result, item, None)
    parsed = movie.parse_name("The.Matrix.1999.1080p.BluRay.mkv")
    file_descriptor = movie.unit_descriptor_from_file("The.Matrix.1999.1080p.BluRay.mkv", parsed)

    assert movie.torrent_bundle_candidate_context(result, item=item) is not None
    assert movie.torrent_file_matches_target(
        file_path="The.Matrix.1999.1080p.BluRay.mkv",
        parsed=parsed,
        file_descriptor=file_descriptor,
        target_descriptors=[target],
    ) is True


def test_smart_quality_no_longer_rejects_large_totals_before_llm() -> None:
    """Large total size can be legitimate for bundles or high-quality releases."""
    from src.core.smart_quality import SmartQualityInferrer
    from src.core.models import QualityProfile, SizeLimitMode

    inferrer = SmartQualityInferrer()
    profile = QualityProfile(max_file_size_mb=4000, size_limit_mode=SizeLimitMode.FILE_SIZE)
    result = SearchResult(title="Large.Bundle.Collection.1080p", magnet="m:1", size_bytes=80 * 1024 * 1024 * 1024)
    accepted, reason = inferrer.should_accept_result(result, profile)

    assert accepted is True
    assert "LLM" in reason or "category" in reason
