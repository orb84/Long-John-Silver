"""TV category manifest, action, and workflow mixin."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from src.core.models import (
    ActionReceipt,
    NotificationMessage,
    CategoryActionDeclaration,
    CategoryLlmProfile,
    CategoryPromptExample,
    CategoryUiSection,
    CategoryWorkflowDeclaration,
    ChangedEntity,
    Intent,
)


class TvWorkflowMixin:
    """Declare and execute TV-specific user actions and workflows.

    Keep this mixin focused on orchestration and receipts.  Expensive domain
    lookups should be delegated to metadata/search helpers so new TV workflows
    can be added without growing the base category class.
    """



    def candidate_requires_user_language_confirmation(self, result: Any, item: Any, unit_label: str | None, preferred_language: str | None) -> bool:
        """Return True when a torrent candidate is visibly not the preferred TV audio language."""
        language = str(preferred_language or self._preferred_media_language(item, type("Ctx", (), {"settings": object()})())).strip()
        status = self._candidate_language_status(str(getattr(result, "title", "") or ""), language)
        return status == "non_preferred"

    async def handle_release_event(
        self,
        item: Any,
        event: dict[str, Any],
        context: Any,
        *,
        notifications: Any | None = None,
        lifecycle: Any | None = None,
    ) -> dict[str, Any]:
        """Handle a concrete TV release event from RSS/search.

        TV-specific semantics stay here: parsing SxxEyy, deciding whether this
        is the user's current frontier episode, enforcing strict media-language
        fallback rules, and deciding whether to auto-download, notify, or watch.
        """
        title = getattr(item, "key", "") or str(event.get("item_id") or "")
        unit_label = str(event.get("unit_label") or "")
        source_result = event.get("source_result") if isinstance(event.get("source_result"), dict) else {}
        season, episode = self._unit_coordinates(unit_label or str(source_result.get("title") or ""))
        unit_key = f"S{season:02d}E{episode:02d}" if season and episode else unit_label
        if not title or not season or not episode:
            logger.info("TV release event ignored for %s: no concrete unit in %r", title, unit_label)
            return {"status": "ignored", "reason": "no_concrete_tv_unit", "unit_label": unit_label}

        downloaded = await self._downloaded_episode_keys(context, title)
        if (season, episode) in downloaded:
            return {"status": "ignored", "reason": "already_downloaded", "unit_key": unit_key}

        frontier = self._is_frontier_episode(downloaded, season, episode)
        preferred_language = self._preferred_media_language(item, context)
        candidate_title = str(source_result.get("title") or "")
        language_status = self._candidate_language_status(candidate_title, preferred_language)
        item_auto = getattr(item, "auto_download", None)
        can_auto = bool(item_auto) and frontier and language_status == "preferred"
        action_arguments: dict[str, Any] = {"item_id": title, "season": season, "episode": episode}
        source_magnet = str(source_result.get("magnet") or "")
        if source_magnet:
            # The notification action is explicit user approval of this concrete
            # provider/RSS candidate.  Passing the magnet preserves the detected
            # release instead of throwing it away and re-running a weaker search.
            action_arguments.update({
                "magnet": source_magnet,
                "torrent_title": candidate_title,
                "estimated_size_bytes": source_result.get("size_bytes"),
                "source_seeders": source_result.get("seeders"),
                "approved_from_notification": True,
                "approved_candidate_language_status": language_status,
            })
        action = {
            "key": "download",
            "label": f"Download {unit_key}",
            "category_workflow": {
                "category_id": self.category_id,
                "workflow": "download_specific_episode",
                "arguments": action_arguments,
            },
        }
        metadata = {
            "unit_key": unit_key,
            "season": season,
            "episode": episode,
            "frontier_episode": frontier,
            "preferred_language": preferred_language,
            "candidate_language_status": language_status,
            "candidate_title": candidate_title,
            "trigger": event.get("trigger") or "release_event",
        }

        if can_auto:
            ok = await context.pipeline.run_discovery(item, episode_label=unit_key, force=False, language=preferred_language)
            if ok:
                if getattr(context.db, "release_watches", None):
                    await context.db.release_watches.complete(self.category_id, title, unit_key)
                return {"status": "queued", "reason": "frontier_auto_download", **metadata}
            # Auto was allowed but no acceptable candidate could be queued; keep watching.
            await self._watch_release(context, title, unit_key, preferred_language, metadata)
            await self._notify_release(
                notifications,
                title=title,
                unit_key=unit_key,
                level="warning",
                body=(f"{title} {unit_key} is available, but I could not queue an acceptable {preferred_language} candidate yet. "
                      "I will retry every couple of hours."),
                action=action,
                metadata=metadata,
            )
            return {"status": "watching", "reason": "auto_candidate_not_acceptable", **metadata}

        await self._watch_release(context, title, unit_key, preferred_language, metadata)
        if language_status != "preferred":
            body = (
                f"{title} {unit_key} was detected, but the release language does not clearly match your preference "
                f"({preferred_language}). Non-preferred language downloads require approval."
            )
        elif frontier:
            body = f"{title} {unit_key} is the next episode after your local frontier. Download it?"
        else:
            body = f"{title} {unit_key} looks missing, but it is an older historical gap. Download it?"
        await self._notify_release(
            notifications,
            title=title,
            unit_key=unit_key,
            level="info" if frontier else "warning",
            body=body,
            action=action,
            metadata=metadata,
        )
        return {"status": "notified", "reason": "approval_required", **metadata}


    def release_watch_notification_action(self, item_id: str, unit_key: str, candidate: Any, preferred_language: str | None = None) -> dict[str, Any]:
        """Build a TV-owned web-inbox action for a release-watch candidate."""
        season, episode = self._unit_coordinates(unit_key)
        arguments: dict[str, Any] = {"item_id": item_id, "unit_key": unit_key}
        if season and episode:
            arguments.update({"season": season, "episode": episode})
        magnet = str(getattr(candidate, "magnet", "") or "")
        if magnet:
            arguments.update({
                "magnet": magnet,
                "torrent_title": str(getattr(candidate, "title", "") or ""),
                "estimated_size_bytes": getattr(candidate, "size_bytes", None),
                "source_seeders": getattr(candidate, "seeders", None),
                "approved_from_notification": True,
            })
        return {
            "key": "download",
            "label": f"Download {unit_key}",
            "category_workflow": {
                "category_id": self.category_id,
                "workflow": "download_specific_episode",
                "arguments": arguments,
            },
        }

    async def _watch_release(self, context: Any, title: str, unit_key: str, preferred_language: str, payload: dict[str, Any]) -> None:
        repo = getattr(context.db, "release_watches", None)
        if not repo:
            return
        await repo.upsert(
            category_id=self.category_id,
            item_id=title,
            unit_key=unit_key,
            preferred_language=preferred_language,
            interval_hours=2.0,
            payload=payload,
        )

    async def _soulseek_fallback_for_episode(
        self,
        context: Any,
        title: str,
        season: int,
        episode: int,
        preferred_language: str | None = None,
    ) -> dict[str, Any]:
        """Try a TV-owned Soulseek fallback after torrent discovery fails.

        Torrent and Soulseek are different backends, so this helper does not
        silently queue Soulseek files. It makes the fallback visible in logs and
        receipts, returning candidates for a later explicit queue action.
        """
        cfg = getattr(getattr(context, "settings", None), "soulseek", None)
        if not cfg or not getattr(cfg, "enabled", False):
            return {"attempted": False, "status": "disabled", "candidate_count": 0}
        enabled_categories = {str(cat).strip().lower() for cat in (getattr(cfg, "search_enabled_categories", []) or []) if str(cat).strip()}
        if enabled_categories and self.category_id not in enabled_categories:
            return {"attempted": False, "status": "category_disabled", "category_id": self.category_id, "candidate_count": 0}
        if not getattr(cfg, "api_configured", False):
            return {"attempted": False, "status": "not_configured", "candidate_count": 0, "error": "slskd API is not configured"}
        if getattr(cfg, "managed", True):
            if not getattr(cfg, "soulseek_credentials_configured", False):
                return {"attempted": False, "status": "needs_credentials", "candidate_count": 0, "error": "Soulseek credentials are missing"}
            if str(getattr(cfg, "account_status", "")).lower() == "auth_failed":
                return {"attempted": False, "status": "auth_failed", "candidate_count": 0, "error": getattr(cfg, "account_status_message", "Soulseek authentication failed")}

        label = f"S{season:02d}E{episode:02d}"
        dotted = re.sub(r"\s+", ".", title.strip())
        queries = [
            f"{title} {label}",
            f"{dotted}.{label}",
            f"{title} {season}x{episode:02d}",
            f"{title} S{season:02d}",
        ]
        if preferred_language:
            queries.append(f"{title} {label} {preferred_language}")
        seen: set[str] = set()
        queries = [q for q in queries if q and not (q.casefold() in seen or seen.add(q.casefold()))]

        tried: list[str] = []
        last_result: dict[str, Any] = {}
        candidates: list[dict[str, Any]] = []
        try:
            from src.integrations.slskd_client import SlskdClient
            client = SlskdClient(cfg)
            for query in queries:
                tried.append(query)
                logger.info(f"TV Soulseek fallback search: item={title!r} unit={label} query={query!r}")
                result = await client.search(query, max_results=min(int(getattr(cfg, "max_search_results", 10) or 10), 10))
                last_result = result if isinstance(result, dict) else {}
                rows = last_result.get("candidates") if isinstance(last_result.get("candidates"), list) else []
                if rows:
                    candidates = rows
                    break
        except Exception as exc:
            logger.warning(f"TV Soulseek fallback failed for {title} {label}: {exc}")
            return {"attempted": True, "status": "error", "candidate_count": 0, "queries": tried, "error": str(exc)}

        logger.info(f"TV Soulseek fallback complete: item={title!r} unit={label} queries={tried} candidates={len(candidates)}")
        return {
            "attempted": True,
            "enabled": True,
            "status": "ready" if last_result.get("ok") is True else (last_result.get("error_code") or "error"),
            "source": "slskd",
            "queries": tried,
            "candidate_count": len(candidates),
            "candidates": candidates[:10],
            "error": last_result.get("error") if isinstance(last_result, dict) else None,
            "queueing_note": "Soulseek candidates are not magnets; queue them through enqueue_soulseek_download after review.",
        }


    async def _notify_release(
        self,
        notifications: Any | None,
        *,
        title: str,
        unit_key: str,
        level: str,
        body: str,
        action: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        if not notifications:
            return
        await notifications.notify(
            NotificationMessage(title=f"{title} {unit_key}", body=body, level=level),
            category_id=self.category_id,
            item_id=title,
            event_type="tv_release_available",
            actions=[action],
            metadata=metadata,
            dedupe_key=f"tv_release:{title}:{unit_key}",
        )

    async def _downloaded_episode_keys(self, context: Any, title: str) -> set[tuple[int, int]]:
        downloaded: set[tuple[int, int]] = set()
        try:
            rows = await context.db.media.list_category_units(self.category_id, title, status="downloaded")
            for row in rows or []:
                season = self._safe_int(row.get("season"))
                episode = self._safe_int(row.get("episode"))
                if season > 0 and episode > 0:
                    downloaded.add((season, episode))
        except Exception as exc:
            logger.debug("TV release event could not read downloaded episode keys for %s: %s", title, exc)
        return downloaded

    @staticmethod
    def _unit_coordinates(text: str) -> tuple[int, int]:
        """Parse a TV unit label into ``(season, episode)`` coordinates.

        Older code only understood episode labels such as ``S01E03``.  Agent
        season-pack requests, however, pass labels like ``Season 1`` or
        ``S01`` into category hooks.  Treat those as season-only descriptors
        instead of falling back to whatever season happens to be present in a
        broad torrent title such as ``S01-S02``.
        """
        value = str(text or "")
        match = re.search(r"S(\d{1,2})E(\d{1,3})", value, flags=re.IGNORECASE)
        if not match:
            match = re.search(r"(\d{1,2})x(\d{1,3})", value, flags=re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))
        season_only = re.search(r"\b(?:Season|Stagione)\s*0*(\d{1,2})\b", value, flags=re.IGNORECASE)
        if not season_only:
            season_only = re.search(r"(?:^|[^A-Za-z0-9])S0*(\d{1,2})(?:$|[^A-Za-z0-9])", value, flags=re.IGNORECASE)
        if season_only:
            return int(season_only.group(1)), 0
        return 0, 0

    @staticmethod
    def _is_frontier_episode(downloaded: set[tuple[int, int]], season: int, episode: int) -> bool:
        if not downloaded:
            return False
        latest_season = max(s for s, _ in downloaded)
        latest_episode = max(e for s, e in downloaded if s == latest_season)
        return season > latest_season or (season == latest_season and episode > latest_episode)

    @staticmethod
    def _preferred_media_language(item: Any, context: Any) -> str:
        value = str(getattr(item, "language", "") or "").strip()
        if value:
            return value
        return str(getattr(context.settings, "language", "Italian") or "Italian").strip() or "Italian"

    @staticmethod
    def _candidate_language_status(title: str, preferred_language: str) -> str:
        """Return preferred/non_preferred/unknown from release-title evidence only.

        Subtitles are not audio fallbacks, and Spanish is never inferred as an
        acceptable media fallback from subtitle settings.
        """
        text = f" {title.casefold()} "
        pref = preferred_language.casefold().strip()
        preferred_markers = {
            "italian": [" ita ", ".ita.", " italian ", " italiano ", " iTa ".casefold()],
            "english": [" eng ", ".eng.", " english "],
        }.get(pref, [f" {pref} ", f".{pref}."])
        if any(marker in text for marker in preferred_markers):
            return "preferred"
        if any(marker in text for marker in [" multi ", ".multi.", " multi-audio ", " dlmux ", " mux "]):
            return "preferred"
        non_pref_markers = [" hindi ", " hin ", ".hin.", " spanish ", " spa ", ".spa.", " latino "]
        if any(marker in text for marker in non_pref_markers):
            return "non_preferred"
        return "unknown"

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def taste_profile_schema(self) -> dict[str, Any]:
        """Return TV-specific taste metadata fields for the agent."""
        schema = super().taste_profile_schema()
        schema["tv_keys"] = [
            "genres", "cast_names", "creators", "networks", "status",
            "first_air_date", "tmdb_id", "tvmaze_id", "rating", "overview",
        ]
        return schema

    def taste_profile_llm_instructions(self) -> list[str]:
        """Guide the agent to record TV taste evidence correctly."""
        return [
            "For TV-show mentions, enrich the series through TV metadata providers before recording taste when possible.",
            "Record genres, cast_names, creators, networks, status, first_air_date, rating, and overview when known.",
            "Distinguish liking a whole show from asking for one missing episode or season pack.",
        ]


    def llm_profile(self) -> CategoryLlmProfile:
        """Return TV-show-specific LLM guidance."""
        return CategoryLlmProfile(
            category_id=self.category_id,
            short_description="Episodic video series organized by show, season, and episode.",
            user_facing_description=(
                "TV Shows are series with seasons and episodes. I can track missing episodes, "
                "check what has aired, download specific episodes or safe season packs, and organize them into season folders."
            ),
            router_description="TV Shows: episodic video series with seasons, episodes, air dates, and missing-episode tracking.",
            domain_vocabulary=[
                "show", "series", "season", "episode", "SxxEyy", "season pack",
                "aired episode", "missing episode", "finale", "special", "pilot",
            ],
            item_types=["show", "season", "episode", "season_pack"],
            identifiers=["title", "season_number", "episode_number", "tmdb_id", "tvmaze_id", "imdb_id"],
            common_user_requests=[
                "Download the latest aired episode.",
                "Download a specific season and episode.",
                "Find missing episodes.",
                "Check when the next episode airs.",
            ],
            ambiguity_rules=[
                "If the user gives only a show name and asks to download, decide whether they mean latest aired episode, all missing episodes, or a specific episode; ask if unclear.",
                "If a title exists as both a movie and a TV show, prefer this category only when the user mentions show, series, season, episode, or SxxEyy.",
                "Do not assume an unaired episode is available; check schedule metadata first.",
            ],
            search_rules=[
                "Resolve show metadata with TV category-owned metadata providers before torrent selection.",
                "Prefer exact SxxEyy matches for specific episodes.",
                "Use season-pack searches only when the user asked for a season or many missing episodes make a pack useful.",
            ],
            download_rules=[
                "Do not download future unaired episodes.",
                "Prefer exact episode releases over full season packs for one-episode requests.",
                "When queueing multiple episodes, assign/start them in viewing order so earlier episodes have higher priority than later episodes.",
                "Reject CAM/TS and unrelated movie/software/book releases unless explicitly allowed.",
            ],
            organization_rules=[
                "Organize episodes into show and season folders using the TV naming template.",
            ],
            tool_usage_notes=[
                "Expose TV state through the category context packet/enquire_about_media; ordinary LLM downloads use search_media_torrents and queue_download, not TV-specific micro-tools.",
                "Use configured language and existing episode audio_languages before queueing; ask before accepting a different-language release.",
                "Only queue when the episode/pack coverage, aired-state evidence, language, and magnet availability are all acceptable.",
            ],
            examples=[
                CategoryPromptExample(
                    user="Get the latest episode of Severance",
                    expected_intent="download",
                    expected_behavior="Read the TV context packet to identify the latest aired missing episode, search exact SxxEyy candidates, and queue the best confident match via generic tools.",
                    tool_plan=["search_media_torrents", "queue_download"],
                ),
                CategoryPromptExample(
                    user="Download The Bear S03E02",
                    expected_intent="download",
                    expected_behavior="Use provider/category context to confirm S03E02 exists and has aired, search exact S03E02 releases, reject unsafe packs unless appropriate, and queue the best candidate.",
                    tool_plan=["search_media_torrents", "queue_download"],
                ),
            ],
        )

    def ui_sections(self) -> list[CategoryUiSection]:
        """Return UI sections for TV dashboards and item details."""
        return [
            CategoryUiSection(id="overview", title="Overview", component="metadata_summary"),
            CategoryUiSection(id="seasons", title="Seasons", component="season_episode_grid", capabilities_required=["episodic"]),
            CategoryUiSection(id="missing", title="Missing Episodes", component="missing_episode_list", capabilities_required=["episodic"]),
            CategoryUiSection(id="downloads", title="Downloads", component="download_list"),
            CategoryUiSection(id="upgrades", title="Upgrades", component="upgrade_candidate_list"),
        ]

    def declare_actions(self) -> list[CategoryActionDeclaration]:
        """Declare TV UI/LLM actions."""
        actions = super().declare_actions()
        actions.extend([
            CategoryActionDeclaration(
                name="refresh_metadata",
                label="Refresh Metadata",
                description="Refresh show, season, and episode metadata from configured TV metadata providers.",
                parameters={
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "required": ["item_id"],
                },
                requires_confirmation=False,
                risk_level="read",
                operation="refresh_metadata",
                capabilities_required=["metadata", "episodic"],
                result_component="metadata_summary",
                tool_name="tv.refresh_metadata",
            ),
            CategoryActionDeclaration(
                name="find_missing_episodes",
                label="Find Missing Episodes",
                description="Compare aired episode metadata with the local library and return missing episodes. Optionally restrict to one season.",
                parameters={
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "season": {"type": "integer", "description": "Optional season to inspect."},
                        "episode_count": {"type": "integer", "description": "Optional expected episode count when metadata is unavailable."},
                    },
                    "required": ["item_id"],
                },
                requires_confirmation=False,
                risk_level="read",
                operation="find_missing_episodes",
                capabilities_required=["episodic", "scheduled_updates"],
                result_component="missing_episode_list",
                tool_name="tv.find_missing_episodes",
            ),
            CategoryActionDeclaration(
                name="download_next_missing_episode",
                label="Download Next Missing Episode",
                description="Find and queue the next aired episode missing from the library.",
                parameters={
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "required": ["item_id"],
                },
                requires_confirmation=False,
                risk_level="write",
                operation="download_next_missing_episode",
                capabilities_required=["episodic", "downloadable"],
                result_component="action_receipt",
                tool_name="tv.download_next_missing_episode",
            ),
            CategoryActionDeclaration(
                name="download_specific_episode",
                label="Download Specific Episode",
                description="Find and queue a specific aired TV episode.",
                parameters={
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "season": {"type": "integer"},
                        "episode": {"type": "integer"},
                    },
                    "required": ["item_id", "season", "episode"],
                },
                requires_confirmation=False,
                risk_level="write",
                operation="download_specific_episode",
                capabilities_required=["episodic", "downloadable"],
                result_component="action_receipt",
                tool_name="tv.download_specific_episode",
            ),
            CategoryActionDeclaration(
                name="download_season_pack",
                label="Download Season Pack",
                description="Search and queue a season pack when explicitly requested or useful for many missing episodes.",
                parameters={
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}, "season": {"type": "integer"}},
                    "required": ["item_id", "season"],
                },
                requires_confirmation=False,
                risk_level="write",
                operation="download_season_pack",
                capabilities_required=["episodic", "downloadable"],
                result_component="action_receipt",
                tool_name="tv.download_season_pack",
            ),
            CategoryActionDeclaration(
                name="download_missing_batch",
                label="Download Missing Episodes",
                description="Queue a confirmed batch of specific aired missing episodes for this show.",
                parameters={
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "episodes": {"type": "array", "description": "Array of [season, episode] pairs."},
                    },
                    "required": ["item_id", "episodes"],
                },
                requires_confirmation=False,
                risk_level="write",
                operation="download_missing_batch",
                capabilities_required=["episodic", "downloadable"],
                confirmation_prompt="Queue all selected aired missing episodes for this show?",
                result_component="action_receipt",
                tool_name="tv.download_missing_batch",
            ),
            CategoryActionDeclaration(
                name="scan_library",
                label="Scan Library",
                description="Scan the configured TV library and reconcile discovered shows and episodes.",
                parameters={"type": "object", "properties": {}, "required": []},
                risk_level="write",
                operation="scan_library",
                capabilities_required=["file_organization"],
                result_component="season_episode_grid",
                tool_name="tv.scan_library",
            ),
            CategoryActionDeclaration(
                name="delete_item",
                label="Delete TV Item",
                description="Delete or untrack one TV item through the TV category workflow.",
                parameters={
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}, "delete_files": {"type": "boolean"}},
                    "required": ["item_id"],
                },
                requires_confirmation=True,
                destructive=True,
                risk_level="destructive",
                operation="delete_item",
                capabilities_required=["file_organization"],
                confirmation_prompt="Delete this TV item? This may remove files if delete_files is true.",
                result_component="action_receipt",
                tool_name="tv.delete_item",
            ),
        ])
        return actions

    def declare_workflows(self) -> list[CategoryWorkflowDeclaration]:
        """Declare TV workflows exposed as category-scoped LLM tools."""
        return [
            CategoryWorkflowDeclaration(
                name="resolve_show",
                description="Resolve show metadata using TV-owned metadata providers.",
                parameters={
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "required": ["item_id"],
                },
                intent=Intent.SEARCH,
                risk_level="read",
                tool_name="tv.resolve_show",
            ),
            CategoryWorkflowDeclaration(
                name="find_missing_episodes",
                description="Find aired episodes missing from the local library, optionally restricted to one season.",
                parameters={
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "season": {"type": "integer", "description": "Optional season to inspect."},
                        "episode_count": {"type": "integer", "description": "Optional expected episode count when metadata is unavailable."},
                    },
                    "required": ["item_id"],
                },
                intent=Intent.SEARCH,
                risk_level="read",
                tool_name="tv.find_missing_episodes",
            ),
            CategoryWorkflowDeclaration(
                name="download_next_missing_episode",
                description="Queue the next missing aired episode for a show.",
                parameters={
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "required": ["item_id"],
                },
                intent=Intent.DOWNLOAD,
                risk_level="write",
                tool_name="tv.download_next_missing_episode",
            ),
            CategoryWorkflowDeclaration(
                name="download_specific_episode",
                description="Queue one specific aired episode.",
                parameters={
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "season": {"type": "integer"},
                        "episode": {"type": "integer"},
                    },
                    "required": ["item_id", "season", "episode"],
                },
                intent=Intent.DOWNLOAD,
                risk_level="write",
                tool_name="tv.download_specific_episode",
            ),
            CategoryWorkflowDeclaration(
                name="scheduled_check",
                description="Run the TV category scheduled automation loop for one item.",
                parameters={"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"]},
                intent=Intent.DOWNLOAD,
                risk_level="write",
                tool_name="tv.scheduled_check",
            ),
            CategoryWorkflowDeclaration(
                name="download_season_pack",
                description="Queue a confirmed season pack for a TV show.",
                parameters={
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}, "season": {"type": "integer"}},
                    "required": ["item_id", "season"],
                },
                intent=Intent.DOWNLOAD,
                risk_level="write",
                tool_name="tv.download_season_pack",
            ),
            CategoryWorkflowDeclaration(
                name="download_missing_batch",
                description="Queue a user-approved batch of specific missing aired episodes.",
                parameters={
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}, "episodes": {"type": "array"}},
                    "required": ["item_id", "episodes"],
                },
                intent=Intent.DOWNLOAD,
                risk_level="write",
                tool_name="tv.download_missing_batch",
            ),
        ]


    async def execute_workflow(self, workflow_name: str, arguments: dict[str, object], context: object) -> ActionReceipt:
        """Execute TV-owned workflows through generic category collaborators."""
        item_id = str(arguments.get("item_id") or arguments.get("title") or "").strip()
        title = item_id or str(arguments.get("name") or "").strip()
        if workflow_name in {"resolve_show", "resolve_metadata", "refresh_metadata"}:
            if not title:
                return self._workflow_failed(workflow_name, "A TV item title is required.")
            metadata = {"title": title, "provider": "category", "category_id": self.category_id}
            from src.core.categories.metadata.cache_policy import get_fresh_category_metadata
            cached = await get_fresh_category_metadata(getattr(context, "db", None), self.category_id, title)
            if cached:
                metadata.update(cached)
            else:
                enricher = getattr(context, "metadata_enricher", None)
                if enricher and self.metadata_provider_enabled(getattr(context, "settings", None), "tmdb", True):
                    try:
                        enriched = await enricher.enrich_series(title)
                        normalized = self.normalize_taste_metadata_payload(
                            self.create_item(title), enriched, "tmdb_tv",
                        )
                        if normalized:
                            metadata.update(normalized)
                    except Exception as exc:
                        logger.debug(f"TV metadata enrichment failed for {title}: {exc}")
            tvmaze = getattr(context, "metadata_clients", {}).get("tvmaze") if hasattr(context, "metadata_clients") else None
            if tvmaze and self.metadata_provider_enabled(getattr(context, "settings", None), "tvmaze", True) and hasattr(tvmaze, "search"):
                try:
                    results = await tvmaze.search(title)
                    if results:
                        metadata["tvmaze"] = results[0]
                except Exception as exc:
                    logger.debug(f"TVMaze metadata search failed for {title}: {exc}")
            metadata = await self.cache_metadata_artwork(
                self.create_item(title), metadata, context, provider="tv_metadata",
            )
            await context.db.media.upsert_category_metadata(
                self.category_id, title, metadata.get("provider", "category"), metadata,
                str(metadata.get("external_id") or metadata.get("tmdb_id") or metadata.get("tvmaze_id") or metadata.get("id", "")),
            )
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success",
                user_message=f"Resolved TV metadata for {title}.",
                changed_entities=[ChangedEntity(entity_type="category_item", entity_id=title, display_name=title, change="metadata_refreshed")],
                data={"metadata": metadata},
            )

        if workflow_name in {"find_missing_episodes", "find_missing_units"}:
            if not title:
                return self._workflow_failed(workflow_name, "A TV item id is required.")
            downloaded = await context.db.media.list_category_units(self.category_id, title, status="downloaded")
            progress = await context.db.media.get_item_progress(self.category_id, title) or {}
            requested_season = int(arguments.get("season") or progress.get("last_season") or 1)
            aired = await self._aired_episode_numbers_for_season(title, requested_season, context)
            expected_count = await self._expected_episode_count(title, requested_season, arguments, context)
            downloaded_keys = {
                (int(unit.get("season") or 0), int(unit.get("episode") or 0))
                for unit in downloaded
            }
            downloaded_for_season = sorted(
                episode for season, episode in downloaded_keys
                if season == requested_season and episode > 0
            )
            if aired:
                episode_numbers = sorted(aired)
                confidence = "aired_metadata"
                expected_count = max(episode_numbers)
            elif expected_count is None:
                # Metadata may not be configured yet.  In that case we can only
                # detect gaps inside the locally observed range and be explicit
                # that the user or metadata provider must provide the season
                # episode count before we can infer later missing episodes.
                expected_count = max(downloaded_for_season or [0])
                episode_numbers = list(range(1, max(expected_count, 0) + 1))
                confidence = "local_range_only"
            else:
                episode_numbers = list(range(1, max(expected_count, 0) + 1))
                confidence = "metadata" if not arguments.get("episode_count") else "user_supplied"

            missing = []
            for episode in episode_numbers:
                if (requested_season, episode) not in downloaded_keys:
                    missing.append({
                        "season": requested_season,
                        "episode": episode,
                        "unit_key": f"S{requested_season:02d}E{episode:02d}",
                    })
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success",
                user_message=(
                    f"Found {len(missing)} missing TV units for {title} season {requested_season}."
                    if confidence != "local_range_only"
                    else f"Checked local gaps for {title} season {requested_season}; metadata did not provide an episode count."
                ),
                data={
                    "season": requested_season,
                    "downloaded_count": len(downloaded_for_season),
                    "downloaded_episodes": downloaded_for_season,
                    "expected_episode_count": expected_count,
                    "coverage_confidence": confidence,
                    "missing": missing,
                },
            )

        if workflow_name in {"download_specific_episode", "download_specific_unit"}:
            if not title:
                return self._workflow_failed(workflow_name, "A TV item id is required.")
            raw_season = arguments.get("season")
            raw_episode = arguments.get("episode")
            if (not raw_season or not raw_episode) and arguments.get("unit_key"):
                parsed_season, parsed_episode = self._unit_coordinates(str(arguments.get("unit_key") or ""))
                raw_season = raw_season or parsed_season
                raw_episode = raw_episode or parsed_episode
            season = int(raw_season or 1)
            episode = int(raw_episode or 1)
            unit_key = f"S{season:02d}E{episode:02d}"
            magnet = str(arguments.get("magnet") or "")
            if magnet and getattr(context, "downloader", None):
                torrent_title = str(arguments.get("torrent_title") or title)
                candidate_probe = type("TvNotificationCandidate", (), {
                    "title": torrent_title,
                    "magnet": magnet,
                    "size_bytes": arguments.get("estimated_size_bytes"),
                    "seeders": arguments.get("source_seeders"),
                    "source": arguments.get("source") or "notification",
                })()
                descriptor = self.unit_descriptor_from_agent_args(season=season, episode=episode)
                bundle_context = self.torrent_bundle_candidate_context(candidate_probe, item=self.create_item(title), unit_label=unit_key)
                import_context = {
                    "category_id": self.category_id,
                    "item_id": title,
                    "display_title": title,
                    "canonical_title": title,
                    "season": season,
                    "episode": episode,
                    "unit_descriptor": descriptor,
                    "release_title": torrent_title,
                    "candidate_snapshot": {
                        "title": torrent_title,
                        "source": getattr(candidate_probe, "source", "notification"),
                        "size_bytes": arguments.get("estimated_size_bytes"),
                        "bundle_context": bundle_context or {},
                    },
                }
                item = await context.downloader.add_magnet(
                    magnet_link=magnet,
                    item_name=title,
                    item_id=title,
                    category_id=self.category_id,
                    season=season,
                    episode=episode,
                    torrent_title=torrent_title,
                    language=getattr(context.settings, "language", ""),
                    estimated_size_bytes=arguments.get("estimated_size_bytes"),
                    source_seeders=arguments.get("source_seeders"),
                    reason=(
                        f"user approved TV notification candidate for {unit_key}"
                        if arguments.get("approved_from_notification")
                        else (f"user approved TV workflow {workflow_name}" if workflow_name != "scheduled_check" else "Scheduled TV workflow")
                    ),
                    import_context=import_context,
                    selective_descriptors=[descriptor] if bundle_context and descriptor else None,
                )
                return ActionReceipt(
                    category_id=self.category_id,
                    action_name=workflow_name,
                    status="success",
                    user_message=f"Queued {title} {unit_key}.",
                    changed_entities=[ChangedEntity(entity_type="download", entity_id=item.id, display_name=title, change="queued")],
                    data={"download_id": item.id, "unit_key": unit_key},
                )
            tracked = next(
                (tracked_item for tracked_item in getattr(context.settings, "tracked_items", [])
                 if getattr(tracked_item, "item_type", None) == self.category_id and tracked_item.key == title),
                None,
            )
            item = tracked or self.create_item(title, language=getattr(context.settings, "language", "English"))
            ok = await context.pipeline.run_discovery(item, episode_label=unit_key, force=True)
            soulseek = None
            user_message = f"Queued discovery for {title} {unit_key}." if ok else f"No torrent candidate found for {title} {unit_key}."
            if not ok:
                soulseek = await self._soulseek_fallback_for_episode(context, title, season, episode, getattr(item, "language", None))
                if soulseek.get("attempted"):
                    count = int(soulseek.get("candidate_count") or 0)
                    if count:
                        user_message = (
                            f"No torrent candidate was queued for {title} {unit_key}. "
                            f"Soulseek was searched and returned {count} candidate(s); review them before queueing."
                        )
                    elif soulseek.get("error"):
                        user_message = (
                            f"No torrent candidate was queued for {title} {unit_key}. "
                            f"Soulseek fallback was attempted but failed: {soulseek.get('error')}"
                        )
                    else:
                        user_message = f"No torrent or Soulseek candidate found for {title} {unit_key}."
                else:
                    reason = soulseek.get("status") or soulseek.get("reason") or "not_available"
                    user_message = f"No torrent candidate found for {title} {unit_key}. Soulseek fallback was not available ({reason})."
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success" if ok else "partial",
                user_message=user_message,
                data={"unit_key": unit_key, "queued": ok, "soulseek": soulseek or {}},
            )

        if workflow_name in {"download_missing_batch", "download_all_missing", "download_remaining_next"}:
            if not title:
                return self._workflow_failed(workflow_name, "A TV item id is required.")
            episodes_arg = arguments.get("episodes") or []
            tracked = next(
                (tracked_item for tracked_item in getattr(context.settings, "tracked_items", [])
                 if getattr(tracked_item, "item_type", None) == self.category_id and tracked_item.key == title),
                None,
            )
            item = tracked or self.create_item(title, language=getattr(context.settings, "language", "English"))
            queued = 0
            for pair in episodes_arg:
                try:
                    season_num, episode_num = int(pair[0]), int(pair[1])
                except Exception:
                    continue
                if await context.pipeline.run_discovery(item, episode_label=f"S{season_num:02d}E{episode_num:02d}", force=True):
                    queued += 1
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success" if queued else "partial",
                user_message=f"Queued {queued} missing episode download(s) for {title}.",
                data={"queued": queued, "requested": len(episodes_arg)},
            )

        if workflow_name in {"download_next_missing_episode", "download_next_missing_unit", "scheduled_check"}:
            if not title:
                return self._workflow_failed(workflow_name, "A TV item id is required.")
            progress = await context.db.media.get_item_progress(self.category_id, title) or {}
            season = int(arguments.get("season") or progress.get("last_season") or 1)
            episode = int(arguments.get("episode") or progress.get("last_episode") or 0) + 1
            tracked = next(
                (tracked_item for tracked_item in getattr(context.settings, "tracked_items", [])
                 if getattr(tracked_item, "item_type", None) == self.category_id and tracked_item.key == title),
                None,
            )
            item = tracked or self.create_item(title, language=getattr(context.settings, "language", "English"))
            # Scheduled checks must respect global/per-item auto_download settings.
            # Explicit user workflows may force queueing; background checks may not.
            force_download = workflow_name != "scheduled_check"
            ok = await context.pipeline.run_discovery(
                item,
                episode_label=f"S{season:02d}E{episode:02d}",
                force=force_download,
            )
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success" if ok else "partial",
                user_message=f"TV scheduled workflow completed for {title}.",
                data={"queued": ok, "season": season, "episode": episode, "auto_download_respected": not force_download},
            )

        if workflow_name in {"search_download_candidates", "search_upgrade"}:
            if not title:
                return self._workflow_failed(workflow_name, "A TV item id is required.")
            season = arguments.get("season")
            episode = arguments.get("episode")
            label = f"S{int(season):02d}E{int(episode):02d}" if season and episode else None
            item = self.create_item(title, language=getattr(context.settings, "language", "English"))
            results = await context.pipeline.run_search(item, episode_label=label, mode="llm")
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success",
                user_message=f"Found {len(results or [])} TV candidates for {title}.",
                data={"candidates": [r.model_dump() for r in (results or [])]},
            )

        if workflow_name in {"delete_item", "delete_show"}:
            payload = {k: v for k, v in arguments.items() if k not in {"confirmed", "confirmation_token"}}
            affected_paths = self._candidate_delete_paths(title, context.settings) if arguments.get("delete_files") else []
            if not arguments.get("confirmed"):
                request = self._confirmation_service.create_request(
                    workflow_name,
                    payload,
                    category_id=self.category_id,
                    affected_paths=affected_paths,
                    risk_level="destructive",
                    user_message=f"Confirm deletion of {title}. Files will be quarantined, not permanently removed.",
                )
                return self._confirmation_service.receipt_for_request(request)
            token = str(arguments.get("confirmation_token") or "")
            if not self._confirmation_service.verify(token, workflow_name, payload):
                return ActionReceipt(
                    category_id=self.category_id,
                    action_name=workflow_name,
                    status="needs_confirmation",
                    user_message="Deletion confirmation is missing, expired, or does not match this exact action.",
                    data={"item_id": title, "affected_paths": affected_paths},
                )
            files_deleted = False
            if arguments.get("delete_files"):
                files_deleted = self._delete_item_files(title, context.settings)
            await context.db.media.delete_category_item(self.category_id, title)
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success",
                user_message=f"Deleted TV item {title}.",
                changed_entities=[ChangedEntity(entity_type="category_item", entity_id=title, display_name=title, change="deleted")],
                data={"files_quarantined": files_deleted, "affected_paths": affected_paths},
            )

        return self._workflow_failed(workflow_name, f"Unsupported TV workflow: {workflow_name}")

    def _workflow_failed(self, workflow_name: str, message: str) -> ActionReceipt:
        """Create a failed receipt for TV workflow validation errors."""
        return ActionReceipt(
            category_id=self.category_id,
            action_name=workflow_name,
            status="failed",
            user_message=message,
            technical_message=message,
        )



