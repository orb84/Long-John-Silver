"""Round 244 regressions for fresh DOWNLOAD recovery and stale context isolation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.download_tool_recovery import DownloadToolRecovery


class Round244ToolRecoveryTests:
    def __init__(self) -> None:
        self.root = ROOT

    def run(self) -> None:
        self._recovery_extracts_literal_title_language_and_season()
        self._fresh_download_context_filter_is_wired()
        self._streaming_recovery_is_wired_before_user_fallback()
        self._batch_suppression_is_wired()
        print("round244_tool_recovery_and_context_tests: PASS")

    def _recovery_extracts_literal_title_language_and_season(self) -> None:
        args = DownloadToolRecovery.build_search_media_torrents_args(
            user_prompt="Can you please grab me A Knight of the Seven Kingdoms in italian ? Full first season",
            active_category_id="tv",
        )
        assert args is not None
        assert args["name"] == "A Knight of the Seven Kingdoms in italian ? Full first season"
        assert args["category_id"] == "tv"
        assert "language" not in args
        assert "season" not in args
        assert "search_scope" not in args

    def _fresh_download_context_filter_is_wired(self) -> None:
        assistant = (self.root / "src/ai/assistant.py").read_text(encoding="utf-8")
        binding = (self.root / "src/ai/conversation_binding.py").read_text(encoding="utf-8")
        assert "fresh_download_request = DownloadContextPolicy.should_suppress_pending_candidates" in assistant
        assert "fresh_download_request=fresh_download_request" in assistant
        assert "if fresh_download_request:" in binding
        assert "suppressed {} older context message(s)" in binding

    def _streaming_recovery_is_wired_before_user_fallback(self) -> None:
        src = (self.root / "src/ai/streaming_agent_loop.py").read_text(encoding="utf-8")
        first_recovery = src.index("suppressing prose and executing recovery search_media_torrents")
        fallback = src.index("I could not get the tool backend to run a search")
        assert first_recovery < fallback
        assert "DownloadToolRecovery.build_search_media_torrents_args" in src
        assert "forced_download_search_attempted" in src

    def _batch_suppression_is_wired(self) -> None:
        src = (self.root / "src/ai/tools/scheduling.py").read_text(encoding="utf-8")
        assert "_should_suppress_batch_recommendation" in src
        assert "batch_recommendation = None" in src
        assert "suppressing deterministic batch_recommendation" in src


if __name__ == "__main__":
    Round244ToolRecoveryTests().run()
