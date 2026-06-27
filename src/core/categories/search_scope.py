"""Category-neutral torrent search scope normalization.

Search scope is a phase hint shared between the agent tool surface and category
implementations.  Generic code must not reason about TV seasons, movie sets, or
book volumes from free text; it may only normalize stable scope labels and ask
the owning category how to interpret them.
"""

from __future__ import annotations


class SearchScopePolicy:
    """Normalize and classify category-neutral search phase hints."""

    DEFAULT = "default"
    BUNDLE_PREFERRED = "bundle_preferred"
    BUNDLE_ONLY = "bundle_only"
    INDIVIDUAL_UNITS_ONLY = "individual_units_only"

    CANONICAL = {DEFAULT, BUNDLE_PREFERRED, BUNDLE_ONLY, INDIVIDUAL_UNITS_ONLY}
    LEGACY_ALIASES = {
        "pack_preferred": BUNDLE_PREFERRED,
        "pack_only": BUNDLE_ONLY,
        "season_pack_preferred": BUNDLE_PREFERRED,
        "season_pack_only": BUNDLE_ONLY,
    }

    @classmethod
    def normalize(cls, value: object | None) -> str:
        """Return a canonical search scope, preserving safe legacy aliases."""
        text = str(value or cls.DEFAULT).strip().lower()
        if not text:
            return cls.DEFAULT
        text = cls.LEGACY_ALIASES.get(text, text)
        return text if text in cls.CANONICAL else cls.DEFAULT

    @classmethod
    def is_bundle_scope(cls, value: object | None) -> bool:
        """Return whether the canonical scope targets a category-owned bundle."""
        return cls.normalize(value) in {cls.BUNDLE_PREFERRED, cls.BUNDLE_ONLY}

    @classmethod
    def is_bundle_only(cls, value: object | None) -> bool:
        """Return whether the canonical scope forbids individual-unit fallback."""
        return cls.normalize(value) == cls.BUNDLE_ONLY

    @classmethod
    def is_bundle_preferred(cls, value: object | None) -> bool:
        """Return whether bundle search should be tried before unit fallback."""
        return cls.normalize(value) == cls.BUNDLE_PREFERRED

    @classmethod
    def is_individual_units_only(cls, value: object | None) -> bool:
        """Return whether the user/tool asked for per-unit results only."""
        return cls.normalize(value) == cls.INDIVIDUAL_UNITS_ONLY
