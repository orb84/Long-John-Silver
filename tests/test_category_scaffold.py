"""Tests for safe category scaffold previews."""

import pytest
from pydantic import ValidationError

from src.core.categories.scaffold import CategoryScaffoldService
from src.core.models import CategoryProperty, CategorySpec


def test_category_scaffold_preview_renders_expected_files() -> None:
    """A validated spec renders reviewable category, prompt, and test files."""
    spec = CategorySpec(
        category_id="audiobook",
        class_name="AudiobookCategory",
        display_name="Audiobooks",
        description="Narrated book media managed as category items and chapter units.",
        default_folder="Audiobooks",
        capabilities=["metadata", "downloadable", "chapters"],
        metadata_providers=[],
        item_types=["audiobook", "chapter"],
        properties=[
            CategoryProperty(
                name="preferred_format",
                value_type="string",
                description="Preferred audiobook container or codec.",
                default_value="m4b",
            )
        ],
    )

    preview = CategoryScaffoldService().preview(spec)

    assert preview.category_id == "audiobook"
    assert "src/core/categories/custom/audiobook.py" in preview.files
    assert "src/core/categories/prompts/audiobook.md" in preview.files
    assert "config/category-templates/audiobook.yaml" in preview.files
    assert "tests/test_category_audiobook.py" in preview.files
    assert "class AudiobookCategory" in preview.files["src/core/categories/custom/audiobook.py"]
    assert any("library_path" in content for content in preview.files.values())
    assert "taste_profile_schema" in preview.files["src/core/categories/custom/audiobook.py"]


def test_category_spec_rejects_unsafe_identifiers() -> None:
    """Unsafe category IDs and class names fail before rendering files."""
    with pytest.raises(ValidationError):
        CategorySpec(
            category_id="../bad",
            class_name="Bad",
            display_name="Bad",
            description="Invalid scaffold.",
            default_folder="Bad",
        )


def test_category_scaffold_apply_requires_approval_and_protects_existing_files(tmp_path) -> None:
    """Applying generated category files requires confirmation and avoids accidental overwrite."""
    spec = CategorySpec(
        category_id="games",
        display_name="Games",
        description="Interactive games with platform and mechanics metadata.",
        taste_dimensions={"mechanics": 0.75, "platforms": 0.2},
    )
    service = CategoryScaffoldService()

    first = service.apply(spec, approved=False, root=tmp_path)
    assert first.status == "needs_confirmation"

    installed = service.apply(spec, approved=True, root=tmp_path)
    assert installed.status == "success"
    assert (tmp_path / "src/core/categories/custom/games.py").exists()
    assert (tmp_path / "config/category-templates/games.yaml").exists()

    second = service.apply(spec, approved=True, root=tmp_path)
    assert second.status == "needs_confirmation"
    assert "existing_files" in second.data



def test_category_scaffold_preserves_provider_research_and_discovery_sources() -> None:
    """Generated prompts/config keep researched provider leads reviewable."""
    spec = CategorySpec(
        category_id="video_games",
        display_name="Video Games",
        description="Interactive games with platform, developer, mechanics, and storefront metadata.",
        capabilities=["metadata", "downloadable"],
        metadata_providers=["rawg"],
        discovery_sources=[
            {
                "provider": "rawg",
                "purpose": "metadata_enrichment",
                "required": False,
                "setting_keys": ["rawg_api_key"],
                "taste_metadata_keys": ["genres", "developers", "platforms", "mechanics"],
            }
        ],
        provider_research=[
            {
                "provider": "rawg",
                "provider_domain": "rawg.io",
                "purpose": "metadata_enrichment",
                "requires_review": True,
            }
        ],
        taste_dimensions={"mechanics": 0.75, "developers": 0.55, "platforms": 0.2},
        download_profile={
            "acceptable_formats": ["installer", "archive"],
            "required_facets": ["title", "platform", "version"],
            "quality_facets": ["version", "language", "source"],
        },
        download_profile_research=[
            {
                "source_title": "Game release filename conventions",
                "source_domain": "example.org",
                "evidence_snippet": "Game downloads commonly identify platform, version, language and archive format.",
                "requires_review": True,
            }
        ],
    )

    preview = CategoryScaffoldService().preview(spec)
    prompt = preview.files["src/core/categories/prompts/video_games.md"]
    config = preview.files["config/category-templates/video_games.yaml"]

    assert "Provider research leads" in prompt
    assert "Download-profile research leads" in prompt
    assert "rawg.io" in prompt
    assert "Game downloads commonly identify platform" in prompt
    assert "discovery_sources:" in config
    assert "download_profile:" in config
    assert "rawg_api_key" in config
    assert preview.warnings == ["No library_path property was provided; the scaffold will add one."]
