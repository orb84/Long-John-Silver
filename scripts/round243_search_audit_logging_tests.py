"""Round 243 regression checks for useful torrent-search audit logging."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import SearchResult
from src.utils.detailed_logger import SearchLogger, ThreadSafeFileWriter


class Round243SearchAuditLoggingTests:
    """Tiny deterministic checks for search-audit visibility contracts."""

    def __init__(self) -> None:
        self.root = ROOT

    async def run(self) -> None:
        await self._search_logger_writes_jsonl_without_magnets()
        self._tv_search_filter_audit_is_present()
        self._workspace_audit_is_present()
        print("round243_search_audit_logging_tests: PASS")

    async def _search_logger_writes_jsonl_without_magnets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            logger = SearchLogger(
                ThreadSafeFileWriter(tmp_path / "searches.log"),
                ThreadSafeFileWriter(tmp_path / "searches.jsonl"),
            )
            result = SearchResult(
                title="A Knight of the Seven Kingdoms S01e01-06 [1080p Ita Eng Spa]",
                magnet="magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567&passkey=SECRET",
                source="Jackett:test",
                size="3.5 GB",
                seeders=100,
            )
            await logger.log_search(
                query="A Knight of the Seven Kingdoms S01E01-06 Ita",
                category="tv",
                active_providers=["JackettSearch"],
                total_raw=1,
                unique_deduped=1,
                quality_filtered=1,
                raw_results=[result],
                deduped_results=[result],
                accepted_results=[result],
                ranked_results=[result],
                fallback_used=False,
            )
            raw = (tmp_path / "searches.jsonl").read_text(encoding="utf-8").strip()
            record = json.loads(raw)
            assert record["event"] == "torrent_search_query"
            assert record["counts"]["raw"] == 1
            row = record["stages"]["raw"]["rows"][0]
            assert row["magnet_present"] is True
            assert row["info_hash_prefix"] == "0123456789abcdef"[:16]
            assert "SECRET" not in raw
            assert "magnet:?" not in raw

    def _tv_search_filter_audit_is_present(self) -> None:
        src = (self.root / "src/core/categories/tv_agent.py").read_text(encoding="utf-8")
        assert "TV_SEARCH_FILTER_AUDIT" in src
        assert "accept_structural_season_pack" in src
        assert "reject_not_detected_as_requested_season_pack" in src
        assert "phase=\"season_pack_ladder\"" in src
        assert "phase=\"exact_label_ladder\"" in src

    def _workspace_audit_is_present(self) -> None:
        src = (self.root / "src/ai/tools/scheduling.py").read_text(encoding="utf-8")
        assert "SEARCH_MEDIA_TORRENTS_WORKSPACE_AUDIT" in src
        assert "quality_choice_policy" in src
        assert "llm_candidate_review_status" in src
        assert "logged_extra_recommended_or_quality_options" in src


if __name__ == "__main__":
    asyncio.run(Round243SearchAuditLoggingTests().run())
