"""Audio conversion collaborator for definition-backed audio categories.

The category owns the policy and safe roots; this module owns the FFmpeg command
construction/execution details.  It always preserves source files and only
returns sidecar paths generated inside category-approved roots.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.core.models import ActionReceipt
from src.core.security.command_policy import CommandPolicy, CommandPolicyError
from src.core.security.path_policy import SafePathResolver, SecurityPolicyError

if TYPE_CHECKING:
    from src.core.models import Settings


AUDIO_SOURCE_SUFFIXES = {".flac", ".alac", ".m4a", ".m4b", ".mp3", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".ape"}
LOSSLESS_AUDIO_SUFFIXES = {".flac", ".alac", ".wav", ".aiff", ".ape"}


class AudioConversionService:
    """Run or preview FFmpeg conversions for one category instance."""

    def __init__(self, category: Any, *, runtime_dependencies: dict[str, Any] | None = None) -> None:
        self.category = category
        self.runtime_dependencies = runtime_dependencies if isinstance(runtime_dependencies, dict) else {}

    async def execute_convert_audio_for_apple(self, arguments: dict[str, Any], context: Any) -> ActionReceipt:
        """Run or preview FFmpeg audio conversion for audio-capable categories."""
        settings = getattr(context, "settings", None)
        if settings is None:
            return self._workflow_error("convert_audio_for_apple", "Settings are unavailable; cannot validate safe paths.")
        source_arg = str(arguments.get("source_path") or "").strip()
        if not source_arg:
            return self._workflow_error("convert_audio_for_apple", "source_path is required.")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return self._workflow_error(
                "convert_audio_for_apple",
                "FFmpeg is not installed or not on PATH, so audio conversion cannot run.",
                data={"missing_dependency": "ffmpeg", "install_hint": self._ffmpeg_install_hint()},
            )
        try:
            resolver = SafePathResolver.for_category(self.category, settings)
            source = resolver.require(source_arg, purpose="audio_conversion:source", must_exist=True)
            if source.suffix.lower() not in AUDIO_SOURCE_SUFFIXES:
                return self._workflow_error("convert_audio_for_apple", f"Unsupported audio source extension: {source.suffix}")
            target = self._conversion_target(source, arguments, resolver)
        except SecurityPolicyError as exc:
            return self._workflow_error("convert_audio_for_apple", f"Unsafe conversion path: {exc}")

        profile = str(arguments.get("target_profile") or "apple_lossless_m4a")
        overwrite = bool(arguments.get("overwrite", False))
        command = self.ffmpeg_command(ffmpeg, source, target, profile, overwrite)
        if not arguments.get("confirmed"):
            return ActionReceipt(
                category_id=self.category.category_id,
                action_name="convert_audio_for_apple",
                status="needs_confirmation",
                user_message=(
                    f"Ready to create an Apple-friendly sidecar for {source.name}. "
                    "Confirm to run FFmpeg; the source file will be preserved."
                ),
                technical_message="Conversion preview only; confirmed=true required for execution.",
                data={"command": command, "source_path": str(source), "target_path": str(target), "target_profile": profile},
            )
        failure = await self.run_ffmpeg_conversion(
            source=source,
            target=target,
            target_profile=profile,
            overwrite=overwrite,
            action_name="convert_audio_for_apple",
        )
        if failure is not None:
            return failure
        return ActionReceipt(
            category_id=self.category.category_id,
            action_name="convert_audio_for_apple",
            status="success",
            user_message=f"Created Apple-friendly audio sidecar: {target.name}",
            data={"source_path": str(source), "target_path": str(target), "target_profile": profile},
        )

    async def after_library_file_imported(
        self,
        *,
        imported_path: Path,
        source_path: Path,
        item: Any,
        settings: "Settings",
        file_info: Any | None = None,
    ) -> list[Path]:
        """Create preference-driven audio sidecars after ready-time import."""
        decision = self.automatic_conversion_decision(imported_path, settings)
        if not decision:
            return []
        target_profile, suffix = decision
        resolver = SafePathResolver.for_category(self.category, settings)
        target = resolver.require(imported_path.with_suffix(suffix), purpose="audio_conversion:auto_target", must_exist=False)
        if target.exists():
            return [target]
        failure = await self.run_ffmpeg_conversion(
            source=imported_path,
            target=target,
            target_profile=target_profile,
            overwrite=False,
            action_name="auto_convert_audio",
        )
        if failure is not None:
            logger.warning("Automatic audio conversion skipped for {}: {}", imported_path, failure.user_message)
            return []
        logger.info("Created preference-driven audio sidecar {}", target)
        return [target]

    def automatic_conversion_decision(self, imported_path: Path, settings: "Settings") -> tuple[str, str] | None:
        """Return ``(target_profile, suffix)`` when preferences require a sidecar."""
        suffix = imported_path.suffix.lower()
        if suffix not in LOSSLESS_AUDIO_SUFFIXES:
            return None
        profile = self.category.category_download_profile(settings)
        if self.category.category_id == "music":
            preferred_lossless = str(profile.get("preferred_lossless_format") or "flac").lower()
            preferred_lossy = str(profile.get("preferred_lossy_format") or "").lower()
            auto = bool(profile.get("auto_convert_lossless_to_preferred", False))
            if preferred_lossless in {"alac", "alac_m4a", "apple_lossless_m4a", "m4a_alac"}:
                return ("apple_lossless_m4a", ".m4a")
            if auto and preferred_lossy in {"aac", "aac_m4a", "apple_aac_m4a", "m4a_aac"}:
                return ("apple_aac_m4a", ".m4a")
        if self.category.category_id == "audiobooks":
            preferred = str(profile.get("preferred_audio_format") or "m4b").lower()
            auto = bool(profile.get("auto_convert_lossless_to_preferred", True))
            if auto and preferred in {"m4b", "m4b_aac", "m4a", "aac_m4a"}:
                return ("apple_aac_m4a", ".m4b" if preferred.startswith("m4b") else ".m4a")
        return None

    def _conversion_target(self, source: Path, arguments: dict[str, Any], resolver: SafePathResolver) -> Path:
        """Resolve a safe conversion destination path."""
        requested = str(arguments.get("target_path") or "").strip()
        overwrite = bool(arguments.get("overwrite", False))
        if requested:
            return resolver.ensure_destination(requested, purpose="audio_conversion:target", allow_overwrite=overwrite)
        suffix = ".m4b" if self.category.category_id == "audiobooks" and source.suffix.lower() == ".m4b" else ".m4a"
        default_name = source.with_name(f"{source.stem}.apple{suffix}")
        return resolver.ensure_destination(default_name, purpose="audio_conversion:target", allow_overwrite=overwrite)

    async def run_ffmpeg_conversion(
        self,
        *,
        source: Path,
        target: Path,
        target_profile: str,
        overwrite: bool,
        action_name: str,
    ) -> ActionReceipt | None:
        """Run FFmpeg and return a failure receipt, or None on success."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return self._workflow_error(action_name, "FFmpeg is not installed or not on PATH.", data={"missing_dependency": "ffmpeg", "install_hint": self._ffmpeg_install_hint()})
        command = self.ffmpeg_command(ffmpeg, source, target, target_profile, overwrite)
        try:
            proc = await CommandPolicy().create_subprocess_exec(
                command,
                purpose="audio_conversion:ffmpeg",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
        except (OSError, CommandPolicyError) as exc:
            return self._workflow_error(action_name, f"Failed to start FFmpeg: {exc}", data={"command": command})
        if proc.returncode != 0:
            return self._workflow_error(
                action_name,
                "FFmpeg conversion failed.",
                data={"command": command, "stderr_tail": stderr.decode("utf-8", errors="replace")[-2000:]},
            )
        return None

    @staticmethod
    def ffmpeg_command(ffmpeg: str, source: Path, target: Path, profile: str, overwrite: bool) -> list[str]:
        """Build an FFmpeg argv list preserving metadata, chapters, and cover art."""
        codec_args = ["-c:a", "alac"] if profile == "apple_lossless_m4a" else ["-c:a", "aac", "-b:a", "256k"]
        return [
            ffmpeg,
            "-nostdin",
            "-y" if overwrite else "-n",
            "-i", str(source),
            "-map", "0:a:0",
            "-map", "0:v?",
            "-map_metadata", "0",
            "-map_chapters", "0",
            *codec_args,
            "-c:v", "copy",
            "-movflags", "+faststart",
            str(target),
        ]

    def _ffmpeg_install_hint(self) -> str:
        """Return the install hint declared for FFmpeg, when available."""
        ffmpeg = self.runtime_dependencies.get("ffmpeg") if isinstance(self.runtime_dependencies, dict) else None
        if isinstance(ffmpeg, dict):
            return str(ffmpeg.get("install_hint") or "Install FFmpeg with your platform package manager.")
        return "Install FFmpeg with your platform package manager."

    def _workflow_error(self, workflow_name: str, message: str, data: dict[str, Any] | None = None) -> ActionReceipt:
        """Return a failed workflow receipt."""
        return ActionReceipt(
            category_id=self.category.category_id,
            action_name=workflow_name,
            status="failed",
            user_message=message,
            technical_message=message,
            data=data or {},
        )
