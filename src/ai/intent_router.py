"""Semantic intent routing for LJS.

Routing remains multilingual and LLM-based. It does not use brittle natural-
language keyword lists, and a provider failure is kept distinct from genuine
user ambiguity so the UI can report the real problem.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from loguru import logger
from typing import Optional, Tuple

from src.core.models import Intent
from src.utils.circuit_breaker import CircuitBreaker, CircuitOpenError
from src.utils.json_parser import LLMResponseParser
from src.utils.runtime_prompt_context import RuntimePromptContext

CLARIFY_THRESHOLD = 0.6


@dataclass(frozen=True)
class IntentRoutingDecision:
    """One routed intent together with confidence and failure provenance."""

    intent: Intent
    confidence: float
    status: str
    raw_response: str = ""
    error: str | None = None

    @property
    def provider_failed(self) -> bool:
        """Return whether routing failed operationally rather than semantically."""
        return self.status in {"provider_error", "circuit_open", "unavailable"}


class ClarificationBuilder:
    """Build user-facing text for ambiguity or routing infrastructure failure."""

    _GENERIC_CLARIFICATION = (
        "Captain, I am not sure which course to chart. Could you clarify whether "
        "you want me to search for information, find/download something, change "
        "settings, or just discuss it?"
    )
    _ROUTER_FAILURE = (
        "The LLM routing step failed before I could process this request, so no "
        "search or download was attempted. Open LLM Diagnostics for the provider "
        "error and retry the request."
    )

    @classmethod
    def build(
        cls,
        message: str,
        intent_hint: Optional[Intent] = None,
        decision: IntentRoutingDecision | None = None,
    ) -> str:
        """Return an honest user-facing message for the routing outcome."""
        if decision and decision.provider_failed:
            return cls._ROUTER_FAILURE
        return cls._GENERIC_CLARIFICATION


class IntentRouter:
    """Route messages semantically through the configured task-aware LLM client."""

    _SYSTEM_PROMPT = (
        "You are an intent router for a media-library assistant. Classify the "
        "current user message into exactly one category: SEARCH, DOWNLOAD, CONFIG, "
        "CHAT, or CLARIFY. Infer intent semantically and multilingually. Return "
        "only one uppercase category label, with no explanation.\n\n"
        "Rules:\n"
        "- Use structured conversation/application context when present.\n"
        "- DOWNLOAD covers finding, queueing, controlling, refining, or continuing downloads, including unit or bundle/range requests and pending torrent choices.\n"
        "- SEARCH covers information, research, and metadata without queueing, tracking, scheduling, or changing state.\n"
        "- CONFIG covers settings, providers, categories, reminders, recurring checks, watches, and app configuration.\n"
        "- CHAT covers ordinary conversation with no tool/action need.\n"
        "- CLARIFY is only for genuine ambiguity after considering context.\n"
        "- A correction or refinement inherits the last relevant SEARCH or DOWNLOAD goal when context makes that clear."
    )
    _USER_TEMPLATE = (
        "STRUCTURED CONTEXT:\n{context}\n\n"
        "CURRENT USER MESSAGE:\n{message}\n\n"
        "CATEGORY:"
    )

    def __init__(
        self,
        llm_client: Optional[object] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self._llm_client = llm_client
        self._breaker = circuit_breaker or CircuitBreaker(
            "intent_router", failure_threshold=3, recovery_seconds=30,
        )
        self._last_clarify_hint: Optional[Intent] = None
        self._last_decision = IntentRoutingDecision(
            Intent.CLARIFY, 0.0, "unavailable", error="Router has not run yet"
        )

    @property
    def last_decision(self) -> IntentRoutingDecision:
        """Return the latest decision for compatibility diagnostics."""
        return self._last_decision

    async def route(self, message: str, context: str | None = None) -> Intent:
        """Return only the routed intent for compatibility callers."""
        return (await self.route_with_details(message, context=context)).intent

    async def route_with_details(
        self, message: str, context: str | None = None,
    ) -> IntentRoutingDecision:
        """Return intent, confidence, and whether routing itself failed."""
        self._last_clarify_hint = None
        if not self._llm_client:
            logger.warning("IntentRouter has no LLM client; routing is unavailable.")
            decision = IntentRoutingDecision(
                Intent.CLARIFY,
                0.0,
                "unavailable",
                error="No LLM client is configured for intent routing",
            )
            self._last_decision = decision
            return decision

        decision = await self._route_with_llm_decision(message, context=context)
        if decision.status == "success" and decision.confidence >= CLARIFY_THRESHOLD:
            self._last_decision = decision
            return decision
        if decision.status == "success":
            self._last_clarify_hint = decision.intent
            if decision.intent == Intent.CHAT:
                self._last_decision = decision
                return decision
            decision = IntentRoutingDecision(
                Intent.CLARIFY,
                decision.confidence,
                "uncertain",
                raw_response=decision.raw_response,
            )
        self._last_decision = decision
        return decision

    async def _route_with_llm(
        self, message: str, context: str | None = None,
    ) -> Tuple[Intent, float]:
        """Compatibility seam returning the historic intent/confidence tuple."""
        decision = await self._route_with_llm_decision(message, context=context)
        confidence = 0.85 if decision.status == "success" else decision.confidence
        return decision.intent, confidence

    async def _route_with_llm_decision(
        self, message: str, context: str | None = None,
    ) -> IntentRoutingDecision:
        messages = [
            {"role": "system", "content": self._SYSTEM_PROMPT},
            {
                "role": "user",
                "content": self._USER_TEMPLATE.format(
                    message=message,
                    context=context or "(none)",
                ),
            },
        ]
        try:
            # Deliberately no max_tokens override. A 20-token cap is unsafe for
            # reasoning-capable models because reasoning and visible output may
            # share one generation budget depending on the provider/model.
            response = await self._breaker.call(
                self._llm_client.completion,
                task="intent_routing",
                messages=messages,
            )
            intent_content = LLMResponseParser.safe_extract_content(response).strip()
            decision = self.parse_response(intent_content)
            if decision.status == "success":
                logger.info(
                    "LLM routed intent: {} (raw content: {!r})",
                    decision.intent,
                    intent_content,
                )
            else:
                logger.warning("Intent router returned ambiguous output: {!r}", intent_content)
            return decision
        except CircuitOpenError as exc:
            logger.warning("Intent routing circuit breaker is OPEN")
            return IntentRoutingDecision(
                Intent.CLARIFY,
                0.0,
                "circuit_open",
                error=str(exc) or "Intent routing circuit breaker is open",
            )
        except Exception as exc:
            logger.error("Intent routing LLM error: {}: {}", type(exc).__name__, str(exc) or "<no message>")
            return IntentRoutingDecision(
                Intent.CLARIFY,
                0.0,
                "provider_error",
                error=f"{type(exc).__name__}: {str(exc) or '<no message>'}",
            )

    @staticmethod
    def parse_response(content: str) -> IntentRoutingDecision:
        """Parse a category label without accepting a prompt echo as certainty."""
        cleaned = str(content or "").strip().upper()
        exact = next((candidate for candidate in Intent if cleaned == candidate.value), None)
        if exact is not None:
            return IntentRoutingDecision(exact, 0.95, "success", raw_response=content)

        labels = []
        for candidate in Intent:
            if re.search(r"\b" + re.escape(candidate.value) + r"\b", cleaned):
                labels.append(candidate)
        if len(labels) == 1:
            return IntentRoutingDecision(labels[0], 0.75, "success", raw_response=content)
        return IntentRoutingDecision(
            Intent.CLARIFY,
            0.1,
            "uncertain",
            raw_response=content,
        )

    @staticmethod
    def route_intent_fast(message: str) -> Tuple[Optional[Intent], float]:
        """Deprecated seam; no language-specific deterministic router is used."""
        return None, 0.0

    @staticmethod
    async def route_intent_with_llm(
        message: str,
        model: str,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        context: str | None = None,
    ) -> Tuple[Intent, float]:
        """Legacy direct-LiteLLM route without an artificial output cap."""
        import litellm

        breaker = circuit_breaker or CircuitBreaker(
            "intent_router", failure_threshold=3, recovery_seconds=30
        )
        messages = RuntimePromptContext.ensure_messages([
            {"role": "system", "content": IntentRouter._SYSTEM_PROMPT},
            {
                "role": "user",
                "content": IntentRouter._USER_TEMPLATE.format(
                    message=message,
                    context=context or "(none)",
                ),
            },
        ])
        try:
            response = await breaker.call(
                litellm.acompletion,
                model=model,
                messages=messages,
                api_base=api_base,
                api_key=api_key,
                timeout=90.0,
                num_retries=0,
            )
            decision = IntentRouter.parse_response(
                LLMResponseParser.safe_extract_content(response).strip()
            )
            return decision.intent, decision.confidence
        except (CircuitOpenError, Exception) as exc:
            logger.error("Legacy intent routing error: {}", exc)
            return Intent.CLARIFY, 0.0

    @staticmethod
    async def route_intent(
        message: str,
        model: str = "",
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        context: str | None = None,
    ) -> Intent:
        """Compatibility wrapper that routes through the LLM when configured."""
        if model:
            llm_result, llm_confidence = await IntentRouter.route_intent_with_llm(
                message, model, api_base, api_key, context=context,
            )
            if llm_confidence >= CLARIFY_THRESHOLD:
                return llm_result
        return Intent.CLARIFY if (message or "").strip() else Intent.CHAT


# Legacy standalone wrappers retained for import compatibility.
def route_intent_fast(message: str) -> Tuple[Optional[Intent], float]:
    """Compatibility wrapper for the retired deterministic fast router."""
    return IntentRouter.route_intent_fast(message)


async def route_intent_with_llm(
    message: str,
    model: str,
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    context: str | None = None,
) -> Tuple[Intent, float]:
    """Compatibility wrapper for direct model-based intent routing."""
    return await IntentRouter.route_intent_with_llm(
        message, model, api_base, api_key, circuit_breaker, context=context,
    )


async def route_intent(
    message: str,
    model: str = "",
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    context: str | None = None,
) -> Intent:
    """Compatibility wrapper returning one routed intent label."""
    return await IntentRouter.route_intent(
        message, model, api_base, api_key, context=context,
    )
