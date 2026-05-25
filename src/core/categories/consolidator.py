"""
Library consolidation for LJS.

Provides LibraryConsolidator as a focused collaborator extracted from
MediaCategory. Walks library directories and renames files to match
current naming templates.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from loguru import logger

from src.core.security.path_policy import SafePathResolver, SecurityPolicyError

if TYPE_CHECKING:
    from src.core.categories.path_planner import CategoryPathPlanner
    from src.core.categories.base import MediaCategory
    from src.core.models import Settings


class LibraryConsolidator:
    """Walks a category's library directory and renames files to match the current naming template.

    Accepts a CategoryPathPlanner for target path computation,
    and a MediaCategory for domain-specific parsing and file patterns.
    """

    def __init__(self, path_planner: "CategoryPathPlanner") -> None:
        """Initialize with a path planner for target path computation.

        Args:
            path_planner: Planner used to compute target paths.
        """
        self._path_planner = path_planner

    def consolidate(
        self,
        category: "MediaCategory",
        root_path: str,
        dry_run: bool = True,
        settings: Optional["Settings"] = None,
    ) -> list[dict[str, Any]]:
        """Walk the category's library directory and rename files to match the current template.

        Args:
            category: The media category (provides parse_name, accepted_file_patterns, etc.).
            root_path: Root directory of the category's library.
            dry_run: If True, only report what would change without moving files.
            settings: Application settings for naming template lookup.

        Returns:
            List of result dicts with old_path, new_path, and status.
        """
        results: list[dict[str, Any]] = []
        root = Path(root_path)
        if not root.exists():
            return results

        extensions = {p.replace("*", "") for p in category.accepted_file_patterns}

        for file_path in root.rglob("*"):
            if file_path.is_dir() or file_path.suffix.lower() not in extensions:
                continue

            try:
                parsed = category.parse_name(file_path.name)

                # The consolidator must not decide what parsed fields mean.
                # It walks files and performs the safe move; the category maps
                # parsed observations and naming-template settings to a target.
                target = category.consolidation_target_for_file(
                    file_path,
                    root,
                    parsed,
                    settings=settings,
                )

                if file_path.resolve() == target.resolve():
                    continue

                if target.exists():
                    results.append({
                        "old_path": str(file_path),
                        "new_path": str(target),
                        "status": "skipped (target exists)",
                    })
                    continue

                result = {
                    "old_path": str(file_path),
                    "new_path": str(target),
                    "status": "pending" if dry_run else "moved",
                }

                if not dry_run:
                    resolver = SafePathResolver(
                        allowed_roots=[root],
                        category_id=getattr(category, "category_id", None),
                        config=getattr(settings, "security", None),
                    )
                    resolver.safe_mkdir(target.parent, purpose=f"{category.category_id}.consolidate.mkdir")
                    resolver.safe_move(file_path, target, purpose=f"{category.category_id}.consolidate.move")

                results.append(result)

            except SecurityPolicyError as e:
                logger.warning(f"Consolidation blocked unsafe path for {file_path}: {e}")
                results.append({
                    "old_path": str(file_path),
                    "new_path": str(target) if 'target' in locals() else "",
                    "status": f"blocked: {str(e)}",
                })

            except Exception as e:
                logger.error(f"Consolidation failed for {file_path}: {e}")
                results.append({
                    "old_path": str(file_path),
                    "status": f"error: {str(e)}",
                })

        return results
