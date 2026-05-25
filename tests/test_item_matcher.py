"""Tests for ItemMatcher and boundary language detection."""

from src.utils.item_matcher import ItemMatcher
from src.utils.quality import extract_quality_tags


class TestItemMatcher:
    """Test suite for the fuzzy ItemMatcher utility."""

    def test_fuzzy_match_names(self):
        """Test fuzzy show name matching works as expected."""
        assert ItemMatcher.fuzzy_match_names("For All Mankind", "All Mankind") is True
        assert ItemMatcher.fuzzy_match_names("For All Mankind", "for all mankind") is True
        assert ItemMatcher.fuzzy_match_names("The Wire", "Wire") is True
        assert ItemMatcher.fuzzy_match_names("Breaking Bad", "Better Call Saul") is False
        assert ItemMatcher.fuzzy_match_names(
            "For All Mankind",
            "The.Pitt.S01.1080p.ITA-ENG.WEBRip.x265.AAC",
        ) is False
        assert ItemMatcher.fuzzy_match_names(
            "For All Mankind",
            "For.All.Mankind.S05E03.1080p.ATVP.WEB-DL.ITA.ENG.H265",
        ) is True

    def test_is_item_mentioned(self):
        """Test if a tracked show is detected inside prompt, goal, or step arguments."""
        class MockStep:
            def __init__(self, tool_name, arguments):
                self.tool_name = tool_name
                self.arguments = arguments

        steps = [
            MockStep("search_torrents", {"name": "All Mankind", "season": 5})
        ]

        # Case 1: Tracked show is in step arguments
        assert ItemMatcher.is_item_mentioned("For All Mankind", "missing episodes", "download some show", steps) is True

        # Case 2: Tracked show is in prompt
        assert ItemMatcher.is_item_mentioned("For All Mankind", "For All Mankind season 5", "", []) is True

        # Case 3: Tracked show is in goal
        assert ItemMatcher.is_item_mentioned("For All Mankind", "", "Retrieve For All Mankind S05", []) is True

        # Case 4: No match
        assert ItemMatcher.is_item_mentioned("For All Mankind", "The Wire", "download the wire", []) is False


class TestLanguageBoundaryDetection:
    """Test suite ensuring that strict word boundaries prevent false positive language matches."""

    def test_titan_is_not_italian(self):
        """Verify that the word 'Titan' does not trigger Italian language detection."""
        tags = extract_quality_tags("For All Mankind S05E07 The Sirens of Titan 2160p ATVP WEB-DL DDP5 1 Atmos D")
        assert "languages" in tags
        # Italian should NOT be detected
        assert "Italian" not in tags["languages"]

    def test_ita_boundary_matches(self):
        """Verify that legitimate Italian tags are correctly detected."""
        tags_ita = extract_quality_tags("For All Mankind S05E07 ITA 1080p WebRip")
        assert "Italian" in tags_ita["languages"]

        tags_italian = extract_quality_tags("For All Mankind S05E07 Italian 1080p WebRip")
        assert "Italian" in tags_italian["languages"]
