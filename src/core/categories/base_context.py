"""Assistant-context and detail payload mixins for media categories.

The methods here are intentionally category-neutral defaults.  Concrete
categories can override the public hooks to expose richer domain units while
keeping web/UI serialization out of the core MediaCategory class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from src.core.categories.identity import canonical_item_key, clean_display_title, looks_like_dirty_release_title
from src.core.library_objects import CanonicalLibraryObjectContext

if TYPE_CHECKING:
    from src.core.models import Settings


class CategoryContextMixin:
    """Build prompt-safe item summaries and UI detail payloads.

    Override the public packet-building methods when a category has domain
    concepts such as seasons, chapters, albums, or editions.  Private helpers
    should stay side-effect free except for optional artwork caching.
    """

    async def build_llm_context_packet(
        self,
        user_message: str,
        intent: Any,
        settings: "Settings",
        db: Any | None = None,
        max_items: int = 40,
        max_units: int = 120,
    ) -> dict[str, Any]:
        """Build a category-owned library context packet for one assistant run.

        The assistant core only decides *which* category is active. The owning
        category decides what tracked items, units, metadata, and hints are
        meaningful for the LLM. Custom categories get this safe generic packet
        automatically and can override the hook for richer domain semantics.
        """
        tracked = self._tracked_items_from_settings(settings)
        matched = self._matched_tracked_items(user_message, tracked)
        # For matched-item requests, expose only the target item's canonical
        # state. For broad category words such as "movie" or "show" without a
        # matched tracked item, do *not* dump dozens of unrelated library rows
        # into the prompt. That bloat was repeatedly pushing short follow-ups
        # out of context. The LLM can call library/query tools when the user
        # actually asks for a complete category inventory.
        if matched:
            summaries = [self.summarize_item_for_llm(item) for item in matched[:5]]
            other_keys = [
                str(getattr(item, "key", "") or "")
                for item in tracked
                if item not in matched
            ][:20]
            context_scope = "matched_item"
        else:
            summaries = []
            other_keys = [
                str(getattr(item, "key", "") or "")
                for item in tracked[: min(max_items, 20)]
            ]
            context_scope = "category_router_overview"
        download_profile = self.category_download_profile(settings) if hasattr(self, "category_download_profile") else {}
        media_language_keys = (
            "language", "preferred_language", "audio_language", "preferred_audio_language",
            "audio_languages", "preferred_audio_languages", "subtitle_languages",
        )
        media_language_preferences = {
            key: download_profile.get(key)
            for key in media_language_keys
            if isinstance(download_profile, dict) and download_profile.get(key) not in (None, "", [], {})
        }
        packet: dict[str, Any] = {
            "category_id": self.category_id,
            "display_name": self.display_name,
            "intent": getattr(intent, "value", str(intent)),
            "download_preferences": media_language_preferences,
            "tracked_items_count": len(tracked),
            "tracked_items": summaries,
            "matched_tracked_items": [self.summarize_item_for_llm(item) for item in matched[:5]],
            "other_tracked_item_keys_sample": other_keys,
            "context_scope": context_scope,
            "llm_contract": [
                "Use exact tracked item keys when a matched_tracked_item fits the user's request.",
                "If the requested item is not tracked, use this category's metadata/research workflow before acting.",
                "If context_scope is category_router_overview, treat the item sample as orientation only; do not assume the request targets those items.",
                "Do not embed category unit information (season, episode, chapter, disc, track) inside name fields when a tool has dedicated fields for those values.",
                "Treat deterministic parser output as a fallback only; localized phrases in the user request should be interpreted by the LLM and expressed in structured tool arguments.",
                "download_preferences describe media/audio/subtitle preferences, not the language the assistant should use when replying.",
            ],
        }
        if matched:
            packet["target_item"] = await self.build_item_context_packet(
                matched[0], settings=settings, db=db, max_units=max_units,
            )
        return packet

    def summarize_item_for_llm(self, item: Any) -> dict[str, Any]:
        """Return a compact category-owned tracked-item summary for prompts."""
        quality = getattr(item, "quality", None)
        return {
            "key": getattr(item, "key", ""),
            "display_name": getattr(item, "display_name", None) or getattr(item, "key", ""),
            "category_id": getattr(item, "item_type", getattr(item, "category_id", self.category_id)),
            "enabled": bool(getattr(item, "enabled", True)),
            "language": getattr(item, "language", None),
            "auto_download": getattr(item, "auto_download", None),
            "quality": quality.model_dump(mode="json") if hasattr(quality, "model_dump") else quality,
            "progress": item.format_progress() if hasattr(item, "format_progress") else "—",
        }

    async def build_item_context_packet(
        self,
        item: Any,
        settings: "Settings",
        db: Any | None = None,
        max_units: int = 120,
    ) -> dict[str, Any]:
        """Return detailed category-owned context for one tracked item."""
        item_id = str(getattr(item, "key", "") or getattr(item, "item_id", ""))
        packet = self.summarize_item_for_llm(item)
        packet.update({"item_id": item_id, "tracked": True})
        if not db or not getattr(db, "media", None) or not item_id:
            return packet
        units: list[dict[str, Any]] = []
        metadata_rows: list[dict[str, Any]] = []
        try:
            progress = await db.media.get_item_progress(self.category_id, item_id)
            if progress:
                packet["library_progress"] = progress
        except Exception as exc:
            logger.debug(f"{self.category_id} context progress skipped for {item_id}: {exc}")
        try:
            units = await db.media.list_category_units(self.category_id, item_id)
            packet["units_count"] = len(units)
            packet["units"] = [self.summarize_unit_for_llm(unit) for unit in units[:max_units]]
        except Exception as exc:
            logger.debug(f"{self.category_id} context units skipped for {item_id}: {exc}")
        try:
            metadata_rows = await db.media.get_category_metadata(self.category_id, item_id)
            if metadata_rows:
                packet["metadata"] = [self.summarize_metadata_for_llm(row) for row in metadata_rows[:3]]
        except Exception as exc:
            logger.debug(f"{self.category_id} context metadata skipped for {item_id}: {exc}")
        try:
            canonical = self.build_library_object(CanonicalLibraryObjectContext(
                category_id=self.category_id,
                item_id=item_id,
                item=self._item_to_public_dict(item, item_id=item_id),
                units=units,
                metadata_rows=metadata_rows,
                settings_item=item,
            ))
            packet["canonical_library_object"] = self.summarize_library_object_for_llm(canonical)
        except Exception as exc:
            logger.debug(f"{self.category_id} canonical context skipped for {item_id}: {exc}")
        return packet


    def summarize_library_object_for_llm(self, canonical: dict[str, Any]) -> dict[str, Any]:
        """Return a compact category-neutral canonical object summary for prompts.

        The full canonical object can be large and category-specific.  This
        summary exposes stable high-level facts and lets concrete categories
        override the hook for richer unit semantics such as seasons, chapters,
        versions, editions, or tracks.
        """
        computed = canonical.get("computed") if isinstance(canonical, dict) else {}
        computed = computed if isinstance(computed, dict) else {}
        safe_computed_keys = (
            "downloaded_episode_count", "downloaded_file_count", "episode_count",
            "season_count", "missing_episode_count", "provider_aired_episode_count",
            "audio_languages", "subtitle_languages", "quality_gaps",
            "language_gaps", "has_local_files", "total_size_bytes",
        )
        sections: dict[str, int] = {}
        for key in ("seasons", "files", "volumes", "versions", "tracks", "units"):
            value = canonical.get(key) if isinstance(canonical, dict) else None
            if isinstance(value, list):
                sections[key] = len(value)
        return {
            "category_id": canonical.get("category_id") if isinstance(canonical, dict) else None,
            "item_id": canonical.get("item_id") if isinstance(canonical, dict) else None,
            "display_name": canonical.get("display_name") if isinstance(canonical, dict) else None,
            "sections": sections,
            "computed": {key: computed.get(key) for key in safe_computed_keys if key in computed},
        }

    def summarize_unit_for_llm(self, unit: dict[str, Any]) -> dict[str, Any]:
        """Summarize a category unit without assuming TV/movie semantics."""
        return {
            "unit_key": unit.get("unit_key"),
            "unit_type": unit.get("unit_type"),
            "display_name": unit.get("display_name"),
            "status": unit.get("status"),
            "quality": unit.get("quality"),
            "language": unit.get("language"),
        }

    def summarize_metadata_for_llm(self, row: dict[str, Any]) -> dict[str, Any]:
        """Summarize provider metadata for a prompt-safe packet."""
        metadata = row.get("metadata") or {}
        keys = (
            "title", "display_name", "year", "overview", "status", "genres",
            "tmdb_id", "tvmaze_id", "imdb_id", "runtime", "poster_path",
            "poster_url", "local_poster_url", "number_of_seasons", "number_of_episodes",
        )
        compact = {key: metadata.get(key) for key in keys if metadata.get(key) not in (None, "", [])}
        overview = compact.get("overview")
        if isinstance(overview, str) and len(overview) > 500:
            compact["overview"] = overview[:500] + "…"
        return {
            "provider": row.get("provider"),
            "external_id": row.get("external_id"),
            "refreshed_at": row.get("refreshed_at"),
            "metadata": compact,
        }

    async def build_item_detail_payload(
        self,
        item_id: str,
        item: Any,
        settings: "Settings",
        db: Any | None = None,
        artwork_manager: Any | None = None,
    ) -> dict[str, Any]:
        """Build the category-owned item detail payload consumed by the UI."""
        payload = self._item_to_public_dict(item, item_id=item_id)
        payload.setdefault("category_id", self.category_id)
        payload.setdefault("item_id", item_id)
        payload.setdefault("key", item_id)
        payload.setdefault("display_name", payload.get("display_name") or payload.get("key") or item_id)
        units: list[dict[str, Any]] = []
        metadata_rows: list[dict[str, Any]] = []
        if db and getattr(db, "media", None):
            try:
                units = await db.media.list_category_units(self.category_id, item_id)
                payload["units"] = units
                payload["unit_groups"] = self.group_units_for_detail(units)
                payload["total_units"] = len(units)
            except Exception as exc:
                logger.debug(f"{self.category_id} detail units skipped for {item_id}: {exc}")
                payload.setdefault("units", [])
                payload.setdefault("unit_groups", {})
            try:
                progress = await db.media.get_item_progress(self.category_id, item_id)
                if progress:
                    payload["progress"] = progress
            except Exception as exc:
                logger.debug(f"{self.category_id} detail progress skipped for {item_id}: {exc}")
            try:
                metadata_rows = await db.media.get_category_metadata(self.category_id, item_id)
                metadata_rows = await self._maybe_cache_detail_artwork(
                    item_id, metadata_rows, db=db, artwork_manager=artwork_manager,
                )
                payload["metadata_rows"] = metadata_rows
                metadata = metadata_rows[0].get("metadata") if metadata_rows else {}
                if metadata:
                    payload["metadata"] = metadata
                    self._merge_display_metadata(payload, metadata)
            except Exception as exc:
                logger.debug(f"{self.category_id} detail metadata skipped for {item_id}: {exc}")
        try:
            canonical = self.build_library_object(CanonicalLibraryObjectContext(
                category_id=self.category_id,
                item_id=item_id,
                item=payload,
                units=units,
                metadata_rows=metadata_rows,
                settings_item=item,
            ))
            # Detail payloads are another canonical-object consumer.  Do not let
            # the modal reconstruct seasons/files from raw unit rows: expose the
            # category-built object and mirror its common sections for older UI
            # components that have not yet been fully schema-driven.
            payload["canonical_object"] = canonical
            payload["units"] = canonical.get("units") or payload.get("units") or []
            payload["unit_groups"] = self.group_units_for_detail(payload["units"])
            payload["computed"] = canonical.get("computed") or {}
            for section_key in ("seasons", "files", "volumes", "versions", "tracks"):
                if canonical.get(section_key) is not None:
                    payload[section_key] = canonical.get(section_key)
            computed = payload["computed"]
            if computed.get("missing_episodes") is not None:
                payload["missing_episodes"] = computed.get("missing_episodes")
            payload["total_units"] = len(payload.get("units") or [])
        except Exception as exc:
            logger.debug(f"{self.category_id} detail canonical object skipped for {item_id}: {exc}")
        direct_metadata = payload.get("metadata") or {}
        self._merge_display_metadata(payload, direct_metadata)
        return payload

    async def maybe_cache_detail_artwork(
        self,
        item_id: str,
        metadata_rows: list[dict[str, Any]],
        db: Any | None,
        artwork_manager: Any | None,
    ) -> list[dict[str, Any]]:
        """Public seam for UI/list routers to cache artwork through category logic."""
        return await self._maybe_cache_detail_artwork(item_id, metadata_rows, db=db, artwork_manager=artwork_manager)

    async def _maybe_cache_detail_artwork(
        self,
        item_id: str,
        metadata_rows: list[dict[str, Any]],
        db: Any | None,
        artwork_manager: Any | None,
    ) -> list[dict[str, Any]]:
        """Cache poster artwork for detail payloads when a manager is available."""
        if not artwork_manager or not metadata_rows:
            return metadata_rows
        updated_rows: list[dict[str, Any]] = []
        for row in metadata_rows:
            metadata = dict(row.get("metadata") or {})
            if metadata.get("local_poster_url") or not (metadata.get("poster_path") or metadata.get("poster_url")):
                updated_rows.append(row)
                continue
            try:
                cached = await artwork_manager.cache_poster_from_metadata(
                    self.category_id, item_id, metadata, provider=str(row.get("provider") or "metadata"),
                )
                if cached and cached != metadata and db and getattr(db, "media", None):
                    await db.media.upsert_category_metadata(
                        self.category_id, item_id, str(row.get("provider") or "metadata"),
                        cached, str(row.get("external_id") or ""),
                    )
                row = dict(row)
                row["metadata"] = cached or metadata
            except Exception as exc:
                logger.debug(f"{self.category_id} detail artwork cache skipped for {item_id}: {exc}")
            updated_rows.append(row)
        return updated_rows

    def merge_display_metadata(self, payload: dict[str, Any], metadata: dict[str, Any]) -> None:
        """Public seam for routers to apply category-owned display metadata."""
        self._merge_display_metadata(payload, metadata)

    def _merge_display_metadata(self, payload: dict[str, Any], metadata: dict[str, Any]) -> None:
        """Merge common display metadata into a detail payload.

        Provider snapshots may know the canonical title even when the catalog
        item key came from a dirty release folder. In that case, prefer the
        provider display title so library cards show the clean canonical name
        instead of a raw release-folder string.
        """
        if not metadata:
            return
        provider_name = clean_display_title(
            metadata.get("display_name") or metadata.get("title") or metadata.get("name") or "",
            fallback="",
        )
        if provider_name:
            current = payload.get("display_name") or payload.get("name") or payload.get("key") or payload.get("item_id") or ""
            item_id = payload.get("item_id") or payload.get("key") or payload.get("name") or ""
            current_key = canonical_item_key(current)
            item_key = canonical_item_key(item_id)
            if not current or looks_like_dirty_release_title(current) or looks_like_dirty_release_title(item_id) or current_key == item_key:
                payload["display_name"] = provider_name
            payload["metadata_display_name"] = provider_name
        for key in (
            "overview", "genres", "cast_names", "status", "year", "runtime",
            "tmdb_id", "tvmaze_id", "imdb_id", "poster_path", "poster_url", "local_poster_url",
        ):
            value = metadata.get(key)
            if value not in (None, "", []):
                payload.setdefault(key, value)
        poster_url = metadata.get("local_poster_url") or metadata.get("poster_url")
        poster_path = metadata.get("poster_path")
        if not poster_url and poster_path:
            poster_url = self._poster_display_url(str(poster_path))
        if poster_url:
            payload["poster_url"] = poster_url

    @staticmethod
    def _poster_display_url(poster_path: str | None) -> str | None:
        """Convert local/TMDB poster identifiers into browser display URLs."""
        if not poster_path:
            return None
        value = str(poster_path)
        if value.startswith(("http://", "https://", "/category-data/")):
            return value
        if value.startswith("/"):
            return f"https://image.tmdb.org/t/p/w500{value}"
        return None

    def group_units_for_detail(self, units: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Group units by type and a category-neutral group key for UI details."""
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for unit in units:
            unit_type = str(unit.get("unit_type") or "unit")
            group_key = str(unit.get("season") or unit.get("disc") or unit.get("album") or "default")
            grouped.setdefault(unit_type, {}).setdefault(group_key, []).append(unit)
        for unit_groups in grouped.values():
            for group_units in unit_groups.values():
                group_units.sort(key=lambda row: (row.get("sort_index") or 0, row.get("season") or 0, row.get("episode") or 0, row.get("unit_key") or ""))
        return grouped

    def _tracked_items_from_settings(self, settings: "Settings") -> list[Any]:
        """Return settings items owned by this category."""
        items = getattr(settings, "tracked_items", []) if settings else []
        return [item for item in items if self._item_category_id(item) == self.category_id]

    def _matched_tracked_items(self, user_message: str, tracked: list[Any]) -> list[Any]:
        """Return tracked items whose key/display name appears to be referenced."""
        try:
            from src.utils.item_matcher import ItemMatcher
        except Exception:
            ItemMatcher = None
        matched = []
        for item in tracked:
            key = str(getattr(item, "key", "") or "")
            display = str(getattr(item, "display_name", "") or "")
            if key and key.lower() in (user_message or "").lower():
                matched.append(item)
                continue
            if display and display.lower() in (user_message or "").lower():
                matched.append(item)
                continue
            if ItemMatcher and key and ItemMatcher.fuzzy_match_names(key, user_message or ""):
                matched.append(item)
        return matched

    @staticmethod
    def _item_category_id(item: Any) -> str:
        return str(getattr(item, "category_id", None) or getattr(item, "item_type", "") or "")

    def _item_to_public_dict(self, item: Any, item_id: str = "") -> dict[str, Any]:
        """Convert Pydantic/dict category items into a public payload."""
        if isinstance(item, dict):
            data = dict(item)
        elif hasattr(item, "model_dump"):
            data = item.model_dump(mode="json")
        else:
            data = {key: value for key, value in vars(item).items() if not key.startswith("_")}
        if item_id:
            data.setdefault("item_id", item_id)
            data.setdefault("key", item_id)
        return data

    # ── Prompt guidance injected into LLM system prompt ───────────

    def build_prompt_guidance(self, for_intent: str, settings: object | None = None) -> str:
        """Return category-specific guidance for the LLM prompt.

        Args:
            for_intent: One of 'download', 'search', 'chat'.
            settings: Optional live settings whose category YAML guidance should
                refine the static category profile.
        """
        profile_fn = getattr(self, "llm_profile_for_settings", None)
        profile = profile_fn(settings) if callable(profile_fn) else self.llm_profile()
        guidance = profile.format_for_prompt(for_intent)
        prompt_file_guidance = self.load_prompt_file()
        if prompt_file_guidance:
            return f"{guidance}\n\nCategory prompt file guidance:\n{prompt_file_guidance}"
        return guidance


    def prompt_file_torrent_skill(self) -> str:
        """Return torrent/search-relevant sections from the category prompt file.

        Category prompt files are the source of truth for domain teaching.  The
        main assistant prompt can receive the full file, but torrent candidate
        review needs the parts that explain release-name formats, language and
        format evidence, bundle semantics, and safe import/rejection behavior.
        Keeping this extraction here prevents TV/movie/music rules from being
        duplicated inside generic prompt builders.
        """
        prompt_file_guidance = self.load_prompt_file()
        if not prompt_file_guidance:
            return ""
        keywords = (
            "release-name skill",
            "language skill",
            "language and collection skill",
            "safety and import skill",
            "import skill",
            "automation safety",
        )
        sections = self._markdown_sections_matching(prompt_file_guidance, keywords)
        return "\n\n".join(sections).strip() or prompt_file_guidance.strip()

    @staticmethod
    def _markdown_sections_matching(markdown: str, heading_keywords: tuple[str, ...]) -> list[str]:
        """Extract markdown sections whose heading contains one keyword.

        This deliberately looks at headings, not body words, so examples inside
        one category cannot accidentally pull in an unrelated section.
        """
        lines = markdown.splitlines()
        selected: list[str] = []
        current: list[str] = []
        current_level = 0
        active = False
        lowered_keywords = tuple(keyword.lower() for keyword in heading_keywords)
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                hashes = len(stripped) - len(stripped.lstrip("#"))
                heading = stripped[hashes:].strip().lower()
                if active and hashes <= current_level:
                    selected.append("\n".join(current).strip())
                    current = []
                    active = False
                if any(keyword in heading for keyword in lowered_keywords):
                    active = True
                    current_level = hashes
                    current = [line]
                    continue
            if active:
                current.append(line)
        if active and current:
            selected.append("\n".join(current).strip())
        return [section for section in selected if section]

    def build_torrent_selection_guidance(self) -> str:
        """Return category-specific guidance for the torrent selection LLM.

        This is injected into the torrent selection prompt. It should
        tell the LLM what content types are acceptable, what to reject,
        and any category-specific quality considerations.
        """
        exts = ", ".join(self.accepted_file_patterns)
        skill = self.prompt_file_torrent_skill()
        skill_block = f"\n\nCategory prompt file torrent skill:\n{skill}" if skill else ""
        return (
            f"This is a {self.display_name} download. Only select candidates that "
            f"are {self.display_name.lower()} media files (expected formats: {exts}). "
            f"Reject candidates that clearly belong to another installed category or unsafe executable/software payloads. "
            f"Do not import richer-category assumptions here: archives, multi-file payloads, adult-rated media, books, audio, games, or sidecars are acceptable only when this category or the user target says they are. "
            f"If a candidate's title or file list suggests it is outside {self.display_name.lower()} scope, reject it or ask for confirmation instead of guessing."
            f"{skill_block}"
        )



