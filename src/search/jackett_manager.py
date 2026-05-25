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
import urllib.request
import urllib.error
import zipfile
from loguru import logger
from pathlib import Path
from typing import Optional

from src.core.security.command_policy import CommandPolicy, CommandPolicyError
from src.core.security.path_policy import SafePathResolver


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
JACKETT_PORT = 9117  # Jackett default port


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
        return self._running and self._process is not None and self._process.returncode is None

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
        return f"http://127.0.0.1:{JACKETT_PORT}"

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
        with zipfile.ZipFile(str(archive), "r") as zf:
            zf.extractall(str(dest))

    @staticmethod
    def _extract_tarball(archive: Path, dest: Path) -> None:
        import tarfile
        with tarfile.open(str(archive), "r:gz") as tf:
            tf.extractall(str(dest))

    # ── start / stop ───────────────────────────────────────────────

    async def start(self) -> bool:
        """Launch Jackett as a subprocess. Idempotent — no-op if already running."""
        if self.is_running:
            return True

        exe = self._executable_path()
        if not exe:
            logger.error("Jackett: executable not found")
            return False

        try:
            cwd = str(exe.parent)
            env = os.environ.copy()
            env["JACKETT_PORT"] = str(JACKETT_PORT)
            env["JACKETT_HOME"] = str(exe.parent.resolve())
            exe_args = [str(exe.resolve())]
            if platform.system() != "Windows":
                exe_args.append("--NoUpdates")

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
                    self._api_key = self._read_api_key()
                    logger.info(f"Jackett: running on {self.url}")
                    return True
                except urllib.error.HTTPError as e:
                    # Server is up — just returned a non-2xx status
                    self._running = True
                    self._api_key = self._read_api_key()
                    logger.info(f"Jackett: running on {self.url} (HTTP {e.code})")
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
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._process.kill()
            except Exception:
                pass
        self._running = False
        self._process = None

    # ── configuration ──────────────────────────────────────────────

    @staticmethod
    def _default_config_dir() -> Path | None:
        """Return Jackett's default config directory for this platform."""
        home = Path.home()
        if platform.system() == "Darwin":
            return home / "Library/Application Support/Jackett"
        if platform.system() == "Windows":
            return Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Jackett"
        # Linux / others
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            return Path(xdg) / "Jackett"
        return home / ".config/Jackett"

    def _read_api_key(self) -> Optional[str]:
        """Read the Jackett API key from its ServerConfig.json."""
        exe = self._executable_path()
        exe_dir = exe.parent.resolve() if exe else None
        for search_dir in (JACKETT_DIR.resolve(), self._default_config_dir(), exe_dir):
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

    # ── health / status ────────────────────────────────────────────

    async def health_check(self) -> dict:
        """Return a status dict suitable for an API endpoint."""
        running = self.is_running
        result = {
            "installed": self.is_installed,
            "running": running,
            "jackett_running": running,
            "url": self.url if running else None,
            "api_key_available": bool(self.api_key),
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
        """Return number of configured Jackett indexers, or zero on failure."""
        api_key = self.api_key
        if not api_key:
            return 0
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                response = await client.get(
                    f"{self.url}/api/v2.0/indexers",
                    params={"apikey": api_key},
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
            data = response.json()
            return sum(1 for indexer in data if indexer.get("configured"))
        except Exception as exc:
            logger.debug(f"Jackett: configured indexer count failed: {exc}")
            return 0

    async def configure_default_indexers(self) -> dict:
        """Configure Jackett's first-run open/public indexer profile."""
        return await self.configure_indexer_profile("all_open_public")

    async def configure_indexer_profile(self, profile: str = "balanced_public") -> dict:
        """Configure a named Jackett indexer profile."""
        api_key = self.api_key
        if not self.is_running:
            started = await self.start()
            if not started:
                return {"status": "error", "error": "Jackett is not running."}
        if not api_key:
            return {"status": "error", "error": "Jackett API key is not available."}
        from src.search.jackett_indexer_config import JackettIndexerConfigurer

        return await JackettIndexerConfigurer(self.url, api_key).configure_profile(profile)

    async def indexer_diagnostics(self) -> dict:
        """Return how much of Jackett's live indexer catalogue is configured."""
        api_key = self.api_key
        if not self.is_running:
            return {"status": "error", "error": "Jackett is not running."}
        if not api_key:
            return {"status": "error", "error": "Jackett API key is not available."}
        from src.search.jackett_indexer_config import JackettIndexerConfigurer

        return await JackettIndexerConfigurer(self.url, api_key).diagnostics()

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
