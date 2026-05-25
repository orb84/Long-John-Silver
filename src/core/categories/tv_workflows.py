"""TV category manifest, action, and workflow mixin."""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.core.models import (
    ActionReceipt,
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
            season = int(arguments.get("season") or 1)
            episode = int(arguments.get("episode") or 1)
            unit_key = f"S{season:02d}E{episode:02d}"
            magnet = str(arguments.get("magnet") or "")
            if magnet and getattr(context, "downloader", None):
                item = await context.downloader.add_magnet(
                    magnet_link=magnet,
                    item_name=title,
                    item_id=title,
                    category_id=self.category_id,
                    season=season,
                    episode=episode,
                    reason=f"Manual TV workflow {workflow_name}" if workflow_name != "scheduled_check" else "Scheduled TV workflow",
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
            return ActionReceipt(
                category_id=self.category_id,
                action_name=workflow_name,
                status="success" if ok else "partial",
                user_message=(f"Queued discovery for {title} {unit_key}." if ok else f"No candidate found for {title} {unit_key}."),
                data={"unit_key": unit_key, "queued": ok},
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



