"""Helpers for preserving stable media identity through download import."""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.core.models import DownloadImportContext, DownloadItem


def _normalize_import_context(
    import_context: DownloadImportContext | dict[str, Any] | None,
    *,
    item_name: str,
    category_id: str,
    item_id: str,
    season: int | None,
    episode: int | None,
    language: str,
    torrent_title: str,
) -> DownloadImportContext | None:
    """Return a queue-time identity snapshot with caller fields filled in."""
    if not import_context:
        return None
    try:
        context = (
            import_context
            if isinstance(import_context, DownloadImportContext)
            else DownloadImportContext(**dict(import_context))
        )
    except Exception as exc:
        logger.warning(f"Ignoring invalid download import context for {item_name}: {exc}")
        return None
    updates: dict[str, Any] = {}
    if category_id and not context.category_id:
        updates["category_id"] = category_id
    if item_id and not context.item_id:
        updates["item_id"] = item_id
    if item_name and not context.planning_title:
        updates["display_title"] = item_name
        updates["canonical_title"] = item_name
    if season is not None and context.season is None:
        updates["season"] = season
    if episode is not None and context.episode is None:
        updates["episode"] = episode
    if context.unit_descriptor:
        coordinates = context.unit_descriptor.get("coordinates") if isinstance(context.unit_descriptor.get("coordinates"), dict) else {}
        if season is not None and coordinates.get("season") is None:
            coordinates = dict(coordinates)
            coordinates["season"] = season
        if episode is not None and coordinates.get("episode") is None:
            coordinates = dict(coordinates)
            coordinates["episode"] = episode
        if coordinates != context.unit_descriptor.get("coordinates"):
            descriptor = dict(context.unit_descriptor)
            descriptor["coordinates"] = coordinates
            updates["unit_descriptor"] = descriptor
    if language and not context.language:
        updates["language"] = language
    if torrent_title and not context.release_title:
        updates["release_title"] = torrent_title
    return context.model_copy(update=updates) if updates else context


def _apply_import_context_defaults(
    context: DownloadImportContext | None,
    *,
    item_name: str,
    category_id: str,
    item_id: str,
    season: int | None,
    episode: int | None,
    language: str,
) -> tuple[str, str, str, int | None, int | None, str]:
    """Fill queue fields from persisted identity without replacing explicit values."""
    if not context:
        return item_name, category_id, item_id, season, episode, language
    return (
        item_name or context.planning_title,
        category_id or context.category_id,
        item_id or context.item_id,
        season if season is not None else context.season,
        episode if episode is not None else context.episode,
        language or context.language,
    )


async def _find_duplicate_import_context(
    downloads_repo: Any,
    context: DownloadImportContext | None,
    *,
    download_id: str,
) -> DownloadItem | None:
    """Return an existing download that already represents this media unit."""
    if not context:
        return None
    duplicates = await downloads_repo.find_existing_by_import_context(context)
    return next((download for download in duplicates if download.id != download_id), None)
