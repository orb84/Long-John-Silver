"""Provider-call policy and task/model prompt hints for LJS LLM traffic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMCallEnvelope:
    """Observable retry and timeout envelope for one provider call."""

    max_attempts: int
    timeout_seconds: float


class LLMCallPolicy:
    """Resolve bounded provider I/O without starving model output budgets."""

    _ROUTING_TASKS = {"intent_routing", "routing_fast"}
    _COSMETIC_FAST_TASKS = {"progress_message"}
    _INTERACTIVE_TASKS = {
        "chat", "search", "download", "tool_agent_reliable", "final_response",
        "torrent_ranker", "planning_strict", "research_web",
    }

    @classmethod
    def resolve(cls, task: str, overrides: dict[str, Any] | None = None) -> LLMCallEnvelope:
        """Return defaults, honoring explicit per-call transport controls."""
        task_name = str(task or "").strip().lower()
        if task_name in cls._ROUTING_TASKS:
            default = LLMCallEnvelope(max_attempts=2, timeout_seconds=90.0)
        elif task_name in cls._COSMETIC_FAST_TASKS:
            default = LLMCallEnvelope(max_attempts=1, timeout_seconds=45.0)
        elif task_name in cls._INTERACTIVE_TASKS:
            default = LLMCallEnvelope(max_attempts=2, timeout_seconds=180.0)
        else:
            default = LLMCallEnvelope(max_attempts=3, timeout_seconds=180.0)

        values = overrides or {}
        max_attempts = cls._bounded_int(values.get("max_attempts"), default.max_attempts, 1, 5)
        timeout_value = values.get("request_timeout_seconds", values.get("timeout"))
        timeout_seconds = cls._bounded_float(timeout_value, default.timeout_seconds, 5.0, 900.0)
        return LLMCallEnvelope(max_attempts=max_attempts, timeout_seconds=timeout_seconds)

    @staticmethod
    def transport_keys() -> set[str]:
        """Return override keys that control I/O rather than model generation."""
        return {"timeout", "request_timeout_seconds", "max_attempts", "num_retries"}

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(maximum, int(value))) if value is not None else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            return max(minimum, min(maximum, float(value))) if value is not None else default
        except (TypeError, ValueError):
            return default


class LLMGenerationPolicy:
    """Merge task configuration and per-call generation options consistently."""

    _NVIDIA_GENERATION_KEYS = {
        "temperature", "max_tokens", "top_p", "seed", "stop",
        "presence_penalty", "frequency_penalty", "response_format",
        "reasoning_effort",
    }

    @classmethod
    def effective_options(cls, resolved: Any, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return effective generation options, excluding credentials/I/O controls."""
        options: dict[str, Any] = {}
        if getattr(resolved, "temperature", None) is not None:
            options["temperature"] = resolved.temperature
        if getattr(resolved, "max_tokens", None) is not None:
            options["max_tokens"] = resolved.max_tokens
        for key, value in (overrides or {}).items():
            if key in {"api_key", "Authorization"} or key in LLMCallPolicy.transport_keys():
                continue
            if value is not None:
                options[key] = value
        return options

    @classmethod
    def nvidia_payload_options(cls, options: dict[str, Any]) -> dict[str, Any]:
        """Return only OpenAI-compatible generation fields accepted by NIM."""
        return {key: value for key, value in options.items() if key in cls._NVIDIA_GENERATION_KEYS}


class LLMTaskPromptPolicy:
    """Apply narrowly scoped model-family hints without changing user content."""

    _LOW_REASONING_TASKS = {"intent_routing", "routing_fast"}

    @classmethod
    def apply(cls, *, task: str, model: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Use GPT-OSS's documented low-reasoning system hint for routing only."""
        copied = [dict(message) for message in messages]
        if str(task or "").lower() not in cls._LOW_REASONING_TASKS:
            return copied
        if "gpt-oss" not in str(model or "").casefold():
            return copied
        hint = (
            "Reasoning: low\n"
            "This is a narrow routing classification. Return only the requested label; "
            "do not provide analysis or an explanation.\n\n"
        )
        first_system = next(
            (index for index, message in enumerate(copied) if message.get("role") == "system"),
            None,
        )
        if first_system is None:
            copied.insert(0, {"role": "system", "content": hint.rstrip()})
        else:
            current = str(copied[first_system].get("content") or "")
            copied[first_system]["content"] = f"{hint}{current}"
        return copied
