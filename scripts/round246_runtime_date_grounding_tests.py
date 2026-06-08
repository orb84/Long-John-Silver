#!/usr/bin/env python3
"""Round 246 checks for runtime-date grounding in current/future research."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.public_web_requirements import PublicWebEvidencePolicy
from src.ai.runtime_date_grounding import RuntimeDateGrounding
from src.ai.tools.metadata_lookup_support import MetadataLookupRequest
from src.ai.tools.research import MetadataLookupTool
from src.core.categories.tv_web_research import TvWebResearchInterpreter, TvWebResearchMixin
from src.core.domain_models.web_search import CategoryWebResearchInput, WebEvidence, WebEvidenceBundle


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, cond: bool, msg: str) -> None:
        if not cond:
            self.failures.append(msg)

    def finish(self) -> None:
        if self.failures:
            print("Round 246 runtime date grounding failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("Round 246 runtime date grounding tests passed.")


def main() -> None:
    check = Check()

    runtime = RuntimeDateGrounding.runtime_context()
    current_year = int(runtime["current_year"])
    past_year = current_year - 1

    check.ok(PublicWebEvidencePolicy.requires_public_web_evidence("what do we know about the next season of Slow Horses?"), "next-season questions must require public web evidence")
    check.ok(PublicWebEvidencePolicy.requires_public_web_evidence("quando esce la prossima stagione di Slow Horses?"), "Italian next-season wording must require public web evidence")

    request = MetadataLookupRequest.from_arguments({
        "query": "Slow Horses",
        "media_type": "tv",
        "question": "What do we know about the next season?",
    })
    check.ok(not isinstance(request, dict) and request.include_episodes, "metadata lookup should include episode/season date context for next-season questions")

    payload = MetadataLookupTool._success_payload(
        request,
        ["tmdb"],
        [],
        {
            "provider": "tmdb",
            "type": "tv",
            "title": "Example Show",
            "number_of_seasons": 3,
            "seasons": [
                {"season_number": 1, "episode_count": 6, "air_date": f"{past_year - 2}-06-01"},
                {"season_number": 2, "episode_count": 6, "air_date": f"{past_year - 1}-06-01"},
                {"season_number": 3, "episode_count": 6, "air_date": f"{past_year}-07-01"},
            ],
            "last_air_date": f"{past_year}-08-01",
        },
    )
    check.ok(payload.get("requires_public_web_evidence") is True, "metadata payload must require web evidence for next-season questions")
    check.ok("runtime_date_context" in payload, "metadata payload should expose runtime date context")
    warnings = payload.get("date_grounding", {}).get("warnings", [])
    check.ok(any("Do not describe" in w or "already" in w or "past" in w for w in warnings), "metadata grounding should warn that the latest known past season is not upcoming")

    stale = RuntimeDateGrounding.source_freshness_signals(
        f"The upcoming third season premieres in summer {past_year}.",
        query="Example Show next season",
        intent="next season information",
    )
    check.ok(stale["stale_upcoming_years"] == [past_year], "source freshness should flag upcoming wording tied to a past year")
    check.ok(stale["warnings"], "stale upcoming source should produce an explicit warning")

    bundle = WebEvidenceBundle(
        topic="Example Show next season",
        intent="next season information",
        ok=True,
        evidence=[WebEvidence(
            claim="Fetched public source",
            value="Example Show season 3 upcoming",
            source_name="SEO calendar",
            url="https://example.com/show-season-3",
            snippet=f"Example Show TV series upcoming season 3 summer {past_year} episode guide.",
            confidence=0.4,
        )],
    )
    interpretation = TvWebResearchInterpreter().interpret(
        bundle,
        CategoryWebResearchInput(
            category_id="tv",
            item_id="Example Show",
            item_name="Example Show",
            intent="next season information",
            context={"user_query": "Example Show next season"},
        ),
    )
    check.ok(any("stale" in w.casefold() or "past year" in w.casefold() for w in interpretation.warnings), "TV interpreter should surface stale upcoming source warnings")

    tv_prompt = (ROOT / "src/core/categories/prompts/tv.md").read_text(encoding="utf-8")
    check.ok("already-released season" in tv_prompt, "TV prompt should explicitly reject treating past seasons as upcoming")
    tv_contract = TvWebResearchMixin().web_research_contract()
    contract_text = " ".join(str(x) for x in tv_contract.get("llm_research_guidance", []) + tv_contract.get("freshness_policy", []) + tv_contract.get("answer_policy", []))
    check.ok("runtime current date" in contract_text and "season 4" in contract_text, "TV web contract should teach next-season date grounding")

    check.finish()


if __name__ == "__main__":
    main()
