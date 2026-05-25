"""Helpers for recovering from bare JSON tool-argument model output.

Some local/OpenAI-compatible providers occasionally emit a JSON arguments
object as assistant text instead of using the function/tool-call channel.  The
runtime should treat that as a malformed tool call, execute it when it maps
unambiguously to an allowed read-only tool, and never show the raw JSON blob to
the user.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BareToolCall:
    """A recovered tool call inferred from a bare JSON argument object."""

    name: str
    arguments: dict[str, Any]
    call_id: str = "bare_json_recovered_call"


class BareToolCallDetector:
    """Detect bare JSON argument blobs that should have been tool calls.

    The detector is intentionally conservative.  It only recovers all-JSON
    responses and only routes schemas that are unambiguous for tools already
    allowed in the current intent.  Legitimate prose or mixed markdown is left
    untouched.
    """

    _FENCE_RE = re.compile(r"^```(?:json)?\s*(?P<body>\{.*\})\s*```$", re.IGNORECASE | re.DOTALL)

    @classmethod
    def from_text(cls, text: str | None, allowed_tool_names: set[str]) -> BareToolCall | None:
        """Return a recovered tool call when ``text`` is a bare args object."""
        payload = cls._parse_json_object(text)
        if not payload:
            return None
        name = cls._explicit_tool_name(payload, allowed_tool_names) or cls._infer_tool_name(payload, allowed_tool_names)
        if not name:
            return None
        arguments = cls._arguments_payload(payload)
        if not isinstance(arguments, dict) or not arguments:
            return None
        if not cls._has_valid_required_shape(name, arguments):
            return None
        return BareToolCall(name=name, arguments=arguments)

    @staticmethod
    def looks_like_json_prefix(text: str | None) -> bool:
        """Return whether streamed text should be buffered before display."""
        stripped = (text or "").lstrip()
        if not stripped:
            return True
        return stripped.startswith("{") or stripped.startswith("```json") or stripped.startswith("```")

    @classmethod
    def _parse_json_object(cls, text: str | None) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            return None
        fence = cls._FENCE_RE.match(raw)
        if fence:
            raw = fence.group("body").strip()
        if not raw.startswith("{") or not raw.endswith("}"):
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _explicit_tool_name(payload: dict[str, Any], allowed_tool_names: set[str]) -> str | None:
        for key in ("tool", "tool_name", "name", "function"):
            value = payload.get(key)
            if isinstance(value, str) and value in allowed_tool_names:
                return value
        return None

    @staticmethod
    def _arguments_payload(payload: dict[str, Any]) -> dict[str, Any]:
        args = payload.get("arguments")
        if isinstance(args, dict):
            return args
        # Remove explicit routing metadata when the model used a wrapper shape.
        return {k: v for k, v in payload.items() if k not in {"tool", "tool_name", "name", "function"}}

    @classmethod
    def _infer_tool_name(cls, payload: dict[str, Any], allowed_tool_names: set[str]) -> str | None:
        args = cls._arguments_payload(payload)
        keys = set(args.keys())
        if "url" in keys and "browser_open" in allowed_tool_names:
            return "browser_open"
        metadata_specific_keys = {"title", "media_type", "service", "tmdb_id", "tvmaze_id", "season", "include_episodes"}
        if keys & metadata_specific_keys and "metadata_lookup" in allowed_tool_names:
            return "metadata_lookup"
        if "max_results" in keys and "web_search" in allowed_tool_names:
            return "web_search"
        if "query" in keys and "web_search" in allowed_tool_names:
            return "web_search"
        if "query" in keys and "metadata_lookup" in allowed_tool_names:
            return "metadata_lookup"
        return None

    @staticmethod
    def _has_valid_required_shape(name: str, arguments: dict[str, Any]) -> bool:
        if name == "web_search":
            return isinstance(arguments.get("query"), str) and bool(arguments["query"].strip())
        if name == "metadata_lookup":
            query = arguments.get("query") or arguments.get("title") or arguments.get("name")
            return isinstance(query, str) and bool(query.strip())
        if name == "browser_open":
            return isinstance(arguments.get("url"), str) and bool(arguments["url"].strip())
        return False
