"""
Category-owned artwork cache for LJS.

Artwork discovered by a category is downloaded into that category's app-data
folder instead of being treated as global UI state. The resulting files are
served read-only by the web app and referenced from category metadata envelopes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger

from src.core.security.path_policy import SafePathResolver, SecurityPolicyError

TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p/w500'
DEFAULT_EXTENSION = '.jpg'
MAX_ARTWORK_BYTES = 8 * 1024 * 1024


class CategoryArtworkManager:
    """Downloads and stores category-owned artwork under ``data/categories``."""

    def __init__(self, base_dir: str | Path = 'data/categories') -> None:
        """Create an artwork manager.

        Args:
            base_dir: App-controlled category data root.
        """
        self._base_dir = Path(base_dir)
        self._resolver = SafePathResolver.for_application(extra_roots=[self._base_dir])

    @property
    def base_dir(self) -> Path:
        """Return the category data root."""
        return self._base_dir

    async def cache_poster_from_metadata(
        self,
        category_id: str,
        item_id: str,
        metadata: dict[str, Any],
        provider: str = 'metadata',
    ) -> dict[str, Any]:
        """Download poster artwork referenced by a metadata envelope.

        The method mutates and returns ``metadata`` with these optional fields:
        ``poster_url``, ``local_poster_path``, and ``local_poster_url``.
        Failed downloads are logged and leave the original metadata intact.
        """
        poster_path = str(metadata.get('poster_path') or metadata.get('poster_url') or '').strip()
        if not poster_path:
            return metadata
        source_url = self.poster_source_url(poster_path)
        if not source_url:
            return metadata
        metadata.setdefault('poster_url', source_url)
        try:
            local_path = await self.cache_poster(category_id, item_id, source_url, provider=provider)
        except Exception as exc:
            logger.debug(f'Artwork cache skipped for {category_id}/{item_id}: {exc}')
            return metadata
        if local_path:
            metadata['local_poster_path'] = str(local_path)
            metadata['local_poster_url'] = self.public_url_for(local_path)
        return metadata

    async def cache_poster(self, category_id: str, item_id: str, source_url: str, provider: str = 'metadata') -> Path | None:
        """Download a poster image into the category metadata/artwork folder."""
        if not source_url.startswith(('http://', 'https://')):
            return None
        extension = self._extension_from_url(source_url)
        target = self._target_path(category_id, item_id, f'poster{extension}')
        if target.exists() and target.stat().st_size > 0:
            return target
        safe_target = self._resolver.ensure_destination(target, purpose=f'{category_id}.artwork.poster', allow_overwrite=True)
        self._resolver.safe_mkdir(safe_target.parent, purpose=f'{category_id}.artwork.mkdir')
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(source_url)
            response.raise_for_status()
            content = response.content
        if len(content) > MAX_ARTWORK_BYTES:
            raise ValueError('Artwork response is larger than the configured safety limit')
        content_type = response.headers.get('content-type', '')
        if content_type and not content_type.startswith('image/'):
            raise ValueError(f'Artwork response is not an image: {content_type}')
        safe_target.write_bytes(content)
        logger.info(f'Cached {provider} artwork for {category_id}/{item_id}: {safe_target}')
        return safe_target

    def public_url_for(self, local_path: Path | str) -> str:
        """Return the web URL for a cached category artwork path."""
        safe_path = self._resolver.require(local_path, purpose='artwork.public_url', must_exist=False)
        relative = safe_path.relative_to(self._base_dir.resolve(strict=False))
        return '/category-data/' + '/'.join(relative.parts)

    @staticmethod
    def poster_source_url(poster_path: str) -> str | None:
        """Convert a provider poster path into a downloadable URL."""
        if poster_path.startswith(('http://', 'https://')):
            return poster_path
        if poster_path.startswith('/'):
            return f'{TMDB_IMAGE_BASE}{poster_path}'
        return None

    def _target_path(self, category_id: str, item_id: str, filename: str) -> Path:
        """Build the safe category-owned artwork target path."""
        return self._base_dir / self._slug(category_id) / 'metadata' / 'artwork' / self._slug(item_id) / filename

    @staticmethod
    def _slug(value: str) -> str:
        """Return a filesystem-safe slug for category data folders."""
        slug = re.sub(r'[^a-zA-Z0-9._-]+', '_', str(value).strip()).strip('._-')
        return slug or 'item'

    @staticmethod
    def _extension_from_url(source_url: str) -> str:
        """Infer a safe image extension from a URL."""
        path = urlparse(source_url).path.lower()
        for ext in ('.jpg', '.jpeg', '.png', '.webp'):
            if path.endswith(ext):
                return ext
        return DEFAULT_EXTENSION
