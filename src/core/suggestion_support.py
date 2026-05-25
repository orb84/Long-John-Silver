"""Shared helpers for suggestion explanations and diagnostics.

Suggestions are used by three surfaces at once: the scheduler/lifecycle ledger,
the web UI, and the assistant tool layer.  Keeping explanation parsing here means
category workflows can store rich evidence in ``metadata_json`` while callers get
one stable, human-readable shape instead of repeatedly hand-parsing JSON blobs.
"""

from __future__ import annotations

import json
from typing import Any


def load_suggestion_metadata(value: Any) -> dict[str, Any]:
    """Return a safe metadata dictionary from a suggestion metadata payload."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def enrich_suggestion_record(suggestion: Any) -> dict[str, Any]:
    """Return a UI/agent friendly dict for a persisted suggestion.

    The database model intentionally stays small and generic. Category-specific
    explanation, evidence, and confidence live in metadata_json so the core app
    does not need a TV/movie/game-specific schema migration every time a category
    learns a better diagnostic. This helper surfaces the common explanation keys.
    """
    if hasattr(suggestion, "model_dump"):
        data = suggestion.model_dump(mode="json")
    elif isinstance(suggestion, dict):
        data = dict(suggestion)
    else:
        data = {
            "id": getattr(suggestion, "id", None),
            "category_id": getattr(suggestion, "category_id", ""),
            "item_id": getattr(suggestion, "item_id", ""),
            "item_name": getattr(suggestion, "item_name", ""),
            "action_type": getattr(suggestion, "action_type", ""),
            "title": getattr(suggestion, "title", ""),
            "description": getattr(suggestion, "description", ""),
            "metadata_json": getattr(suggestion, "metadata_json", "{}"),
        }
    metadata = load_suggestion_metadata(data.get("metadata_json"))
    explanation = str(
        metadata.get("explanation")
        or metadata.get("rationale")
        or metadata.get("human_reason")
        or data.get("description")
        or ""
    )
    evidence = metadata.get("evidence") if isinstance(metadata.get("evidence"), dict) else {}
    if "metadata" not in data:
        data["metadata"] = metadata
    data["explanation"] = explanation
    data["evidence"] = evidence
    data["confidence"] = metadata.get("confidence") or evidence.get("confidence") or ""
    data["diagnostics"] = metadata.get("diagnostics") if isinstance(metadata.get("diagnostics"), dict) else {}
    return data


def summarize_suggestion_for_agent(suggestion: Any) -> dict[str, Any]:
    """Return a compact suggestion summary suitable for LLM prompt/tool output."""
    data = enrich_suggestion_record(suggestion)
    evidence = data.get("evidence") or {}
    return {
        "id": data.get("id"),
        "category_id": data.get("category_id"),
        "item_id": data.get("item_id"),
        "item_name": data.get("item_name"),
        "action_type": data.get("action_type"),
        "title": data.get("title"),
        "description": data.get("description"),
        "explanation": data.get("explanation"),
        "confidence": data.get("confidence"),
        "priority": data.get("priority"),
        "status": data.get("status"),
        "evidence": {
            key: value
            for key, value in evidence.items()
            if key in {
                "provider", "provider_episode_count", "downloaded_episode_count",
                "missing_episode_count", "aliases_checked", "matched_item_ids",
                "library_evidence_source", "quality_preference", "current_quality",
                "target_quality", "reason_code",
            }
        },
    }
