"""Persona package discovery and safe asset resolution.

A persona is now more than a prompt snippet.  The canonical on-disk shape is::

    config/personas/<persona_id>/
      persona.md       # required prompt/personality text
      persona.json     # optional display metadata
      avatar.png       # optional assistant avatar
      theme.json       # optional bounded UI theme hints

Loose ``config/personas/<id>.txt`` files are intentionally not supported by
the open-source baseline.  Every selectable persona is a package folder so
prompt text, avatar, metadata, and theme hints travel together as one unit.
Only local files under ``config/personas`` are ever resolved; the web layer
serves avatars through this registry instead of trusting arbitrary paths from
JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from pathlib import Path
from typing import Any


_PERSONA_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ALLOWED_AVATAR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_PROMPT_FILENAME = "persona.md"
_AVATAR_FILENAMES = ("avatar.png", "avatar.jpg", "avatar.jpeg", "avatar.webp")
_ALLOWED_THEME_COLOR_KEYS = {
    # Semantic persona keys.  The frontend maps these onto known CSS variables.
    "accent",
    "accent_gold",
    "accent_gold_glow",
    "accent_teal",
    "accent_teal_glow",
    "accent_red",
    "accent_red_glow",
    "background_deep",
    "bg_deep",
    "ocean_center",
    "ocean_mid",
    "ocean_edge",
    "glass_bg",
    "glass_border",
    "text_main",
    "text_dim",
    "text",
    "text_muted",
    "gold",
    "teal",
    "border",
    # A few bounded ambient colors used by the current website chrome.
    "nav_bg",
    "bubble_bg",
    "compass_bg",
}
_ALLOWED_THEME_STRING_KEYS = {
    "background_style",
    "panel_style",
    "avatar_shape",
    "chat_bubble_style",
}
_ALLOWED_AVATAR_SHAPES = {"freeform", "rounded", "circle", "square"}
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")
_RGBA_RE = re.compile(r"^rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(0|1|0?\.\d+)\s*\)$")


@dataclass(frozen=True)
class PersonaTheme:
    """Bounded presentation hints attached to a persona package.

    The frontend may translate these hints into CSS variables, but the registry
    deliberately accepts only a small whitelist.  Persona packages are user-
    editable files; they should never become arbitrary CSS injection points.
    """

    colors: dict[str, str] = field(default_factory=dict)
    styles: dict[str, str] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for API responses."""
        payload: dict[str, Any] = {**self.colors, **self.styles}
        if self.colors:
            payload["colors"] = dict(self.colors)
        if self.styles:
            payload["styles"] = dict(self.styles)
        return payload


@dataclass(frozen=True)
class PersonaPackage:
    """Resolved persona prompt, metadata, local avatar, and theme hints."""

    id: str
    display_name: str
    description: str
    version: int
    prompt_path: Path
    root_dir: Path
    avatar_path: Path | None = None
    theme: PersonaTheme = field(default_factory=PersonaTheme)

    def read_prompt(self) -> str:
        """Read the persona prompt text, stripping only outer whitespace."""
        return self.prompt_path.read_text(encoding="utf-8").strip()

    @property
    def avatar_filename(self) -> str | None:
        """Return the local avatar filename when this package has an avatar."""
        return self.avatar_path.name if self.avatar_path else None

    def api_summary(self, active: bool = False) -> dict[str, Any]:
        """Return safe metadata suitable for browser consumption."""
        avatar_url = f"/api/personas/{self.id}/avatar" if self.avatar_path else None
        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "version": self.version,
            "active": active,
            "avatar_url": avatar_url,
            "avatar_filename": self.avatar_filename,
            "theme": self.theme.model_dump(),
        }


class PersonaRegistry:
    """Discover and resolve assistant persona packages from ``config/personas``.

    Callers use this as the only authority for active persona assets.  It keeps
    prompt loading, avatar path validation, and theme sanitization in one place
    so backend prompts and frontend chrome cannot drift apart.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or Path.cwd()).resolve()
        self._personas_dir = (self._root / "config" / "personas").resolve()

    @property
    def personas_dir(self) -> Path:
        """Return the configured persona package directory."""
        return self._personas_dir

    def list_packages(self) -> list[PersonaPackage]:
        """Return every valid persona package, sorted with ``default`` first."""
        packages: dict[str, PersonaPackage] = {}
        if not self._personas_dir.exists():
            return [self._fallback_default_package()]

        for child in self._personas_dir.iterdir():
            if child.is_dir() and self._valid_id(child.name):
                package = self._load_directory_package(child.name, child)
                if package:
                    packages[package.id] = package

        if "default" not in packages:
            packages["default"] = self._fallback_default_package()

        return sorted(packages.values(), key=lambda p: (p.id != "default", p.display_name.lower()))

    def load(self, persona_id: str | None = None) -> PersonaPackage:
        """Load a persona by id, falling back to ``default`` safely."""
        requested = persona_id or "default"
        if not self._valid_id(requested):
            requested = "default"
        packages = {package.id: package for package in self.list_packages()}
        return packages.get(requested) or packages.get("default") or self._fallback_default_package()

    def prompt_text(self, persona_id: str | None = None) -> str:
        """Return prompt text for the requested persona with a final fallback."""
        try:
            text = self.load(persona_id).read_prompt()
            return text or self._fallback_prompt()
        except OSError:
            return self._fallback_prompt()

    def avatar_path(self, persona_id: str | None = None) -> Path | None:
        """Return the safe local avatar path for a persona, if one exists."""
        return self.load(persona_id).avatar_path

    def _load_directory_package(self, persona_id: str, directory: Path) -> PersonaPackage | None:
        prompt_path = (directory / _PROMPT_FILENAME).resolve()
        if not (prompt_path.exists() and prompt_path.is_file() and self._is_under(prompt_path, directory)):
            return None
        metadata = self._read_json(directory / "persona.json")
        theme = self._read_theme(directory / "theme.json")
        avatar_path = self._resolve_avatar(directory, metadata.get("avatar"))
        display_name = str(metadata.get("display_name") or metadata.get("displayName") or self._humanize_id(persona_id)).strip()
        description = str(metadata.get("description") or "User-selectable assistant persona package.").strip()
        version = self._int_value(metadata.get("version"), 1)
        return PersonaPackage(
            id=persona_id,
            display_name=display_name or self._humanize_id(persona_id),
            description=description,
            version=version,
            prompt_path=prompt_path,
            root_dir=directory,
            avatar_path=avatar_path,
            theme=theme,
        )

    def _fallback_default_package(self) -> PersonaPackage:
        # Do not create a loose ``default.txt`` fallback.  The repository's
        # canonical default is the folder package in ``config/personas/default``;
        # if an install accidentally deletes it, callers still receive a safe
        # in-memory summary and ``prompt_text()`` falls back to a minimal prompt.
        default_dir = (self._personas_dir / "default").resolve()
        return PersonaPackage(
            id="default",
            display_name="Long John Silver",
            description="Built-in fallback assistant persona package.",
            version=1,
            prompt_path=(default_dir / _PROMPT_FILENAME).resolve(),
            root_dir=default_dir,
            avatar_path=None,
            theme=PersonaTheme(),
        )

    def _resolve_avatar(self, directory: Path, configured: Any = None) -> Path | None:
        candidates: list[str] = []
        if isinstance(configured, str) and configured.strip():
            candidates.append(configured.strip())
        candidates.extend(_AVATAR_FILENAMES)
        for name in candidates:
            candidate = (directory / name).resolve()
            if candidate.suffix.lower() not in _ALLOWED_AVATAR_EXTENSIONS:
                continue
            if candidate.exists() and candidate.is_file() and self._is_under(candidate, directory):
                return candidate
        return None

    def _read_theme(self, path: Path) -> PersonaTheme:
        data = self._read_json(path)
        raw_colors = data.get("colors") if isinstance(data.get("colors"), dict) else data
        raw_styles = data.get("styles") if isinstance(data.get("styles"), dict) else data
        colors: dict[str, str] = {}
        styles: dict[str, str] = {}

        # Theme JSON is meant to hold the current website color system, but it
        # remains a data file rather than executable styling.  Only known keys
        # and simple color values survive this sanitizer.
        for key in _ALLOWED_THEME_COLOR_KEYS:
            value = raw_colors.get(key) if isinstance(raw_colors, dict) else None
            if isinstance(value, str) and self._safe_color(value):
                colors[key] = value.strip()

        for key in _ALLOWED_THEME_STRING_KEYS:
            value = raw_styles.get(key) if isinstance(raw_styles, dict) else None
            if isinstance(value, str):
                normalized = value.strip().lower().replace(" ", "-")
                if key == "avatar_shape" and normalized not in _ALLOWED_AVATAR_SHAPES:
                    continue
                if normalized and len(normalized) <= 40 and re.match(r"^[a-z0-9_-]+$", normalized):
                    styles[key] = normalized
        return PersonaTheme(colors=colors, styles=styles)

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}


    def _valid_id(self, value: str) -> bool:
        return bool(value and _PERSONA_ID_RE.match(value))

    def _safe_color(self, value: str) -> bool:
        value = value.strip()
        if _HEX_COLOR_RE.match(value):
            return True
        match = _RGBA_RE.match(value)
        if not match:
            return False
        return all(0 <= int(match.group(i)) <= 255 for i in (1, 2, 3))

    def _is_under(self, path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    def _int_value(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _humanize_id(self, persona_id: str) -> str:
        if persona_id == "default":
            return "Long John Silver"
        return persona_id.replace("_", " ").replace("-", " ").title()

    def _fallback_prompt(self) -> str:
        return "You are a helpful assistant. Speak clearly and honestly."
