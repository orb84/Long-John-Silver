#!/usr/bin/env python3
"""
Codebase dumper for LJS.

Walks the project tree and writes a single JSON file containing
an array of {name, path, content} objects for every non-binary,
non-ignored file in the repository.

Usage:
    python scripts/dump_codebase.py                          # stdout
    python scripts/dump_codebase.py -o codebase.json         # to file
    python scripts/dump_codebase.py --src-only               # src/ and config/ only
"""

import argparse
import json
import sys
from pathlib import Path

BINARY_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".pyd",
    ".so", ".dll", ".dylib",
    ".exe", ".bin", ".msi",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".flac", ".wav", ".ogg",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".db", ".sqlite", ".sqlite3",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".ico", ".icns",
    ".whl", ".egg", ".egg-info",
})

EXTRA_IGNORED_DIRS = frozenset({
    "UI_Mocks", "ui_mocks", "UI",
    "skills",
    "scratch",
    "fake_dir",
    ".gemini",
    ".old_config_backup",
})

MAX_FILE_SIZE = 1_024_000  # 1 MB


class CodebaseDumper:
    """Walks the project tree and produces structured JSON of all source files.

    Each entry contains the file's base name, its relative path from the
    project root, and its text content. Binary files, .gitignored files,
    and other non-source artifacts are skipped.
    """

    def __init__(self, root: Path, src_only: bool = False, max_size: int = MAX_FILE_SIZE):
        self._root = root.resolve()
        self._src_only = src_only
        self._max_size = max_size
        self._ignored_dirs: set[str] = set()
        self._ignored_files: set[str] = set()
        self._ignored_globs: list[str] = []
        self._load_gitignore()

    def _load_gitignore(self) -> None:
        """Parse .gitignore and combine with hard-coded ignored paths."""
        gitignore_path = self._root / ".gitignore"
        gitignored_dirs: set[str] = set()
        gitignored_files: set[str] = set()
        gitignored_globs: list[str] = []

        if gitignore_path.exists():
            for line in gitignore_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("!"):
                    continue
                if line.endswith("/"):
                    gitignored_dirs.add(line.rstrip("/"))
                elif line.startswith("*."):
                    gitignored_globs.append(line.lstrip("*"))
                else:
                    gitignored_files.add(line)

        self._ignored_dirs = gitignored_dirs | EXTRA_IGNORED_DIRS
        self._ignored_files = gitignored_files
        self._ignored_globs = gitignored_globs

    def run(self) -> list[dict[str, str]]:
        """Walk the project and return a list of {name, path, content} dicts."""
        entries: list[dict[str, str]] = []
        for filepath in sorted(self._iter_files()):
            entry = self._build_entry(filepath)
            if entry is not None:
                entries.append(entry)
        return entries

    def _iter_files(self) -> list[Path]:
        """Recursively collect eligible file paths."""
        files: list[Path] = []
        for child in self._root.iterdir():
            if child.name.startswith(".") and child.name not in {".env.example", ".gitignore"}:
                continue
            if child.name in self._ignored_dirs:
                continue
            if child.is_dir():
                files.extend(self._walk(child))
            elif child.is_file():
                if self._should_include(child):
                    files.append(child)
        return files

    def _walk(self, directory: Path) -> list[Path]:
        """Recursively walk a directory, collecting eligible files."""
        files: list[Path] = []
        try:
            for child in directory.iterdir():
                if child.name in self._ignored_dirs:
                    continue
                if child.is_dir():
                    files.extend(self._walk(child))
                elif child.is_file():
                    if self._should_include(child):
                        files.append(child)
        except PermissionError:
            pass
        return files

    def _should_include(self, path: Path) -> bool:
        """Determine whether a file should be included in the dump."""
        name = path.name

        if name in self._ignored_files:
            return False

        ext = path.suffix.lower()
        if ext in BINARY_EXTENSIONS:
            return False

        if ext in self._ignored_globs:
            return False

        if self._src_only:
            rel = path.relative_to(self._root)
            top = rel.parts[0] if rel.parts else ""
            if top not in {"src", "config", "migrations", "scripts", "tests"}:
                if rel.name not in {"main.py", "requirements.txt", "pyproject.toml", "Makefile"}:
                    return False

        try:
            if path.stat().st_size > self._max_size:
                return False
        except OSError:
            return False

        return True

    def _is_binary_by_content(self, path: Path) -> bool:
        """Check first 8 KB for null bytes as a binary-content heuristic."""
        try:
            with path.open("rb") as f:
                chunk = f.read(8192)
            return b"\0" in chunk
        except OSError:
            return True

    def _build_entry(self, filepath: Path) -> dict[str, str] | None:
        """Build a {name, path, content} dict for a single file, or None if unreadable."""
        rel_path = filepath.relative_to(self._root)

        if self._is_binary_by_content(filepath):
            return None

        try:
            content = filepath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return None

        return {
            "name": filepath.name,
            "path": str(rel_path),
            "content": content,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dump the LJS codebase to a structured JSON file."
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Write output to FILE instead of stdout.",
    )
    parser.add_argument(
        "--src-only",
        action="store_true",
        help="Only include src/, config/, tests/, scripts/, migrations/, and root files.",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Project root directory (default: parent of scripts/).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.root:
        root = Path(args.root).resolve()
    else:
        root = Path(__file__).resolve().parent.parent

    dumper = CodebaseDumper(root=root, src_only=args.src_only)
    entries = dumper.run()
    output = json.dumps(entries, indent=2, ensure_ascii=False)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Wrote {len(entries)} files to {args.output}")
    else:
        sys.stdout.write(output)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
