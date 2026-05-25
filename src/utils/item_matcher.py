"""
Category item matcher utility for LJS.

Provides fuzzy name matching and intersection rules to map partial user text to
tracked category item keys without depending on TV/show terminology.
"""

import re
from typing import Any


class ItemMatcher:
    """Helper class for matching fuzzy media/category item names to tracked keys."""

    @staticmethod
    def is_item_mentioned(
        tracked_key: str,
        prompt: str,
        goal: str,
        steps: list[Any],
    ) -> bool:
        """Return whether a tracked item is mentioned in prompt, goal, or plan steps."""
        key_lower = tracked_key.lower().strip()
        prompt_lower = prompt.lower().strip()
        goal_lower = goal.lower().strip()

        if key_lower in prompt_lower or key_lower in goal_lower:
            return True
        if ItemMatcher.fuzzy_match_names(tracked_key, prompt):
            return True
        if ItemMatcher.fuzzy_match_names(tracked_key, goal):
            return True

        key_words = ItemMatcher.get_clean_words(key_lower)
        if not key_words:
            return False

        for step in steps:
            args = getattr(step, "arguments", {})
            if isinstance(args, dict):
                for arg_key in ("name", "title", "item_id", "item_name", "query"):
                    val = args.get(arg_key)
                    if val and isinstance(val, str):
                        val_lower = val.lower().strip()
                        if key_lower in val_lower:
                            return True
                        if ItemMatcher.fuzzy_match_names(tracked_key, val):
                            return True
        return False

    @staticmethod
    def get_clean_words(text: str) -> set[str]:
        """Split text into content words suitable for fuzzy matching."""
        ignore_words = {
            'the', 'a', 'an', 'for', 'show', 'series', 'season', 'episode', 'movie', 'film', 'item', 'download',
            'missing', 'remaining', 'you', 'your', 'can', 'please', 'thanks', 'me', 'my', 'we', 'us', 'our',
            'they', 'them', 'their', 'he', 'him', 'his', 'she', 'her', 'it', 'its', 'who', 'whom', 'whose',
            'which', 'what', 'this', 'that', 'these', 'those', 'and', 'but', 'or', 'nor', 'yet', 'so', 'at',
            'by', 'in', 'of', 'on', 'to', 'with', 'from', 'about', 'into', 'through', 'over', 'under', 'above',
            'below', 'is', 'am', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do',
            'does', 'did', 'shall', 'will', 'should', 'would', 'may', 'might', 'must', 'could', 'get', 'got',
            'make', 'made', 'go', 'went', 'take', 'took', 'come', 'came', 'give', 'gave', 'find', 'found',
            'think', 'thought', 'see', 'saw', 'know', 'knew', 'want', 'wanted', 'use', 'used', 'now', 'then',
            'here', 'there', 'when', 'where', 'why', 'how', 'all', 'any', 'both', 'each', 'few', 'more',
            'most', 'other', 'some', 'such', 'no', 'not', 'only', 'own', 'same', 'than', 'too', 'very',
            'just', 'yes', 'ok', 'okay', 'thank',
            # Torrent/release tokens are deliberately ignored so a release name
            # like "1080p WEB-DL ITA ENG H265" cannot map to an unrelated
            # tracked item just because both contain generic quality words.
            '1080p', '720p', '2160p', '480p', '4k', 'uhd', 'hdr', 'hdr10',
            'web', 'webdl', 'webrip', 'web-dl', 'dlmux', 'bluray', 'bdrip',
            'hdtv', 'xvid', 'divx', 'h264', 'h265', 'x264', 'x265', 'hevc',
            'avc', 'aac', 'ac3', 'ddp', 'dd5', 'atvp', 'nf', 'amzn', 'dsnp',
            'ita', 'italian', 'eng', 'english', 'multi', 'sub', 'subs',
            'proper', 'repack', 'remux', 'internal', 'complete', 'pack',
            'rarbg', 'eztv', 'mephisto', 'mem', 'g66', 'pir8', 'v3sp4ev3r'
        }
        words = re.split(r'\W+', text.lower())
        return {w for w in words if w and w not in ignore_words and len(w) > 2}

    @staticmethod
    def fuzzy_match_names(name1: str, name2: str) -> bool:
        """Return whether two item names are plausible fuzzy matches."""
        n1 = name1.lower().strip()
        n2 = name2.lower().strip()
        if not n1 or not n2:
            return False
        if n1 == n2:
            return True

        w1 = ItemMatcher.get_clean_words(n1)
        w2 = ItemMatcher.get_clean_words(n2)
        if not w1 or not w2:
            return False

        # Substring matches are accepted only when the shorter side still has
        # enough content after stop-word cleanup. This keeps "The Wire" ↔
        # "Wire" working while rejecting accidental matches on release junk.
        if n1 in n2 or n2 in n1:
            shorter = w1 if len(n1) <= len(n2) else w2
            longer = w2 if len(n1) <= len(n2) else w1
            if len(shorter) >= 2:
                return True
            if len(shorter) == 1:
                token = next(iter(shorter))
                return len(token) >= 4 and token in longer and len(longer) <= 3

        common = w1.intersection(w2)
        if not common:
            return False

        # Require strong overlap, not merely one shared token. This prevents
        # selected torrent release names from being remapped to unrelated
        # library items because both contain a common word.
        overlap = len(common) / max(1, min(len(w1), len(w2)))
        if len(common) >= 2 and overlap >= 0.6:
            return True

        if len(w1) == 1 or len(w2) == 1:
            token = next(iter(common)) if len(common) == 1 else ''
            return bool(token and len(token) >= 4 and max(len(w1), len(w2)) <= 3)

        return False
