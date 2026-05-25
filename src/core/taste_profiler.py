"""
Taste profile builder for LJS.

Aggregates category-owned metadata from the user's library into a generic
``TasteProfile`` that feeds recommendations, prompt context, and proactive
suggestions without embedding TV/movie-specific assumptions in core code.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.core.models import CategoryItem, GenreProfile, PeopleProfile, TasteProfile
from src.core.taste_signals import TasteFacetDeriver, TasteSignalNormalizer
if TYPE_CHECKING:
    from src.core.categories.registry import CategoryRegistry
    from src.core.database import Database
    from src.core.vector_store import VectorStore


@dataclass
class TasteMetadataRuntimeContext:
    """Runtime collaborators passed from the profiler to category hooks.

    The profiler treats this object as opaque. Built-in and custom categories
    may read the collaborators they understand, such as a TMDB enricher or a
    provider client map, without forcing generic core code to branch on category
    identifiers or media type semantics.
    """

    metadata_enricher: object | None = None
    settings_manager: object | None = None
    metadata_clients: dict[str, Any] = field(default_factory=dict)
    artwork_manager: object | None = None


class TasteProfiler:
    """Builds and maintains a taste profile from generic category metadata."""

    ENRICH_COOLDOWN: float = 1.5
    VECTOR_ID_OFFSET = 10_000_000

    def __init__(
        self,
        db: "Database",
        category_registry: "CategoryRegistry | None" = None,
        metadata_context: TasteMetadataRuntimeContext | None = None,
        vector_store: "VectorStore | None" = None,
    ) -> None:
        """Inject storage, category registry, metadata context, and vector store.

        Args:
            db: Initialized database facade.
            category_registry: Registry used to find the owning category for
                each item. Missing categories simply skip enrichment.
            metadata_context: Opaque runtime collaborators for category hooks.
            vector_store: Optional semantic index for metadata overviews.
        """
        self._db = db
        self._categories = category_registry
        self._metadata_context = metadata_context or TasteMetadataRuntimeContext()
        self._vector_store = vector_store

    async def build_profile(self, items: list[CategoryItem], *, enrich_missing: bool = True) -> TasteProfile:
        """Build an aggregated taste profile for configured category items.

        Args:
            items: Tracked category items to include.
            enrich_missing: When true, category providers may be called to fill
                missing metadata. Startup callers pass false so launching LJS
                never fans out to TMDB/TVMaze for the whole library.
        """
        now = datetime.now(timezone.utc).isoformat()
        if enrich_missing:
            await self._enrich_all_items(items)

        genre_counts: Counter[str] = Counter()
        actor_counts: Counter[str] = Counter()
        director_counts: Counter[str] = Counter()
        writer_counts: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        dimension_counts: dict[str, Counter[str]] = {}
        rated_items: list[tuple[str, float]] = []

        for item in items:
            category_id = self._category_id_for(item)
            category_counts[category_id] += 1
            for metadata in await self._metadata_rows(category_id, item.key):
                self._merge_metadata(metadata, genre_counts, actor_counts, director_counts, writer_counts)
                self._merge_category_dimensions(metadata, 1.0, dimension_counts)
                rating = metadata.get('rating')
                name = metadata.get('display_name') or item.display_name or item.key
                if isinstance(rating, int | float):
                    rated_items.append((name, float(rating)))

        primary_genres = [genre for genre, _ in genre_counts.most_common(5)]
        rated_items.sort(key=lambda entry: entry[1], reverse=True)

        return TasteProfile(
            genres=GenreProfile(counts=dict(genre_counts.most_common(30)), primary=primary_genres),
            people=PeopleProfile(
                actors=dict(actor_counts.most_common(20)),
                directors=dict(director_counts.most_common(10)),
                writers=dict(writer_counts.most_common(10)),
            ),
            category_counts=dict(category_counts),
            metadata_dimensions=self._top_dimensions(dimension_counts),
            top_items=[name for name, _ in rated_items[:10]],
            updated_at=now,
        )



    async def load_category_profile_snapshot(
        self,
        category_id: str,
        user_id: str | None = None,
    ) -> TasteProfile | None:
        """Load the persisted category taste snapshot, if available.

        Prompt builders should prefer this cheap snapshot path.  Rebuilding a
        category profile remains available for explicit preference review or
        after new taste evidence is recorded, but normal chat context should not
        repeatedly aggregate the whole library.
        """
        if not hasattr(self._db, "system") or not hasattr(self._db.system, "get_taste_profile_snapshot"):
            return None
        row = await self._db.system.get_taste_profile_snapshot(user_id, category_id)
        if not row or not row.get("profile"):
            return None
        try:
            return TasteProfile(**row["profile"])
        except Exception:
            return None

    async def record_taste_signal(
        self,
        category_id: str,
        item_id: str,
        display_name: str = "",
        signal_type: str = "mention",
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
        source: str = "conversation",
        confidence: float = 1.0,
        weight: float | None = None,
        notes: str = "",
        polarity: str | None = None,
        strength: float | None = None,
        interpreted_facets: dict[str, Any] | None = None,
        evidence_text: str = "",
        rebuild_profile: bool = True,
    ) -> int:
        """Persist category-scoped taste evidence and rebuild derived memory.

        The raw signal is the source of truth.  Derived genre/person/facet
        scores are rebuilt from the signal log so that a user can later say
        "I watched it but didn't like it" without the earlier download/library
        event permanently poisoning the profile as a like.
        """
        enriched_metadata = await self._enrich_signal_metadata(
            category_id=category_id,
            item_id=item_id,
            display_name=display_name,
            metadata=metadata or {},
        )
        signal = TasteSignalNormalizer.normalize_signal(
            user_id=user_id,
            category_id=category_id,
            item_id=item_id,
            display_name=display_name,
            signal_type=signal_type,
            metadata=enriched_metadata,
            interpreted_facets=interpreted_facets or {},
            source=source,
            confidence=confidence,
            weight=weight,
            polarity=polarity,
            strength=strength,
            notes=notes,
            evidence_text=evidence_text,
        )
        signal_id = await self._db.system.upsert_taste_signal(signal.to_repository_payload())
        if signal.metadata:
            await self._index_metadata(signal.category_id, signal.item_id, signal.metadata)
        if rebuild_profile:
            await self.rebuild_category_taste_derivatives(signal.category_id, user_id=signal.user_id or None)
        return signal_id

    async def build_category_profile(
        self,
        category_id: str,
        user_id: str | None = None,
        include_library: bool = True,
        limit: int = 200,
    ) -> TasteProfile:
        """Build one category's evidence-based taste profile.

        Explicit conversation feedback dominates.  Downloads/library presence are
        treated as weak interest or engagement signals, not as proof that the
        user liked every genre/person attached to an item.
        """
        now = datetime.now(timezone.utc).isoformat()
        genre_counts: Counter[str] = Counter()
        actor_counts: Counter[str] = Counter()
        director_counts: Counter[str] = Counter()
        writer_counts: Counter[str] = Counter()
        weighted_items: Counter[str] = Counter()
        category_counts: Counter[str] = Counter()
        dimension_counts: dict[str, Counter[str]] = {}

        signals = []
        if hasattr(self._db, "system") and hasattr(self._db.system, "list_taste_signals"):
            signals = await self._db.system.list_taste_signals(
                user_id=user_id, category_id=category_id, limit=limit,
            )

        for signal in signals:
            weight = float(signal.get("weight") or 0.0) * float(signal.get("confidence") or 1.0)
            name = signal.get("display_name") or signal.get("item_id") or "?"
            if weight > 0:
                weighted_items[str(name)] += weight
            category_counts[category_id] += 1
            metadata = signal.get("metadata") or {}
            metadata_weight = self._metadata_weight_for_signal(signal, weight)
            self._merge_weighted_metadata(metadata, metadata_weight, genre_counts, actor_counts, director_counts, writer_counts)
            self._merge_category_dimensions(metadata, metadata_weight, dimension_counts)

        if include_library and hasattr(self._db, "media"):
            # Library metadata is useful context, but it is not a like.  Keep it
            # weak and positive-interest only; explicit negative feedback can
            # override it through the signal log above.
            try:
                rows = await self._db.media.get_all_category_metadata(category_id=category_id)
            except TypeError:
                rows = await self._db.media.get_all_category_metadata()
                rows = [row for row in rows if row.get("category_id") == category_id]
            except Exception:
                rows = []
            for row in rows:
                metadata = TasteSignalNormalizer.normalize_metadata(row.get("metadata") or {})
                item_id = row.get("item_id") or metadata.get("display_name") or "?"
                weak_interest = 0.18
                category_counts[category_id] += 1
                weighted_items[str(metadata.get("display_name") or item_id)] += weak_interest
                self._merge_weighted_metadata(metadata, weak_interest, genre_counts, actor_counts, director_counts, writer_counts)
                self._merge_category_dimensions(metadata, weak_interest, dimension_counts)

        facet_scores = await self._load_facet_scores(category_id, user_id=user_id)
        facet_affinities, positive_contexts, negative_contexts, evidence_counts = self._shape_facet_scores(facet_scores)
        for key, values in facet_affinities.items():
            bucket = dimension_counts.setdefault(key, Counter())
            for value, affinity in values.items():
                if affinity:
                    bucket[value] += affinity

        top_items = [name for name, score in weighted_items.most_common(12) if score > 0]
        primary_genres = [genre for genre, score in genre_counts.most_common(5) if score > 0]
        summary = self._summarize_profile(
            primary_genres=primary_genres,
            facet_affinities=facet_affinities,
            positive_contexts=positive_contexts,
            negative_contexts=negative_contexts,
        )
        return TasteProfile(
            genres=GenreProfile(counts=dict(genre_counts.most_common(30)), primary=primary_genres),
            people=PeopleProfile(
                actors=dict(actor_counts.most_common(20)),
                directors=dict(director_counts.most_common(10)),
                writers=dict(writer_counts.most_common(10)),
            ),
            category_counts=dict(category_counts),
            metadata_dimensions=self._top_dimensions(dimension_counts),
            facet_affinities=facet_affinities,
            positive_contexts=positive_contexts,
            negative_contexts=negative_contexts,
            evidence_counts=evidence_counts,
            top_items=top_items,
            summary=summary,
            updated_at=now,
        )

    async def rebuild_category_taste_derivatives(
        self,
        category_id: str,
        user_id: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Rebuild derived facet scores and profile snapshot from raw signals."""
        if not hasattr(self._db, "system") or not hasattr(self._db.system, "list_taste_signals"):
            return {"status": "unavailable", "signals": 0, "facets": 0}
        signals = await self._db.system.list_taste_signals(
            user_id=user_id, category_id=category_id, limit=limit,
        )
        category = self._category_for_id(category_id)
        buckets: dict[tuple[str, str], dict[str, Any]] = {}
        for signal in signals:
            for update in TasteFacetDeriver.derive_updates(signal, category=category):
                key = (update.facet_key, update.facet_value)
                bucket = buckets.setdefault(key, {
                    "facet_key": update.facet_key,
                    "facet_value": update.facet_value,
                    "positive_score": 0.0,
                    "negative_score": 0.0,
                    "confidence_total": 0.0,
                    "evidence_count": 0,
                    "source_signal_ids": [],
                })
                if update.score >= 0:
                    bucket["positive_score"] += update.score
                else:
                    bucket["negative_score"] += abs(update.score)
                bucket["confidence_total"] += update.confidence
                bucket["evidence_count"] += 1
                if update.signal_id is not None and update.signal_id not in bucket["source_signal_ids"]:
                    bucket["source_signal_ids"].append(update.signal_id)
        scores = []
        for bucket in buckets.values():
            evidence_count = int(bucket["evidence_count"] or 1)
            affinity = float(bucket["positive_score"]) - float(bucket["negative_score"])
            scores.append({
                "facet_key": bucket["facet_key"],
                "facet_value": bucket["facet_value"],
                "affinity": affinity,
                "positive_score": bucket["positive_score"],
                "negative_score": bucket["negative_score"],
                "confidence": max(0.0, min(1.0, float(bucket["confidence_total"]) / evidence_count)),
                "evidence_count": evidence_count,
                "source_signal_ids": bucket["source_signal_ids"],
            })
        if hasattr(self._db.system, "replace_taste_facet_scores"):
            await self._db.system.replace_taste_facet_scores(user_id or "", category_id, scores)
        profile = await self.build_category_profile(category_id, user_id=user_id, include_library=True, limit=limit)
        if hasattr(self._db.system, "upsert_taste_profile_snapshot"):
            await self._db.system.upsert_taste_profile_snapshot(
                user_id or "",
                category_id,
                profile.model_dump() if hasattr(profile, "model_dump") else dict(profile),
                summary=profile.summary,
                evidence_count=len(signals),
            )
        return {"status": "ok", "signals": len(signals), "facets": len(scores)}

    async def enrich_single_item(self, item: CategoryItem) -> dict[str, Any] | None:
        """Ask the owning category to enrich one item and persist the result."""
        category_id = self._category_id_for(item)
        metadata = await self._enrich_item(item)
        if not metadata:
            return None
        await self._upsert_metadata(category_id, item.key, metadata)
        await self._index_metadata(category_id, item.key, metadata)
        return metadata

    async def _enrich_all_items(self, items: list[CategoryItem]) -> None:
        """Ensure configured category items have provider metadata when supported."""
        enabled = [item for item in items if item.enabled]
        for index, item in enumerate(enabled):
            category_id = self._category_id_for(item)
            existing = await self._metadata_rows(category_id, item.key)
            if any(row.get('genres') for row in existing):
                cached = await self._cache_existing_artwork(item, existing[0])
                await self._index_metadata(category_id, item.key, cached)
                continue
            metadata = await self._enrich_item(item)
            if metadata:
                await self._upsert_metadata(category_id, item.key, metadata)
                await self._index_metadata(category_id, item.key, metadata)
            if index < len(enabled) - 1:
                await asyncio.sleep(self.ENRICH_COOLDOWN)

    async def _cache_existing_artwork(self, item: CategoryItem, metadata: dict[str, Any]) -> dict[str, Any]:
        """Cache artwork for an existing metadata row if only a remote poster is stored."""
        if metadata.get('local_poster_url') or not metadata.get('poster_path'):
            return metadata
        category = self._category_for(item)
        if not category:
            return metadata
        updated = await category.cache_metadata_artwork(
            item, dict(metadata), self._metadata_context,
            provider=str(metadata.get('provider') or f'{category.category_id}_metadata'),
        )
        if updated != metadata:
            await self._upsert_metadata(self._category_id_for(item), item.key, updated)
        return updated

    async def _enrich_signal_metadata(
        self,
        category_id: str,
        item_id: str,
        display_name: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge caller metadata with category-owned enrichment when available."""
        normalized = TasteSignalNormalizer.normalize_metadata(metadata or {})
        category = self._category_for_id(category_id)
        if not category or not hasattr(category, "enrich_taste_metadata"):
            return normalized
        key = item_id or display_name
        if not key:
            return normalized
        try:
            item = category.create_item(key, display_name=display_name) if hasattr(category, "create_item") else CategoryItem(key=key, display_name=display_name)
            enriched = await category.enrich_taste_metadata(item, self._metadata_context)
        except Exception:
            enriched = None
        if enriched:
            merged = dict(TasteSignalNormalizer.normalize_metadata(enriched))
            merged.update(normalized)
            return merged
        return normalized

    async def _load_facet_scores(self, category_id: str, user_id: str | None = None) -> list[dict[str, Any]]:
        """Load derived facet scores when the repository supports them."""
        if not hasattr(self._db, "system") or not hasattr(self._db.system, "list_taste_facet_scores"):
            return []
        try:
            return await self._db.system.list_taste_facet_scores(user_id=user_id, category_id=category_id)
        except Exception:
            return []

    @staticmethod
    def _shape_facet_scores(
        rows: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, float]], dict[str, list[str]], dict[str, list[str]], dict[str, int]]:
        """Shape repository facet rows for the prompt-facing TasteProfile."""
        affinities: dict[str, dict[str, float]] = defaultdict(dict)
        positive: dict[str, list[str]] = defaultdict(list)
        negative: dict[str, list[str]] = defaultdict(list)
        counts: dict[str, int] = {}
        for row in rows:
            key = str(row.get("facet_key") or "")
            value = str(row.get("facet_value") or "")
            if not key or not value:
                continue
            affinity = float(row.get("affinity") or 0.0)
            affinities[key][value] = affinity
            counts[f"{key}:{value}"] = int(row.get("evidence_count") or 0)
            if affinity > 0 and value not in positive[key]:
                positive[key].append(value)
            if affinity < 0 and value not in negative[key]:
                negative[key].append(value)
        return dict(affinities), dict(positive), dict(negative), counts

    @staticmethod
    def _summarize_profile(
        primary_genres: list[str],
        facet_affinities: dict[str, dict[str, float]],
        positive_contexts: dict[str, list[str]],
        negative_contexts: dict[str, list[str]],
    ) -> str:
        """Create a compact, grounded summary from derived evidence."""
        parts: list[str] = []
        strongest: list[str] = []
        for key, values in facet_affinities.items():
            for value, score in sorted(values.items(), key=lambda entry: abs(entry[1]), reverse=True)[:3]:
                if score > 0:
                    strongest.append(f"likes {value} ({key})")
                elif score < 0:
                    strongest.append(f"avoids {value} ({key})")
        if primary_genres:
            parts.append("Genre evidence: " + ", ".join(primary_genres[:5]))
        if strongest:
            parts.append("Strongest facet evidence: " + ", ".join(strongest[:8]))
        if positive_contexts.get("aspects"):
            parts.append("Positive reasons: " + ", ".join(positive_contexts["aspects"][:6]))
        if negative_contexts.get("aspects"):
            parts.append("Negative reasons: " + ", ".join(negative_contexts["aspects"][:6]))
        return " ".join(parts)

    async def _enrich_item(self, item: CategoryItem) -> dict[str, Any] | None:
        """Delegate taste metadata enrichment to the owning category."""
        category = self._category_for(item)
        if not category:
            return None
        metadata = await category.enrich_taste_metadata(item, self._metadata_context)
        if not metadata:
            return None
        metadata.setdefault('category_id', category.category_id)
        metadata.setdefault('item_id', item.key)
        metadata.setdefault('display_name', item.display_name or item.key)
        return metadata

    def _category_for(self, item: CategoryItem) -> Any | None:
        """Return the owning category for a tracked item, if registered."""
        if not self._categories:
            return None
        return self._categories.get(self._category_id_for(item))

    async def _metadata_rows(self, category_id: str, item_id: str) -> list[dict[str, Any]]:
        """Load generic metadata rows for one category item."""
        rows = await self._db.media.get_category_metadata(category_id, item_id)
        return [row.get('metadata') or {} for row in rows if row.get('metadata')]

    async def _upsert_metadata(self, category_id: str, item_id: str, metadata: dict[str, Any]) -> None:
        """Persist one category-owned provider metadata envelope."""
        category = self._category_for_id(category_id)
        provider = category.taste_metadata_provider_name(metadata) if category else str(metadata.get('provider') or f'{category_id}_taste')
        external_id = str(metadata.get('external_id') or metadata.get('tmdb_id') or metadata.get('tvmaze_id') or '')
        await self._db.media.upsert_category_metadata(category_id, item_id, provider, metadata, external_id)

    def _category_for_id(self, category_id: str) -> Any | None:
        """Return a category by id when a registry was provided."""
        if not self._categories:
            return None
        return self._categories.get(category_id)

    async def _index_metadata(self, category_id: str, item_id: str, metadata: dict[str, Any]) -> None:
        """Index metadata overview in the vector store when available."""
        if not self._vector_store or not metadata.get('overview'):
            return
        digest = hashlib.sha256(f"{category_id}:{item_id}".encode()).hexdigest()
        stable_key = int(digest[:12], 16) % self.VECTOR_ID_OFFSET
        await self._vector_store.upsert(
            item_id=self.VECTOR_ID_OFFSET + stable_key,
            text=metadata['overview'],
            metadata={'category_id': category_id, 'item_id': item_id},
        )

    @staticmethod
    def _metadata_weight_for_signal(signal: dict[str, Any], weight: float) -> float:
        """Return a cautious metadata contribution for one taste event.

        Item-level sentiment is strong for the item itself, but broad metadata
        dimensions such as genre/cast should move slowly unless the user gave a
        reason or repeated evidence accumulates.
        """
        polarity = str(signal.get("polarity") or "").lower()
        facets = signal.get("interpreted_facets") or {}
        has_reason = False
        if isinstance(facets, dict):
            has_reason = any(facets.get(key) for key in (
                "liked", "liked_aspects", "positive",
                "disliked", "disliked_aspects", "negative", "avoid", "dimensions",
            ))
        if polarity == "negative":
            return weight * (0.25 if has_reason else 0.08)
        if polarity == "positive":
            return weight * (0.55 if has_reason else 0.35)
        if polarity in {"interest", "engagement"}:
            return weight * 0.35
        return weight * 0.05

    @staticmethod
    def _merge_metadata(
        metadata: dict[str, Any],
        genres: Counter[str],
        actors: Counter[str],
        directors: Counter[str],
        writers: Counter[str],
    ) -> None:
        """Merge one metadata envelope into aggregate counters."""
        for genre in metadata.get('genres') or []:
            genres[str(genre)] += 1
        for actor in metadata.get('cast_names') or []:
            actors[str(actor)] += 1
        for director in metadata.get('directors') or []:
            directors[str(director)] += 1
        for writer in metadata.get('writers') or []:
            writers[str(writer)] += 1


    @staticmethod
    def _merge_weighted_metadata(
        metadata: dict[str, Any],
        weight: float,
        genres: Counter[str],
        actors: Counter[str],
        directors: Counter[str],
        writers: Counter[str],
    ) -> None:
        """Merge metadata using a signed preference weight."""
        for genre in metadata.get('genres') or []:
            genres[str(genre)] += weight
        for actor in metadata.get('cast_names') or metadata.get('creators') or []:
            actors[str(actor)] += weight
        for director in metadata.get('directors') or []:
            directors[str(director)] += weight
        for writer in metadata.get('writers') or []:
            writers[str(writer)] += weight

    @staticmethod
    def _merge_category_dimensions(
        metadata: dict[str, Any],
        weight: float,
        dimensions: dict[str, Counter[str]],
    ) -> None:
        """Merge category-defined preference dimensions generically.

        New categories such as video games can provide ``platforms``,
        ``studios``, ``mechanics``, ``moods``, or any of the common list keys
        without changing core profile code.
        """
        dimension_keys = (
            "platforms", "studios", "publishers", "developers", "creators",
            "tags", "themes", "moods", "mechanics", "game_modes",
            "storefronts", "formats", "languages", "networks",
        )
        for key in dimension_keys:
            raw = metadata.get(key)
            if not raw:
                continue
            values = raw if isinstance(raw, list) else [raw]
            bucket = dimensions.setdefault(key, Counter())
            for value in values:
                if isinstance(value, dict):
                    value = value.get("name") or value.get("title") or value.get("id")
                if value:
                    bucket[str(value)] += weight

    @staticmethod
    def _top_dimensions(dimensions: dict[str, Counter[str]]) -> dict[str, dict[str, float]]:
        """Return compact top values for category-defined dimensions."""
        return {key: dict(counter.most_common(12)) for key, counter in dimensions.items() if counter}

    @staticmethod
    def _default_signal_weight(signal_type: str) -> float:
        """Return a conservative default weight by signal type."""
        weights = {
            'like': 1.0,
            'favorite': 1.25,
            'download': 0.85,
            'watch': 0.75,
            'research': 0.45,
            'mention': 0.25,
            'dislike': 1.0,
            'reject': 0.75,
            'negative': 0.75,
        }
        return weights.get(signal_type, 0.35)

    @staticmethod
    def _category_id_for(item: CategoryItem) -> str:
        """Return the storage category id for an item."""
        return getattr(item, 'category_id', getattr(item, 'item_type', 'media')) or 'media'


    def format_category_profile_for_prompt(self, category_id: str, profile: TasteProfile) -> str:
        """Render a category-specific taste block for LLM prompts."""
        body = self.format_for_prompt(profile)
        category = self._category_for_id(category_id)
        guidance: list[str] = []
        if category and hasattr(category, "taste_profile_llm_instructions"):
            try:
                guidance = [str(line) for line in category.taste_profile_llm_instructions() if line]
            except Exception:
                guidance = []
        schema_keys: list[str] = []
        if category and hasattr(category, "taste_profile_schema"):
            try:
                schema = category.taste_profile_schema() or {}
                for value in schema.values():
                    if isinstance(value, list):
                        schema_keys.extend(str(entry) for entry in value[:12])
            except Exception:
                schema_keys = []
        sections: list[str] = []
        if body:
            sections.append(body)
        if guidance:
            sections.append("Category taste rules: " + " ".join(guidance[:3]))
        if schema_keys:
            unique_keys: list[str] = []
            for key in schema_keys:
                if key not in unique_keys:
                    unique_keys.append(key)
            sections.append("Useful metadata keys: " + ", ".join(unique_keys[:18]))
        if not sections:
            return ""
        return f"CATEGORY TASTE PROFILE [{category_id}]:\n" + "\n".join(sections)

    def format_for_prompt(self, profile: TasteProfile) -> str:
        """Render a taste profile for LLM prompt context."""
        lines: list[str] = []
        if getattr(profile, "summary", ""):
            lines.append(f"Evidence-based taste summary: {profile.summary}")
        if profile.genres.primary:
            lines.append(f"Genre preferences: {', '.join(profile.genres.primary)}")
            top_genres = list(profile.genres.counts.items())[:5]
            lines.append('Genre breakdown: ' + ', '.join(f'{genre} ({count})' for genre, count in top_genres))
        if profile.people.actors:
            top_actors = list(profile.people.actors.items())[:5]
            lines.append('Frequent actors: ' + ', '.join(f'{actor} ({count})' for actor, count in top_actors))
        if profile.people.directors:
            top_directors = list(profile.people.directors.items())[:3]
            lines.append('Frequent directors: ' + ', '.join(f'{name} ({count})' for name, count in top_directors))
        if profile.metadata_dimensions:
            for key, values in list(profile.metadata_dimensions.items())[:6]:
                top_values = list(values.items())[:5]
                if top_values:
                    label = key.replace("_", " ").title()
                    lines.append(label + ": " + ", ".join(f"{name} ({score:g})" for name, score in top_values))
        if getattr(profile, "positive_contexts", None):
            positives = []
            for key, values in list(profile.positive_contexts.items())[:4]:
                positives.extend(f"{value} ({key})" for value in values[:3])
            if positives:
                lines.append("Positive taste evidence: " + ", ".join(positives[:8]))
        if getattr(profile, "negative_contexts", None):
            negatives = []
            for key, values in list(profile.negative_contexts.items())[:4]:
                negatives.extend(f"{value} ({key})" for value in values[:3])
            if negatives:
                lines.append("Negative taste evidence: " + ", ".join(negatives[:8]))
        if profile.top_items:
            lines.append(f"Interest/liked item evidence: {', '.join(profile.top_items[:5])}")
        if profile.category_counts:
            summary = ', '.join(f'{category}: {count}' for category, count in sorted(profile.category_counts.items()))
            lines.append(f'Library composition by category: {summary}')
        return '\n'.join(lines)
