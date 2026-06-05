"""
Intent router for LJS.

Routes user messages into intent categories through the configured LLM. The
router deliberately avoids English keyword matching: users may speak any
language, and follow-ups such as confirmations/selections must be understood
from semantic conversation context rather than brittle hard-coded phrases.
"""

from __future__ import annotations

import re
from loguru import logger
from typing import Optional, Tuple

from src.core.models import Intent
from src.utils.circuit_breaker import CircuitBreaker, CircuitOpenError
from src.utils.json_parser import LLMResponseParser

CLARIFY_THRESHOLD = 0.6


class ClarificationBuilder:
    """Build a generic clarification when the LLM cannot route confidently.

    The clarification text is user-facing guidance, not a parser. It is not
    used to classify intent and therefore does not hard-code recognized user
    phrases.
    """

    _GENERIC_CLARIFICATION = (
        "Captain, I am not sure which course to chart. Could you clarify whether "
        "you want me to search for information, find/download something, change "
        "settings, or just discuss it?"
    )

    @classmethod
    def build(cls, message: str, intent_hint: Optional[Intent] = None) -> str:
        """Return a user-facing clarification for an uncertain route."""
        return cls._GENERIC_CLARIFICATION


class IntentRouter:
    """Routes user messages to assistant intents using the LLM.

    The current user message is classified together with a compact structured
    context packet (recent candidates, pending actions, and relevant history).
    This is the only reliable architecture for multilingual users and for
    follow-ups that may occur several turns after the original candidate list.
    """

    _ROUTING_PROMPT = (
        "You are an intent router for a media-library assistant.\n"
        "Classify the CURRENT USER MESSAGE into exactly one category: "
        "SEARCH, DOWNLOAD, CONFIG, CHAT, or CLARIFY.\n\n"
        "Rules:\n"
        "- Infer intent semantically from the user's language; the user may write in any language.\n"
        "- Use the structured conversation/application context. If it contains recent "
        "torrent candidates, result_set_id values, or queue_download arguments, and the "
        "current message refers to selecting/continuing/acting on that pending choice, "
        "classify DOWNLOAD even if the current message is short or indirect.\n"
        "- DOWNLOAD means the user wants to find/queue/control downloads, act on pending candidates, or research something specifically so it can be tracked/downloaded when safe.\n"
        "- A follow-up asking to search for a better torrent option, season pack, full pack, alternate release, or fallback for the previously discussed download target is still DOWNLOAD, not general SEARCH.\n"
        "- SEARCH means the user wants information/research/metadata without queueing, tracking, scheduling, or changing app state.\n"
        "- Short correction/refinement follow-ups inherit the last relevant user goal from context: "
        "phrases like 'I meant released movie', 'not future', 'in Italian', 'the older one', "
        "or equivalent wording in any language should usually keep the prior SEARCH or DOWNLOAD "
        "intent rather than CLARIFY when context makes the target obvious.\n"
        "- If a follow-up refines an information question, classify SEARCH. If it refines a pending "
        "download/search-for-torrent task, classify DOWNLOAD.\n"
        "- CONFIG means the user wants settings, providers, categories, scheduled reminders, recurring checks/watches, or app configuration changed/discussed.\n"
        "- CHAT means ordinary conversation with no tool/action need.\n"
        "- CLARIFY means the message is genuinely ambiguous even with context.\n\n"
        "Return only one uppercase category word.\n\n"
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

    async def route(self, message: str, context: str | None = None) -> Intent:
        """Route user intent using the configured LLM.

        No natural-language keyword parser is used. When an LLM client is not
        available, the safe degradation is CLARIFY for non-empty messages rather
        than pretending an English keyword heuristic is universal.
        """
        self._last_clarify_hint = None
        if self._llm_client:
            llm_result, llm_confidence = await self._route_with_llm(message, context=context)
            if llm_confidence >= CLARIFY_THRESHOLD:
                return llm_result
            self._last_clarify_hint = llm_result
            if llm_result == Intent.CHAT:
                return Intent.CHAT
            return Intent.CLARIFY
        logger.warning("IntentRouter has no LLM client; returning CLARIFY instead of keyword routing.")
        return Intent.CLARIFY if (message or "").strip() else Intent.CHAT

    async def _route_with_llm(self, message: str, context: str | None = None) -> Tuple[Intent, float]:
        prompt = self._ROUTING_PROMPT.format(message=message, context=context or "(none)")
        try:
            response = await self._breaker.call(
                self._llm_client.completion,
                task="intent_routing",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20,
                temperature=0.0,
            )
            intent_content = LLMResponseParser.safe_extract_content(response)
            upper_content = intent_content.upper()
            for candidate in Intent:
                if re.search(r'\b' + re.escape(candidate.value) + r'\b', upper_content):
                    logger.info(f"LLM routed intent: {candidate} (raw content: {intent_content!r})")
                    return candidate, 0.85
            if "CLARIFY" in upper_content:
                return Intent.CLARIFY, 0.85
            logger.warning(f"Intent router returned unknown category: {intent_content!r}")
            return Intent.CLARIFY, 0.2
        except CircuitOpenError:
            logger.warning("Intent routing circuit breaker is OPEN — returning CLARIFY")
            return Intent.CLARIFY, 0.3
        except Exception as e:
            logger.error(f"Intent routing LLM error: {e}")
            return Intent.CLARIFY, 0.3

    @staticmethod
    def route_intent_fast(message: str) -> Tuple[Optional[Intent], float]:
        """Deprecated compatibility seam.

        Returns no decision because natural-language keyword routing is not
        acceptable for multilingual/free-form users. Callers should use
        route()/route_intent_with_llm().
        """
        return None, 0.0

    @staticmethod
    async def route_intent_with_llm(message: str, model: str,
                                     api_base: Optional[str] = None,
                                     api_key: Optional[str] = None,
                                     circuit_breaker: Optional[CircuitBreaker] = None,
                                     context: str | None = None) -> Tuple[Intent, float]:
        """Legacy LLM routing logic without keyword matching."""
        import litellm
        breaker = circuit_breaker or CircuitBreaker("intent_router", failure_threshold=3, recovery_seconds=30)
        prompt = IntentRouter._ROUTING_PROMPT.format(message=message, context=context or "(none)")
        try:
            response = await breaker.call(
                litellm.acompletion,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                api_base=api_base,
                api_key=api_key,
                max_tokens=20,
                temperature=0.0,
            )
            intent_content = LLMResponseParser.safe_extract_content(response)
            upper_content = intent_content.upper()
            for candidate in Intent:
                if re.search(r'\b' + re.escape(candidate.value) + r'\b', upper_content):
                    logger.info(f"LLM routed intent: {candidate} (raw content: {intent_content!r})")
                    return candidate, 0.85
            if "CLARIFY" in upper_content:
                return Intent.CLARIFY, 0.85
            return Intent.CLARIFY, 0.2
        except CircuitOpenError:
            logger.warning("Intent routing circuit breaker is OPEN — returning CLARIFY")
            return Intent.CLARIFY, 0.3
        except Exception as e:
            logger.error(f"Intent routing LLM error: {e}")
            return Intent.CLARIFY, 0.3

    @staticmethod
    async def route_intent(message: str, model: str = "",
                           api_base: Optional[str] = None,
                           api_key: Optional[str] = None,
                           context: str | None = None) -> Intent:
        """Compatibility wrapper that routes through the LLM when configured."""
        if model:
            llm_result, llm_confidence = await IntentRouter.route_intent_with_llm(
                message, model, api_base, api_key, context=context,
            )
            if llm_confidence >= CLARIFY_THRESHOLD:
                return llm_result
        return Intent.CLARIFY if (message or "").strip() else Intent.CHAT


# ─── Legacy Standalone Wrappers ─────────────────────────────────────

def route_intent_fast(message: str) -> Tuple[Optional[Intent], float]:
    """Return no fast route because phrase-based routing is disabled."""
    return IntentRouter.route_intent_fast(message)


async def route_intent_with_llm(message: str, model: str,
                                 api_base: Optional[str] = None,
                                 api_key: Optional[str] = None,
                                 circuit_breaker: Optional[CircuitBreaker] = None,
                                 context: str | None = None) -> Tuple[Intent, float]:
    """Route a message using the requested LLM model and structured context."""
    return await IntentRouter.route_intent_with_llm(message, model, api_base, api_key, circuit_breaker, context=context)


async def route_intent(message: str, model: str = "",
                       api_base: Optional[str] = None,
                       api_key: Optional[str] = None,
                       context: str | None = None) -> Intent:
    """Route a message through the configured LLM router compatibility path."""
    return await IntentRouter.route_intent(message, model, api_base, api_key, context=context)
