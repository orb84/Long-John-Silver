"""TV-specific LLM and item-detail serialization."""

from __future__ import annotations

from typing import Any


class TvContextMixin:
    """Expose compact TV summaries and detail payloads to UI/LLM callers.

    Override these methods for additional display fields while preserving the
    category-neutral shape returned by the base context mixin.
    """

    def summarize_item_for_llm(self, item: Any) -> dict[str, Any]:
        """Return TV-owned tracked-show context for prompts."""
        summary = super().summarize_item_for_llm(item)
        summary.update({
            "last_season": getattr(item, "last_season", None),
            "last_episode": getattr(item, "last_episode", None),
            "tvmaze_id": getattr(item, "tvmaze_id", None),
            "tmdb_id": getattr(item, "tmdb_id", None),
            "overview": (getattr(item, "overview", "") or "")[:300],
            "genres": getattr(item, "genres", []) or [],
            "instruction": "For TV tools, pass the exact show key as item_id/name and put season/episode numbers in dedicated arguments.",
        })
        return summary

    def summarize_unit_for_llm(self, unit: dict[str, Any]) -> dict[str, Any]:
        """Summarize TV episode units with season/episode semantics."""
        return {
            "unit_key": unit.get("unit_key"),
            "unit_type": unit.get("unit_type") or "episode",
            "season": unit.get("season"),
            "episode": unit.get("episode"),
            "title": unit.get("title") or unit.get("display_name"),
            "status": unit.get("status"),
            "quality": unit.get("quality"),
            "language": unit.get("language"),
            "audio_languages": unit.get("audio_languages") or [],
            "subtitle_languages": unit.get("subtitle_languages") or [],
            "downloaded_at": str(unit.get("downloaded_at") or "")[:10],
        }


    def summarize_library_object_for_llm(self, canonical: dict[str, Any]) -> dict[str, Any]:
        """Return TV canonical facts that the LLM needs for decisions.

        This deliberately exposes computed state and compact per-season episode
        facts instead of requiring bespoke category micro-tools. The LLM should
        decide requested units from this context, then use the generic
        search/queue toolchain.
        """
        base = super().summarize_library_object_for_llm(canonical)
        computed = canonical.get("computed") or {}
        missing = list(computed.get("missing_episodes") or [])
        seasons_summary: list[dict[str, Any]] = []
        for season in list(canonical.get("seasons") or [])[:20]:
            episodes = list(season.get("episodes") or [])
            seasons_summary.append({
                "season": season.get("season") or season.get("season_number"),
                "downloaded_episode_count": len(episodes),
                "downloaded_episodes": [
                    {
                        "episode": ep.get("episode"),
                        "quality": ep.get("quality") or ep.get("best_resolution"),
                        "audio_languages": ep.get("audio_languages") or [],
                        "subtitle_languages": ep.get("subtitle_languages") or [],
                    }
                    for ep in episodes[:80]
                ],
            })
        base.update({
            "local_episode_keys": list(computed.get("local_episode_keys") or [])[:200],
            "missing_episodes": missing[:120],
            "missing_episode_count": computed.get("missing_episode_count", len(missing)),
            "seasons": seasons_summary,
            "decision_hint": (
                "Use missing_episodes/local_episode_keys plus provider metadata to decide requested units; "
                "then call generic search_media_torrents and queue_download. Do not call category micro-tools."
            ),
        })
        return base

    def _language_profile_for_llm(self, units: list[dict[str, Any]], configured_language: str | None) -> dict[str, Any]:
        """Summarize configured and observed TV episode languages for prompts."""
        existing_audio: list[str] = []
        existing_subtitles: list[str] = []
        per_season: dict[str, list[str]] = {}
        non_preferred: list[dict[str, Any]] = []
        missing_audio_metadata = 0
        preferred = self._language_token(configured_language)
        for unit in units or []:
            season = unit.get("season")
            episode = unit.get("episode")
            langs = [str(lang) for lang in (unit.get("audio_languages") or []) if lang]
            subs = [str(lang) for lang in (unit.get("subtitle_languages") or []) if lang]
            if not langs:
                missing_audio_metadata += 1
            for lang in langs:
                if lang not in existing_audio:
                    existing_audio.append(lang)
                if season is not None and lang not in per_season.setdefault(str(season), []):
                    per_season[str(season)].append(lang)
            for lang in subs:
                if lang not in existing_subtitles:
                    existing_subtitles.append(lang)
            if preferred and langs and preferred not in {self._language_token(lang) for lang in langs}:
                non_preferred.append({
                    "season": season,
                    "episode": episode,
                    "audio_languages": langs,
                })
        return {
            "configured_language": configured_language,
            "existing_audio_languages": existing_audio,
            "existing_subtitle_languages": existing_subtitles,
            "audio_languages_by_season": per_season,
            "episodes_with_non_preferred_audio": non_preferred[:20],
            "episodes_missing_audio_metadata_count": missing_audio_metadata,
            "rules_for_llm": [
                "If the user did not explicitly ask for another language, search using configured_language.",
                "Prefer releases whose audio matches configured_language or existing_audio_languages for continuity.",
                "Multi-audio is acceptable when it contains the configured/existing language.",
                "If only a different-language candidate exists, ask the user before queueing it.",
            ],
        }

    @staticmethod
    def _language_token(value: Any) -> str:
        """Normalize common language names for lightweight prompt comparisons."""
        text = str(value or "").strip().lower()
        aliases = {
            "ita": "italian", "it": "italian", "italiano": "italian", "italian": "italian",
            "eng": "english", "en": "english", "inglese": "english", "english": "english",
        }
        return aliases.get(text, text)

    async def build_item_context_packet(
        self,
        item: Any,
        settings: "Settings",
        db: Any | None = None,
        max_units: int = 120,
    ) -> dict[str, Any]:
        """Return detailed TV library context for the matched show."""
        packet = await super().build_item_context_packet(item, settings=settings, db=db, max_units=max_units)
        units = packet.get("units") or []
        by_season: dict[str, list[dict[str, Any]]] = {}
        for unit in units:
            unit_type = str(unit.get("unit_type") or "")
            role = str(unit.get("role") or "")
            if role != "episode_payload" and unit_type not in ("", "episode", "file"):
                continue
            if unit_type == "file" and not (unit.get("season") and unit.get("episode")):
                continue
            season = unit.get("season")
            if season is None:
                continue
            by_season.setdefault(str(season), []).append(unit)
        packet["episodes_by_season"] = by_season
        packet["language_policy"] = self._language_profile_for_llm(
            units, configured_language=getattr(item, "language", None),
        )
        packet["tv_instructions"] = [
            "Use the category context packet and canonical_library_object to determine which episodes are owned, missing, aired, and language-compatible.",
            "For missing/latest/whole-season requests, decide the concrete target units from context, then use search_media_torrents with the exact show key, season/episode fields when needed, and configured language.",
            "Search exact SxxEyy candidates as well as safe packs/bundles exposed by the category search hook; queue only clear candidate IDs through queue_download.",
            "Do not call TV-specific micro-tools from the LLM. The TV category owns context/search/ranking hooks; the LLM uses generic tools.",
            "Never assume auto-download permission from tracking state; explicit user download requests and scheduler automation are separate flows.",
        ]
        return packet

    async def build_item_detail_payload(
        self,
        item_id: str,
        item: Any,
        settings: "Settings",
        db: Any | None = None,
        artwork_manager: Any | None = None,
    ) -> dict[str, Any]:
        """Build a TV-detail payload with season/episode groups for the UI."""
        payload = await super().build_item_detail_payload(
            item_id=item_id, item=item, settings=settings, db=db, artwork_manager=artwork_manager,
        )
        if payload.get("auto_download") is None:
            # TV new-episode automation is default-on; the inspector presents a
            # simple enabled/disabled checkbox rather than leaking the legacy
            # global-inherit null state.
            payload["auto_download"] = True
        canonical = payload.get("canonical_object") or {}
        canonical_seasons = list(canonical.get("seasons") or [])

        def _sort_ep(row: dict[str, Any]) -> tuple[int, int]:
            try:
                return (int(row.get("season") or row.get("season_number") or 0), int(row.get("episode") or 0))
            except Exception:
                return (0, 0)

        def _episode_from_unit(unit: dict[str, Any]) -> dict[str, Any] | None:
            unit_type = str(unit.get("unit_type") or "")
            role = str(unit.get("role") or "")
            if unit_type not in {"episode", "file", ""} and role != "episode_payload":
                return None
            if role != "episode_payload" and unit_type == "file" and not (unit.get("season") and unit.get("episode")):
                return None
            return {
                "unit_key": unit.get("unit_key"),
                "season": unit.get("season"),
                "episode": unit.get("episode"),
                "title": unit.get("title") or unit.get("display_name") or unit.get("unit_key"),
                "quality": unit.get("quality", ""),
                "language": unit.get("language", ""),
                "audio_languages": unit.get("audio_languages") or [],
                "audio_tracks": unit.get("audio_tracks") or [],
                "subtitle_languages": unit.get("subtitle_languages") or [],
                "subtitle_tracks": unit.get("subtitle_tracks") or [],
                "file_path": unit.get("file_path", ""),
                "downloaded_at": str(unit.get("downloaded_at") or "")[:10],
                "status": unit.get("status") or "downloaded",
                "files": unit.get("files") or [],
                "file_count": unit.get("file_count"),
                "total_size_bytes": unit.get("total_size_bytes"),
            }

        if canonical_seasons:
            # The canonical TV object is the source of truth.  It already groups
            # physical files into logical episode objects, so the modal should
            # not rebuild seasons from raw unit rows and accidentally discard
            # file-backed episode payloads.
            seasons_payload = []
            episodes = []
            for season in canonical_seasons:
                season_copy = dict(season)
                season_episodes = [dict(ep) for ep in (season_copy.get("episodes") or [])]
                season_episodes.sort(key=_sort_ep)
                season_copy["episodes"] = season_episodes
                season_copy["episode_count"] = season_copy.get("episode_count") or len(season_episodes)
                seasons_payload.append(season_copy)
                episodes.extend(season_episodes)
            payload["seasons"] = sorted(
                seasons_payload,
                key=lambda group: int(group.get("season") or group.get("season_number") or 0)
                if str(group.get("season") or group.get("season_number") or "0").isdigit() else 9999,
            )
            payload["episodes"] = sorted(episodes, key=_sort_ep)
        else:
            episodes = []
            for unit in payload.get("units") or []:
                episode = _episode_from_unit(unit)
                if episode is not None:
                    episodes.append(episode)
            seasons: dict[str, dict[str, Any]] = {}
            for ep in episodes:
                season_num = ep.get("season")
                key = str(season_num if season_num is not None else "Unknown")
                seasons.setdefault(key, {"season": season_num, "episodes": []})["episodes"].append(ep)
            for group in seasons.values():
                group["episodes"].sort(key=_sort_ep)
                group["episode_count"] = len(group["episodes"])
            payload["episodes"] = sorted(episodes, key=_sort_ep)
            payload["seasons"] = sorted(
                seasons.values(),
                key=lambda group: int(group.get("season") or 0) if str(group.get("season") or "0").isdigit() else 9999,
            )

        computed = canonical.get("computed") or payload.get("computed") or {}
        payload["downloaded_episodes_count"] = int(computed.get("downloaded_episode_count") or len(payload.get("episodes") or []))
        metadata = payload.get("metadata") or {}
        if metadata:
            payload.setdefault("total_seasons", metadata.get("number_of_seasons"))
            payload.setdefault("total_episodes", metadata.get("number_of_episodes"))
            payload.setdefault("status", metadata.get("status"))
        return payload


