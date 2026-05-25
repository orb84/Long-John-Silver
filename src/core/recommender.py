"""
Recommendation engine for LJS.

Combines Trakt/TMDB recommendations, behavioral profile, and
taste profile (genres, actors, directors from category metadata) to
generate personalized category item recommendations. Runs weekly as a
scheduled job and sends proactive notifications.
"""

from datetime import datetime, timezone
from loguru import logger
from typing import Optional
from src.integrations.trakt import TraktClient
from src.core.behavior_tracker import BehaviorTracker
from src.core.database import Database
from src.core.notifications import NotificationService
from src.core.models import TasteProfile, CategoryItem


from src.core.vector_store import VectorStore


class RecommendationEngine:
    """Generates personalized category item recommendations."""

    def __init__(
        self,
        trakt_client: TraktClient | None,
        behavior_tracker: BehaviorTracker | None,
        db: Database,
        notifications: NotificationService,
        vector_store: VectorStore | None = None,
    ):
        self._trakt = trakt_client
        self._behavior = behavior_tracker
        self._db = db
        self._notifications = notifications
        self._vector_store = vector_store
        self._taste_vector: Optional[list[float]] = None

    async def get_recommendations(
        self,
        user_id: str | None = None,
        limit: int = 5,
        taste_profile: TasteProfile | None = None,
        tracked_items: list[CategoryItem] | None = None,
    ) -> list[dict]:
        """Generate personalized recommendations using hybrid scoring."""
        candidates: list[dict] = []
        tracked_names = {item.key.lower() for item in (tracked_items or [])}

        # 1. Warm up taste vector if possible
        if not self._taste_vector and taste_profile and self._vector_store:
            self._taste_vector = await self._calculate_user_taste_vector(taste_profile)

        # Source 1: Trakt recommendations
        if self._trakt:
            try:
                trakt_recs = await self._trakt.get_personal_recommendations(limit=limit * 3)
                source_name = "trakt_personal"
                if not trakt_recs:
                    trakt_recs = await self._trakt.get_recommended_shows(limit=limit * 3)
                    source_name = "trakt_trending"

                for show in trakt_recs:
                    show_data = show.get("show", show)
                    title = show_data.get("title", "Unknown")
                    if title.lower() in tracked_names:
                        continue

                    score = self._score_trakt_candidate(show_data, taste_profile)
                    if source_name == "trakt_personal":
                        score = min(score + 0.2, 1.0)

                    candidates.append({
                        "title": title,
                        "year": show_data.get("year"),
                        "source": source_name,
                        "score": score,
                        "genres": show_data.get("genres", []),
                        "overview": show_data.get("overview", ""),
                    })
            except Exception as e:
                logger.warning(f"Trakt recommendations failed: {e}")

        # ... (rest of sources)

        # Source 2: TMDB genre-based recommendations from taste profile
        if taste_profile and taste_profile.genres.primary:
            try:
                tmdb_recs = await self._get_tmdb_genre_recommendations(taste_profile)
                for rec in tmdb_recs:
                    if rec["title"].lower() in tracked_names:
                        continue
                    candidates.append(rec)
            except Exception as e:
                logger.warning(f"TMDB genre recommendations failed: {e}")

        # Source 3: Behavioral/library favorites
        if self._behavior and user_id:
            try:
                profile = await self._behavior.get_behavior_profile(user_id)
                top_items = profile.get("top_items", []) or profile.get("top_media", [])
                for item_name in top_items[:3]:
                    if item_name.lower() in tracked_names:
                        continue
                    candidates.append({
                        "title": item_name,
                        "year": None,
                        "source": "behavior",
                        "score": 0.35,
                        "genres": [],
                        "overview": "",
                    })
            except Exception as e:
                logger.warning(f"Behavioral recommendation failed: {e}")

        if not candidates:
            logger.info("No recommendation candidates available")
            return []

        # 2. Semantic Reranking
        if self._vector_store and self._taste_vector:
            logger.info(f"Performing semantic reranking for {len(candidates)} candidates")
            for cand in candidates:
                semantic_score = await self._get_semantic_score(cand)
                # Boost candidate score by semantic similarity (+0.25 max)
                cand["score"] = min(cand["score"] + (semantic_score * 0.25), 1.0)
                if semantic_score > 0.7:
                    cand["is_semantic_match"] = True

        # Deduplicate by lowercase title, keeping highest score
        seen: dict[str, dict] = {}
        for c in candidates:
            key = c["title"].lower()
            if key not in seen or c["score"] > seen[key]["score"]:
                seen[key] = c

        unique = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
        recommendations = unique[:limit]

        # Add human-readable reason
        for rec in recommendations:
            rec["reason"] = self._format_reason(rec, taste_profile)

        logger.info(f"Generated {len(recommendations)} recommendations")
        return recommendations

    async def _calculate_user_taste_vector(self, taste_profile: TasteProfile) -> Optional[list[float]]:
        """Compute an average embedding vector from highlighted library items."""
        if not self._vector_store:
            return None

        vectors = []
        for name in taste_profile.top_items[:10]:
            for category_id in taste_profile.category_counts:
                rows = await self._db.media.get_category_metadata(category_id, name)
                if rows and rows[0].get("metadata", {}).get("overview"):
                    vectors.append(await self._vector_store.embed(rows[0]["metadata"]["overview"]))
                    break

        if not vectors:
            return None

        dim = self._vector_store.DIMENSION
        avg_vector = [0.0] * dim
        for vector in vectors:
            for index in range(dim):
                avg_vector[index] += vector[index]

        return [value / len(vectors) for value in avg_vector]

    async def _get_semantic_score(self, candidate: dict) -> float:
        """Calculate cosine similarity between candidate overview and user taste vector."""
        if not self._taste_vector or not candidate.get("overview") or not self._vector_store:
            return 0.0
            
        try:
            cand_vector = await self._vector_store.embed(candidate["overview"])
            # Cosine similarity (normalized dot product)
            dot_product = sum(a * b for a, b in zip(self._taste_vector, cand_vector))
            mag_a = sum(a * a for a in self._taste_vector) ** 0.5
            mag_b = sum(b * b for b in cand_vector) ** 0.5
            
            if mag_a == 0 or mag_b == 0:
                return 0.0
                
            return max(0.0, dot_product / (mag_a * mag_b))
        except Exception:
            return 0.0

    def _score_trakt_candidate(
        self, show_data: dict, taste_profile: TasteProfile | None
    ) -> float:
        """Score a Trakt candidate against the user's taste profile."""
        score = 0.5
        if not taste_profile:
            return score

        show_genres: list[str] = show_data.get("genres", [])
        if show_genres and taste_profile.genres.counts:
            genre_overlap = sum(
                1 for g in show_genres
                if g.lower() in {k.lower() for k in taste_profile.genres.counts}
            )
            if genre_overlap:
                score += min(genre_overlap * 0.08, 0.3)

        return round(score, 2)

    async def _get_tmdb_genre_recommendations(
        self, taste_profile: TasteProfile
    ) -> list[dict]:
        """STUB: Would call TMDB discover endpoint."""
        return []

    def _format_reason(
        self, rec: dict, taste_profile: TasteProfile | None
    ) -> str:
        """Build a human-readable reason string for a recommendation."""
        source = rec.get("source", "")
        genres = rec.get("genres", [])
        is_semantic = rec.get("is_semantic_match", False)

        parts = []
        if source == "trakt_personal":
            parts.append("Personalized for you")
        elif source == "trakt_trending":
            parts.append("Trending on Trakt")
        elif source == "behavior":
            parts.append("Based on history")
        
        if is_semantic:
            parts.append("Strong taste match")

        if genres and taste_profile and taste_profile.genres.primary:
            matching = [g for g in genres if g in taste_profile.genres.primary]
            if matching:
                parts.append(f"matches your {matching[0]} taste")

        return " · ".join(parts) if parts else "Recommended for you"

    async def send_recommendations(
        self,
        user_id: str | None = None,
        taste_profile: TasteProfile | None = None,
    ) -> None:
        """Generate recommendations and send as a notification.

        Runs as a weekly scheduled job. Formats recommendations into
        a pirate-themed notification message.
        """
        # Check weekly cooldown limit (7 days minus 1 hour for minor scheduler drift)
        last_sent_str = await self._db.system.get_preference("last_recommendation_time", "")
        if last_sent_str:
            try:
                last_sent = datetime.fromisoformat(last_sent_str)
                elapsed = datetime.now(timezone.utc) - last_sent
                if elapsed.total_seconds() < 7 * 24 * 3600 - 3600:
                    logger.info("Skipping weekly recommendation send (cooldown active)")
                    return
            except Exception as e:
                logger.warning(f"Error parsing last_recommendation_time preference: {e}")

        recs = await self.get_recommendations(
            user_id=user_id, limit=5, taste_profile=taste_profile,
        )
        if not recs:
            return

        lines = ["Ahoy Captain! Based on what's in your library, you might enjoy:"]
        for i, rec in enumerate(recs, 1):
            year_str = f" ({rec['year']})" if rec.get("year") else ""
            lines.append(f"  {i}. {rec['title']}{year_str} — {rec.get('reason', '')}")

        message = "\n".join(lines)

        try:
            await self._notifications.send_message(message, "Weekly Recommendations")
            await self._db.system.set_preference("last_recommendation_time", datetime.now(timezone.utc).isoformat())
            logger.info(f"Sent {len(recs)} recommendations")
        except Exception as e:
            logger.warning(f"Failed to send recommendation notification: {e}")
