#!/usr/bin/env python3
"""Round 250 regressions for log-driven torrent language/scope recovery.

Covers the For All Mankind session failure where an English-configured install
surfaced ITA+ENG range-pack rows, re-searched after a quality follow-up, and
presented wrong logical units instead of a queueable Season 1 path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from types import MethodType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.tools.scheduling import _annotate_selection_policy, _batch_candidate_score
from src.core.categories.tv import TvShowCategory


@dataclass
class FakeItem:
    key: str


@dataclass
class FakeResult:
    title: str
    magnet: str = "magnet:?xt=urn:btih:deadbeef"
    source: str = "test"
    seeders: int = 1
    size: str = "1"
    size_bytes: int = 1
    quality_score: float = 0.0


def test_english_extra_language_pack_triggers_episode_fallback() -> None:
    tv = TvShowCategory()
    rows = [FakeResult("For All ManKind S01e01-10 (720p Ita Eng)")]
    assert tv._pack_results_need_individual_fallback(rows, language="English") is True
    assert tv._pack_results_need_individual_fallback(rows, language="Italian") is False


def test_unknown_or_english_only_pack_does_not_trigger_fallback() -> None:
    tv = TvShowCategory()
    assert tv._pack_results_need_individual_fallback(
        [FakeResult("For All Mankind S01E01-10 1080p WEB-DL x265")],
        language="English",
    ) is False
    assert tv._pack_results_need_individual_fallback(
        [FakeResult("For All Mankind S01E01-10 1080p ENG WEB-DL x265")],
        language="English",
    ) is False


def test_english_extra_language_candidates_are_marked_as_fallback() -> None:
    extra = {
        "candidate_id": "dual-pack",
        "title": "For All ManKind S01e01-10 (720p Ita Eng)",
        "languages": ["Italian", "English"],
        "seeders": 36,
    }
    clean = {
        "candidate_id": "unknown-scene",
        "title": "For All Mankind S01E01 Red Moon 1080p WEB-DL x265-Scene",
        "languages": [],
        "seeders": 88,
    }
    candidates = [extra, clean]
    _annotate_selection_policy(candidates, preferred_language="English")
    assert any("extra non-preferred audio" in warning for warning in extra["selection_warnings"])
    assert not any("extra non-preferred audio" in warning for warning in clean["selection_warnings"])


def test_batch_score_keeps_english_dual_audio_behind_clean_english() -> None:
    clean = {"languages": ["English"], "resolution": "1080p", "seeders": 10, "size_bytes": 2_000_000_000}
    dual = {"languages": ["Italian", "English"], "resolution": "1080p", "seeders": 1000, "size_bytes": 2_000_000_000}
    assert _batch_candidate_score(clean, "English") > _batch_candidate_score(dual, "English")


def test_season_search_suppresses_extra_language_pack_when_episode_fallback_exists() -> None:
    tv = TvShowCategory()
    pack = FakeResult("For All ManKind S01e01-10 (720p Ita Eng)")
    episode_result = FakeResult("For All Mankind S01E01 Red Moon 1080p WEB-DL x265-Scene")

    async def fake_pack_queries(self, item, season, *, language, context, summary_suffix=None):
        return [pack], "pack summary"

    async def fake_episode_labels(self, item, season, context):
        return ["S01E01"]

    async def fake_run_labels(self, item, labels, *, language, season, episode, context, summary_suffix=None):
        return [episode_result], "episode fallback summary"

    async def fake_rank(self, results, *, item, language=None, season=None, episode=None, context=None):
        return list(results)

    tv._run_agent_pack_queries = MethodType(fake_pack_queries, tv)
    tv._episode_fallback_labels_for_agent = MethodType(fake_episode_labels, tv)
    tv._run_agent_labels = MethodType(fake_run_labels, tv)
    tv.rank_agent_search_results = MethodType(fake_rank, tv)

    results, summary = asyncio.run(tv.search_agent_candidates(
        FakeItem("For All Mankind"),
        season=1,
        episode=None,
        language="English",
        search_scope="bundle_preferred",
        context=object(),
    ))
    assert results == [episode_result]
    assert "suppressed" in summary
    assert "English" in summary


def test_prompt_guidance_no_longer_tells_llm_ita_eng_is_equivalent() -> None:
    guidance = (ROOT / "src/ai/task_prompt_guidance.py").read_text(encoding="utf-8")
    assistant = (ROOT / "src/ai/assistant.py").read_text(encoding="utf-8")
    adjudicator = (ROOT / "src/ai/download_candidate_adjudicator.py").read_text(encoding="utf-8")
    assert "prefer English-only or language-unknown scene releases over ITA+ENG/MULTI rows" in guidance
    assert "do not let ITA+ENG/MULTI rows outrank" in assistant
    assert "ITA+ENG/MULTI is a fallback only" in adjudicator
    assert "ITA+ENG is acceptable" not in adjudicator


def test_quality_choice_policy_is_persisted_for_followups() -> None:
    scheduling = (ROOT / "src/ai/tools/scheduling.py").read_text(encoding="utf-8")
    assert '"quality_choice_policy": quality_choice_policy' in scheduling


def main() -> None:
    test_english_extra_language_pack_triggers_episode_fallback()
    test_unknown_or_english_only_pack_does_not_trigger_fallback()
    test_english_extra_language_candidates_are_marked_as_fallback()
    test_batch_score_keeps_english_dual_audio_behind_clean_english()
    test_season_search_suppresses_extra_language_pack_when_episode_fallback_exists()
    test_prompt_guidance_no_longer_tells_llm_ita_eng_is_equivalent()
    test_quality_choice_policy_is_persisted_for_followups()
    print("round250_torrent_language_scope_session_recovery_tests: OK")


if __name__ == "__main__":
    main()
