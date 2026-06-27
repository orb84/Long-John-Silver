"""LLM prompt construction for torrent candidate selection."""

from __future__ import annotations

from typing import Optional

from src.core.models import NormalizedTorrentCandidate


class TorrentSelectionPromptBuilder:
    """Builds the LLM-facing prompt for torrent selection."""

    @staticmethod
    def build(
        item_display_name: str,
        unit_key: str,
        preferred_language: str,
        media_category: str,
        quality_context: str,
        quality_ref: str,
        candidates: list[NormalizedTorrentCandidate],
        preferred_resolution: Optional[str] = None,
        max_file_size_mb: Optional[int] = None,
        selection_guidance: Optional[str] = None,
    ) -> str:
        """Return the complete LLM prompt for choosing one torrent candidate."""
        candidate_lines = [f"[{i}] {n.llm_summary}" for i, n in enumerate(candidates)]
        prompt_parts = TorrentSelectionPromptBuilder.intro_parts(
            item_display_name, unit_key, media_category, selection_guidance
        )
        prompt_parts.extend(TorrentSelectionPromptBuilder.rejection_parts(media_category, preferred_language))
        TorrentSelectionPromptBuilder.append_quality_constraints(
            prompt_parts, preferred_resolution, max_file_size_mb
        )
        if quality_context:
            prompt_parts.extend(["", f"Library context: {quality_context}"])
        prompt_parts.extend(
            TorrentSelectionPromptBuilder.language_and_quality_parts(
                preferred_language, quality_ref
            )
        )
        prompt_text = " ".join(prompt_parts)
        prompt_text += "\n" + "\n".join(candidate_lines)
        prompt_text += (
            '\n\nRespond with ONLY a JSON object: {"index": <number>} '
            '(select the best candidate) or {"index": -1} '
            "(if no candidate is acceptable)."
        )
        return prompt_text

    @staticmethod
    def intro_parts(
        item_display_name: str,
        unit_key: str,
        media_category: str,
        selection_guidance: Optional[str],
    ) -> list[str]:
        """Return the opening task and category-owned guardrail prompt parts."""
        if not selection_guidance:
            cat_label = media_category or "requested media category"
            selection_guidance = (
                f"Content type: This is a {cat_label} download. Reject candidates "
                "that clearly belong to another category. Concrete file formats, "
                "unit names, and domain-specific reject rules should come from the "
                "owning category's torrent-selection guidance when available."
            )
        return [
            f"Task: Select the best torrent to download for {item_display_name} {unit_key or ''}.",
            "",
            selection_guidance,
            "",
            "Rejection criteria — you MUST reject candidates that:",
        ]

    @staticmethod
    def rejection_parts(media_category: str, preferred_language: str) -> list[str]:
        """Return generic rejection rules shared by deterministic and LLM selection.

        Category-specific rejection rules belong in ``selection_guidance`` from
        the owning category. This fallback stays deliberately broad so new
        categories do not inherit TV/movie assumptions.
        """
        cat_label = media_category or "requested category"
        parts = [
            f"  1. Clearly belong to a different category than {cat_label}",
            "  2. Contain only archives or samples when the category expects a usable payload",
            "  3. Are obviously malformed, spam, fake, or unrelated releases",
        ]
        if preferred_language:
            parts.extend([
                "  4. Have a clearly wrong language — if lang:X shows a single language ",
                f"     that does NOT match the preferred language '{preferred_language}', reject it.",
                "     Example: lang:X when preferred is Y → REJECT.",
                "  5. Have no magnet link — can't be downloaded",
            ])
        else:
            parts.extend([
                "  4. Have no magnet link — can't be downloaded",
                "  5. Do not invent a language requirement when the category declares language irrelevant.",
            ])
        return parts

    @staticmethod
    def append_quality_constraints(
        parts: list[str],
        preferred_resolution: Optional[str],
        max_file_size_mb: Optional[int],
    ) -> None:
        """Append optional resolution and size-budget constraints in place."""
        if preferred_resolution:
            parts.append(
                f"  6. Have a resolution HIGHER than the preferred resolution of '{preferred_resolution}' "
                f"(e.g., 4k/2160p when preferred is {preferred_resolution}) — reject unless this is clearly the only sane match."
            )
        if max_file_size_mb:
            parts.append(
                f"  7. Are obviously above the desired per-unit/file size budget of {max_file_size_mb} MB. "
                "Use judgment for packs and unclear sizes instead of blindly rejecting acronyms."
            )

    @staticmethod
    def language_and_quality_parts(preferred_language: str, quality_ref: str) -> list[str]:
        """Return candidate language rules and compact quality reference text."""
        parts = [""]
        if preferred_language:
            parts.extend([
                "Language rules:",
                f"  - Preferred language: {preferred_language}",
                "  - Multi-language or multi-format releases are acceptable when category guidance says they include the requested language/content form, but they are not automatically better than a clean preferred-language match",
                f"  - Releases explicitly including {preferred_language} plus other languages can be acceptable when category guidance treats that as useful evidence",
                f"  - Releases clearly in {preferred_language} are preferred when identity, coverage, quality, and size are otherwise comparable",
                "  - Single-language releases in a DIFFERENT language → REJECT",
                "  - No language tag: UNKNOWN risk; prefer confirmed language matches",
                "  - If ALL candidates have unknown language, pick the best quality one",
                "    but prefer higher seed counts (more likely to be legitimate)",
                "",
            ])
        else:
            parts.extend([
                "Language rules:",
                "  - This category did not provide a search language constraint.",
                "  - Do not penalize candidates merely because they lack language tags.",
                "  - Do not introduce language-specific or multi-audio preferences unless the user explicitly asked for language-specific content.",
                "",
            ])
        parts.extend([
            "Seeder / availability rules:",
            "  - Seeders are a first-class availability metric, not decoration.",
            "  - When two candidates are otherwise equivalent in unit coverage, language, resolution, codec, and size, choose the one with more seeders.",
            "  - Prefer a slightly lower theoretical quality release with many healthy seeders over an equivalent-looking low-seed duplicate that may stall.",
            "  - Do not call a candidate 'top-ranked' if a clearly equivalent candidate has materially more seeders.",
            "",
            "Quality guide:",
            quality_ref,
            "",
            "Candidates (index, summary):",
        ])
        return parts
