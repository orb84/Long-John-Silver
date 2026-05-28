"""slskd configuration planning helpers.

LJS treats Soulseek as a companion source through slskd.  These helpers build a
safe, user-reviewable share/download plan from LJS settings without requiring
users to edit slskd YAML by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.core.models import Settings, SoulseekShareMode

LEGACY_SLSKD_DOWNLOAD_DEFAULTS = {"./downloads/soulseek", "downloads/soulseek"}
LEGACY_SLSKD_INCOMPLETE_DEFAULTS = {"./downloads/soulseek-incomplete", "downloads/soulseek-incomplete"}


def _same_resolved_path(left: str | None, right: str | None) -> bool:
    """Best-effort path equality for config migration decisions."""
    if not left or not right:
        return False
    try:
        return Path(_resolved_path(left)) == Path(_resolved_path(right))
    except Exception:
        return False


def _path_within_root(path_text: str | None, root_text: str | None) -> bool:
    """Return true when path resolves inside root, with cross-platform fallback."""
    if not path_text or not root_text:
        return False
    try:
        path = Path(_resolved_path(path_text))
        root = Path(_resolved_path(root_text))
        path.relative_to(root)
        return True
    except Exception:
        try:
            path_s = str(Path(_resolved_path(path_text))).replace("\\", "/").rstrip("/")
            root_s = str(Path(_resolved_path(root_text))).replace("\\", "/").rstrip("/")
            return path_s == root_s or path_s.startswith(root_s + "/")
        except Exception:
            return False


def _managed_staging_downloads_dir(settings: Settings) -> str:
    """Return the obsolete Round 159 app-local slskd download path.

    This is kept only so existing bad configs can be detected and migrated back
    to the user-selected LJS download root. Managed slskd must not default here.
    """
    app_dir = Path(_resolved_path(getattr(settings.soulseek, "app_dir", "./data/slskd")))
    return str((app_dir / "downloads").resolve(strict=False))


def _managed_staging_incomplete_dir(settings: Settings) -> str:
    """Return the obsolete Round 159 app-local incomplete path for migration."""
    app_dir = Path(_resolved_path(getattr(settings.soulseek, "app_dir", "./data/slskd")))
    return str((app_dir / "incomplete").resolve(strict=False))


def _download_dir_root(settings: Settings) -> str:
    """Return the user-selected LJS download root used by every download backend."""
    return _resolved_path(getattr(settings, "download_dir", "./downloads"))


def _managed_directory_mode(settings: Settings) -> str:
    """Return managed slskd directory mode.

    Round 168 briefly introduced a ``slskd_default`` mode that moved slskd's
    APP_DIR to the user download root.  That was wrong: slskd always write-tests
    APP_DIR itself for logs/database/bootstrap state, so APP_DIR must remain on
    stable local LJS storage.  Managed LJS always uses explicit completed and
    incomplete folders under ``settings.download_dir``.  The persisted field is
    retained only so old settings can be migrated back to explicit mode.
    """
    return "explicit"


def _managed_runtime_app_dir(settings: Settings) -> str:
    """Return the managed slskd APP_DIR.

    APP_DIR is application state, not media payload storage.  Keep it on local
    LJS data and pass downloads/incomplete as explicit directories instead.
    """
    return _resolved_path(settings.soulseek.app_dir)


def _slskd_default_downloads_dir(settings: Settings) -> str:
    """Return the obsolete Round 168 APP_DIR/downloads path for migration tests."""
    return str((Path(_download_dir_root(settings)) / "downloads").resolve(strict=False))


def _slskd_default_incomplete_dir(settings: Settings) -> str:
    """Return the obsolete Round 168 APP_DIR/incomplete path for migration tests."""
    return str((Path(_download_dir_root(settings)) / "incomplete").resolve(strict=False))


def _is_legacy_app_local_path(settings: Settings, raw: str | None, *, incomplete: bool = False) -> bool:
    """Return true for paths produced by the rejected app-local slskd staging build."""
    if not raw:
        return False
    legacy = _managed_staging_incomplete_dir(settings) if incomplete else _managed_staging_downloads_dir(settings)
    return _same_resolved_path(raw, legacy)


@dataclass(frozen=True)
class SlskdSharePlan:
    """Computed slskd folder/share plan."""

    enabled: bool
    share_mode: str
    app_dir: str
    downloads_dir: str
    incomplete_dir: str
    shared_directories: list[str] = field(default_factory=list)
    excluded_directories: list[str] = field(default_factory=list)
    share_filters: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def slskd_share_entries(self) -> list[str]:
        """Return slskd-compatible share entries including exclusion prefixes."""
        entries = list(self.shared_directories)
        entries.extend(f"!{path}" for path in self.excluded_directories)
        return entries

    def as_public_dict(self) -> dict[str, Any]:
        """Return a user/LLM-safe plan dict."""
        return {
            "enabled": self.enabled,
            "share_mode": self.share_mode,
            "app_dir": self.app_dir,
            "downloads_dir": self.downloads_dir,
            "incomplete_dir": self.incomplete_dir,
            "shared_directories": self.shared_directories,
            "excluded_directories": self.excluded_directories,
            "share_filters": self.share_filters,
            "slskd_share_entries": self.slskd_share_entries,
            "warnings": self.warnings,
        }


def _resolved_path(value: str | None, *, base: Path | None = None) -> str:
    """Resolve a user/config path without requiring it to exist yet."""
    base_path = base or Path.cwd()
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        path = base_path / path
    return str(path.resolve(strict=False))


def _default_managed_downloads_dir(settings: Settings) -> str:
    """Return the managed slskd completed directory.

    Managed slskd is a download backend.  Completed Soulseek payloads must land
    in the user-selected LJS download folder, not in the project data directory
    and not in a newly invented child folder.  Earlier logs proved this basic
    flow can work; the broken layer was completed-file import/path planning.
    """
    return _download_dir_root(settings)


def _default_managed_incomplete_dir(settings: Settings) -> str:
    """Return the managed slskd incomplete directory inside the download root."""
    return str((Path(_download_dir_root(settings)) / ".slskd-incomplete").resolve(strict=False))


def _is_legacy_slskd_default_path(settings: Settings, raw: str | None, *, incomplete: bool = False) -> bool:
    """Return true for paths produced by the rejected Round 168 default APP_DIR mode."""
    if not raw:
        return False
    legacy = _slskd_default_incomplete_dir(settings) if incomplete else _slskd_default_downloads_dir(settings)
    return _same_resolved_path(raw, legacy)


def _managed_downloads_dir(settings: Settings) -> str:
    """Return the completed slskd download directory.

    In managed mode, slskd writes completed Soulseek files directly to
    ``settings.download_dir``.  Do not migrate to app-local staging,
    ``download_root/downloads``, or ``download_root/Soulseek``.  Those were
    failed experiments that obscured the real bug: importing completed files
    from slskd's remote names into the category library.
    """
    if getattr(settings.soulseek, "managed", True):
        return _default_managed_downloads_dir(settings)
    raw = str(getattr(settings.soulseek, "downloads_dir", "") or "").strip()
    if (
        not raw
        or raw.replace("\\", "/") in LEGACY_SLSKD_DOWNLOAD_DEFAULTS
        or _is_legacy_app_local_path(settings, raw)
        or _is_legacy_slskd_default_path(settings, raw)
    ):
        return _default_managed_downloads_dir(settings)
    return _resolved_path(raw)


def _managed_incomplete_dir(settings: Settings, downloads_dir: str) -> str:
    """Return slskd's incomplete-transfer directory inside the download root."""
    if getattr(settings.soulseek, "managed", True):
        return _default_managed_incomplete_dir(settings)
    raw = str(getattr(settings.soulseek, "incomplete_dir", "") or "").strip()
    normalized = raw.replace("\\", "/")
    if (
        not raw
        or normalized in LEGACY_SLSKD_INCOMPLETE_DEFAULTS
        or _is_legacy_app_local_path(settings, raw, incomplete=True)
        or _is_legacy_slskd_default_path(settings, raw, incomplete=True)
    ):
        return _default_managed_incomplete_dir(settings)
    return _resolved_path(raw)

def _is_unsafe_share_root(path_text: str) -> bool:
    """Return true for roots that slskd rejects or LJS should never share wholesale."""
    try:
        path = Path(path_text)
    except Exception:
        return True
    # On POSIX Path('/').parent == Path('/').  On Windows this also catches
    # drive roots such as C:\ when tests run on Windows.
    return path == path.parent


def _alias_entry(alias: str, path_text: str) -> str:
    """Return a privacy-preserving slskd alias entry."""
    cleaned = alias.replace("/", "-").replace("\\", "-").strip() or "LJS"
    return f"[{cleaned}]{path_text}"


def build_slskd_share_plan(settings: Settings) -> SlskdSharePlan:
    """Compute the slskd share plan from LJS settings.

    Full-library mode shares the configured LJS library root under a neutral
    alias.  Custom mode shares only explicitly selected folders.  Downloads and
    incomplete directories are automatically excluded when they live underneath
    a shared root so users do not accidentally re-share partial downloads.
    """
    cfg = settings.soulseek
    warnings: list[str] = []
    app_dir = _managed_runtime_app_dir(settings) if _managed_directory_mode(settings) == "slskd_default" else _resolved_path(cfg.app_dir)
    downloads_dir = _managed_downloads_dir(settings)
    incomplete_dir = _managed_incomplete_dir(settings, downloads_dir)

    shared: list[str] = []
    raw_share_mode = getattr(cfg.share_mode, "value", str(cfg.share_mode))
    if cfg.share_mode == SoulseekShareMode.FULL_LIBRARY:
        library_root = _resolved_path(settings.library_root)
        if _is_unsafe_share_root(library_root):
            warnings.append("Library root is a filesystem root; slskd sharing has been disabled until a narrower folder is selected.")
        else:
            shared.append(_alias_entry("LJS Library", library_root))
    elif cfg.share_mode == SoulseekShareMode.CUSTOM:
        seen: set[str] = set()
        for idx, folder in enumerate(cfg.share_directories, start=1):
            path_text = _resolved_path(folder)
            if _is_unsafe_share_root(path_text):
                warnings.append(f"Skipped unsafe share root: {path_text}")
                continue
            if path_text in seen:
                continue
            seen.add(path_text)
            shared.append(_alias_entry(f"LJS Share {idx}", path_text))
    elif cfg.share_mode == SoulseekShareMode.DISABLED:
        warnings.append("Soulseek sharing is disabled; slskd will not advertise any LJS library folders.")

    excluded: list[str] = []
    seen_excluded: set[str] = set()
    for folder in list(cfg.excluded_share_directories) + [downloads_dir, incomplete_dir]:
        path_text = _resolved_path(folder)
        if _is_unsafe_share_root(path_text):
            continue
        if path_text in seen_excluded:
            continue
        # Keep user exclusions even if they are outside current shares; slskd
        # tolerates exclusions and this avoids surprising users after changing
        # share mode later.
        seen_excluded.add(path_text)
        excluded.append(path_text)

    return SlskdSharePlan(
        enabled=bool(cfg.enabled),
        share_mode=raw_share_mode,
        app_dir=app_dir,
        downloads_dir=downloads_dir,
        incomplete_dir=incomplete_dir,
        shared_directories=shared,
        excluded_directories=excluded,
        share_filters=list(cfg.share_filters),
        warnings=warnings,
    )


def build_slskd_config_dict(settings: Settings, *, redact_secrets: bool = False) -> dict[str, Any]:
    """Return a slskd YAML-compatible configuration dict.

    The dict is intended for preview/export.  LJS does not enable slskd remote
    configuration by default because that can expose secrets through slskd's web
    API; users can still copy this into slskd.yml or a future managed runtime can
    write it before process start.
    """
    cfg = settings.soulseek
    plan = build_slskd_share_plan(settings)
    api_key = cfg.api_key or ""
    password = cfg.soulseek_password or ""
    data = {
        "remote_configuration": False,
        "remote_file_management": False,
        "soulseek": {
            "username": cfg.soulseek_username or "",
            "password": "********" if redact_secrets and password else password,
            "listen_ip_address": "0.0.0.0",
            "listen_port": 50300,
        },
        "web": {
            "port": 5030,
            "ip_address": "127.0.0.1",
            "url_base": cfg.url_base or "/",
            "logging": False,
            "https": {"disabled": True},
            "authentication": {
                "disabled": False,
                "username": cfg.web_username or "ljs",
                "password": "********" if redact_secrets and cfg.web_password else (cfg.web_password or ""),
                "jwt": {"key": "********" if redact_secrets and cfg.jwt_key else (cfg.jwt_key or ""), "ttl": 604800000},
                "api_keys": {
                    "ljs": {
                        "key": "********" if redact_secrets and api_key else api_key,
                        "cidr": "127.0.0.1/32,::1/128",
                    }
                }
            }
        },
        "shares": {
            "directories": plan.slskd_share_entries,
            "filters": plan.share_filters,
        },
    }
    if _managed_directory_mode(settings) != "slskd_default":
        data["directories"] = {
            "downloads": plan.downloads_dir,
            "incomplete": plan.incomplete_dir,
        }
    return data


def render_slskd_yaml(settings: Settings, *, redact_secrets: bool = True) -> str:
    """Render the planned slskd YAML configuration."""
    return yaml.safe_dump(build_slskd_config_dict(settings, redact_secrets=redact_secrets), sort_keys=False)
