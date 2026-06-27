"""
Notification-only retry policy for unmatched media searches.

The retry scheduler is deliberately separate from individual agent tools so
missed-search follow-up behavior has one policy boundary. It may schedule a
future search prompt, but it must not silently authorize a future download.
"""

from __future__ import annotations

import hashlib
from typing import Any

from loguru import logger

from src.core.models import ToolExecutionContext


class UnmatchedSearchRetryScheduler:
    """Create bounded notification-only retries for zero-result searches.

    Peer-to-peer availability can change, so an explicit user search that finds
    nothing may benefit from a later re-check. The scheduled task is a discovery
    notification, not an auto-download grant; queueing still requires a fresh
    user/LLM decision using stable candidate IDs and category policy.
    """

    async def maybe_schedule(
        self,
        *,
        scheduler: Any,
        res: dict[str, Any],
        name: str,
        category_id: str | None,
        search_scope: str | None,
        context: ToolExecutionContext,
    ) -> None:
        """Schedule one deduplicated retry when torrents and companion sources both miss."""
        if int(res.get('candidate_count') or 0) > 0:
            return
        companion = res.get('companion_soulseek') if isinstance(res.get('companion_soulseek'), dict) else {}
        if int(companion.get('candidate_count') or 0) > 0:
            return
        prompt_scheduler = getattr(scheduler, '_prompt_scheduler', None) if scheduler is not None else None
        settings = getattr(getattr(scheduler, '_settings_manager', None), 'settings', None) if scheduler is not None else None
        cfg = getattr(settings, 'soulseek', None) if settings is not None else None
        if not prompt_scheduler or not cfg or not getattr(cfg, 'auto_retry_unmatched_searches', True):
            return

        marker_src = f'{category_id or ""}:{name}:{search_scope or "default"}'
        marker = 'ljs:auto-retry-search:' + hashlib.sha256(marker_src.encode('utf-8')).hexdigest()[:16]
        try:
            existing = await prompt_scheduler.list_tasks(user_id=context.user_id)
            for task in existing:
                if getattr(task, 'enabled', False) and marker in str(getattr(task, 'prompt', '')):
                    res['deferred_search_retry'] = {
                        'scheduled': True,
                        'existing': True,
                        'task_id': getattr(task, 'id', ''),
                        'interval_minutes': getattr(task, 'interval_minutes', None),
                        'reason': 'A notification-only recurring retry already exists for this missed search.',
                    }
                    return

            prompt = self._retry_prompt(
                marker=marker,
                name=name,
                category_id=category_id,
                search_scope=search_scope,
            )
            task = await prompt_scheduler.create_task(
                prompt=prompt,
                interval_minutes=int(getattr(cfg, 'retry_search_interval_minutes', 360) or 360),
                user_id=context.user_id,
                channel=context.source or 'web',
                title=f'Retry search: {name}',
                task_type='condition_check',
                schedule_type='recurring',
                delay_minutes=int(getattr(cfg, 'retry_search_interval_minutes', 360) or 360),
                max_runs=int(getattr(cfg, 'retry_search_max_runs', 12) or 12),
                session_id=context.session_id,
            )
            res['deferred_search_retry'] = {
                'scheduled': True,
                'existing': False,
                'task_id': task.id,
                'interval_minutes': task.interval_minutes,
                'max_runs': task.max_runs,
                'reason': (
                    'No torrent or Soulseek candidates were found; LJS will retry as a '
                    'notification-only search because P2P availability changes over time.'
                ),
            }
        except Exception as exc:
            logger.warning(f'Failed to schedule unmatched-search retry for {name!r}: {exc}')
            res['deferred_search_retry'] = {'scheduled': False, 'error': str(exc)}

    @staticmethod
    def _retry_prompt(*, marker: str, name: str, category_id: str | None, search_scope: str | None) -> str:
        """Build the safe prompt used by the scheduled retry task."""
        return (
            f'[{marker}] Search again for {name!r} in category {category_id or "auto"} '
            'using torrents and Soulseek if configured. Use concise source-appropriate queries; '
            'preserve title and category terms, but do not add generic acquisition words unless part of the title. '
            'Never queue, start, or auto-download anything from this retry task, even if one candidate looks clear. '
            'Notify me with the best candidates, stable candidate IDs, source, size, seeders, and any category-owned '
            'language/quality warnings so I can explicitly choose what to queue. '
            f'Original search_scope={search_scope or "default"}.'
        )
