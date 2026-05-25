from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_generic_suggestion_approval_declares_no_tv_workflow_names() -> None:
    source = (project_root() / "src/web/action_handlers/suggestions.py").read_text()
    assert "download_specific_episode" not in source
    assert '"tv"' not in source
    assert "category workflow" in source.lower()


def test_scheduler_uses_category_schedule_hook_not_core_air_monitor() -> None:
    scheduler = (project_root() / "src/core/scheduler.py").read_text()
    assert "AirDateMonitor" not in scheduler
    assert "next_scheduled_unit" in scheduler
    assert not (project_root() / "src/core/air_date_monitor.py").exists()


def test_consolidator_delegates_target_paths_to_category() -> None:
    consolidator = (project_root() / "src/core/categories/consolidator.py").read_text()
    assert "consolidation_target_for_file" in consolidator
    assert "compute_target_path(" not in consolidator


def test_generic_path_planner_does_not_invent_category_aliases() -> None:
    planner = (project_root() / "src/core/categories/path_planner.py").read_text()
    assert "show_title" not in planner
    assert "movie_title" not in planner
    assert "series_title" not in planner
    assert "filename_stem" in planner


def test_base_setup_requirements_are_provider_neutral() -> None:
    contract = (project_root() / "src/core/categories/base_contract.py").read_text()
    base_requirements = contract.split("def provider_setup_requirements", 1)[0] + contract.split("def provider_setup_requirements", 1)[1].split("def setup_requirements", 1)[1].split("def declare_workflows", 1)[0]
    assert "TVMaze" not in base_requirements
    assert "TMDB" not in base_requirements
    assert "provider_setup_requirements" in contract


def test_tv_units_are_physical_files_not_episode_rows() -> None:
    tv_source = (project_root() / "src/core/categories/tv.py").read_text()
    assert 'unit_type": "file"' in tv_source or "unit_type': 'file'" in tv_source
    assert 'role": "episode_payload"' in tv_source or "role': 'episode_payload'" in tv_source
    assert 'unit_key = f"file:{file_identity}"' in tv_source
