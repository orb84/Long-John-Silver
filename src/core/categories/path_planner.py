"""
Category path planning for LJS.

Provides CategoryPathPlanner as a focused collaborator extracted from
MediaCategory. Owns naming template lookup and target path calculation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.categories.identity import clean_display_title, clean_path_fragment

if TYPE_CHECKING:
    from src.core.models import Settings


class CategoryPathPlanner:
    """Computes target library paths from category-supplied templates.

    This helper is deliberately domain-neutral. Categories choose the template
    and the fields they pass; the planner only formats, sanitizes, and appends
    the source file extension.
    """

    @staticmethod
    def format_template(template: str, data: dict) -> str:
        """Safely format a naming template with provided data.

        Missing/``None`` optional fields must not leak into user-facing paths.
        Categories that need domain aliases should pass them explicitly in
        ``data``; the generic planner does not invent category-specific names.
        """
        safe_data = {key: ("" if value is None else value) for key, value in dict(data or {}).items()}
        title = clean_display_title(safe_data.get("title"), fallback="Unknown")
        safe_data.setdefault("title", title)
        safe_data.setdefault("filename", "")
        safe_data.setdefault("filename_stem", "")
        safe_data.setdefault("unit_title", "")
        safe_data.setdefault("year", "")
        safe_data.setdefault("quality", "")
        safe_data.setdefault("release_group", "")
        try:
            formatted = template.format(**safe_data)
        except Exception:
            formatted = title
        return clean_path_fragment(formatted, fallback=title)

    def compute_target_path_from_fields(
        self,
        *,
        source_name: str,
        template: str,
        library_root: str = "./library",
        fields: dict[str, Any] | None = None,
    ) -> Path:
        """Compute a target path from category-supplied template fields.

        This is the category-neutral path-planning primitive.  The planner does
        not know what the fields mean; it only supplies universal file aliases,
        formats the category's template, sanitizes the result, and appends the
        source suffix.  Categories are responsible for adding any domain fields
        their naming template supports.
        """
        source = Path(source_name or "")
        template_data: dict[str, Any] = dict(fields or {})
        template_data.setdefault("title", clean_display_title(template_data.get("title"), fallback="Unknown"))
        template_data.setdefault("filename", source.name)
        template_data.setdefault("filename_stem", source.stem)
        template_data.setdefault("unit_title", "")
        template_data.setdefault("year", "")
        template_data.setdefault("quality", "")
        template_data.setdefault("release_group", "")

        relative_path = self.format_template(template, template_data)
        suffix = source.suffix or ".mkv"
        return Path(library_root) / (relative_path + suffix)

    def compute_target_path(
        self,
        source_name: str,
        item_name: str,
        season: int,
        episode: int,
        template: str,
        library_root: str = "./library",
        **kwargs: Any,
    ) -> Path:
        """Legacy wrapper for older category path callers.

        New category code should call ``compute_target_path_from_fields`` so the
        generic planner is not treated as owning any category-specific unit
        vocabulary.  This wrapper remains for existing category callers and
        tests; the fields are merely passed through as category-supplied data.
        """
        fields = {
            "title": clean_display_title(item_name, fallback="Unknown"),
            "year": kwargs.get("year") or "",
            "season": season,
            "episode": episode,
            "unit_title": kwargs.get("unit_title") or kwargs.get("episode_title") or "",
            "quality": kwargs.get("quality") or "",
            "release_group": kwargs.get("release_group") or "",
        }
        for key, value in kwargs.items():
            if key not in {"settings", "library_root", "item_name"}:
                fields.setdefault(key, value)
        return self.compute_target_path_from_fields(
            source_name=source_name,
            template=template,
            library_root=library_root,
            fields=fields,
        )
