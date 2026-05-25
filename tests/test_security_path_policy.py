"""Tests for filesystem path safety policy."""

from pathlib import Path

import pytest

from src.core.models import SecurityConfig
from src.core.security.path_policy import SafePathResolver, SecurityPolicyError


def test_safe_path_resolver_blocks_paths_outside_allowed_root(tmp_path: Path) -> None:
    """A category resolver must reject paths outside its explicit root."""
    allowed = tmp_path / "library"
    outside = tmp_path / "outside" / "evil.mkv"
    allowed.mkdir()
    outside.parent.mkdir()
    outside.write_text("bad", encoding="utf-8")

    resolver = SafePathResolver([allowed], category_id="tv")
    decision = resolver.resolve(outside, purpose="test", must_exist=True)

    assert not decision.ok
    assert "outside" in (decision.reason or "")
    with pytest.raises(SecurityPolicyError):
        resolver.require(outside, purpose="test", must_exist=True)


def test_safe_path_resolver_allows_nested_paths_inside_root(tmp_path: Path) -> None:
    """Nested paths under an allowed root should resolve successfully."""
    allowed = tmp_path / "library"
    target = allowed / "Show" / "Season 01" / "episode.mkv"
    target.parent.mkdir(parents=True)
    target.write_text("ok", encoding="utf-8")

    resolver = SafePathResolver([allowed], category_id="tv")
    resolved = resolver.require(target, purpose="test", must_exist=True)

    assert resolved == target.resolve()


def test_safe_unlink_quarantines_files_in_category_trash(tmp_path: Path) -> None:
    """Deletes should move files to .ljs-trash by default instead of permanently removing them."""
    allowed = tmp_path / "library"
    file_path = allowed / "Movie" / "Movie.mkv"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("media", encoding="utf-8")

    resolver = SafePathResolver([allowed], category_id="movie", config=SecurityConfig(use_trash_for_deletes=True))
    operation = resolver.safe_unlink(file_path, purpose="movie.delete", move_to_trash=True)

    assert operation.allowed
    assert operation.operation == "trash_file"
    assert not file_path.exists()
    assert operation.trash_path is not None
    assert Path(operation.trash_path).exists()
    assert ".ljs-trash" in operation.trash_path


def test_safe_hardlink_rejects_destination_escape(tmp_path: Path) -> None:
    """Copy/link destinations cannot escape the allowed roots."""
    allowed = tmp_path / "downloads"
    source = allowed / "file.mkv"
    outside = tmp_path / "outside" / "file.mkv"
    allowed.mkdir()
    outside.parent.mkdir()
    source.write_text("media", encoding="utf-8")

    resolver = SafePathResolver([allowed], category_id="tv")

    with pytest.raises(SecurityPolicyError):
        resolver.safe_hardlink(source, outside, purpose="test.hardlink")
