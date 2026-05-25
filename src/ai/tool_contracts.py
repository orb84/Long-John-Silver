"""Runtime contracts for LLM tool calls.

The assistant may reason freely, but concrete tool execution must be bounded by
registered schemas.  This module provides small dependency-free validation so a
model can no longer crash a turn by inventing tool names, placeholder paths, or
argument shapes that the application never promised.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_PLACEHOLDER_RE = re.compile(
    r"(\$\{[^}]+\}|\{[A-Za-z0-9_-]+\.[^}]+\}|<[^>]*(?:from|result|url|id|candidate|season)[^>]*>)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ToolValidationResult:
    """Result of validating one model-authored tool call."""

    ok: bool
    arguments: dict[str, Any]
    error_code: str | None = None
    message: str | None = None
    recoverable: bool = True

    def error_payload(self, tool_name: str) -> dict[str, Any]:
        """Return a compact typed error payload for the LLM tool loop."""
        return {
            "ok": False,
            "tool": tool_name,
            "error_code": self.error_code or "TOOL_ARGUMENT_INVALID",
            "recoverable": self.recoverable,
            "error": self.message or "Tool arguments did not match the declared schema.",
            "next_actions": [
                "Call a read/search tool to obtain a result handle or candidate_id.",
                "Omit unknown dynamic fields and let the category-owned tool resolve them.",
                "Ask the user only if required information cannot be inferred from context or tools.",
            ],
        }


class ToolContractValidator:
    """Validate LLM-authored calls against a registered tool schema.

    This is intentionally conservative and dependency-free.  It does not try to
    implement full JSON Schema; it covers the schema features this project uses
    for agent tools: required properties, primitive types, enums, arrays, and
    objects.  It also blocks planner placeholders in natural tool calls because
    placeholders are an internal plan-executor compatibility detail, not a valid
    agent-facing API.
    """

    def validate(self, *, tool_name: str, arguments: dict[str, Any], schema: dict[str, Any] | None) -> ToolValidationResult:
        """Validate arguments and return a possibly coerced argument dict."""
        args = dict(arguments or {})
        placeholder_path = self._find_placeholder(args)
        if placeholder_path:
            return ToolValidationResult(
                ok=False,
                arguments=args,
                error_code="UNRESOLVED_MODEL_PLACEHOLDER",
                message=(
                    f"Argument {placeholder_path} contains a model-authored placeholder. "
                    "Use literal values, stable result handles, result_set_id/candidate_id, "
                    "or omit dynamic fields so the owning category can resolve them."
                ),
            )

        if not schema or not isinstance(schema, dict):
            return ToolValidationResult(ok=True, arguments=args)
        parameters = schema.get("parameters") if "parameters" in schema else schema
        if not isinstance(parameters, dict):
            return ToolValidationResult(ok=True, arguments=args)
        properties = parameters.get("properties") or {}
        required = parameters.get("required") or []
        if not isinstance(properties, dict):
            return ToolValidationResult(ok=True, arguments=args)

        for name in required:
            if name not in args or args.get(name) in (None, ""):
                return ToolValidationResult(
                    ok=False,
                    arguments=args,
                    error_code="MISSING_REQUIRED_ARGUMENT",
                    message=f"Missing required argument '{name}' for tool '{tool_name}'.",
                )

        for name, value in list(args.items()):
            prop = properties.get(name)
            if not isinstance(prop, dict):
                continue
            enum_values = prop.get("enum")
            if enum_values and value not in enum_values:
                return ToolValidationResult(
                    ok=False,
                    arguments=args,
                    error_code="INVALID_ENUM_ARGUMENT",
                    message=f"Argument '{name}' must be one of {enum_values}; got {value!r}.",
                )
            expected = prop.get("type")
            coerced, error = self._coerce_type(name, value, expected)
            if error:
                return ToolValidationResult(
                    ok=False,
                    arguments=args,
                    error_code="INVALID_ARGUMENT_TYPE",
                    message=error,
                )
            args[name] = coerced
        return ToolValidationResult(ok=True, arguments=args)

    def audit_registry(self, registry: Any, policy: Any, intents: list[Any], *, categories: list[Any] | None = None) -> list[str]:
        """Return contract findings for tools exposed by policy but absent from registry."""
        registered = set(registry.get_tool_names()) if registry else set()
        findings: list[str] = []
        for intent in intents:
            try:
                allowed = set(policy.allowed_tool_names(intent, category=None))
            except TypeError:
                allowed = set(policy.allowed_tool_names(intent))
            missing = sorted(name for name in allowed if name not in registered)
            for name in missing:
                findings.append(f"{intent.value if hasattr(intent, 'value') else intent}: policy exposes unregistered tool '{name}'")
            for category in categories or []:
                try:
                    cat_allowed = set(policy.allowed_tool_names(intent, category=category))
                except Exception:
                    continue
                for name in sorted(cat_allowed - registered):
                    findings.append(
                        f"{intent.value if hasattr(intent, 'value') else intent}: category {getattr(category, 'category_id', '<unknown>')} exposes unregistered tool '{name}'"
                    )
        return findings

    def _find_placeholder(self, value: Any, path: str = "arguments") -> str | None:
        if isinstance(value, str):
            token = value.strip()
            if _PLACEHOLDER_RE.search(token):
                return path
            return None
        if isinstance(value, dict):
            for key, child in value.items():
                found = self._find_placeholder(child, f"{path}.{key}")
                if found:
                    return found
        if isinstance(value, list):
            for idx, child in enumerate(value):
                found = self._find_placeholder(child, f"{path}[{idx}]")
                if found:
                    return found
        return None

    def _coerce_type(self, name: str, value: Any, expected: Any) -> tuple[Any, str | None]:
        if expected is None:
            return value, None
        expected_types = expected if isinstance(expected, list) else [expected]
        if "string" in expected_types:
            if isinstance(value, str):
                return value, None
            if value is None:
                return value, None
            return str(value), None
        if "integer" in expected_types:
            if isinstance(value, bool):
                return value, f"Argument '{name}' must be an integer; got boolean."
            if isinstance(value, int):
                return value, None
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip()), None
            if value is None:
                return value, None
            return value, f"Argument '{name}' must be an integer; got {type(value).__name__}."
        if "number" in expected_types:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value, None
            try:
                return float(value), None
            except (TypeError, ValueError):
                return value, f"Argument '{name}' must be a number; got {type(value).__name__}."
        if "boolean" in expected_types:
            if isinstance(value, bool):
                return value, None
            if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                return value.strip().lower() == "true", None
            if value is None:
                return value, None
            return value, f"Argument '{name}' must be a boolean; got {type(value).__name__}."
        if "array" in expected_types:
            if isinstance(value, list):
                return value, None
            return value, f"Argument '{name}' must be an array; got {type(value).__name__}."
        if "object" in expected_types:
            if isinstance(value, dict) or value is None:
                return value, None
            return value, f"Argument '{name}' must be an object; got {type(value).__name__}."
        return value, None
