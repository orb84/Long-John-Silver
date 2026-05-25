"""LLM-led taste-signal ingestion for conversation memory.

This service turns casual user statements such as "I loved Heat years ago" or
"I bounced off that game" into category-scoped evidence.  It deliberately uses
only lightweight deterministic checks to decide whether extraction is worth an
LLM call; the interpretation of taste, sentiment, nuance, and category facets is
LLM-led and then normalized by category-owned metadata hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.core.models import Intent
from src.utils.json_parser import LLMResponseParser


@dataclass(frozen=True)
class TasteIngestionResult:
    """Summary of one post-turn taste ingestion pass."""

    attempted: bool = False
    stored: int = 0
    skipped_reason: str = ""
    error: str = ""


class TasteSignalIngestionService:
    """Extract and store category-scoped taste evidence after user turns."""

    _TRIGGER_TERMS = (
        "like", "liked", "love", "loved", "favorite", "favourite", "enjoyed",
        "hate", "hated", "dislike", "disliked", "didn't like", "didnt like",
        "not my thing", "bounced off", "boring", "too slow", "too bleak",
        "watched", "played", "read", "downloaded", "in my library",
        "reminds me", "more like", "less like", "not into", "kind of thing",
    )

    def __init__(
        self,
        *,
        llm_client: Any | None,
        settings: Any | None,
        taste_profiler: Any | None,
        category_registry: Any | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._settings = settings
        self._taste_profiler = taste_profiler
        self._category_registry = category_registry

    async def ingest_user_turn(
        self,
        *,
        user_message: str,
        assistant_response: str = "",
        user_id: str | None = None,
        session_id: str | None = None,
        active_category_id: str | None = None,
        intent: Intent | None = None,
    ) -> TasteIngestionResult:
        """Extract and persist preference evidence from one user turn.

        The deterministic check here is intentionally shallow.  It answers only
        "could this contain taste evidence?" and never decides whether the user
        likes a genre/actor/mechanic.  That interpretation is left to the LLM.
        """
        if not self._taste_profiler:
            return TasteIngestionResult(skipped_reason="taste profiler unavailable")
        if not self._llm_client:
            return TasteIngestionResult(skipped_reason="llm client unavailable")
        if not self._could_contain_taste_evidence(user_message, intent):
            return TasteIngestionResult(skipped_reason="no taste-evidence trigger")
        try:
            payload = await self._extract_with_llm(
                user_message=user_message,
                assistant_response=assistant_response,
                active_category_id=active_category_id,
            )
        except Exception as exc:  # pragma: no cover - defensive around LLM endpoints
            logger.debug(f"Taste signal extraction skipped: {exc}")
            return TasteIngestionResult(attempted=True, error=str(exc))

        signals = payload.get("signals") if isinstance(payload, dict) else None
        if not isinstance(signals, list):
            return TasteIngestionResult(attempted=True, skipped_reason="extractor returned no signal list")

        stored = 0
        for raw in signals[:8]:
            if not isinstance(raw, dict):
                continue
            if not self._should_store_signal(raw):
                continue
            category_id = str(raw.get("category_id") or active_category_id or "").strip()
            if not category_id:
                continue
            display_name = str(raw.get("display_name") or raw.get("item_name") or raw.get("title") or "").strip()
            item_id = str(raw.get("item_id") or raw.get("external_id") or display_name).strip()
            if not display_name and not item_id:
                continue
            try:
                await self._taste_profiler.record_taste_signal(
                    category_id=category_id,
                    item_id=item_id,
                    display_name=display_name or item_id,
                    signal_type=str(raw.get("signal_type") or "mention"),
                    metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
                    user_id=user_id,
                    source="conversation",
                    confidence=float(raw.get("confidence", 0.7) or 0.7),
                    weight=float(raw["weight"]) if raw.get("weight") is not None else None,
                    notes=str(raw.get("notes") or ""),
                    polarity=str(raw.get("polarity") or "").strip() or None,
                    strength=float(raw["strength"]) if raw.get("strength") is not None else None,
                    interpreted_facets=raw.get("interpreted_facets") if isinstance(raw.get("interpreted_facets"), dict) else {},
                    evidence_text=str(raw.get("evidence_text") or user_message[:300]),
                )
                stored += 1
            except Exception as exc:
                logger.debug(f"Failed to store extracted taste signal {raw!r}: {exc}")
        return TasteIngestionResult(attempted=True, stored=stored)

    def _could_contain_taste_evidence(self, user_message: str, intent: Intent | None) -> bool:
        """Cheap routing-only trigger, not deterministic taste extraction."""
        text = (user_message or "").strip().lower()
        if not text or len(text) < 4:
            return False
        if intent == Intent.DOWNLOAD:
            # A download request is weak interest evidence; the LLM still decides
            # whether there is any explicit taste statement to store.
            return True
        return any(term in text for term in self._TRIGGER_TERMS)

    async def _extract_with_llm(
        self,
        *,
        user_message: str,
        assistant_response: str,
        active_category_id: str | None,
    ) -> dict[str, Any]:
        """Ask the configured LLM for structured taste evidence."""
        prompt = self._build_extraction_prompt(
            user_message=user_message,
            assistant_response=assistant_response,
            active_category_id=active_category_id,
        )
        config = self._task_config("taste_extraction")
        response = await self._llm_client.completion(
            task="taste_extraction",
            messages=[
                {"role": "system", "content": "Extract category-scoped user taste evidence as strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            tools=None,
            max_tokens=config.get("max_tokens") or 900,
            temperature=config.get("temperature") if config.get("temperature") is not None else 0.0,
        )
        content = LLMResponseParser.safe_extract_content(response)
        return LLMResponseParser.extract_json_resilient(content)

    def _build_extraction_prompt(
        self,
        *,
        user_message: str,
        assistant_response: str,
        active_category_id: str | None,
    ) -> str:
        category_briefs = self._category_briefs_text()
        return f"""
Analyze the user's latest message for durable taste evidence.

Use the LLM to interpret nuance. Do not rely on simple genre scoreboards.
Store raw item-level events and careful facet evidence only.

Rules:
- Return strict JSON, no prose.
- Do not store mere factual mentions unless they imply interest, engagement, like, dislike, rejection, or comparison.
- Download/library/watched signals are interest or engagement, not proof of liking.
- Negative feedback is item-level unless the user explicitly names a disliked dimension or reason.
- A liked thriller is weak genre evidence unless the user says they like thrillers or repeats the pattern.
- Extract reasons into interpreted_facets.liked_aspects / disliked_aspects.
- Use interpreted_facets.do_not_infer to prevent overgeneralization.
- Do not add anything to the library.
- Category metadata is optional; category hooks can enrich later. Include known metadata only when clearly known from context.

Known categories / router hints:
{category_briefs}

Active category hint: {active_category_id or "none"}

User message:
{user_message}

Assistant response context, if useful:
{assistant_response[:1200] if assistant_response else ""}

Return JSON in this shape:
{{
  "signals": [
    {{
      "category_id": "movie|tv|video_game|book|custom id",
      "item_id": "stable external id if known, otherwise title",
      "display_name": "human title",
      "signal_type": "explicit_like|explicit_dislike|like|dislike|favorite|reject|curious|watchlist|downloaded|library_item|watched|mention",
      "polarity": "positive|negative|interest|engagement|neutral|mixed",
      "strength": 0.0,
      "confidence": 0.0,
      "evidence_text": "short quote/paraphrase",
      "notes": "why this was stored",
      "metadata": {{}},
      "interpreted_facets": {{
        "liked_aspects": [],
        "disliked_aspects": [],
        "do_not_infer": [],
        "dimensions": {{}}
      }}
    }}
  ]
}}
""".strip()

    def _category_briefs_text(self) -> str:
        """Return compact category router hints for the extractor prompt."""
        if not self._category_registry or not hasattr(self._category_registry, "router_briefs"):
            return "[]"
        briefs: list[dict[str, Any]] = []
        try:
            for brief in self._category_registry.router_briefs():
                if hasattr(brief, "model_dump"):
                    data = brief.model_dump()
                else:
                    data = dict(getattr(brief, "__dict__", {}))
                briefs.append({
                    "category_id": data.get("category_id"),
                    "display_name": data.get("display_name"),
                    "keywords": data.get("keywords", [])[:12],
                    "item_types": data.get("item_types", [])[:8],
                })
        except Exception:
            return "[]"
        import json
        return json.dumps(briefs[:20], ensure_ascii=False)

    def _task_config(self, task: str) -> dict[str, Any]:
        """Resolve optional max_tokens/temperature for a lightweight task."""
        llm = getattr(self._settings, "llm", None)
        if not llm:
            return {}
        return {
            "model": llm.get_model_for_task(task),
            "api_base": llm.get_api_base_for_task(task),
            "api_key": llm.get_api_key_for_task(task),
            "max_tokens": llm.get_max_tokens_for_task(task),
            "temperature": llm.get_temperature_for_task(task),
        }

    @staticmethod
    def _should_store_signal(raw: dict[str, Any]) -> bool:
        """Guardrail for low-confidence or empty extractor output."""
        try:
            confidence = float(raw.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0.45:
            return False
        signal_type = str(raw.get("signal_type") or "mention").lower()
        polarity = str(raw.get("polarity") or "neutral").lower()
        if signal_type == "mention" and polarity == "neutral":
            return False
        return True
