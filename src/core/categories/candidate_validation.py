"""Generic candidate validation for definition-backed categories.

The validator owns search-result safety and category-boundary checks that are
shared by YAML-backed categories.  Keeping this logic outside
``DefinitionBackedCategory`` avoids turning that bootstrap class into a grab bag
of prompt, scanner, workflow, and torrent-ranking behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from loguru import logger

from src.core.categories.boundary_signatures import default_boundary_signature_index


DANGEROUS_FILE_SUFFIXES = {
    ".apk", ".app", ".bat", ".cmd", ".com", ".deb", ".dmg", ".exe", ".jar",
    ".msi", ".pkg", ".ps1", ".rpm", ".run", ".scr", ".sh", ".vbs",
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9]{3,}")


@dataclass(frozen=True)
class CandidateValidationResult:
    """Outcome of validating one torrent candidate against a category request."""

    accepted: bool
    reason: str = ""


class DefinitionCandidateValidator:
    """Validate generic torrent candidates using category-owned declarations."""

    def __init__(
        self,
        *,
        category_id: str,
        search_policy: dict[str, Any],
        string_list: Callable[[Any], list[str]],
    ) -> None:
        self.category_id = category_id
        self._policy = search_policy if isinstance(search_policy, dict) else {}
        self._string_list = string_list

    def validate(self, *, title: str, requested: str) -> CandidateValidationResult:
        """Return whether ``title`` is safe and relevant for ``requested``."""
        if not title.strip():
            return CandidateValidationResult(False, "empty title")
        if self.looks_dangerous(title):
            return CandidateValidationResult(False, "dangerous executable/crack-looking title")
        if not self._has_term_overlap(requested, title):
            return CandidateValidationResult(False, "candidate title does not overlap the requested item")
        if self._has_rejected_title_term(title):
            return CandidateValidationResult(False, "category-declared reject term")
        foreign = self._foreign_category_signature(title)
        if foreign:
            return CandidateValidationResult(False, f"foreign category signature: {foreign.category_id}")
        if self._request_needs_bundle_match(requested) and not self._candidate_has_bundle_term(title):
            return CandidateValidationResult(False, "bundle request without bundle-looking candidate title")
        return CandidateValidationResult(True)

    @staticmethod
    def looks_dangerous(title: str) -> bool:
        """Return whether a torrent title suggests an executable/crack payload."""
        lower = title.lower()
        return any(suffix in lower for suffix in DANGEROUS_FILE_SUFFIXES) or any(
            token in lower for token in ("keygen", "crack", "activator", "serial number")
        )

    def _has_rejected_title_term(self, title: str) -> bool:
        lower = f" {title.lower()} "
        reject_terms = (
            self._string_list(self._policy.get("reject_title_terms"))
            + self._string_list(self._policy.get("reject_terms"))
        )
        for term in reject_terms:
            term_l = str(term).strip().lower()
            if term_l and re.search(r"(?<![a-z0-9])" + re.escape(term_l) + r"(?![a-z0-9])", lower):
                return True
        return False

    def _foreign_category_signature(self, title: str) -> Any | None:
        """Return a strong foreign signature match, if one is detected."""
        try:
            return default_boundary_signature_index().strongest_foreign_match(
                active_category_id=self.category_id,
                title=title,
            )
        except Exception as exc:  # pragma: no cover - validation should degrade open
            logger.debug("Category boundary signature check failed for {}: {}", self.category_id, exc)
            return None

    def _request_needs_bundle_match(self, requested: str) -> bool:
        lower = requested.lower()
        trigger_terms = self._string_list(self._policy.get("bundle_request_terms"))
        if not trigger_terms:
            trigger_terms = ["discography", "complete", "catalogue", "catalog", "collection"]
        return any(term.lower() in lower for term in trigger_terms)

    def _candidate_has_bundle_term(self, title: str) -> bool:
        lower = title.lower()
        bundle_terms = self._string_list(self._policy.get("bundle_candidate_terms"))
        if not bundle_terms:
            bundle_terms = ["discography", "complete", "collection", "anthology", "box set"]
        return any(term.lower() in lower for term in bundle_terms)

    @staticmethod
    def _has_term_overlap(requested: str, title: str) -> bool:
        requested_tokens = {token.group(0).lower() for token in _TOKEN_RE.finditer(requested or "")}
        title_tokens = {token.group(0).lower() for token in _TOKEN_RE.finditer(title or "")}
        if not requested_tokens:
            return True
        return bool(requested_tokens & title_tokens)
