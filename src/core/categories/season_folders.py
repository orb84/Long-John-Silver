"""Season-folder convention helpers for episodic media libraries.

The same TV show can already contain human-created folders such as
``Season 5`` while LJS' default naming template may prefer ``Season 05``.
This module keeps imports and repairs convention-aware so completed downloads
join the existing library layout instead of silently splitting a season across
multiple sibling folders.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from loguru import logger


_SEASON_FOLDER_RE = re.compile(r"^\s*(?:season|series)\s*(?P<number>\d{1,3})\s*$", re.IGNORECASE)
_VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".mpg", ".mpeg", ".wmv"}


class SeasonFolderLayout:
    """Detect and preserve a show's existing season-folder style.

    The helper is intentionally filesystem-light and category-agnostic: callers
    pass the show directory or a planned target path, and it only inspects
    sibling folders named like ``Season 5``/``Season 05``.  It never overwrites
    user files while repairing duplicate folders.
    """

    @classmethod
    def season_number_from_name(cls, name: str) -> int | None:
        """Return the season number encoded in a folder name, if any."""
        match = _SEASON_FOLDER_RE.match(str(name or ""))
        if not match:
            return None
        try:
            return int(match.group("number"))
        except (TypeError, ValueError):
            return None

    @classmethod
    def season_dirs(cls, show_dir: Path) -> dict[int, list[Path]]:
        """Return existing season folders below ``show_dir`` grouped by number."""
        grouped: dict[int, list[Path]] = defaultdict(list)
        if not show_dir.is_dir():
            return {}
        try:
            children = list(show_dir.iterdir())
        except OSError:
            return {}
        for child in children:
            if not child.is_dir() or child.name.startswith("."):
                continue
            number = cls.season_number_from_name(child.name)
            if number is not None:
                grouped[number].append(child)
        return dict(grouped)

    @classmethod
    def prefer_existing_parent(cls, target: Path, *, season: int | None = None) -> Path:
        """Rewrite a planned episode target to an existing season folder when possible.

        Args:
            target: Planned full file path, normally ``Show/Season 05/file.mkv``.
            season: Optional season override from metadata.

        Returns:
            The same path, or a path whose parent is the best matching existing
            season folder for that show.
        """
        planned_season = season if season is not None else cls.season_number_from_name(target.parent.name)
        if planned_season is None:
            return target
        show_dir = target.parent.parent
        if not show_dir.exists() or not show_dir.is_dir():
            return target
        preferred_parent = cls.preferred_season_dir(show_dir, int(planned_season), proposed_dir=target.parent)
        if preferred_parent == target.parent:
            return target
        return preferred_parent / target.name

    @classmethod
    def preferred_season_dir(cls, show_dir: Path, season: int, *, proposed_dir: Path | None = None) -> Path:
        """Return the folder that should hold ``season`` for ``show_dir``.

        Existing same-season folders always win over blindly applying the naming
        template. When duplicates exist, the folder matching the wider show
        convention is preferred. With no style evidence, unpadded ``Season 5``
        is preferred over padded ``Season 05`` because it is more often the
        manually-created folder that the user already had before LJS imported.
        """
        grouped = cls.season_dirs(show_dir)
        candidates = grouped.get(int(season), [])
        preferred_name = cls._preferred_name(show_dir, int(season), grouped)

        if candidates:
            exact = [path for path in candidates if path.name == preferred_name]
            if exact:
                return sorted(exact, key=lambda p: p.name.lower())[0]
            unpadded_name = f"Season {int(season)}"
            unpadded = [path for path in candidates if path.name == unpadded_name]
            if unpadded:
                return sorted(unpadded, key=lambda p: p.name.lower())[0]
            return sorted(candidates, key=lambda p: (-cls._media_file_count(p), len(p.name), p.name.lower()))[0]

        if proposed_dir is not None and cls.season_number_from_name(proposed_dir.name) == int(season):
            return proposed_dir
        return show_dir / preferred_name

    @classmethod
    def repair_duplicate_season_folders(cls, show_dir: Path) -> int:
        """Merge duplicate season folders such as ``Season 5`` and ``Season 05``.

        The merge is conservative: files are moved inside the same show folder,
        conflicting names are given a numbered suffix, and non-empty source
        folders are left in place for manual inspection.

        Returns:
            Number of child entries moved.
        """
        moved = 0
        grouped = cls.season_dirs(show_dir)
        for season, folders in grouped.items():
            if len(folders) < 2:
                continue
            target = cls.preferred_season_dir(show_dir, season)
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(f"Could not create preferred season folder {target}: {exc}")
                continue
            for folder in sorted(folders, key=lambda p: p.name.lower()):
                if folder == target:
                    continue
                try:
                    children = list(folder.iterdir())
                except OSError as exc:
                    logger.warning(f"Could not inspect duplicate season folder {folder}: {exc}")
                    continue
                for child in children:
                    destination = cls._unique_destination(target / child.name)
                    try:
                        child.rename(destination)
                        moved += 1
                    except OSError as exc:
                        logger.warning(f"Could not merge {child} into {target}: {exc}")
                try:
                    folder.rmdir()
                except OSError:
                    pass
        if moved:
            logger.info(f"Merged {moved} entr{'y' if moved == 1 else 'ies'} from duplicate season folders in {show_dir}")
        return moved

    @classmethod
    def _preferred_name(cls, show_dir: Path, season: int, grouped: dict[int, list[Path]] | None = None) -> str:
        width = cls._preferred_padding_width(show_dir, grouped or cls.season_dirs(show_dir), season)
        if width and width > 1:
            return f"Season {season:0{width}d}"
        return f"Season {season}"

    @classmethod
    def _preferred_padding_width(
        cls,
        show_dir: Path,
        grouped: dict[int, list[Path]],
        current_season: int | None = None,
    ) -> int | None:
        """Infer whether the show mostly uses padded or unpadded season names."""
        votes: Counter[int] = Counter()
        for season, folders in grouped.items():
            # Duplicate folders for the current season are the ambiguity we are
            # trying to resolve, so do not let them dominate the style vote.
            if current_season is not None and season == current_season and len(folders) > 1:
                continue
            for folder in folders:
                token = cls._number_token(folder.name)
                if not token:
                    continue
                width = len(token) if token.startswith("0") and len(token) > 1 else 1
                votes[width] += 1
        if not votes:
            return None
        padded_votes = sum(count for width, count in votes.items() if width > 1)
        unpadded_votes = votes.get(1, 0)
        if padded_votes > unpadded_votes:
            return max((width for width in votes if width > 1), default=2)
        return None

    @staticmethod
    def _number_token(name: str) -> str | None:
        match = _SEASON_FOLDER_RE.match(str(name or ""))
        return match.group("number") if match else None

    @classmethod
    def _media_file_count(cls, folder: Path) -> int:
        count = 0
        try:
            for child in folder.rglob("*"):
                if child.is_file() and child.suffix.lower() in _VIDEO_EXTENSIONS:
                    count += 1
        except OSError:
            return 0
        return count

    @staticmethod
    def _unique_destination(destination: Path) -> Path:
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        parent = destination.parent
        for index in range(2, 1000):
            candidate = parent / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                return candidate
        return parent / f"{stem} (merged){suffix}"
