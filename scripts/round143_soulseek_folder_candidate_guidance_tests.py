#!/usr/bin/env python3
"""Round 143 Soulseek folder-candidate guidance tests."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.slskd_client import SlskdClient


def test_folder_candidate_scores_album_folder_and_keeps_sidecars() -> None:
    payload = {
        "responses": [{
            "username": "baitattack",
            "hasFreeUploadSlot": True,
            "queueLength": 0,
            "folder": "Persiana Jones - 1999 - Puerto Hurraco/P/Albums/music",
            "files": [
                {"filename": "01,Un Giorno Nuovo.mp3", "size": 2948155, "extension": "mp3"},
                {"filename": "02,Tremarella.mp3", "size": 2687495, "extension": "mp3"},
                {"filename": "03,Spacco Tutto.mp3", "size": 2744816, "extension": "mp3"},
                {"filename": "cover.jpg", "size": 106256, "extension": "jpg"},
                {"filename": "album.log", "size": 1000, "extension": "log"},
                {"filename": "suspicious.exe", "size": 2000, "extension": "exe"},
            ],
        }]
    }
    candidates, _stats = SlskdClient.normalize_search_payload_detailed(payload)
    public = SlskdClient._public_candidates(candidates, limit=10, query="Puerto Hurraco Persiana Jones")  # noqa: SLF001
    folder = public[0]
    assert folder["candidate_type"] == "folder"
    assert folder["folder_relevance"] == "strong"
    assert folder["folder_query_match_score"] >= 0.72
    assert folder["audio_file_count"] == 3
    assert folder["supporting_file_count"] == 2
    assert any(name.endswith("cover.jpg") for name in folder["filenames"])
    assert any(name.endswith("album.log") for name in folder["filenames"])
    assert not any(name.endswith("suspicious.exe") for name in folder["filenames"])
    assert "Folder candidate" in folder["note"]


def test_music_category_teaches_llm_soulseek_folder_evidence() -> None:
    text = (ROOT / "config/category-definitions/music.yaml").read_text(encoding="utf-8")
    assert "In Soulseek results" in text
    assert "folder name" in text
    assert "full filenames array" in text
    assert "single-track request" in text


def test_generic_prompt_keeps_folder_decision_category_owned() -> None:
    text = (ROOT / "src/ai/prompt_builder.py").read_text(encoding="utf-8")
    assert "Soulseek folder candidates" in text
    assert "active category guidance" in text
    assert "whole filenames array" in text


def main() -> None:
    test_folder_candidate_scores_album_folder_and_keeps_sidecars()
    test_music_category_teaches_llm_soulseek_folder_evidence()
    test_generic_prompt_keeps_folder_decision_category_owned()
    print("Round 143 Soulseek folder-candidate guidance tests passed")


if __name__ == "__main__":
    main()
