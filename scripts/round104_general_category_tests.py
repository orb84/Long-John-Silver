"""Round 104 regressions for the built-in General Files category.

These tests are static/offline so they can run in minimal sandboxes. They protect
three contracts:
- General Files is a real registered category with its own prompt/config/setup;
- the assistant must explicitly opt into General through `category_id` rather
  than relying on classifier fallback;
- frontend/category-creation surfaces know how to onboard the new category
  without duplicating it through the scaffold flow.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_general_category_is_first_class_and_conservative() -> None:
    src = read("src/core/categories/general.py")
    prompt = read("src/core/categories/prompts/general.md")
    definition = read("config/category-definitions/general.yaml")
    config = read("config/category-config-templates/general.yaml")
    assert "class GeneralCategory(CategoryMedia)" in src
    assert 'category_id = "general"' in src
    assert 'display_name = "General Files"' in src
    assert 'prompt_file = "general.md"' in src
    assert "def get_properties" in src and 'name="library_path"' in src
    assert "def build_search_query" in src and "do not append global media language" in src.lower()
    assert "def validate_search_result_for_request" in src
    assert "_DANGEROUS_SUFFIXES" in src and "_DANGEROUS_TITLE_RE" in src
    assert "Richer installed categories win" in prompt
    assert 'category_id: "general"' in prompt
    assert "Do not silently reinterpret a failed TV/movie search" in prompt
    assert "category_id: general" in definition
    assert "Richer categories win" in definition
    assert "category_id: general" in config
    assert "library_path" not in config


def test_registry_registers_general_without_dynamic_duplicate() -> None:
    registry = read("src/core/categories/registry.py")
    init = read("src/core/categories/__init__.py")
    assert "from src.core.categories.general import GeneralCategory" in registry
    assert "self.register(GeneralCategory())" in registry
    assert '"general",' in registry
    assert "from src.core.categories.general import GeneralCategory" in init
    assert '"GeneralCategory"' in init


def test_search_tool_accepts_explicit_category_id() -> None:
    tool = read("src/ai/tools/scheduling.py")
    scheduler = read("src/core/scheduler.py")
    service = read("src/core/scheduler_services.py")
    assert '"category_id"' in tool
    assert "Pass 'general' only for exact miscellaneous file targets" in tool
    assert "category_id = str(arguments.get(\"category_id\")" in tool
    assert "category_id=category_id" in tool
    assert "category_id: str | None = None" in scheduler
    assert "category_id=category_id" in scheduler
    assert "explicit_category_id" in service
    assert "Unknown category" in service
    assert "requested_category = explicit_category_id or" in service


def test_frontend_onboards_categories_from_manifests() -> None:
    app = read("src/web/static/js/app.js")
    settings = read("src/web/static/js/components/settingsPanel.js")
    assert "_checkCategoryOnboarding" in app
    assert "/api/setup/requirements" in app
    assert "ljs_known_categories_v1" in app
    assert "New category available" in app
    assert "Category setup needed" in app
    assert "_categorySetupNotices" in settings
    assert "cat.id === 'general'" in settings
    assert "General Files category" in settings


def test_category_creation_skill_knows_general_is_builtin() -> None:
    guide = read("skills/category_creation_guide.md")
    tool = read("src/ai/tools/categories.py")
    assert "Built-in General Files Category" in guide
    assert "Do **not** scaffold another generic catch-all category" in guide
    assert "category_id: general" in guide
    assert "Setup UI is manifest-driven" in guide
    assert "builtin_category_available" in tool
    assert "_looks_like_builtin_general_request" in tool
    assert "Do not scaffold a duplicate catch-all category" in tool


def main() -> None:
    test_general_category_is_first_class_and_conservative()
    test_registry_registers_general_without_dynamic_duplicate()
    test_search_tool_accepts_explicit_category_id()
    test_frontend_onboards_categories_from_manifests()
    test_category_creation_skill_knows_general_is_builtin()
    print("Round 104 General Files category regression traces passed")


if __name__ == "__main__":
    main()
