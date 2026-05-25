"""
Media categories package for LJS.

Pluggable media type system. To add support for a new media type
(music, documentaries, anime, etc.), create a subclass of MediaCategory
and register it with the CategoryRegistry.

Utility collaborators (LanguageDetector, MediaVerifier, etc.) are available
from their respective submodules.
"""

from src.core.categories.base import MediaCategory, CategoryMedia
from src.core.categories.search_patterns import SearchPatterns
from src.core.categories.types import ParsedMedia, ScannedEpisode, ScannedFileObservation, ScannedItem
from src.core.categories.tv import TvShowCategory
from src.core.categories.movie import MovieCategory
from src.core.categories.general import GeneralCategory
from src.core.categories.registry import CategoryRegistry
from src.core.categories.language import LanguageDetector, LanguageSearchTagger
from src.core.categories.verifier import MediaVerifier
from src.core.categories.path_planner import CategoryPathPlanner
from src.core.categories.consolidator import LibraryConsolidator

__all__ = [
    "MediaCategory",
    "CategoryMedia",
    "ScannedItem",
    "ScannedFileObservation",
    "ScannedEpisode",  # backwards-compatible alias
    "ParsedMedia",
    "SearchPatterns",
    "TvShowCategory",
    "MovieCategory",
    "GeneralCategory",
    "CategoryRegistry",
    "LanguageDetector",
    "LanguageSearchTagger",
    "MediaVerifier",
    "CategoryPathPlanner",
    "LibraryConsolidator",
]
