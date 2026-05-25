"""Round 70 regression checks for suggestion explanations and diagnostics."""

from pathlib import Path

from src.core.models import SuggestedActionRecord
from src.core.suggestion_support import enrich_suggestion_record, summarize_suggestion_for_agent


def test_suggestion_metadata_is_exposed_for_ui_and_agent() -> None:
    record = SuggestedActionRecord(
        category_id="tv",
        item_id="Pluribus",
        item_name="Pluribus",
        action_type="download_next",
        title="Download Next: S01E09",
        description="fallback",
        metadata_json=(
            '{"explanation":"Provider says S01E09 aired and the local ledger lacks it",'
            '"confidence":"high","evidence":{"provider_episode_count":9,'
            '"downloaded_episode_count":8,"missing_episode_count":1}}'
        ),
    )

    enriched = enrich_suggestion_record(record)
    assert enriched["explanation"].startswith("Provider says")
    assert enriched["confidence"] == "high"
    assert enriched["evidence"]["provider_episode_count"] == 9

    summary = summarize_suggestion_for_agent(record)
    assert summary["item_name"] == "Pluribus"
    assert summary["evidence"]["downloaded_episode_count"] == 8


def test_tv_suggestion_workflow_has_alias_and_progress_diagnostics() -> None:
    source = Path("src/core/categories/workflows/tv_suggestions.py").read_text()
    assert "canonical_item_key" in source
    assert "progress_backfill" in source
    assert "TV suggestion audit" in source
    assert "all aired episodes appear missing" in source


def test_agent_and_ui_have_suggestion_explanation_paths() -> None:
    assert "suggestions_list" in Path("src/core/actions/registration.py").read_text()
    assert "suggestions_list" in Path("src/ai/tool_policy.py").read_text()
    assert "Pending suggestions:" in Path("src/ai/memory_composer.py").read_text()
    js = Path("src/web/static/js/components/suggestionsPanel.js").read_text()
    assert "suggestion-why" in js
    assert "suggestion-evidence-pills" in js
