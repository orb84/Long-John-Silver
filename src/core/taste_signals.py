"""Evidence-based taste signal modeling for category-scoped memory.

This module keeps the core app category-neutral.  It does not know what a
movie, game, book, or album *means*; it only normalizes user-specific evidence
into durable signals and cautious facet updates.  Category implementations own
which metadata fields they emit and how those fields should be interpreted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


POSITIVE_TYPES = {"like", "explicit_like", "favorite", "loved", "recommend_like"}
NEGATIVE_TYPES = {"dislike", "explicit_dislike", "reject", "negative", "hated"}
INTEREST_TYPES = {"curious", "watchlist", "download", "downloaded", "library", "library_item", "searched"}
ENGAGEMENT_TYPES = {"watch", "watched", "completed", "played", "read"}
NEUTRAL_TYPES = {"mention", "research"}


@dataclass(frozen=True)
class TasteFacetUpdate:
    """One derived preference update for a category-owned facet."""

    facet_key: str
    facet_value: str
    score: float
    confidence: float
    evidence: str = ""
    source: str = "metadata"
    signal_id: int | None = None


@dataclass(frozen=True)
class NormalizedTasteSignal:
    """Normalized event payload stored as category taste evidence."""

    user_id: str
    category_id: str
    item_id: str
    display_name: str
    signal_type: str
    polarity: str
    strength: float
    confidence: float
    weight: float
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)
    interpreted_facets: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    evidence_text: str = ""

    def to_repository_payload(self) -> dict[str, Any]:
        """Return a dict suitable for the SystemRepository."""
        return {
            "user_id": self.user_id,
            "category_id": self.category_id,
            "item_id": self.item_id,
            "display_name": self.display_name,
            "signal_type": self.signal_type,
            "polarity": self.polarity,
            "strength": self.strength,
            "weight": self.weight,
            "source": self.source,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "interpreted_facets": self.interpreted_facets,
            "notes": self.notes,
            "evidence_text": self.evidence_text,
        }


class TasteSignalNormalizer:
    """Normalize raw user/item events without category-specific branching."""

    METADATA_FIELD_ALIASES = {
        "cast": "cast_names",
        "lead_cast": "cast_names",
        "actors": "cast_names",
        "runtime_minutes": "runtime",
        "release_date": "release_year",
        "first_air_date": "release_year",
        "plot": "overview",
        "summary": "overview",
        "id": "external_id",
    }

    DEFAULT_STRENGTHS = {
        "favorite": 1.0,
        "loved": 0.95,
        "explicit_like": 0.9,
        "like": 0.85,
        "recommend_like": 0.7,
        "explicit_dislike": 0.9,
        "hated": 0.95,
        "dislike": 0.85,
        "reject": 0.55,
        "negative": 0.65,
        "completed": 0.45,
        "watched": 0.35,
        "watch": 0.35,
        "played": 0.35,
        "read": 0.35,
        "downloaded": 0.25,
        "download": 0.25,
        "library_item": 0.25,
        "library": 0.25,
        "watchlist": 0.3,
        "curious": 0.25,
        "searched": 0.15,
        "research": 0.05,
        "mention": 0.08,
    }

    POLARITIES = {
        **{key: "positive" for key in POSITIVE_TYPES},
        **{key: "negative" for key in NEGATIVE_TYPES},
        **{key: "interest" for key in INTEREST_TYPES},
        **{key: "engagement" for key in ENGAGEMENT_TYPES},
        **{key: "neutral" for key in NEUTRAL_TYPES},
    }

    @classmethod
    def normalize_signal(
        cls,
        *,
        user_id: str | None,
        category_id: str,
        item_id: str,
        display_name: str,
        signal_type: str,
        metadata: dict[str, Any] | None,
        interpreted_facets: dict[str, Any] | None = None,
        source: str = "conversation",
        confidence: float = 1.0,
        weight: float | None = None,
        polarity: str | None = None,
        strength: float | None = None,
        notes: str = "",
        evidence_text: str = "",
    ) -> NormalizedTasteSignal:
        """Return a safe, normalized taste-signal event."""
        normalized_type = cls.normalize_signal_type(signal_type)
        normalized_polarity = cls.normalize_polarity(polarity or cls.POLARITIES.get(normalized_type, "neutral"))
        normalized_strength = cls.clamp01(strength if strength is not None else cls.DEFAULT_STRENGTHS.get(normalized_type, 0.2))
        normalized_confidence = cls.clamp01(confidence)
        if weight is None:
            weight = cls.signed_weight(normalized_polarity, normalized_strength)
        normalized_metadata = cls.normalize_metadata(metadata or {})
        normalized_facets = cls.normalize_interpreted_facets(interpreted_facets or {})
        normalized_item = (item_id or display_name or "").strip()
        normalized_display = (display_name or normalized_item).strip()
        if not normalized_item:
            raise ValueError("item_id or display_name is required for a taste signal")
        return NormalizedTasteSignal(
            user_id=user_id or "",
            category_id=(category_id or "media").strip() or "media",
            item_id=normalized_item,
            display_name=normalized_display,
            signal_type=normalized_type,
            polarity=normalized_polarity,
            strength=normalized_strength,
            confidence=normalized_confidence,
            weight=float(weight),
            source=(source or "conversation").strip().lower() or "conversation",
            metadata=normalized_metadata,
            interpreted_facets=normalized_facets,
            notes=(notes or "").strip(),
            evidence_text=(evidence_text or "").strip(),
        )

    @classmethod
    def normalize_signal_type(cls, signal_type: str) -> str:
        """Normalize loose LLM/user labels into stable event types."""
        raw = (signal_type or "mention").strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "love": "like",
            "liked": "like",
            "enjoyed": "like",
            "enjoy": "like",
            "favourite": "favorite",
            "fave": "favorite",
            "hate": "dislike",
            "hated": "hated",
            "did_not_like": "dislike",
            "didnt_like": "dislike",
            "not_for_me": "dislike",
            "rejected": "reject",
            "download": "downloaded",
            "downloaded": "downloaded",
            "in_library": "library_item",
            "library": "library_item",
            "watched": "watched",
            "watch": "watched",
        }
        return aliases.get(raw, raw or "mention")

    @staticmethod
    def normalize_polarity(polarity: str) -> str:
        """Return one of the supported evidence polarities."""
        raw = (polarity or "neutral").strip().lower()
        aliases = {
            "plus": "positive",
            "pos": "positive",
            "minus": "negative",
            "neg": "negative",
            "curiosity": "interest",
            "implicit_interest": "interest",
            "owned": "interest",
        }
        raw = aliases.get(raw, raw)
        return raw if raw in {"positive", "negative", "interest", "engagement", "neutral", "mixed"} else "neutral"

    @staticmethod
    def clamp01(value: Any) -> float:
        """Clamp a loose value to [0, 1]."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        return max(0.0, min(1.0, number))

    @staticmethod
    def signed_weight(polarity: str, strength: float) -> float:
        """Return a signed aggregate weight from polarity and strength."""
        if polarity == "positive":
            return abs(float(strength))
        if polarity == "negative":
            return -abs(float(strength))
        if polarity == "interest":
            return abs(float(strength)) * 0.35
        if polarity == "engagement":
            return abs(float(strength)) * 0.25
        if polarity == "mixed":
            return 0.0
        return abs(float(strength)) * 0.05

    @classmethod
    def normalize_metadata(cls, metadata: dict[str, Any]) -> dict[str, Any]:
        """Normalize common field aliases while preserving category-owned keys."""
        normalized = dict(metadata or {})
        for source, target in cls.METADATA_FIELD_ALIASES.items():
            if source not in normalized or target in normalized:
                continue
            value = normalized[source]
            if target == "cast_names":
                normalized[target] = cls._names_from_people(value)
            elif target == "release_year":
                normalized[target] = cls._year_from_value(value)
            else:
                normalized[target] = value
        if "cast_names" in normalized:
            normalized["cast_names"] = cls._names_from_people(normalized.get("cast_names"))
        for key in ("genres", "directors", "writers", "creators", "studios", "platforms", "themes", "moods", "mechanics", "tags"):
            if key in normalized:
                normalized[key] = cls.string_list(normalized[key])
        return normalized

    @classmethod
    def normalize_interpreted_facets(cls, facets: dict[str, Any]) -> dict[str, Any]:
        """Normalize LLM-extracted facets without assuming a category domain."""
        if not isinstance(facets, dict):
            return {}
        normalized: dict[str, Any] = {}
        for key, value in facets.items():
            if value is None or value == "":
                continue
            normalized[str(key)] = value
        for key in ("liked", "liked_aspects", "positive", "disliked", "disliked_aspects", "negative", "avoid", "do_not_infer", "not_inferred"):
            if key in normalized:
                normalized[key] = cls.string_list(normalized[key])
        return normalized

    @staticmethod
    def _names_from_people(value: Any) -> list[str]:
        values = value if isinstance(value, list) else [value]
        names: list[str] = []
        for entry in values:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("title") or entry.get("id")
            else:
                name = entry
            if name:
                names.append(str(name))
        return names

    @staticmethod
    def _year_from_value(value: Any) -> int | str | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        text = str(value)
        import re
        match = re.search(r"(19|20)\d{2}", text)
        return int(match.group(0)) if match else value

    @staticmethod
    def string_list(value: Any) -> list[str]:
        """Normalize scalar/list/dict metadata values into unique strings."""
        values = value if isinstance(value, list) else [value]
        result: list[str] = []
        for entry in values:
            if isinstance(entry, dict):
                entry = entry.get("name") or entry.get("title") or entry.get("id")
            if entry is not None and str(entry).strip():
                text = str(entry).strip()
                if text not in result:
                    result.append(text)
        return result


class TasteFacetDeriver:
    """Derive cautious facet scores from signals plus category metadata."""

    DEFAULT_METADATA_MULTIPLIERS = {
        "genres": 0.22,
        "cast_names": 0.12,
        "directors": 0.38,
        "writers": 0.28,
        "creators": 0.35,
        "studios": 0.32,
        "developers": 0.35,
        "publishers": 0.18,
        "themes": 0.48,
        "moods": 0.45,
        "mechanics": 0.55,
        "tags": 0.28,
        "platforms": 0.12,
        "languages": 0.08,
        "networks": 0.14,
    }

    @classmethod
    def derive_updates(cls, signal: dict[str, Any], category: Any | None = None) -> list[TasteFacetUpdate]:
        """Return derived facet updates for one stored signal row."""
        base = float(signal.get("weight") or 0.0) * float(signal.get("confidence") or 1.0)
        polarity = str(signal.get("polarity") or "").lower()
        if polarity == "negative" and base > 0:
            base *= -1.0
        metadata = TasteSignalNormalizer.normalize_metadata(signal.get("metadata") or {})
        interpreted = TasteSignalNormalizer.normalize_interpreted_facets(signal.get("interpreted_facets") or {})
        evidence = str(signal.get("notes") or signal.get("evidence_text") or "")
        confidence = TasteSignalNormalizer.clamp01(signal.get("confidence", 1.0))
        updates: list[TasteFacetUpdate] = []

        # LLM-extracted reasons are the strongest evidence because they come
        # from the user's wording, not just from item metadata.
        for key in ("liked", "liked_aspects", "positive"):
            for value in TasteSignalNormalizer.string_list(interpreted.get(key) or []):
                updates.append(cls._update("aspects", value, abs(base), confidence, evidence, "interpreted", signal.get("id")))
        for key in ("disliked", "disliked_aspects", "negative", "avoid"):
            for value in TasteSignalNormalizer.string_list(interpreted.get(key) or []):
                updates.append(cls._update("aspects", value, -abs(base), confidence, evidence, "interpreted", signal.get("id")))

        explicit_dimensions = interpreted.get("dimensions") if isinstance(interpreted.get("dimensions"), dict) else {}
        for dimension, value_map in explicit_dimensions.items():
            if isinstance(value_map, dict):
                for value, score in value_map.items():
                    try:
                        explicit_score = float(score)
                    except (TypeError, ValueError):
                        explicit_score = base
                    updates.append(cls._update(str(dimension), str(value), explicit_score, confidence, evidence, "interpreted", signal.get("id")))
            else:
                for value in TasteSignalNormalizer.string_list(value_map):
                    updates.append(cls._update(str(dimension), value, base, confidence, evidence, "interpreted", signal.get("id")))

        metadata_multipliers = cls._metadata_multipliers_for_category(category)
        metadata_base = cls._metadata_base_for_signal(signal, base, interpreted)
        if abs(metadata_base) > 0:
            for key, multiplier in metadata_multipliers.items():
                raw = metadata.get(key)
                if not raw:
                    continue
                for value in TasteSignalNormalizer.string_list(raw):
                    updates.append(cls._update(key, value, metadata_base * float(multiplier), confidence, evidence, "metadata", signal.get("id")))
        return [update for update in updates if update.facet_value and abs(update.score) > 0.0001]

    @classmethod
    def _metadata_base_for_signal(cls, signal: dict[str, Any], base: float, interpreted: dict[str, Any]) -> float:
        """Return cautious broad-metadata contribution for one signal."""
        polarity = str(signal.get("polarity") or "").lower()
        has_reason = any(interpreted.get(key) for key in (
            "liked", "liked_aspects", "positive",
            "disliked", "disliked_aspects", "negative", "avoid", "dimensions",
        ))
        if polarity == "negative":
            return base * (0.25 if has_reason else 0.08)
        if polarity == "positive":
            return base * (0.55 if has_reason else 0.35)
        if polarity in {"interest", "engagement"}:
            return base * 0.35
        return base * 0.05

    @classmethod
    def _metadata_multipliers_for_category(cls, category: Any | None) -> dict[str, float]:
        multipliers = dict(cls.DEFAULT_METADATA_MULTIPLIERS)
        if category and hasattr(category, "taste_dimension_weights"):
            try:
                for key, value in (category.taste_dimension_weights() or {}).items():
                    multipliers[str(key)] = float(value)
            except Exception:
                pass
        return multipliers

    @staticmethod
    def _update(
        facet_key: str,
        facet_value: str,
        score: float,
        confidence: float,
        evidence: str,
        source: str,
        signal_id: Any,
    ) -> TasteFacetUpdate:
        sid: int | None
        try:
            sid = int(signal_id) if signal_id is not None else None
        except (TypeError, ValueError):
            sid = None
        return TasteFacetUpdate(
            facet_key=facet_key,
            facet_value=facet_value,
            score=float(score),
            confidence=float(confidence),
            evidence=evidence,
            source=source,
            signal_id=sid,
        )
