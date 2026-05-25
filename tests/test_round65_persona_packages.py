"""Persona package regressions for the open-source baseline."""

from __future__ import annotations

import json
from pathlib import Path

from src.ai.persona_context import PersonaContext
from src.ai.persona_registry import PersonaRegistry


def test_default_persona_package_loads_prompt_avatar_and_theme() -> None:
    """The bundled default persona should resolve as a folder package."""
    registry = PersonaRegistry(Path.cwd())
    package = registry.load("default")

    assert package.id == "default"
    assert package.display_name == "Long John Silver"
    assert "Long John Silver" in package.read_prompt()
    assert package.avatar_path is not None
    assert package.avatar_path.name == "avatar.png"
    assert package.theme.colors.get("accent_gold") == "#f4a261"
    assert package.theme.colors.get("nav_bg") == "rgba(0, 0, 0, 0.25)"
    assert package.theme.styles.get("avatar_shape") == "freeform"


def test_persona_context_uses_package_prompt() -> None:
    """Prompt-facing code must read through the registry package layer."""
    context = PersonaContext("default", root=Path.cwd())

    assert "Long John Silver" in context.prompt_preamble()
    assert "active persona package" in context.response_contract()


def test_loose_txt_personas_are_not_selectable_or_created(tmp_path: Path) -> None:
    """Personas are packages now; loose .txt files should not become options."""
    personas = tmp_path / "config" / "personas"
    personas.mkdir(parents=True)
    (personas / "minimal.txt").write_text("You are Minimal.", encoding="utf-8")

    registry = PersonaRegistry(tmp_path)
    package_ids = {package.id for package in registry.list_packages()}

    assert "minimal" not in package_ids
    assert not (personas / "default.txt").exists()
    assert registry.load("minimal").id == "default"
    assert registry.prompt_text("minimal") == "You are a helpful assistant. Speak clearly and honestly."


def test_persona_registry_rejects_unsafe_avatar_and_theme(tmp_path: Path) -> None:
    """Persona JSON must not expose parent paths or arbitrary CSS values."""
    personas = tmp_path / "config" / "personas" / "bad"
    personas.mkdir(parents=True)
    (personas / "persona.md").write_text("You are Bad.", encoding="utf-8")
    (personas / "persona.json").write_text(json.dumps({"avatar": "../secret.png"}), encoding="utf-8")
    (personas / "theme.json").write_text(json.dumps({
        "colors": {
            "accent": "url(javascript:alert(1))",
            "nav_bg": "rgba(0, 0, 0, 0.25)",
        },
        "styles": {
            "avatar_shape": "../../circle",
        },
    }), encoding="utf-8")

    package = PersonaRegistry(tmp_path).load("bad")

    assert package.avatar_path is None
    assert "accent" not in package.theme.colors
    assert package.theme.colors["nav_bg"] == "rgba(0, 0, 0, 0.25)"
    assert "avatar_shape" not in package.theme.styles


def test_frontend_uses_persona_api_and_theme_variables() -> None:
    """The browser should bootstrap chrome and theme from the package API."""
    app_js = Path("src/web/static/js/app.js").read_text(encoding="utf-8")
    base_html = Path("src/web/templates/base.html").read_text(encoding="utf-8")
    settings_js = Path("src/web/static/js/components/settingsPanel.js").read_text(encoding="utf-8")
    css = Path("src/web/static/css/style.css").read_text(encoding="utf-8")

    assert "/api/personas/active" in app_js
    assert "_applyPersonaChrome" in app_js
    assert "nav_bg: '--nav-bg'" in app_js
    assert "persona-display-name" in base_html
    assert "Assistant Persona" in settings_js
    assert "/api/personas" in settings_js
    assert "--font-body" in css
    assert "--nav-bg" in css
