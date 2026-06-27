#!/usr/bin/env python3
"""Round 268 regression checks for category-owned LLM skill prompt injection."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.download_candidate_adjudicator import DownloadCandidateAdjudicator
from src.ai.task_prompt_guidance import TaskPromptGuidance
from src.core.categories.movie import MovieCategory
from src.core.categories.tv import TvShowCategory


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_tv_main_prompt_includes_prompt_file_skill() -> None:
    guidance = TvShowCategory().build_prompt_guidance("download")
    require("Category prompt file guidance" in guidance, "TV prompt file guidance should be injected")
    require("TV release-name skill" in guidance, "TV prompt should teach release-name handling")
    require("S01E01-06" in guidance, "TV prompt should teach episode-range pack notation")
    require("ITA" in guidance and "ENG" in guidance, "TV prompt should teach compressed language tags")


def test_movie_main_prompt_includes_prompt_file_skill() -> None:
    guidance = MovieCategory().build_prompt_guidance("download")
    require("Category prompt file guidance" in guidance, "movie prompt file guidance should be injected")
    require("Collection handling must be based on payload structure" in guidance, "movie prompt should prevent marketing-word collection inference")


def test_torrent_selection_skill_is_category_owned() -> None:
    tv_guidance = TvShowCategory().build_torrent_selection_guidance()
    movie_guidance = MovieCategory().build_torrent_selection_guidance()
    require("TV torrent-selection skill" in tv_guidance, "TV should expose a torrent-selection skill")
    require("S01E01-E06" in tv_guidance and "S01E01-06" in tv_guidance, "TV skill should teach range pack formats")
    require("iTALiAN" in tv_guidance and "Inglese" in tv_guidance, "TV skill should teach common language tags")
    require("Movie torrent-selection skill" in movie_guidance, "movie should expose a torrent-selection skill")
    require("payload/file evidence" in movie_guidance, "movie skill should keep collection detection evidence-based")


def test_generic_adjudicator_uses_category_guidance_not_tv_rules() -> None:
    bare_prompt = DownloadCandidateAdjudicator()._build_prompt(
        request={"user_prompt": "download season 1", "effective_search": {"language": "Italian"}},
        rows=[],
        category_guidance="",
        review_stage="budget_probe",
        chunk_index=1,
        chunk_count=1,
        context_limit_tokens=8192,
    )
    require("TV full-season requests" not in bare_prompt, "generic adjudicator must not carry TV-specific full-season wording")
    require("ITA+ENG/MULTI" not in bare_prompt, "generic adjudicator must not carry TV-specific language fallback wording")
    require("S01E01-06" not in bare_prompt, "generic adjudicator must not carry TV-specific range examples")

    tv_prompt = DownloadCandidateAdjudicator()._build_prompt(
        request={"user_prompt": "download season 1", "effective_search": {"language": "Italian"}},
        rows=[],
        category_guidance=TvShowCategory().build_torrent_selection_guidance(),
        review_stage="budget_probe",
        chunk_index=1,
        chunk_count=1,
        context_limit_tokens=8192,
    )
    require("TV torrent-selection skill" in tv_prompt, "TV-specific skill should arrive through category guidance")
    require("S01E01-06" in tv_prompt, "TV range examples should be present only when TV guidance is supplied")


def test_generic_download_task_guidance_is_category_neutral() -> None:
    guidance = TaskPromptGuidance.download_task_rules()
    require("TV show title with no specific episode" not in guidance, "generic download prompt must not teach TV-specific bundle rules")
    require("S01E01-06" not in guidance, "generic download prompt must not carry TV range examples")
    require("owning category's language-tag skill" in guidance, "generic download prompt should delegate language semantics to category skills")


def main() -> None:
    test_tv_main_prompt_includes_prompt_file_skill()
    test_movie_main_prompt_includes_prompt_file_skill()
    test_torrent_selection_skill_is_category_owned()
    test_generic_adjudicator_uses_category_guidance_not_tv_rules()
    test_generic_download_task_guidance_is_category_neutral()
    print("round268_category_llm_skill_prompt_tests: OK")


if __name__ == "__main__":
    main()
