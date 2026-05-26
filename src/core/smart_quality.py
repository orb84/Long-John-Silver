"""
Smart quality inferrer for LJS.

Analyzes existing library files to infer the user's quality preferences
and maximum file sizes for new downloads. Feeds this context into the
AI assistant so it makes informed decisions.
"""

from loguru import logger
from src.core.models import (
    ScannedLibraryItem, QualityProfile, SizeLimitMode, SearchResult,
)


class SmartQualityInferrer:
    """Infers quality constraints from existing library files."""

    def infer_for_item(self, scanned: ScannedLibraryItem) -> QualityProfile:
        """Infer a quality profile from a scanned library item.

        Uses average file size, common codecs, and resolutions
        to produce a profile that matches the user's established patterns.
        """
        profile = QualityProfile()
        profile.size_limit_mode = SizeLimitMode.SMART

        if scanned.avg_file_size_mb > 0:
            profile.max_file_size_mb = int(scanned.avg_file_size_mb * 1.3)

        if scanned.resolutions:
            most_common = max(
                set(scanned.resolutions),
                key=scanned.resolutions.count,
            )
            profile.preferred_resolution = most_common

        if scanned.codecs:
            profile.preferred_codecs = list(scanned.codecs)[:3]

        if scanned.avg_bitrate_kbps:
            profile.max_bitrate_kbps = int(scanned.avg_bitrate_kbps * 1.2)

        logger.debug(
            f"Inferred quality for '{scanned.name}': "
            f"res={profile.preferred_resolution}, "
            f"max_size={profile.max_file_size_mb}MB, "
            f"codecs={profile.preferred_codecs}"
        )
        return profile

    async def get_average_library_item_size_mb(
        self,
        *,
        category_id: str,
        scan_result: object | None = None,
        settings: object | None = None,
        category: object | None = None,
    ) -> float:
        """Calculate average local file size for one registered category.

        Category-specific callers pass their own id and optional category
        instance.  This helper aggregates generic scan fields only; it does not
        instantiate or branch on concrete built-in categories.
        """
        sizes: list[float] = []
        if scan_result and hasattr(scan_result, 'items'):
            for item in scan_result.items:
                if getattr(item, 'category_id', '') != category_id:
                    continue
                if getattr(item, 'avg_file_size_mb', 0.0) > 0.0:
                    sizes.append(float(item.avg_file_size_mb))
                elif getattr(item, 'total_size_bytes', 0) > 0 and getattr(item, 'file_count', 0) > 0:
                    sizes.append((item.total_size_bytes / (1024 * 1024)) / item.file_count)
            if sizes:
                avg = sum(sizes) / len(sizes)
                logger.info(f"Calculated average {category_id} size from scan result: {avg:.1f} MB")
                return avg

        if settings and category is not None and hasattr(category, 'scan') and hasattr(category, 'get_root_path'):
            try:
                root_path = category.get_root_path(settings)
                scanned_items = await category.scan(root_path)
                sizes = [
                    (item.total_size_bytes / (1024 * 1024)) / item.file_count
                    for item in scanned_items or []
                    if getattr(item, 'file_count', 0) > 0
                ]
                if sizes:
                    avg = sum(sizes) / len(sizes)
                    logger.info(f"Calculated average {category_id} size from category crawl: {avg:.1f} MB")
                    return avg
            except Exception as e:
                logger.warning(f"Backup {category_id} directory crawl failed: {e}")

        return 0.0

    def should_accept_result(
        self, result: SearchResult, profile: QualityProfile,
    ) -> tuple[bool, str]:
        """Soft-check if a search result should reach LLM/category ranking.

        Size alone is not a reliable hard reject: many useful payloads are
        inside large bundles/collections/packs, and high-quality movies or game
        releases can legitimately be very large.  This helper therefore only
        blocks clearly non-playable/malicious payloads already tagged elsewhere;
        semantic size judgment belongs to the LLM plus category bundle hooks.
        """
        return True, "Passes to LLM/category evaluation; useful payload size may differ from total torrent size"

    def build_quality_context(
        self,
        item_name: str,
        profile: QualityProfile,
        scanned: ScannedLibraryItem | None = None,
    ) -> str:
        """Build a text summary of quality constraints for the AI prompt.

        This gives the LLM the context it needs to make smart decisions
        about which torrent to select. Includes bundle/pack guidance
        and release type quality tiers from TorrentKnowledge.
        """
        lines = [f"Quality constraints for '{item_name}':"]

        if profile.size_limit_mode == SizeLimitMode.SMART and scanned:
            lines.append(f"  User's existing files average {scanned.avg_file_size_mb:.0f}MB per local file/unit.")
            lines.append(f"  Smart max file size: {profile.max_file_size_mb}MB (1.3x average).")
            if scanned.resolutions:
                lines.append(f"  Existing resolutions: {', '.join(scanned.resolutions)}.")
            if scanned.codecs:
                lines.append(f"  Existing codecs: {', '.join(scanned.codecs)}.")
        elif profile.size_limit_mode == SizeLimitMode.BITRATE and profile.max_bitrate_kbps:
            lines.append(f"  Maximum bitrate: {profile.max_bitrate_kbps} kbps.")
        elif profile.size_limit_mode == SizeLimitMode.FILE_SIZE and profile.max_file_size_mb:
            lines.append(f"  Maximum file size: {profile.max_file_size_mb} MB.")

        if profile.preferred_resolution:
            lines.append(f"  Preferred resolution: {profile.preferred_resolution}.")
        if profile.preferred_codecs:
            lines.append(f"  Preferred codecs: {', '.join(profile.preferred_codecs)}.")
        if profile.prefer_hdr:
            lines.append(f"  Prefers HDR content.")

        # Bundle guidance: the LLM should consider category-owned packs when
        # available and should understand that total size != useful payload size.
        lines.append(
            "  BUNDLES/PACKS: A torrent may contain a full season, collection, "
            "game bundle, book anthology, extras, or other grouped payload. "
            "Do not reject it only because the total torrent is large. Evaluate "
            "whether the requested item/unit can be identified and selectively "
            "downloaded, and reason about useful per-file/per-unit size instead."
        )

        # The comprehensive torrent quality guide is injected into the prompt
        # separately via get_quality_guide() — no need to duplicate release type
        # knowledge here
        lines.append(
            "  YOU ARE THE QUALITY EVALUATOR: The system provides hints (tags, "
            "red flags, bundle hints) but you make the final call. "
            "Use the torrent quality reference guide for terminology you don't recognize."
        )

        return "\n".join(lines)
