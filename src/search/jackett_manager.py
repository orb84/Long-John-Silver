"""
Jackett manager for LJS.

Auto-downloads, installs, starts, and configures a Jackett instance
as a local torrent meta-search engine. Jackett aggregates multiple
torrent indexers and exposes a unified API — bypassing Cloudflare
and ISP blocks that break direct Playwright scraping.

Cross-platform: Linux (x64, arm64), macOS (x64, arm64), Windows (x64).
"""

import asyncio
import json
import os
import platform
import tempfile
import socket
import shutil
import secrets
import hashlib
import re
import urllib.request
import urllib.error
import urllib.parse
import zipfile
from loguru import logger
from pathlib import Path
from typing import Optional

from src.core.security.command_policy import CommandPolicy, CommandPolicyError
from src.core.security.path_policy import SafePathResolver
from src.utils.archive_safety import safe_extract_tar, safe_extract_zip


# Pinned fallback version if GitHub API is unreachable
JACKETT_FALLBACK_VERSION = "0.24.1827"
JACKETT_RELEASE_BASE = "https://github.com/Jackett/Jackett/releases/download"

# Platform-specific archive names from the Jackett releases page
_JACKETT_ASSETS = {
    "Linux": {
        "x86_64": "Jackett.Binaries.LinuxAMDx64.tar.gz",
    },
    "Darwin": {
        "x86_64": "Jackett.Binaries.macOS.tar.gz",
        "arm64": "Jackett.Binaries.macOSARM64.tar.gz",
    },
    "Windows": {
        "x86_64": "Jackett.Binaries.Windows.zip",
    },
}

# Where Jackett lives on disk (resolved relative to project root at import time)
JACKETT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "jackett"
JACKETT_STATE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "jackett_state"
JACKETT_DEFAULT_PORT = 9117  # Jackett default port
JACKETT_PORT = JACKETT_DEFAULT_PORT  # backwards-compatible module constant


class JackettManager:
    """Manages a local Jackett instance: download, install, start, configure.

    Usage from the setup wizard or main.py:
        mgr = JackettManager()
        await mgr.ensure_installed()
        await mgr.start()
        if not mgr.is_running:
            # handle failure
        settings.jackett_url = f"http://127.0.0.1:{JACKETT_PORT}"
        settings.jackett_api_key = mgr.api_key
    """

    def __init__(self):
        self._process: Optional[asyncio.subprocess.Process] = None
        self._installed = False
        self._running = False
        self._api_key: Optional[str] = None
        self._port: int = JACKETT_DEFAULT_PORT
        self._managed_state_dir = JACKETT_STATE_DIR
        self._state_mode: str | None = None
        self._state_reason: str = "not selected yet"
        self._active_config_dirs: list[Path] = []
        self._attached_existing = False
        self._api_key_validated = False
        self._runtime_config_dir: Optional[Path] = None
        self._runtime_config_dir_bootstrap_safe = False


    # ── public properties ──────────────────────────────────────────

    @property
    def is_installed(self) -> bool:
        """Return whether JackettManager satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        return self._installed or self._detect_existing()

    @property
    def is_running(self) -> bool:
        """Return whether JackettManager satisfies this condition.

        Use this method as a read-only capability check.  Avoid side effects
        so callers can safely use it in routing, health checks, and tests.
        """
        if not self._running:
            return False
        if self._attached_existing:
            return True
        return self._process is not None and self._process.returncode is None

    @property
    def api_key(self) -> Optional[str]:
        """Return the requested api key value.

        This public accessor should normalize missing or optional data at the
        boundary and avoid leaking storage/provider internals to callers.
        """
        if not self._api_key:
            self._api_key = self._read_api_key()
        return self._api_key

    @property
    def url(self) -> str:
        """Return the requested url value.

        This public accessor should normalize missing or optional data at the
        boundary and avoid leaking storage/provider internals to callers.
        """
        return f"http://127.0.0.1:{self._port}"

    # ── asset selection ────────────────────────────────────────────

    @staticmethod
    def platform_key() -> tuple[str, str] | None:
        """Return (os_key, arch) for the current machine or None if unsupported."""
        system = platform.system()  # "Darwin", "Linux", "Windows"
        machine = platform.machine().lower()

        # Normalise arch
        if machine in ("x86_64", "amd64"):
            arch = "x86_64"
        elif machine in ("arm64", "aarch64"):
            arch = "arm64"
        else:
            return None

        assets = _JACKETT_ASSETS.get(system, {})
        if arch not in assets:
            # fallback: macOS universal / Linux x86_64
            if system == "Darwin":
                arch = "x86_64"  # Rosetta 2 handles this
            else:
                return None

        return system, arch

    _get_platform_key = platform_key

    @staticmethod
    def _asset_name() -> str | None:
        pk = JackettManager.platform_key()
        if not pk:
            return None
        return _JACKETT_ASSETS[pk[0]].get(pk[1])

    # ── download & install ─────────────────────────────────────────

    @staticmethod
    async def _resolve_version() -> str:
        """Fetch the latest Jackett release tag from GitHub API."""
        try:
            loop = asyncio.get_running_loop()
            import urllib.request, json
            req = urllib.request.Request(
                "https://api.github.com/repos/Jackett/Jackett/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            resp = await loop.run_in_executor(None, urllib.request.urlopen, req, None, 10)
            data = json.loads(resp.read())
            return data["tag_name"].lstrip("v")
        except Exception:
            return JACKETT_FALLBACK_VERSION

    async def ensure_installed(self, force: bool = False) -> bool:
        """Download and extract Jackett if not already present.

        Args:
            force: Re-download even if already installed.

        Returns:
            True if Jackett is ready to use.
        """
        if not force and self._detect_existing():
            self._installed = True
            return True

        asset = self._asset_name()
        if not asset:
            logger.error(f"Jackett: unsupported platform {platform.system()}-{platform.machine()}")
            return False

        version = await self._resolve_version()
        url = f"{JACKETT_RELEASE_BASE}/v{version}/{asset}"
        logger.info(f"Jackett v{version}: downloading {asset} ...")

        JACKETT_DIR.mkdir(parents=True, exist_ok=True)

        # Download to temp file (async blocking call → to_thread)
        tmp = Path(tempfile.mktemp(suffix="_" + asset))
        try:
            await asyncio.to_thread(self._download_file, url, str(tmp))
            logger.info(f"Jackett: downloaded {os.path.getsize(str(tmp)) / 1024 / 1024:.1f} MB")
        except Exception as e:
            logger.error(f"Jackett download failed: {e}")
            return False

        # Extract
        try:
            if asset.endswith(".zip"):
                await asyncio.to_thread(self._extract_zip, tmp, JACKETT_DIR)
            elif asset.endswith(".tar.gz"):
                await asyncio.to_thread(self._extract_tarball, tmp, JACKETT_DIR)
            else:
                logger.error(f"Jackett: unknown archive format: {asset}")
                return False
        except Exception as e:
            logger.error(f"Jackett extract failed: {e}")
            return False
        finally:
            try:
                SafePathResolver.for_application(extra_roots=[tmp.parent]).safe_unlink(
                    tmp, purpose="jackett.cleanup_tmp", move_to_trash=False,
                )
            except Exception:
                pass

        logger.info("Jackett: installed successfully")
        return True

    def _detect_existing(self) -> bool:
        """Check if Jackett binary already exists in the expected dir."""
        exe = self._executable_path()
        return exe is not None and exe.exists()

    def _executable_path(self) -> Path | None:
        """Return path to the Jackett binary, or None."""
        if not JACKETT_DIR.exists():
            return None
        target = "Jackett.Console.exe" if platform.system() == "Windows" else "jackett"
        for found in JACKETT_DIR.rglob(target):
            if found.is_file():
                # Ensure executable on Unix
                if platform.system() != "Windows":
                    found.chmod(found.stat().st_mode | 0o111)
                return found
        return None

    @staticmethod
    def _download_file(url: str, dest: str) -> None:
        """Synchronous download with progress."""
        # Use urllib to avoid requiring httpx (which we have, but this is simpler for to_thread)
        urllib.request.urlretrieve(url, dest)

    @staticmethod
    def _extract_zip(archive: Path, dest: Path) -> None:
        safe_extract_zip(archive, dest)

    @staticmethod
    def _extract_tarball(archive: Path, dest: Path) -> None:
        safe_extract_tar(archive, dest)

    # ── start / stop ───────────────────────────────────────────────

    async def start(self) -> bool:
        """Launch or attach to Jackett. Idempotent — no-op if already running."""
        if self.is_running:
            return True

        exe = self._executable_path()
        if not exe:
            logger.error("Jackett: executable not found")
            return False

        self._select_state_mode()

        # Round 188 compatibility matters on upgrades: if the user already has
        # a Jackett state with configured public/private indexers, do not start
        # a fresh isolated runtime with zero indexers.  Attach to the existing
        # localhost Jackett when it is already listening, otherwise start Jackett
        # with the normal platform HOME/XDG state that owns those indexers.
        if self._state_mode == "legacy":
            self._port = JACKETT_DEFAULT_PORT
            if self._port_in_use(self._port) and await self._probe_existing_server(self._port):
                self._running = True
                self._attached_existing = True
                self._api_key = self._read_api_key()
                self._log_active_config_diagnostics("attached-legacy")
                logger.info(
                    "Jackett: attached to existing legacy state on {} "
                    "(state_reason={}, api_key_available={})",
                    self.url,
                    self._state_reason,
                    bool(self._api_key),
                )
                return True
        else:
            self._prepare_managed_state_home()
            self._port = self._choose_port()
            self._ensure_managed_startup_config()
            self._repair_managed_admin_auth_config()
            self._log_active_config_diagnostics("pre-start")

        try:
            cwd = str(exe.parent)
            env = os.environ.copy()
            if self._state_mode == "managed":
                env = self._managed_environment(env, exe.parent.resolve())
            env["JACKETT_PORT"] = str(self._port)
            env["JACKETT_HOME"] = str(exe.parent.resolve())
            exe_args = [str(exe.resolve())]
            if platform.system() != "Windows":
                exe_args.append("--NoUpdates")
                exe_args.extend(["--Port", str(self._port)])
            else:
                exe_args.extend(["--Port", str(self._port)])
                if self._state_mode == "managed":
                    exe_args.extend(["--DataFolder", str(self._config_dir().resolve())])

            self._process = await CommandPolicy().create_subprocess_exec(
                exe_args,
                purpose="jackett.start",
                approved=True,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            # Wait for the server to respond (Jackett needs time to init DB + indexers)
            for i in range(60):
                await asyncio.sleep(1)
                if self._process.returncode is not None:
                    logger.error(f"Jackett: process exited immediately (rc={self._process.returncode})")
                    return False
                try:
                    reader = await asyncio.to_thread(
                        urllib.request.urlopen,
                        f"{self.url}/",
                        timeout=3,
                    )
                    # Any HTTP response means the server is up
                    self._running = True
                    self._attached_existing = False
                    self._api_key = self._read_api_key()
                    await self._adopt_valid_runtime_api_key()
                    self._log_active_config_diagnostics("post-start")
                    logger.info("Jackett: running on {} (state_mode={}, state_reason={})", self.url, self._state_mode, self._state_reason)
                    return True
                except urllib.error.HTTPError as e:
                    # Server is up — just returned a non-2xx status
                    self._running = True
                    self._attached_existing = False
                    self._api_key = self._read_api_key()
                    await self._adopt_valid_runtime_api_key()
                    self._log_active_config_diagnostics("post-start")
                    logger.info("Jackett: running on {} (HTTP {}, state_mode={}, state_reason={})", self.url, e.code, self._state_mode, self._state_reason)
                    return True
                except Exception:
                    continue

            logger.error("Jackett: timed out waiting for startup")
            return False

        except CommandPolicyError as e:
            logger.error(f"Jackett: start blocked by security policy: {e}")
            return False
        except Exception as e:
            logger.error(f"Jackett: failed to start: {e}")
            return False

    async def stop(self) -> None:
        """Gracefully stop the Jackett process."""
        if self._attached_existing:
            self._running = False
            self._attached_existing = False
            self._process = None
            return
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._process.kill()
            except Exception:
                pass
        self._running = False
        self._attached_existing = False
        self._process = None

    async def _probe_existing_server(self, port: int) -> bool:
        try:
            await asyncio.to_thread(urllib.request.urlopen, f"http://127.0.0.1:{int(port)}/", timeout=3)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            return False

    # ── configuration ──────────────────────────────────────────────

    def _managed_home_dir(self) -> Path:
        return self._managed_state_dir / "home"

    def _config_dir(self) -> Path:
        """Return the primary LJS-managed Jackett config directory.

        Jackett's Unix/macOS archive builds are XDG-aware and the current
        upstream docs list macOS config under both ``~/.config/Jackett`` and
        ``~/Library/Application Support/Jackett``.  LJS sets ``XDG_CONFIG_HOME``
        for the managed process, so the primary managed path must be the XDG
        path.  Earlier rounds repaired only the Library path on macOS, which
        left the active runtime login-gated.
        """
        system = platform.system()
        if system == "Windows":
            return self._managed_state_dir / "programdata" / "Jackett"
        return self._managed_state_dir / "config" / "Jackett"

    def _managed_config_dirs(self) -> list[Path]:
        """Return all managed Jackett config dirs that this platform may use."""
        roots: list[Path] = [
            self._config_dir(),
            self._managed_state_dir / "config" / "jackett",
            self._managed_home_dir() / ".config" / "Jackett",
            self._managed_home_dir() / ".config" / "jackett",
        ]
        if platform.system() == "Darwin":
            roots.append(self._managed_home_dir() / "Library/Application Support/Jackett")
        if platform.system() == "Windows":
            roots.extend([
                self._managed_state_dir / "appdata" / "Jackett",
                self._managed_state_dir / "localappdata" / "Jackett",
            ])
        seen: set[str] = set()
        out: list[Path] = []
        for root in roots:
            key = self._path_identity_key(root)
            if key in seen:
                continue
            seen.add(key)
            out.append(root)
        return out

    def _prepare_managed_state_home(self) -> None:
        for path in {self._managed_state_dir, self._managed_home_dir(), *self._managed_config_dirs()}:
            path.mkdir(parents=True, exist_ok=True)

    def _managed_environment(self, env: dict[str, str], exe_parent: Path) -> dict[str, str]:
        self._prepare_managed_state_home()
        home = self._managed_home_dir().resolve()
        config_root = (self._managed_state_dir / "config").resolve()
        data_root = (self._managed_state_dir / "data").resolve()
        cache_root = (self._managed_state_dir / "cache").resolve()
        for root in (config_root, data_root, cache_root):
            root.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(config_root)
        env["XDG_DATA_HOME"] = str(data_root)
        env["XDG_CACHE_HOME"] = str(cache_root)
        env["JACKETT_HOME"] = str(exe_parent)
        if platform.system() == "Windows":
            programdata = (self._managed_state_dir / "programdata").resolve()
            appdata = (self._managed_state_dir / "appdata").resolve()
            localappdata = (self._managed_state_dir / "localappdata").resolve()
            for root in (programdata, appdata, localappdata):
                root.mkdir(parents=True, exist_ok=True)
            env["ProgramData"] = str(programdata)
            env["APPDATA"] = str(appdata)
            env["LOCALAPPDATA"] = str(localappdata)
        return env

    def _choose_port(self) -> int:
        preferred = self._port or JACKETT_DEFAULT_PORT
        for port in [preferred, *range(JACKETT_DEFAULT_PORT + 1, JACKETT_DEFAULT_PORT + 20)]:
            if not self._port_in_use(port):
                if port != preferred:
                    logger.warning(f"Jackett: port {preferred} is already in use; starting isolated managed Jackett on {port}.")
                return int(port)
        logger.warning(f"Jackett: all preferred ports are busy; falling back to {preferred} and relying on Jackett startup diagnostics.")
        return int(preferred)

    @staticmethod
    def _port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex(("127.0.0.1", int(port))) == 0

    @staticmethod
    def _default_config_dir() -> Path | None:
        """Return Jackett's primary default config directory for this platform."""
        dirs = JackettManager._default_config_dirs()
        return dirs[0] if dirs else None

    @staticmethod
    def _default_config_dirs() -> list[Path]:
        """Return platform default Jackett config directories.

        Jackett documents XDG_CONFIG_HOME for Linux service installs and lists
        both ~/.config/Jackett and ~/Library/Application Support/Jackett as
        possible macOS log/config locations.  Keep both so upgrades preserve
        whichever path the previous runtime actually used.
        """
        home = Path.home()
        system = platform.system()
        if system == "Darwin":
            return [
                home / ".config" / "Jackett",
                home / "Library/Application Support/Jackett",
            ]
        if system == "Windows":
            return [
                Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Jackett",
                Path(os.environ.get("APPDATA", str(home / "AppData/Roaming"))) / "Jackett",
                Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local"))) / "Jackett",
            ]
        xdg = os.environ.get("XDG_CONFIG_HOME")
        roots = [Path(xdg) / "Jackett"] if xdg else []
        roots.extend([home / ".config" / "Jackett", home / ".config" / "jackett"])
        return roots

    def _legacy_config_dirs(self) -> list[Path]:
        """Return config dirs that the *default* Jackett runtime can really use.

        Earlier recovery rounds treated the Jackett install/executable folder
        (``data/jackett`` or ``data/jackett/Jackett``) as legacy state on every
        platform.  That was the core macOS mistake: current Jackett macOS builds
        log and use ``~/Library/Application Support/Jackett`` as the app
        config/log directory, so files beside the bundled executable can be
        stale residue that Jackett never reads.  Selecting those residue files as
        "legacy" made LJS preserve the wrong folder and skip bootstrapping the
        actual zero-indexer runtime.

        Use documented/default platform config roots as legacy candidates.  Keep
        executable-adjacent preservation only for non-macOS installs where some
        service packagers historically place config under the install tree.
        """
        exe = self._executable_path()
        raw: list[Path | None] = [*self._default_config_dirs()]
        if platform.system() != "Darwin":
            raw.append(JACKETT_DIR.resolve() / "Jackett")
            raw.append(JACKETT_DIR.resolve())
            if exe:
                raw.append(exe.parent.resolve() / "Jackett")
                raw.append(exe.parent.resolve())
        return self._dedupe_paths(path for path in raw if path is not None and not self._is_under_managed_state(path))

    def _select_state_mode(self) -> None:
        """Choose the Jackett state root once for this manager instance.

        Upgrades must preserve real configured indexers from Round 188/Linux or
        private trackers the user configured manually.  Fresh installs use the
        isolated LJS state introduced for macOS split-brain protection.
        """
        if self._state_mode:
            return
        legacy = self._state_probe(self._legacy_config_dirs())
        managed = self._state_probe(self._managed_config_dirs())
        if legacy["configured_indexers"] > 0:
            self._state_mode = "legacy"
            self._state_reason = f"preserving legacy Jackett state with {legacy['configured_indexers']} configured indexer file(s)"
            self._active_config_dirs = self._dedupe_paths(legacy["dirs_with_state"] or self._legacy_config_dirs())
        elif managed["configured_indexers"] > 0 or managed["server_configs"] > 0:
            self._state_mode = "managed"
            self._state_reason = "using existing LJS-managed isolated Jackett state"
            self._active_config_dirs = self._managed_config_dirs()
        elif legacy["server_configs"] > 0:
            # A legacy ServerConfig without any Indexers is not useful state: it
            # is exactly the macOS failure mode where Jackett boots with a fresh
            # API key but zero configured trackers.  Preserve legacy only when it
            # has real indexer files; otherwise use isolated managed state so LJS
            # can safely repair auth and bootstrap public indexers.
            self._state_mode = "managed"
            self._state_reason = "ignoring zero-indexer legacy ServerConfig and using managed state for first-run public-indexer bootstrap"
            self._active_config_dirs = self._managed_config_dirs()
        else:
            self._state_mode = "managed"
            self._state_reason = "fresh install: no legacy Jackett config/indexer state found"
            self._active_config_dirs = self._managed_config_dirs()
        logger.info(
            "Jackett state selected: mode={} reason={} legacy_candidates={} managed_candidates={}",
            self._state_mode,
            self._state_reason,
            self._format_state_probe(legacy),
            self._format_state_probe(managed),
        )

    def _state_probe(self, dirs: list[Path]) -> dict[str, object]:
        server_configs = 0
        configured_indexers = 0
        dirs_with_state: list[Path] = []
        rows: list[str] = []
        for root in self._dedupe_paths(dirs):
            cfg = root / "ServerConfig.json"
            indexer_count = self._configured_indexer_file_count(root)
            has_cfg = cfg.exists()
            if has_cfg:
                server_configs += 1
            configured_indexers += indexer_count
            if has_cfg or indexer_count:
                dirs_with_state.append(root)
            rows.append(f"{root}:server={has_cfg}:indexers={indexer_count}")
        return {
            "server_configs": server_configs,
            "configured_indexers": configured_indexers,
            "dirs_with_state": dirs_with_state,
            "rows": rows,
        }

    @classmethod
    def _configured_indexer_file_count(cls, root: Path) -> int:
        """Count unique configured indexer config files below a Jackett state root."""
        seen_folders: set[str] = set()
        seen_files: set[str] = set()
        count = 0
        for name in ("Indexers", "indexers"):
            folder = root / name
            folder_key = cls._path_identity_key(folder)
            if folder_key in seen_folders:
                continue
            seen_folders.add(folder_key)
            if not folder.is_dir():
                continue
            for path in folder.glob("*.json"):
                if not path.is_file() or path.name.lower().startswith("temp"):
                    continue
                file_key = cls._path_identity_key(path)
                if file_key in seen_files:
                    continue
                seen_files.add(file_key)
                count += 1
        return count

    @classmethod
    def _configured_indexer_ids_in_root(cls, root: Path) -> set[str]:
        ids: set[str] = set()
        seen_folders: set[str] = set()
        for name in ("Indexers", "indexers"):
            folder = root / name
            folder_key = cls._path_identity_key(folder)
            if folder_key in seen_folders:
                continue
            seen_folders.add(folder_key)
            if not folder.is_dir():
                continue
            for path in folder.glob("*.json"):
                if not path.is_file() or path.name.lower().startswith("temp"):
                    continue
                stem = path.stem.strip()
                if stem:
                    ids.add(stem)
        return ids

    def _configured_indexer_ids_across_active_dirs(self) -> set[str]:
        ids: set[str] = set()
        for root in self._candidate_config_dirs():
            ids.update(self._configured_indexer_ids_in_root(root))
        return ids

    @staticmethod
    def _api_fingerprint(api_key: object) -> str:
        """Return a short non-secret fingerprint for config diagnostics."""
        value = str(api_key or "")
        if not value:
            return "missing"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]

    def _configured_indexer_file_count_across_active_dirs(self) -> int:
        """Count unique local configured indexer IDs in the selected Jackett state."""
        return len(self._configured_indexer_ids_across_active_dirs())

    @staticmethod
    def _format_state_probe(probe: dict[str, object]) -> str:
        rows = probe.get("rows") or []
        return "; ".join(str(row) for row in rows)

    def _is_under_managed_state(self, path: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(self._managed_state_dir.resolve(strict=False))
            return True
        except Exception:
            return False

    @staticmethod
    def _path_identity_key(path: Path) -> str:
        """Return a platform-aware identity key for a filesystem path.

        macOS and Windows are commonly case-insensitive.  Without normalizing
        case, ``.../Jackett`` and ``.../jackett`` look like two different
        directories to Python while they are the same directory on the user's
        disk.  That was inflating configured-indexer counts and causing LJS to
        mirror state onto itself.
        """
        text = str(Path(path).resolve(strict=False))
        if platform.system() in {"Darwin", "Windows"}:
            return text.lower()
        return text

    @classmethod
    def _dedupe_paths(cls, paths) -> list[Path]:
        seen: set[str] = set()
        out: list[Path] = []
        for path in paths:
            key = cls._path_identity_key(Path(path))
            if key in seen:
                continue
            seen.add(key)
            out.append(Path(path))
        return out

    def _read_api_key(self) -> Optional[str]:
        """Read the Jackett API key from its ServerConfig.json."""
        self._select_state_mode()
        for search_dir in self._candidate_config_dirs():
            if search_dir is None:
                continue
            config_path = search_dir / "ServerConfig.json"
            if config_path.exists():
                try:
                    data = json.loads(config_path.read_text())
                    return data.get("APIKey")
                except Exception:
                    continue
        return None

    def _candidate_config_dirs(self) -> list[Path]:
        """Return Jackett config directories LJS may have to inspect.

        The selected state is the preferred source of truth, but macOS archive
        builds have repeatedly ignored HOME/XDG overrides and used the platform
        default Application Support directory anyway.  When LJS discovers that
        the running process accepts a key from such a runtime directory, include
        that directory in subsequent diagnostics/bootstrap targets; otherwise we
        can write perfectly valid Indexers/*.json files into a managed folder
        Jackett is not actually reading.
        """
        self._select_state_mode()
        active = self._active_config_dirs or (self._managed_config_dirs() if self._state_mode == "managed" else self._legacy_config_dirs())
        extra: list[Path] = []
        if self._runtime_config_dir is not None:
            extra.append(self._runtime_config_dir)
        # API key discovery must follow the selected runtime first.  A stale
        # fresh managed config must not poison an upgrade using legacy indexers.
        return self._dedupe_paths([*active, *extra])

    def _server_config_paths(self) -> list[Path]:
        self._select_state_mode()
        if self._state_mode != "managed":
            # Legacy mode is preservation mode: do not rewrite global/manual
            # ServerConfig.json files that may contain private tracker setup.
            return []
        paths = [*(root / "ServerConfig.json" for root in self._managed_config_dirs())]
        # Do not write managed ServerConfig beside the downloaded executable on
        # macOS/Unix.  Jackett's own docs say to use HOME/XDG config roots on
        # Unix and current macOS builds report Application Support as the app
        # config/log directory.  Earlier rounds wrote executable-adjacent config
        # files, which later looked like "legacy" state but were not read by
        # the running process.  Windows keeps using --DataFolder instead.
        out: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            parent = path.parent
            if parent.exists() and not parent.is_dir():
                continue
            key = self._path_identity_key(path)
            if key not in seen:
                seen.add(key)
                out.append(path)
        return out


    def _ensure_managed_startup_config(self) -> None:
        """Create and synchronize managed Jackett ServerConfig files.

        macOS/Unix archive builds have changed which config folder they consult
        across Jackett releases (XDG config, lower-case jackett, app-support,
        or executable-adjacent state).  A fresh LJS-managed runtime must not end
        up with two ServerConfig.json files containing different API keys: LJS
        may read one while Jackett serves another, making every API-key call look
        broken.  In managed mode only, mirror one localhost-only config to every
        plausible managed path before start/restart.
        """
        if self._state_mode != "managed":
            return

        paths = self._server_config_paths()
        existing_payloads: list[tuple[Path, dict]] = []
        for path in paths:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
                if isinstance(data, dict):
                    existing_payloads.append((path, data))
            except Exception as exc:
                logger.warning(f"Jackett: ignoring unreadable managed ServerConfig {path}: {exc}")

        canonical_path = self._config_dir() / "ServerConfig.json"
        canonical: dict = {}
        # Prefer the canonical managed file when it exists, then any existing
        # managed file with an API key.  All candidate paths are LJS-managed, so
        # syncing them is safe and avoids API-key split-brain on macOS.
        for path, data in existing_payloads:
            if path.resolve(strict=False) == canonical_path.resolve(strict=False) and data.get("APIKey"):
                canonical = dict(data)
                break
        if not canonical:
            for _path, data in existing_payloads:
                if data.get("APIKey"):
                    canonical = dict(data)
                    break
        if not canonical:
            canonical = {"APIKey": secrets.token_hex(16)}

        canonical["AdminPassword"] = None
        canonical["AllowExternal"] = False
        canonical["Port"] = int(self._port or JACKETT_DEFAULT_PORT)
        api_key = str(canonical.get("APIKey") or secrets.token_hex(16))
        canonical["APIKey"] = api_key

        created = 0
        updated = 0
        for path in paths:
            try:
                before = None
                if path.exists():
                    try:
                        before = json.loads(path.read_text())
                    except Exception:
                        before = None
                path.parent.mkdir(parents=True, exist_ok=True)
                if before != canonical:
                    path.write_text(json.dumps(canonical, indent=2, sort_keys=True) + "\n")
                    if before is None:
                        created += 1
                    else:
                        updated += 1
            except Exception as exc:
                logger.warning(f"Jackett: failed to synchronize managed ServerConfig {path}: {exc}")

        self._api_key = api_key
        logger.info(
            "Jackett: synchronized managed startup config (created={}, updated={}, api_fingerprint={}, AdminPassword=null, AllowExternal=false)",
            created,
            updated,
            self._api_fingerprint(api_key),
        )

    def _log_managed_config_diagnostics(self, stage: str) -> None:
        self._log_active_config_diagnostics(stage)

    def _log_active_config_diagnostics(self, stage: str) -> None:
        """Log where the selected Jackett runtime can read/write config."""
        self._select_state_mode()
        rows: list[str] = []
        for root in self._candidate_config_dirs():
            config_path = root / "ServerConfig.json"
            exists = config_path.exists()
            admin_state = "missing"
            api_state = "missing"
            allow_external = "missing"
            if exists:
                try:
                    data = json.loads(config_path.read_text())
                    if isinstance(data, dict):
                        if data.get("AdminPassword") is None:
                            admin_state = "null"
                        elif data.get("AdminPassword") == "":
                            admin_state = "empty_string"
                        else:
                            admin_state = "set"
                        api_state = f"present:{self._api_fingerprint(data.get('APIKey'))}" if data.get("APIKey") else "missing"
                        allow_external = str(data.get("AllowExternal"))
                except Exception as exc:
                    admin_state = f"unreadable:{type(exc).__name__}"
            rows.append(
                f"path={config_path} exists={exists} admin={admin_state} api={api_state} allow_external={allow_external} indexer_files={self._configured_indexer_file_count(root)}"
            )
        logger.info("Jackett config diagnostics [{} mode={}]: {}", stage, self._state_mode, " | ".join(rows))

    def _repair_managed_admin_auth_config(self) -> bool:
        """Disable Jackett's local admin-password gate for LJS-managed Jackett.

        Jackett's search/Torznab API uses the API key, but its indexer
        administration API is cookie-login gated whenever AdminPassword is set.
        LJS runs managed Jackett as a localhost companion process; requiring a
        browser login makes automatic indexer configuration impossible.  Repair
        only the config files that belong to this local managed installation and
        keep external access disabled.
        """
        changed_any = False
        for config_path in self._server_config_paths():
            if not config_path.exists():
                continue
            try:
                data = json.loads(config_path.read_text())
                if not isinstance(data, dict):
                    continue
                before = dict(data)
                if data.get("AdminPassword") is not None:
                    data["AdminPassword"] = None
                # The managed instance is a local companion; do not expose an
                # unauthenticated dashboard beyond localhost.
                if data.get("AllowExternal") is not False:
                    data["AllowExternal"] = False
                if data != before:
                    config_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
                    logger.info(
                        "Jackett: repaired managed admin auth in {} (AdminPassword=null, AllowExternal=false)",
                        config_path,
                    )
                    changed_any = True
            except Exception as exc:
                logger.warning(f"Jackett: failed to inspect/repair admin auth config {config_path}: {exc}")
        return changed_any

    async def repair_admin_auth_and_restart(self) -> dict:
        """Repair the managed Jackett login gate and restart if needed."""
        await self.stop()
        changed = self._repair_managed_admin_auth_config()
        running = await self.start()
        return {
            "status": "ok" if running else "error",
            "changed": changed,
            "running": running,
            "url": self.url if running else None,
            "port": self._port,
            "api_key_available": bool(self.api_key),
        }

    def _discover_server_config_api_keys(self) -> list[tuple[Path, str]]:
        """Return API keys from every plausible Jackett config file.

        This is deliberately broader than _candidate_config_dirs(): if Jackett
        created a new ServerConfig somewhere unexpected, the only safe repair is
        to find the key the running process actually accepts and adopt that key.
        """
        paths: list[Path] = []
        paths.extend(self._server_config_paths())
        paths.extend(root / "ServerConfig.json" for root in self._candidate_config_dirs())
        paths.extend(self._managed_state_dir.rglob("ServerConfig.json") if self._managed_state_dir.exists() else [])
        paths.extend(JACKETT_DIR.rglob("ServerConfig.json") if JACKETT_DIR.exists() else [])
        paths.extend(root / "ServerConfig.json" for root in self._legacy_config_dirs())

        found: list[tuple[Path, str]] = []
        seen_paths: set[str] = set()
        for path in paths:
            key_path = self._path_identity_key(path)
            if key_path in seen_paths or not path.exists() or not path.is_file():
                continue
            seen_paths.add(key_path)
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            api_key = str(data.get("APIKey") or "").strip() if isinstance(data, dict) else ""
            if not api_key:
                continue
            found.append((path, api_key))
        return found

    async def _api_key_is_accepted(self, api_key: str | None) -> bool:
        """Return whether the running Jackett process accepts this API key."""
        key = str(api_key or "").strip()
        if not key:
            return False
        try:
            import httpx
            async with httpx.AsyncClient(timeout=8.0, verify=False, follow_redirects=False) as client:
                # t=indexers is cheap compared with a real search and returns an
                # explicit XML error when the key is wrong.  It may be empty when
                # no indexers are configured yet, so absence of the invalid-key
                # error is what matters here.
                response = await client.get(
                    f"{self.url}/api/v2.0/indexers/all/results/torznab/api",
                    params={"apikey": key, "t": "indexers", "configured": "true"},
                    headers={"Accept": "application/xml,text/xml,*/*"},
                )
            body = (response.text or "")[:500].lower()
            if response.status_code in {401, 403}:
                return False
            if "invalid api key" in body or 'code="100"' in body and "api key" in body:
                return False
            if response.status_code < 500 and not self._response_is_login_redirect(response.status_code, response.headers.get("location")):
                return True
        except Exception as exc:
            logger.debug(f"Jackett: API-key acceptance probe failed: {exc}")
        return False

    @staticmethod
    def _response_is_login_redirect(status_code: int, location: str | None) -> bool:
        if status_code not in {301, 302, 303, 307, 308}:
            return False
        return "/ui/login" in str(location or "").lower()

    def _adopt_runtime_config_dir(self, config_path: Path, *, source: str = "api_key") -> None:
        """Remember the config directory the running Jackett process really uses.

        The only directory that matters is the one Jackett reports/uses at
        runtime.  API-key equality is not enough because previous LJS repair
        attempts mirrored the same API key into multiple folders.  When the
        actual runtime folder has zero indexers, it is safe to bootstrap public
        no-credential indexers there even if stale files exist elsewhere.
        """
        root = Path(config_path).parent
        if not root.exists() or not root.is_dir():
            return

        indexer_count = self._configured_indexer_file_count(root)
        self._runtime_config_dir = root

        if indexer_count > 0:
            self._runtime_config_dir_bootstrap_safe = False
            if self._path_identity_key(root) not in {self._path_identity_key(p) for p in (self._active_config_dirs or [])}:
                self._active_config_dirs = self._dedupe_paths([*(self._active_config_dirs or []), root])
            logger.info(
                "Jackett: runtime config dir discovered from {}: {} ({} configured indexer file(s)); preserving it.",
                source,
                root,
                indexer_count,
            )
            return

        # Zero indexers in the actual runtime config dir means Jackett has no
        # private/manual setup in the folder it is reading.  Bootstrap is safe
        # and necessary even if stale LJS-managed files exist elsewhere.
        self._runtime_config_dir_bootstrap_safe = True
        self._active_config_dirs = self._dedupe_paths([root])
        if self._state_mode != "managed":
            logger.warning(
                "Jackett: selected state mode was {} but runtime config dir discovered from {} is zero-indexer {}; ignoring stale non-runtime indexer files and enabling safe public bootstrap there.",
                self._state_mode,
                source,
                root,
            )
        else:
            logger.warning(
                "Jackett: running process uses zero-indexer runtime config dir discovered from {}: {}; adding it as the authoritative bootstrap target.",
                source,
                root,
            )

    def _discover_runtime_config_dir_from_logs(self) -> Path | None:
        """Return Jackett's self-reported app config/log directory, if logged.

        Jackett startup logs include a line like:
        ``Info App config/log directory: /Users/.../Library/Application Support/Jackett``.
        This is more authoritative than guessing from HOME/XDG or API keys.
        """
        roots: list[Path] = []
        roots.extend(self._default_config_dirs())
        roots.extend(self._managed_config_dirs())
        roots.extend(self._active_config_dirs or [])
        if self._runtime_config_dir is not None:
            roots.append(self._runtime_config_dir)
        exe = self._executable_path()
        if exe:
            roots.extend([exe.parent.resolve(), exe.parent.resolve() / "Jackett"])
        roots.extend([JACKETT_DIR.resolve(), JACKETT_DIR.resolve() / "Jackett"])

        for root in self._dedupe_paths(roots):
            for log_path in (root / "log.txt", root / "Jackett" / "log.txt"):
                parsed = self._parse_runtime_config_dir_from_log(log_path)
                if parsed:
                    return parsed
        return None

    @staticmethod
    def _parse_runtime_config_dir_from_log(log_path: Path) -> Path | None:
        try:
            if not log_path.exists() or not log_path.is_file():
                return None
            text = log_path.read_text(errors="replace")[-200000:]
        except Exception:
            return None
        matches = re.findall(r"App config/log directory:\s*(.+)", text)
        if not matches:
            return None
        value = matches[-1].strip().strip('"').strip("'")
        if not value:
            return None
        # Drop trailing log decorations if present.
        value = re.split(r"\s+(?:Info|Debug|Warn|Error)\s+", value, maxsplit=1)[0].strip()
        return Path(value).expanduser()

    def _public_file_bootstrap_allowed(self) -> bool:
        """Return whether direct public indexer-file bootstrap is safe."""
        if self._state_mode == "managed":
            return True
        return bool(self._runtime_config_dir is not None and self._runtime_config_dir_bootstrap_safe)

    def _force_managed_api_key(self, api_key: str) -> None:
        """Mirror an accepted runtime API key back to managed ServerConfig files."""
        if self._state_mode != "managed" or not api_key:
            return
        updated = 0
        for path in self._server_config_paths():
            try:
                if path.parent.exists() and not path.parent.is_dir():
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                data = {}
                if path.exists():
                    try:
                        loaded = json.loads(path.read_text())
                        if isinstance(loaded, dict):
                            data = loaded
                    except Exception:
                        data = {}
                before = dict(data)
                data["APIKey"] = api_key
                data["AdminPassword"] = None
                data["AllowExternal"] = False
                data["Port"] = int(self._port or JACKETT_DEFAULT_PORT)
                if data != before:
                    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
                    updated += 1
            except Exception as exc:
                logger.warning(f"Jackett: failed to mirror accepted API key to {path}: {exc}")
        if updated:
            logger.info("Jackett: mirrored accepted runtime API key to managed configs (updated={}, api_fingerprint={})", updated, self._api_fingerprint(api_key))

    async def _adopt_valid_runtime_api_key(self) -> bool:
        """Ensure self._api_key is accepted and bind it to the real runtime dir."""
        current = self._api_key or self._read_api_key()

        async def bind_runtime_dir_for_key(api_key: str, *, adopted_from: Path | None = None) -> None:
            logged_dir = self._discover_runtime_config_dir_from_logs()
            if logged_dir is not None:
                self._adopt_runtime_config_dir(logged_dir / "ServerConfig.json", source="jackett_log")
                return
            if adopted_from is not None:
                self._adopt_runtime_config_dir(adopted_from, source="accepted_api_key")
                return
            # If multiple configs share the same accepted key, do not blindly
            # mark all of them as runtime.  Prefer platform default roots, then
            # the first matching config as a weak fallback.
            matches = [(path, candidate) for path, candidate in self._discover_server_config_api_keys() if candidate == api_key]
            default_keys = {self._path_identity_key(root) for root in self._default_config_dirs()}
            for path, _candidate in matches:
                if self._path_identity_key(path.parent) in default_keys:
                    self._adopt_runtime_config_dir(path, source="accepted_api_key_default_dir")
                    return
            # If Jackett ignored HOME/XDG, the accepted key may be mirrored both
            # into managed configs and into the real platform default runtime.
            # Prefer non-managed roots before falling back to managed guesses.
            for path, _candidate in matches:
                if not self._is_under_managed_state(path.parent):
                    self._adopt_runtime_config_dir(path, source="accepted_api_key_non_managed")
                    return
            if matches:
                self._adopt_runtime_config_dir(matches[0][0], source="accepted_api_key_fallback")

        if await self._api_key_is_accepted(current):
            self._api_key = current
            self._api_key_validated = True
            await bind_runtime_dir_for_key(str(current or ""))
            logger.info("Jackett: runtime API key accepted (api_fingerprint={})", self._api_fingerprint(current))
            return True

        for path, candidate in self._discover_server_config_api_keys():
            if candidate == current:
                continue
            if await self._api_key_is_accepted(candidate):
                self._api_key = candidate
                self._api_key_validated = True
                await bind_runtime_dir_for_key(candidate, adopted_from=path)
                logger.warning(
                    "Jackett: adopted API key from running runtime config {} (api_fingerprint={}); previous selected key was rejected",
                    path,
                    self._api_fingerprint(candidate),
                )
                self._force_managed_api_key(candidate)
                return True

        self._api_key_validated = False
        logger.warning(
            "Jackett: no discovered ServerConfig API key is accepted by the running process; API-key dependent search is not ready."
        )
        return False

    # ── health / status ────────────────────────────────────────────

    async def health_check(self) -> dict:
        """Return a status dict suitable for an API endpoint."""
        running = self.is_running
        result = {
            "installed": self.is_installed,
            "running": running,
            "jackett_running": running,
            "url": self.url if running else None,
            "port": self._port,
            "api_key_available": bool(self.api_key),
            "api_key_validated": bool(self._api_key_validated),
            "configured_indexers": await self.configured_indexer_count() if running else 0,
            "last_successful_search_at": None,
            "last_error": None,
            "error": None,
        }

        if not self.is_installed:
            asset = self._asset_name()
            result["supported_platform"] = asset is not None
            if not asset:
                result["error"] = f"Unsupported platform: {platform.system()}-{platform.machine()}"
            else:
                result["error"] = "Jackett not installed — use POST /api/jackett/install"
        elif not self.is_running:
            result["error"] = "Jackett installed but not running — use POST /api/jackett/start"

        return result

    async def configured_indexer_count(self) -> int:
        """Return number of configured Jackett indexers, or zero on failure.

        Prefer the Torznab t=indexers surface because current Jackett builds can
        redirect the admin /api/v2.0/indexers endpoint to the UI login while the
        API-key search/Torznab endpoints still work.
        """
        api_key = self.api_key
        if not api_key:
            return 0
        if not self._api_key_validated:
            await self._adopt_valid_runtime_api_key()
            api_key = self.api_key
        if not self._api_key_validated:
            logger.warning("Jackett: refusing to use configured-indexer file count because the running process rejects every known API key.")
            return 0
        try:
            from src.search.jackett_indexer_config import JackettIndexerConfigurer

            catalogue = await JackettIndexerConfigurer(self.url, api_key).fetch_indexer_catalogue()
            if catalogue:
                return sum(1 for indexer in catalogue if indexer.configured)
            file_count = self._configured_indexer_file_count_across_active_dirs()
            if file_count > 0:
                logger.info("Jackett: API catalogue is unavailable/empty; using unique configured indexer file count from selected state: {}", file_count)
                return file_count
            return 0
        except Exception as exc:
            logger.debug(f"Jackett: configured indexer count failed: {exc}")
            file_count = self._configured_indexer_file_count_across_active_dirs()
            if file_count > 0:
                return file_count
            return 0

    async def configure_default_indexers(self) -> dict:
        """Configure Jackett's first-run open/public indexer profile."""
        return await self.configure_indexer_profile("all_open_public")

    async def configure_indexer_profile(self, profile: str = "balanced_public") -> dict:
        """Configure a named Jackett indexer profile."""
        if not self.is_running:
            started = await self.start()
            if not started:
                return {"status": "error", "error": "Jackett is not running."}
        api_key = self.api_key
        if not api_key:
            return {"status": "error", "error": "Jackett API key is not available."}
        from src.search.jackett_indexer_config import JackettIndexerConfigurer

        configurer = JackettIndexerConfigurer(self.url, api_key)
        result = await configurer.configure_profile(profile)

        # Current Jackett builds may expose the catalogue through Torznab but
        # redirect the admin config endpoints to /UI/Login.  When this happens
        # on a local LJS-managed fresh install, fall back to bootstrapping the
        # public/open indexer JSON files directly into Jackett's selected data
        # folder.  Do not do this in legacy mode: legacy mode may contain user
        # private tracker state and must remain preservation-only.
        try:
            configured_now = await self.configured_indexer_count()
        except Exception:
            configured_now = 0
        requested = int(result.get("requested", 0) or 0)
        added = int(result.get("added", 0) or 0)
        failed = int(result.get("failed", 0) or 0)
        if (
            configured_now <= 0
            and self._public_file_bootstrap_allowed()
            and added <= 0
            and (requested <= 0 or failed >= requested or str(result.get("status") or "") == "degraded")
        ):
            bootstrap = await self.bootstrap_public_indexer_files(profile=profile, catalogue_configurer=configurer)
            result["file_bootstrap"] = bootstrap
            if int(bootstrap.get("written", 0) or 0) > 0:
                await self.stop()
                restarted = await self.start()
                result["file_bootstrap_restart"] = restarted
                try:
                    result["diagnostics"] = await self.indexer_diagnostics()
                    summary = (result.get("diagnostics") or {}).get("summary", {})
                    effective_configured = int(summary.get("configured_indexers", 0) or 0)
                    if effective_configured > 0:
                        # The admin API path may still have failed because current
                        # Jackett builds can gate /api/v2.0/indexers behind the UI
                        # login.  If the file bootstrap has been verified through
                        # Torznab diagnostics, report the effective state clearly
                        # instead of leaving a scary `failed: 107` in startup logs.
                        result["admin_config_attempt"] = {
                            "requested": requested,
                            "added": added,
                            "failed": failed,
                            "admin_error": (result.get("diagnostics") or {}).get("admin_error")
                                or result.get("error"),
                        }
                        result["added"] = effective_configured
                        result["failed"] = 0
                        result["effective_configured_indexers"] = effective_configured
                        result["note"] = (
                            "Jackett admin indexer configuration was unavailable, "
                            "so LJS bootstrapped public indexer files into the "
                            "runtime config directory and verified them through Torznab diagnostics."
                        )
                except Exception as exc:
                    result["post_bootstrap_diagnostics_error"] = str(exc)
        return result

    async def bootstrap_public_indexer_files(self, profile: str = "all_open_public", catalogue_configurer=None) -> dict:
        """Directly seed LJS-managed public Jackett indexer files.

        This is a compatibility fallback for Jackett builds where the Torznab
        API-key surface works but admin config endpoints are cookie-gated or
        login-redirected.  It writes only public/open indexer files and only into
        LJS-managed state.  Private/credentialed indexers still go through the
        explicit UI schema path.
        """
        self._select_state_mode()
        if not self._public_file_bootstrap_allowed():
            return {"status": "skipped", "reason": "direct indexer bootstrap is disabled because the running Jackett config dir is not a safe zero-indexer LJS-managed/runtime state"}
        api_key = self.api_key
        if not api_key:
            return {"status": "error", "error": "Jackett API key is not available."}
        from src.search.jackett_indexer_config import (
            DEFAULT_JACKETT_PROFILE,
            JACKETT_INDEXER_PROFILES,
            JackettIndexerConfigurer,
        )

        configurer = catalogue_configurer or JackettIndexerConfigurer(self.url, api_key)
        catalogue = await configurer.fetch_indexer_catalogue()
        catalogue_source = "jackett_api"
        if not catalogue:
            catalogue = self._indexer_catalogue_from_local_definitions(public_only=False)
            catalogue_source = "local_definitions" if catalogue else "empty"
        available = {entry.id: entry for entry in catalogue if entry.id}
        profile = (profile or DEFAULT_JACKETT_PROFILE).strip()
        if profile in {"all_open_public", "broad_public"}:
            requested_entries = [entry for entry in catalogue if configurer._is_public_like(entry)]
        else:
            requested_entries = [available[idx] for idx in JACKETT_INDEXER_PROFILES.get(profile, []) if idx in available]

        indexer_dirs = self._managed_indexer_config_dirs()
        for directory in indexer_dirs:
            directory.mkdir(parents=True, exist_ok=True)
        written = skipped_existing = skipped_no_link = failed = 0
        written_ids: list[str] = []
        touched_dirs: set[str] = set()
        for entry in requested_entries:
            safe_id = "".join(ch for ch in entry.id if ch.isalnum() or ch in {"-", "_", "."}).strip(".")
            if not safe_id:
                failed += 1
                continue
            link = (getattr(entry, "link", "") or "").strip()
            # The Torznab/local definition payload must include the tracker link.
            # Without it, we cannot reliably construct a public Cardigann config
            # and should skip rather than writing fake/broken tracker state.
            if not link:
                skipped_no_link += 1
                continue
            payload = [
                {"id": "sitelink", "type": "inputstring", "name": "Site Link", "value": link},
                {"id": "cookieheader", "type": "hiddendata", "name": "CookieHeader", "value": ""},
                {"id": "lasterror", "type": "hiddendata", "name": "LastError", "value": None},
            ]
            wrote_for_id = False
            existed_for_id = False
            failed_for_id = False
            for indexer_dir in indexer_dirs:
                dest = indexer_dir / f"{safe_id}.json"
                if dest.exists():
                    existed_for_id = True
                    continue
                try:
                    tmp = dest.with_suffix(dest.suffix + ".tmp")
                    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
                    tmp.replace(dest)
                    wrote_for_id = True
                    touched_dirs.add(str(indexer_dir))
                except Exception as exc:
                    failed_for_id = True
                    logger.warning(f"Jackett: failed to write managed public indexer config {dest}: {exc}")
            if wrote_for_id:
                written += 1
                written_ids.append(safe_id)
            elif existed_for_id:
                skipped_existing += 1
            elif failed_for_id:
                failed += 1

        logger.info(
            "Jackett managed public-indexer file bootstrap: unique_written={} skipped_existing={} skipped_no_link={} failed={} targets={} profile={} catalogue_source={} available_catalogue={} requested_public={}",
            written,
            skipped_existing,
            skipped_no_link,
            failed,
            [str(path) for path in indexer_dirs],
            profile,
            catalogue_source,
            len(catalogue),
            len(requested_entries),
        )
        return {
            "status": "ok",
            "profile": profile,
            "requested": len(requested_entries),
            "written": written,
            "skipped_existing": skipped_existing,
            "skipped_no_link": skipped_no_link,
            "failed": failed,
            "target": str(indexer_dirs[0]) if indexer_dirs else "",
            "targets": [str(path) for path in indexer_dirs],
            "catalogue_source": catalogue_source,
            "available_catalogue_count": len(catalogue),
            "requested_public_count": len(requested_entries),
            "written_ids_sample": written_ids[:25],
        }

    def _managed_indexer_config_dirs(self) -> list[Path]:
        """Return Indexers folders for safe first-run public bootstrap.

        When Jackett's runtime config dir is discovered and has zero indexers,
        write there first.  Do not write to stale executable-adjacent folders in
        legacy mode: those caused round 199 to create convincing-looking files
        that the running macOS Jackett process never loaded.
        """
        roots: list[Path] = []
        if self._runtime_config_dir is not None and self._runtime_config_dir_bootstrap_safe:
            roots.append(self._runtime_config_dir)

        if self._state_mode == "managed":
            roots.extend(self._managed_config_dirs())
            roots.extend(path.parent for path in self._server_config_paths())
            roots.extend(path for path in (self._active_config_dirs or []) if self._is_under_managed_state(path))

        out: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            if root.exists() and not root.is_dir():
                continue
            indexer_dir = root / "Indexers"
            key = self._path_identity_key(indexer_dir)
            if key in seen:
                continue
            seen.add(key)
            out.append(indexer_dir)
        return out

    def _public_indexer_catalogue_from_local_definitions(self) -> list:
        return self._indexer_catalogue_from_local_definitions(public_only=True)

    def _indexer_catalogue_from_local_definitions(self, *, public_only: bool = False) -> list:
        """Build indexer entries from Jackett's bundled definition YAMLs.

        The local Definitions directory contains Jackett's full catalogue: public,
        semi-private, and private trackers.  LJS uses the full list for
        diagnostics/UI coverage, while first-run automatic bootstrap filters it
        down to open/public no-credential indexers only.
        """
        try:
            import yaml
            from src.search.jackett_indexer_config import JackettIndexerInfo
        except Exception as exc:
            logger.info(f"Jackett: cannot parse local indexer definitions for bootstrap: {exc}")
            return []

        roots: list[Path] = []
        exe = self._executable_path()
        if exe:
            roots.extend([exe.parent / "Definitions", exe.parent.parent / "Definitions"])
        roots.extend([JACKETT_DIR / "Definitions", JACKETT_DIR / "Jackett" / "Definitions"])

        entries = []
        seen: set[str] = set()
        configured_ids = self._configured_indexer_ids_across_active_dirs()
        for root in self._dedupe_paths(root for root in roots if root):
            if not root.is_dir():
                continue
            for path in sorted([*root.glob("*.yml"), *root.glob("*.yaml")]):
                try:
                    data = yaml.safe_load(path.read_text(errors="replace"))
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                idx = str(data.get("id") or path.stem).strip()
                if not idx or idx in seen:
                    continue
                idx_type = str(data.get("type") or "unknown").strip().lower()
                if public_only and idx_type != "public":
                    continue
                link = self._first_definition_link(data)
                if public_only and not link:
                    continue
                seen.add(idx)
                language = str(data.get("language") or "").strip()
                tags = tuple(str(tag).strip().lower() for tag in (data.get("tags") or []) if str(tag).strip()) if isinstance(data.get("tags"), list) else ()
                categories = tuple(str(cat).strip() for cat in (data.get("categories") or []) if str(cat).strip()) if isinstance(data.get("categories"), list) else ()
                entries.append(JackettIndexerInfo(
                    id=idx,
                    name=str(data.get("name") or idx).strip(),
                    configured=idx in configured_ids,
                    type=idx_type,
                    language=language,
                    categories=categories,
                    tags=tags,
                    link=link,
                ))
        if entries:
            logger.info(
                "Jackett: recovered {} {}indexer definition(s) from local Jackett Definitions",
                len(entries),
                "public " if public_only else "",
            )
        else:
            logger.info("Jackett: no local {}indexer definitions found", "public " if public_only else "")
        return entries

    @staticmethod
    def _first_definition_link(data: dict) -> str:
        for key in ("links", "legacylinks"):
            raw = data.get(key)
            values = raw if isinstance(raw, list) else [raw]
            for item in values:
                link = str(item or "").strip()
                if link.startswith(("http://", "https://")):
                    return link
        return ""

    async def indexer_diagnostics(self) -> dict:
        """Return how much of Jackett's live/local indexer catalogue is configured."""
        api_key = self.api_key
        if not self.is_running:
            return {"status": "error", "error": "Jackett is not running."}
        if not api_key:
            return {"status": "error", "error": "Jackett API key is not available."}
        if not self._api_key_validated:
            await self._adopt_valid_runtime_api_key()
            api_key = self.api_key
        from src.search.jackett_indexer_config import JackettIndexerConfigurer

        configurer = JackettIndexerConfigurer(self.url, api_key)
        diagnostics = await configurer.diagnostics()
        summary = diagnostics.get("summary") if isinstance(diagnostics, dict) else None
        if isinstance(summary, dict) and int(summary.get("total_indexers", 0) or 0) > 0:
            return diagnostics

        local_catalogue = self._indexer_catalogue_from_local_definitions(public_only=False)
        if not local_catalogue:
            return diagnostics
        local_summary = configurer.summarize_catalogue(local_catalogue)
        return {
            "status": "degraded" if diagnostics.get("status") != "ok" else "ok",
            "admin_error": diagnostics.get("admin_error"),
            "catalogue_source": "local_definitions",
            "api_key_validated": bool(self._api_key_validated),
            "summary": local_summary,
            "configured": [entry.__dict__ for entry in local_catalogue if entry.configured][:500],
            "unconfigured": [entry.__dict__ for entry in local_catalogue if not entry.configured][:500],
            "open_public_recommended": [entry.__dict__ for entry in local_catalogue if configurer._is_public_like(entry)][:500],
            "profiles": diagnostics.get("profiles", {}),
            "dynamic_profiles": diagnostics.get("dynamic_profiles", ["all_open_public", "broad_public"]),
            "note": "Live Jackett catalogue API was unavailable; showing Jackett bundled Definitions catalogue instead.",
        }

    async def get_indexer_config_schema(self, indexer_id: str) -> dict:
        """Return a Jackett indexer configuration schema for advanced setup."""
        api_key = self.api_key
        if not self.is_running:
            return {"status": "error", "error": "Jackett is not running."}
        if not api_key:
            return {"status": "error", "error": "Jackett API key is not available."}
        from src.search.jackett_indexer_config import JackettIndexerConfigurer

        return await JackettIndexerConfigurer(self.url, api_key).get_indexer_config_schema(indexer_id)

    async def configure_custom_indexer(self, indexer_id: str, values: dict) -> dict:
        """Configure a Jackett indexer with user-supplied schema values."""
        api_key = self.api_key
        if not self.is_running:
            started = await self.start()
            if not started:
                return {"status": "error", "error": "Jackett is not running."}
        if not api_key:
            return {"status": "error", "error": "Jackett API key is not available."}
        from src.search.jackett_indexer_config import JackettIndexerConfigurer

        return await JackettIndexerConfigurer(self.url, api_key).configure_custom_indexer(indexer_id, values or {})

    def save_to_settings(self, settings: "Settings") -> None:
        """Write the Jackett URL and API key into the application settings.

        Args:
            settings: The application Settings object to mutate.
        """
        settings.jackett_url = self.url
        settings.jackett_api_key = self.api_key or ""
