"""Category-first architecture guard for LJS.

Fails when retired global TV/movie/show symbols are reintroduced outside
category-owned or integration-owned boundaries. This guard intentionally scans
plain source text so it can run before the full dependency-backed test suite.
"""

from __future__ import annotations

import re
from pathlib import Path


class CategoryArchitectureGuard:
    """Scans source files for retired category-specific global symbols."""

    BANNED_SYMBOLS = {
        "/api/shows",
        "ShowsRouter",
        "ShowActionHandler",
        "show_tracking",
        "get_tmdb_details",
        "get_tmdb_season",
        "get_tvmaze_show",
        "get_rotten_tomatoes_and_reviews",
        "add_show_to_watch",
        "remove_show_from_watch",
        "delete_episode",
        "delete_movie",
        "search_web",
        "sync_tracked_shows",
        "tracked_shows",
        "paused_shows",
        "show_progress",
        "shows_data",
        "total_shows",
        "show_names",
        "ShowMetadata",
        "MovieMetadata",
        "enrich_feature",
        "enrich_series",
        "tmdb_feature",
        "tmdb_series",
        "tmdb_movie",
        "tmdb_tv",
        "category_id == \"tv\"",
        "category_id == 'tv'",
        "category_id == \"movie\"",
        "category_id == 'movie'",
        "media_category == \"tv\"",
        "media_category == 'tv'",
        "media_category == \"movie\"",
        "media_category == 'movie'",
    }
    CATEGORY_OWNED_PREFIXES = (
        Path("src/core/categories"),
        Path("src/integrations"),
    )
    SCANNED_SUFFIXES = {".py", ".js", ".html"}
    RETIRED_PATHS = {Path("src/core/season_pack.py")}

    def __init__(self, root: Path) -> None:
        """Initialize the guard with a repository root."""
        self._root = root.resolve()

    def scan(self) -> dict[str, list[str]]:
        """Return offending files keyed by banned symbol."""
        offenders: dict[str, list[str]] = {}
        for retired in self.RETIRED_PATHS:
            if (self._root / retired).exists():
                offenders.setdefault("retired_path", []).append(str(retired))

        for path in self._source_files():
            relative = path.relative_to(self._root)
            if self._is_allowed_category_file(relative):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for symbol in self.BANNED_SYMBOLS:
                if self._contains_symbol(text, symbol):
                    offenders.setdefault(symbol, []).append(str(relative))
        return offenders

    def _source_files(self) -> list[Path]:
        """Return source files covered by the guard."""
        files: list[Path] = []
        for source_root in (self._root / "src", self._root / "scripts"):
            if source_root.exists():
                files.extend(path for path in source_root.rglob("*") if path.suffix in self.SCANNED_SUFFIXES)
        return files


    def _contains_symbol(self, text: str, symbol: str) -> bool:
        """Return whether text contains a banned symbol as a real token.

        Identifier bans should not match larger identifiers such as
        ``research_web`` when the retired symbol is ``search_web``. Non-code
        path/string bans still use plain containment because they include
        punctuation such as ``/api/shows`` or explicit category-id comparisons.
        """
        if symbol.isidentifier():
            return re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", text) is not None
        return symbol in text

    def _is_allowed_category_file(self, relative_path: Path) -> bool:
        """Return true for explicit category/integration ownership boundaries."""
        if relative_path == Path("scripts/check_category_architecture.py"):
            return True
        return any(relative_path.is_relative_to(prefix) for prefix in self.CATEGORY_OWNED_PREFIXES)


def main() -> int:
    """Run the guard from the command line."""
    root = Path(__file__).resolve().parents[1]
    offenders = CategoryArchitectureGuard(root).scan()
    if offenders:
        for symbol, paths in offenders.items():
            print(f"{symbol}: {', '.join(paths)}")
        return 1
    print("Category architecture guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
