"""Tests for LLM-first torrent selection guardrails."""

from src.ai.torrent_selection import TorrentSelectionService
from src.core.models import SearchResult


def test_pre_filter_keeps_language_ambiguity_for_llm() -> None:
    """Wrong-looking language tags are prompt evidence, not hard regex rejects."""
    service = TorrentSelectionService()
    results = [
        SearchResult(title="Example.Show.S01E01.HINDI.1080p.WEB-DL", magnet="magnet:?xt=urn:btih:1", seeders=5),
        SearchResult(title="Example.Show.S01E01.English.1080p.WEB-DL", magnet="magnet:?xt=urn:btih:2", seeders=3),
    ]

    filtered = service.deterministic_pre_filter(results, preferred_language="English")

    assert {r.title for r in filtered} == {r.title for r in results}


def test_pre_filter_still_removes_non_queueable_candidates() -> None:
    """Missing magnets and obvious theater captures stay deterministic rejects."""
    service = TorrentSelectionService()
    results = [
        SearchResult(title="Example.Show.S01E01.1080p.WEB-DL", magnet="magnet:?xt=urn:btih:1"),
        SearchResult(title="Example.Show.S01E01.CAMRip", magnet="magnet:?xt=urn:btih:2"),
        SearchResult(title="Example.Show.S01E01.720p.WEBRip", magnet=None),
    ]

    filtered = service.deterministic_pre_filter(results, require_magnet=True)

    assert [r.title for r in filtered] == ["Example.Show.S01E01.1080p.WEB-DL"]
