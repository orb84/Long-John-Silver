import asyncio
import os
import signal
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
import uvicorn

from src.core.config import SettingsManager
from src.core.autostart import AutoStartManager
from src.core.database import Database
from src.core.task_supervisor import TaskSupervisor, TaskCriticality
from src.core.preferences import PreferenceManager
from src.utils.blacklist import BlacklistManager
from src.utils.auth import AuthService, load_auth_config
from src.llm_providers.manager import LLMProviderManager
from src.llm_providers.task_client import TaskLLMClient
from src.core.vector_store import VectorStore
from src.core.conversation import ConversationManager
from src.core.behavior_tracker import BehaviorTracker
from src.ai.behavior_recorder import BehaviorRecorder
from src.core.release_groups import ReleaseGroupTracker
from src.search.aggregator import SearchAggregator
from src.core.notifications import NotificationService
from src.core.content_cleanup import ContentCleanup
from src.ai.intent_router import IntentRouter
from src.ai.assistant import AIAssistant, AgentDependencies
from src.core.prompt_scheduler import PromptScheduler
from src.core.torrent_engine import TorrentEngine
from src.core.queue_manager import QueueManager
from src.core.bandwidth_manager import BandwidthManager
from src.core.downloader import DownloadManager, DownloadDependencies
from src.core.download_handler import DownloadCompletionHandler
from src.utils.bencode import BencodeDecoder
from src.core.torrent_resolver import TorrentUrlResolver
from src.core.categories.metadata.enricher import TMDBMetadataEnricher
from src.core.taste_profiler import TasteMetadataRuntimeContext, TasteProfiler
from src.core.recommender import RecommendationEngine
from src.core.librarian import Librarian
from src.core.scheduler import MediaScheduler, SchedulerDependencies
from src.core.smart_quality import SmartQualityInferrer
from src.core.categories.registry import CategoryRegistry
from src.core.suggestion_compiler import SuggestionCompiler
from src.core.category_lifecycle import CategoryLifecycleEngine
from src.core.torrent_racer import TorrentRacer
from src.utils.library_scanner import LibraryScanner
from src.web.app import create_app
from src.web.access_logs import install_quiet_polling_access_log_filter
from src.web.comms import create_registry
from src.core.state_coordinator import StateCoordinator
from src.search.jackett_manager import JackettManager
from src.search.web.searxng_manager import SearXNGManager
from src.integrations.slskd_manager import SlskdManager
from src.integrations.slskd_import_monitor import SlskdImportMonitor
from src.core.categories.artwork import CategoryArtworkManager
from src.utils.browser.runtime import BrowserRuntime
from src.utils.browser.domain_policy import BrowserDomainPolicy
from src.utils.browser.challenge_detector import ChallengeDetector
from src.ai.web_reader import WebReader
from src.ai.tool_catalog import AgentToolCatalog
from src.ai.tools.downloads import DownloadToolProvider
from src.ai.tools.library import LibraryToolProvider
from src.ai.tools.preferences import PreferencesToolProvider
from src.ai.tools.research import ResearchToolProvider
from src.ai.tools.scheduling import SchedulingToolProvider
from src.ai.tools.web import WebToolProvider
from src.ai.tools.categories import CategoryToolProvider
from src.ai.tools.storage import StorageToolProvider
from src.core.storage import StorageMonitor
from src.ai.torrent_selection import TorrentSelectionService
from src.core.actions.audit import ActionEventStore
from src.utils.detailed_logger import DetailedLoggingSubsystem

def _discover_lan_ips() -> list[str]:
    """Return likely LAN addresses for startup diagnostics.

    This is intentionally best-effort and dependency-free. It helps users avoid
    chasing a stale DHCP address such as yesterday's 192.168.x.x when the server
    is actually listening on a different interface.
    """
    ips: set[str] = set()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    # UDP connect does not have to send packets, but it asks the OS which local
    # address it would use for outbound traffic. This usually finds the active
    # Wi-Fi/Ethernet address even when hostname resolution is unhelpful.
    for target in (("8.8.8.8", 80), ("1.1.1.1", 80)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.2)
                sock.connect(target)
                ip = sock.getsockname()[0]
                if ip and not ip.startswith("127."):
                    ips.add(ip)
        except Exception:
            continue

    return sorted(ips)


def _format_access_urls(host: str, port: int) -> list[str]:
    """Build the URLs that should be useful to the user at startup."""
    urls = [f"http://127.0.0.1:{port}/", f"http://localhost:{port}/"]
    if host in {"0.0.0.0", "::", ""}:
        urls.extend(f"http://{ip}:{port}/" for ip in _discover_lan_ips())
    elif host not in {"127.0.0.1", "localhost"}:
        urls.append(f"http://{host}:{port}/")
    # Stable order, no duplicates.
    return list(dict.fromkeys(urls))


def _category_service_value(settings: object, service_id: str, key: str, category_ids: tuple[str, ...] = ("media",)) -> str | None:
    """Return one category-owned service value without using legacy globals.

    Shared media integrations are configured in the abstract ``media`` category
    and inherited by TV/Movie. A small fallback category list is accepted so
    custom deployments can intentionally override a service in a child category
    without reintroducing application-wide TMDB/Trakt/Plex fields.
    """
    getter = getattr(settings, "first_category_service_value", None)
    if callable(getter):
        value = getter(list(category_ids), service_id, key)
    else:
        value = None
    if value in (None, ""):
        return None
    return str(value)


def _category_service_enabled(
    settings: object,
    category_id: str,
    service_id: str,
    *,
    default: bool = True,
) -> bool:
    """Return a category-local enable flag with a conservative fallback."""
    enabled = getattr(settings, "category_service_enabled", None)
    if callable(enabled):
        return bool(enabled(category_id, service_id, default=default))
    return bool(default)


async def _probe_http_live(host: str, port: int, *, timeout: float = 0.75) -> str:
    """Return the raw response prefix from LJS's lightweight live endpoint."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    try:
        request = (
            "GET /api/live HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Connection: close\r\n"
            "User-Agent: ljs-startup-probe\r\n"
            "\r\n"
        )
        writer.write(request.encode("ascii"))
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        data = await asyncio.wait_for(reader.read(512), timeout=timeout)
        return data.decode("utf-8", errors="replace")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _wait_for_web_server_ready(host: str, port: int, task: asyncio.Task, timeout_seconds: float = 15.0) -> None:
    """Wait until the LJS web app answers HTTP, not merely until a port accepts TCP.

    Round 50 only proved that *something* accepted a connection.  That can mask
    a stale process already bound to the port, or a socket that bound but whose
    event loop is too busy to answer requests.  The readiness gate now calls the
    app-owned /api/live endpoint and requires an HTTP 200 response containing the
    LJS marker before startup jobs are allowed to begin.
    """
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_error: Exception | str | None = None

    while asyncio.get_running_loop().time() < deadline:
        if task.done():
            exc = task.exception()
            if exc:
                raise RuntimeError(f"web server task exited before answering /api/live on port {port}: {exc}") from exc
            raise RuntimeError(f"web server task exited before answering /api/live on port {port}")
        try:
            response = await _probe_http_live(connect_host, port)
            if "200" in response.split("\r\n", 1)[0] and "ljs-live" in response:
                return
            last_error = f"unexpected readiness response: {response[:120]!r}"
        except Exception as exc:  # noqa: BLE001 - readiness probes intentionally retry broadly.
            last_error = exc
        await asyncio.sleep(0.1)

    raise RuntimeError(
        f"web server did not answer LJS /api/live on {connect_host}:{port} "
        f"within {timeout_seconds:.1f}s; last probe error: {last_error}"
    )


async def _event_loop_watchdog(interval_seconds: float = 1.0, warn_after_seconds: float = 3.0) -> None:
    """Log if startup/background work blocks the asyncio loop for too long."""
    loop = asyncio.get_running_loop()
    expected = loop.time() + interval_seconds
    while True:
        await asyncio.sleep(interval_seconds)
        now = loop.time()
        lag = now - expected
        if lag > warn_after_seconds:
            logger.warning(f"Event loop lag detected: {lag:.1f}s. A background job may be blocking the web UI.")
        expected = now + interval_seconds


async def _start_managed_searxng_after_ui(settings_manager: SettingsManager, searxng_manager: SearXNGManager) -> None:
    """Best-effort managed SearXNG startup after the web UI is reachable.

    Managed web research is useful immediately after launch, but a previous LJS
    run may have installed the runtime while the sidecar process is stopped.
    Starting it here prevents the assistant from silently degrading to the
    DuckDuckGo fallback on the first research turn after an app restart.
    """
    settings = settings_manager.settings
    cfg = getattr(settings, "web_search", None)
    if cfg is None:
        return
    if not getattr(cfg, "enabled", True):
        logger.info("Managed SearXNG startup skipped because web search is disabled.")
        return
    if getattr(cfg, "provider", "") != "searxng" or getattr(cfg, "mode", "managed") != "managed":
        logger.info(
            "Managed SearXNG startup skipped for provider={} mode={}",
            getattr(cfg, "provider", ""),
            getattr(cfg, "mode", ""),
        )
        return
    if not getattr(cfg, "auto_install", True) and not searxng_manager.is_installed:
        logger.info("Managed SearXNG startup skipped because auto_install is disabled and runtime is absent.")
        return

    cfg.status = "starting"
    cfg.status_message = "Starting managed SearXNG in the background."
    settings_manager.save(settings)
    try:
        logger.info("Web research provider is managed SearXNG — starting sidecar after web startup...")
        ok = await searxng_manager.start(settings, health_timeout_seconds=45.0)
        searxng_manager.save_to_settings(settings)
        if ok:
            cfg.status = "ready"
            cfg.status_message = "Managed SearXNG is running and JSON search is available."
            logger.info("Managed SearXNG startup ready at {}", searxng_manager.url)
        else:
            cfg.status = "error"
            cfg.status_message = searxng_manager.last_error or "Managed SearXNG did not become ready."
            logger.warning("Managed SearXNG startup failed: {}", cfg.status_message)
        settings_manager.save(settings)
    except Exception as exc:  # noqa: BLE001 - startup sidecar must not crash LJS.
        cfg.status = "error"
        cfg.status_message = f"Managed SearXNG startup failed: {exc}"
        settings_manager.save(settings)
        logger.exception("Managed SearXNG background startup crashed: {}", exc)


async def _start_managed_soulseek_after_ui(settings_manager: SettingsManager, slskd_manager: SlskdManager) -> None:
    """Best-effort managed slskd startup after the web UI is already reachable.

    Soulseek is useful, but it must never be on the critical path for opening
    the dashboard.  Installation, process start, API probing, and account login
    validation are all recoverable setup states shown in Compass/Setup.
    """
    settings = settings_manager.settings
    cfg = getattr(settings, "soulseek", None)
    if cfg is None or not getattr(cfg, "enabled", False) or not getattr(cfg, "managed", True):
        return
    if not cfg.soulseek_username or not cfg.soulseek_password:
        cfg.account_status = "needs_credentials"
        cfg.account_status_message = "Soulseek username and password are required before LJS can start slskd."
        cfg.account_checked_at = datetime.now(timezone.utc).isoformat()
        settings_manager.save(settings)
        logger.warning("Soulseek companion is enabled but credentials are missing; managed slskd was not started.")
        return

    cfg.account_status = "checking"
    cfg.account_status_message = "Starting managed slskd in the background."
    cfg.account_checked_at = datetime.now(timezone.utc).isoformat()
    settings_manager.save(settings)

    try:
        logger.info("Soulseek companion enabled — starting managed slskd in the background after web startup...")
        ok = False
        # External/autofs/USB/NAS mounts can be visible but briefly refuse new
        # files during login or wake.  That is enough for slskd to abort even if
        # libtorrent later writes to the same folder.  Retry storage-related
        # startup failures instead of freezing Soulseek in a permanent error
        # state until the user restarts LJS.
        retry_delays = [0, 15, 30, 60, 120, 300]
        for attempt, delay in enumerate(retry_delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            ok = await slskd_manager.start(settings, login_timeout_seconds=120.0)
            slskd_manager.save_to_settings(settings)
            settings_manager.save(settings)
            if ok:
                logger.info(f"slskd managed runtime active at {slskd_manager.url} after attempt {attempt}")
                break
            message = str(cfg.account_status_message or slskd_manager.last_error or "")
            storage_related = (
                cfg.account_status == "storage_unavailable"
                or "not writeable" in message.lower()
                or "not writable" in message.lower()
                or "input/output error" in message.lower()
                or "read-only file system" in message.lower()
            )
            if not storage_related:
                break
            logger.warning(
                "slskd managed runtime is waiting for storage before retrying: "
                f"attempt={attempt}/{len(retry_delays)} status={cfg.account_status!r} message={message[:500]!r}"
            )
        if ok:
            pass
        elif cfg.account_status == "checking":
            logger.warning(
                "slskd is running or starting, but LJS has not confirmed Soulseek login yet. "
                "Continuing background validation after startup."
            )
            for _ in range(24):
                await asyncio.sleep(5)
                account = await slskd_manager.validate_account(settings, timeout_seconds=0)
                if account.get("status") == "ready":
                    logger.info("slskd managed runtime authenticated during background validation.")
                    break
                if account.get("status") == "auth_failed":
                    logger.warning("slskd background validation stopped because Soulseek rejected the credentials.")
                    break
        else:
            logger.warning(f"slskd managed runtime did not become ready: {cfg.account_status_message or slskd_manager.last_error}")
    except Exception as exc:
        cfg.account_status = "error"
        cfg.account_status_message = f"Managed slskd background startup failed: {exc}"
        cfg.account_checked_at = datetime.now(timezone.utc).isoformat()
        logger.exception(cfg.account_status_message)
    finally:
        try:
            settings_manager.save(settings)
        except Exception as exc:
            # Managed Soulseek is a best-effort background startup job.  A
            # settings persistence failure should be visible in logs, but it
            # must not crash the app after the UI has opened.
            logger.exception(f"Could not persist managed slskd startup status: {exc}")


async def _run_deferred_startup_jobs(
    *,
    supervisor: TaskSupervisor,
    scheduler: MediaScheduler,
    taste_profiler: TasteProfiler,
    settings_manager: SettingsManager,
    delay_seconds: float = 12.0,
) -> None:
    """Start expensive optional jobs after the UI has had time to become usable."""
    await asyncio.sleep(delay_seconds)

    # The scheduler loop itself is cheap and should be enabled early.  The
    # expensive jobs below are intentionally staggered and best-effort so a large
    # dirty library cannot make launch look dead.
    # Startup must be cheap.  The library scan is still non-blocking; when the
    # filesystem ledger is already fresh it now launches a repository-backed
    # missing-artwork repair pass so covers can appear without a hard page reload
    # or a second manual scan. Taste profiling still uses existing snapshots.
    supervisor.spawn_one_shot("library_boundary_cleanup", scheduler.cleanup_category_boundary_leaks())
    supervisor.spawn_one_shot(
        "category_lifecycle_startup_reconcile",
        scheduler.reconcile_lifecycle_ledgers(),
    )
    scheduler.request_library_scan(force=False, refresh_metadata=True, reason="startup")

    async def _profile_tastes() -> None:
        try:
            await taste_profiler.build_profile(
                settings_manager.settings.tracked_items.items,
                enrich_missing=False,
            )
        except Exception as e:
            logger.warning(f"Taste profiling failed (non-critical): {e}")

    supervisor.spawn_one_shot("taste_profiling_existing_metadata", _profile_tastes())

    # Air/suggestion jobs can be provider-heavy on large libraries.  They now
    # run from the scheduler cadence or explicit UI requests, not every launch.


async def main():
    """Main async entry point: initialize services and run concurrently."""
    logger.add("logs/ljs.log", rotation="10 MB")
    logger.info("Starting LJS: AI-Powered Torrent Automation")

    # --- Initialize Detailed Logging Subsystem ---
    detailed_logger = DetailedLoggingSubsystem()

    # --- Pre-initialize objects to ensure they exist in finally block ---
    db = Database()
    settings_manager = SettingsManager()
    supervisor = None
    downloader = None
    jackett_manager = JackettManager()
    slskd_manager = SlskdManager()
    searxng_manager = SearXNGManager()
    plex_client = None
    tmdb_client = None
    tvmaze_client = None
    browser_runtime = BrowserRuntime(BrowserDomainPolicy(), ChallengeDetector(), WebReader())
    comms_registry = create_registry()
    server = None
    scheduler = None

    try:
        # --- Initialize configuration ---
        settings = settings_manager.settings

        # Keep the OS login entry aligned if the user enabled it in Compass.
        # This is intentionally best-effort: a failure should never prevent LJS
        # from launching manually.
        if getattr(settings, "auto_start_at_login", False):
            result = AutoStartManager().set_enabled(True)
            if not result.get("ok"):
                logger.warning(f"Auto-start preference is enabled but OS registration failed: {result.get('message')}")

        # --- Initialize database ---
        await db.initialize()

        # --- Sync state between YAML and SQLite ---
        state_coordinator = StateCoordinator(settings_manager, db)
        await state_coordinator.sync_category_items()

        # --- Action event store (shared between AIAssistant and ActionGateway) ---
        action_event_store = ActionEventStore(db.raw_connection)

        # --- Initialize shared services ---
        quality_inferrer = SmartQualityInferrer()

        behavior_tracker = BehaviorTracker(db=db)
        behavior_recorder = BehaviorRecorder(db=db)
        preference_manager = PreferenceManager(
            db,
            behavior_tracker=behavior_tracker,
            quality_inferrer=quality_inferrer
        )
        await preference_manager.get_summary()  # Warm up

        blacklist = BlacklistManager(db)
        await blacklist.initialize()

        auth_service = AuthService(config=load_auth_config())

        # --- Graceful shutdown ---
        shutdown_event = asyncio.Event()

        def _on_critical_failure(name, error):
            logger.critical(f"Critical task '{name}' failed: {error}. Triggering global shutdown...")
            shutdown_event.set()

        # --- Initialize task supervisor (manages all background tasks) ---
        supervisor = TaskSupervisor(on_critical_failure=_on_critical_failure)

        # --- Initialize LLM provider manager ---
        llm_manager = LLMProviderManager()
        if settings.llm.active_provider:
            llm_manager.registry.set_active_provider(settings.llm.active_provider)
        if settings.llm.api_key:
            active_provider = settings.llm.active_provider or "openrouter"
            if not llm_manager.keys.has_keys(active_provider):
                llm_manager.keys.add_key(active_provider, settings.llm.api_key, label="imported")

        # --- Initialize task-aware LLM runtime ---
        task_llm_client = TaskLLMClient(
            manager=llm_manager,
            llm_config=settings.llm,
            llm_logger=detailed_logger.llm_logger,
        )

        # --- Initialize vector store ---
        vector_store = VectorStore(db=db, llm_client=task_llm_client, embedding_settings=settings.embeddings)
        try:
            await vector_store.initialize()
            logger.info(f"Vector store initialized (semantic search available, provider={vector_store.provider_label})")
        except Exception as e:
            logger.warning(f"Vector store init failed, semantic search disabled: {e}")

        if vector_store.is_initialized and getattr(settings.embeddings, "warmup_on_startup", True):
            supervisor.spawn_one_shot("embedding_model_warmup", vector_store.warm_up())

        # --- Initialize conversation manager ---
        conversation_manager = ConversationManager(
            db=db,
            vector_store=vector_store if vector_store.is_initialized else None,
            llm_client=task_llm_client,
        )

        release_group_tracker = ReleaseGroupTracker(db=db, blacklist_manager=blacklist)

        # --- Initialize category registry ---
        cat_registry = CategoryRegistry()
        cat_registry.register_defaults()
        storage_monitor = StorageMonitor(settings_manager=settings_manager, category_registry=cat_registry)

        # --- Initialize search ---
        providers = []
        fallback_search_providers = []

        if not settings.jackett_url:
            logger.info("Jackett not configured — attempting auto-install...")
            if jackett_manager.is_installed or await jackett_manager.ensure_installed():
                running = await jackett_manager.start()
                if running:
                    jackett_manager.save_to_settings(settings)
                    settings_manager.save(settings)
                    logger.info(f"Jackett auto-installed and running at {jackett_manager.url}")
                else:
                    logger.warning("Jackett installed but failed to start")
        else:
            if not jackett_manager.is_installed:
                if await jackett_manager.ensure_installed(force=True):
                    logger.info("Jackett re-installed successfully")
            await jackett_manager.start()

        if jackett_manager.is_running and jackett_manager.api_key:
            configured_count = 0
            try:
                configured_count = await jackett_manager.configured_indexer_count()
                if configured_count <= 0:
                    logger.warning(
                        "Jackett is running but has no configured indexers; repairing managed auth and configuring default open/public indexers."
                    )
                    repair = await jackett_manager.repair_admin_auth_and_restart()
                    logger.info(f"Jackett managed auth repair result: {repair}")
                    indexers = await jackett_manager.configure_default_indexers()
                    logger.info(f"Jackett default indexer configuration result: {indexers}")
                    configured_count = await jackett_manager.configured_indexer_count()
            except Exception as exc:
                logger.warning(f"Jackett first-run indexer readiness check failed: {exc}")
            from src.search.jackett import JackettSearch
            if configured_count > 0:
                # The manager may have adopted the API key actually accepted by
                # the running Jackett process during readiness probing. Persist it
                # now so the next launch does not reuse a rejected stale key.
                jackett_manager.save_to_settings(settings)
                settings_manager.save(settings)
                providers.append(JackettSearch(
                    jackett_manager.url,
                    jackett_manager.api_key,
                    configured_indexers=configured_count,
                    allow_filter_indexers=False,
                ))
                logger.info(f"Jackett search provider active (native JSON API, configured_indexers={configured_count})")
            else:
                logger.error(
                    "Jackett is running but still has zero configured indexers after managed repair. "
                    "Not registering Jackett as a torrent provider: full Jackett functionality requires real configured indexers. "
                    "Open Search settings diagnostics; LJS has logged the managed config paths and admin API probe state."
                )
        elif settings.jackett_url and settings.jackett_api_key:
            from src.search.jackett import JackettSearch
            providers.append(JackettSearch(settings.jackett_url, settings.jackett_api_key))
            logger.info("Jackett search provider active from saved settings")

        if getattr(settings.soulseek, "enabled", False) and getattr(settings.soulseek, "managed", True):
            if settings.soulseek.soulseek_username and settings.soulseek.soulseek_password:
                logger.info("Soulseek companion enabled — managed slskd will start after the web UI is reachable.")
            else:
                logger.warning("Soulseek companion is enabled but credentials are missing; slskd will not start until credentials are saved.")

        if settings.direct_scraper_fallback:
            logger.info(
                "Direct scraper fallback is enabled as an emergency provider set. "
                "Jackett remains the primary provider when available; direct scrapers are used only when no primary provider exists "
                "or the primary search is empty/degraded."
            )
            from src.search.btdigg import BTDiggSearch
            from src.search.search_1337x import Search1337x
            from src.search.torrentgalaxy import TorrentGalaxySearch
            from src.search.nyaa import NyaaSearch
            fallback_search_providers.extend([
                BTDiggSearch(),
                Search1337x(),
                TorrentGalaxySearch(),
                NyaaSearch(),
            ])

        if not providers and not fallback_search_providers:
            logger.warning(
                "No torrent search provider is configured. Install/start Jackett or enable direct scraper fallback."
            )

        aggregator = SearchAggregator(
            providers=providers,
            fallback_providers=fallback_search_providers,
            blacklist=blacklist,
            release_group_tracker=release_group_tracker,
            search_logger=detailed_logger.search_logger,
        )

        # --- Initialize category-owned external clients ---
        # Media integrations are no longer global settings.  The abstract
        # ``media`` category owns shared audiovisual services (TMDB, Trakt,
        # Plex, OpenSubtitles); TV/Movie inherit them and may override locally.
        if _category_service_enabled(settings, "media", "plex", default=False):
            plex_url = _category_service_value(settings, "plex", "url")
            plex_token = _category_service_value(settings, "plex", "token")
            if plex_url and plex_token:
                from src.integrations.plex import PlexClient
                plex_client = PlexClient(plex_url, plex_token)

        if _category_service_enabled(settings, "media", "tmdb", default=True):
            tmdb_api_key = _category_service_value(settings, "tmdb", "api_key", ("media", "movie", "tv"))
            if tmdb_api_key:
                try:
                    from src.integrations.tmdb import TMDBClient
                    tmdb_client = TMDBClient(tmdb_api_key)
                    logger.info("TMDB integration configured from category settings; client initialized.")
                except Exception as exc:
                    logger.warning(f"TMDB integration is configured, but startup client initialization failed: {exc}")
            else:
                logger.info("TMDB integration not configured; add the API key under Media defaults in Compass/setup.")

        if _category_service_enabled(settings, "tv", "tvmaze", default=True):
            from src.integrations.tvmaze import TVMazeClient
            tvmaze_client = TVMazeClient()
            logger.info("TVMaze metadata client initialized from TV category settings.")
        else:
            logger.info("TVMaze metadata client disabled by TV category settings.")

        # --- Initialize notifications ---
        notifications = NotificationService(db=db)

        # --- Initialize librarian early enough for recovered torrents. ---
        # Download recovery can immediately reach the ready/completion callbacks;
        # those callbacks need the librarian already wired so completed movies/TV
        # episodes do not remain stranded in the staging folder after startup.
        librarian = Librarian(settings, registry=cat_registry)

        # --- Initialize content cleanup ---
        content_cleanup = ContentCleanup(
            settings_manager=settings_manager,
            db=db,
            notifications=notifications,
            plex_client=plex_client,
            category_registry=cat_registry,
        )
        # --- Initialize assistant services ---
        intent_router = IntentRouter(llm_client=task_llm_client)
        from src.utils.circuit_breaker import CircuitBreaker
        torrent_selection = TorrentSelectionService(
            llm_client=task_llm_client,
            circuit_breaker=CircuitBreaker("torrent_selection", failure_threshold=3, recovery_seconds=30),
            release_group_tracker=release_group_tracker,
            category_registry=cat_registry,
            torrent_logger=detailed_logger.torrent_logger,
        )

        # --- Initialize download manager ---
        quality = settings.default_quality
        engine = TorrentEngine(settings.download_dir, settings.max_concurrent_downloads)
        queue = QueueManager(db, engine, settings.max_concurrent_downloads)
        bandwidth = BandwidthManager(settings_manager, engine)
        
        bencode_decoder = BencodeDecoder()
        torrent_resolver = TorrentUrlResolver(decoder=bencode_decoder)
        
        downloader = DownloadManager(DownloadDependencies(
            download_dir=settings.download_dir,
            db=db,
            supervisor=supervisor,
            engine=engine,
            queue=queue,
            bandwidth=bandwidth,
            settings_manager=settings_manager,
            max_concurrent=settings.max_concurrent_downloads,
            seed_ratio_target=quality.seed_ratio_target,
            seed_duration_hours=quality.seed_duration_hours,
            category_registry=cat_registry,
            torrent_resolver=torrent_resolver,
            blacklist=blacklist,
            storage_monitor=storage_monitor,
        ))
        # Wire ready/completion callbacks before recovery starts; recovered
        # torrents may complete immediately after their handles are restored.
        completion_handler = DownloadCompletionHandler(
            downloader=downloader,
            librarian=librarian,
            notifications=notifications,
            category_registry=cat_registry,
            settings=settings,
            download_dir=Path(settings.download_dir).resolve(),
            settings_manager=settings_manager,
            db=db,
        )
        downloader.set_ready_callback(completion_handler.on_download_ready)
        downloader.set_completion_callback(completion_handler.on_download_complete)
        await downloader.initialize()
        await downloader.apply_speed_limits(quality)
        await downloader.recover_downloads()
        # Repair any completed downloads whose live monitor or ready/completion
        # callback was missed. First promote stranded 100% active rows to the
        # seeding/ready state, then repair library paths for seeding/complete
        # rows. Both operations are idempotent and run before scheduler scans.
        await downloader.reconcile_completed_downloads()
        await completion_handler.reconcile_completed_imports()

        # --- Initialize category-owned artwork cache ---
        artwork_manager = CategoryArtworkManager()

        # --- Initialize recommendation engine ---
        trakt_client = None
        if _category_service_enabled(settings, "media", "trakt", default=True):
            from src.integrations.trakt_defaults import resolve_trakt_client_id
            trakt_client_id = resolve_trakt_client_id(settings)
            trakt_access_token = _category_service_value(settings, "trakt", "access_token")
            if trakt_client_id:
                from src.integrations.trakt import TraktClient
                trakt_client = TraktClient(trakt_client_id, access_token=trakt_access_token)

        metadata_enricher = TMDBMetadataEnricher(tmdb_client, settings_manager=settings_manager)
        recommender = RecommendationEngine(
            trakt_client=trakt_client,
            behavior_tracker=behavior_tracker,
            db=db,
            notifications=notifications,
            vector_store=vector_store if vector_store.is_initialized else None
        )

        # --- Initialize scanner ---
        scanner = LibraryScanner(cat_registry)
        scanner.set_llm_client(task_llm_client)

        # --- Initialize torrent racer ---
        torrent_racer = TorrentRacer(downloader=downloader, db=db, supervisor=supervisor)

        # --- Initialize category lifecycle/suggestion policy ledger ---
        lifecycle_engine = CategoryLifecycleEngine(db=db, category_registry=cat_registry)

        # --- Initialize suggestion compiler ---
        suggestion_compiler = SuggestionCompiler(
            db=db,
            tmdb_client=tmdb_client,
            tvmaze_client=tvmaze_client,
            settings_manager=settings_manager,
            category_registry=cat_registry,
            lifecycle_engine=lifecycle_engine,
        )

        # --- Initialize scheduler (with all deps including suggestion_compiler) ---
        scheduler = MediaScheduler(SchedulerDependencies(
            settings_manager=settings_manager,
            db=db,
            downloader=downloader,
            aggregator=aggregator,
            librarian=librarian,
            content_cleanup=content_cleanup,
            notifications=notifications,
            scanner=scanner,
            quality_inferrer=quality_inferrer,
            recommender=recommender,
            tvmaze=tvmaze_client,
            category_registry=cat_registry,
            torrent_selection=torrent_selection,
            suggestion_compiler=suggestion_compiler,
            lifecycle_engine=lifecycle_engine,
            torrent_racer=torrent_racer,
            metadata_enricher=metadata_enricher,
            artwork_manager=artwork_manager,
        ))
        completion_handler.set_library_reconciler(scheduler)

        # --- Initialize prompt scheduler (no assistant yet — breaks circular dep) ---
        prompt_scheduler = PromptScheduler(
            db=db,
            notifications=notifications,
        )

        # --- Initialize category-scoped taste profiler before assistant memory wiring ---
        taste_profiler = TasteProfiler(
            db=db,
            category_registry=cat_registry,
            metadata_context=TasteMetadataRuntimeContext(
                metadata_enricher=metadata_enricher,
                settings_manager=settings_manager,
                metadata_clients={"tmdb": tmdb_client, "tvmaze": tvmaze_client},
                artwork_manager=artwork_manager,
            ),
            vector_store=vector_store if vector_store.is_initialized else None,
        )

        # --- Initialize assistant with AgentDependencies (empty tool_registry for now) ---
        assistant = AIAssistant(AgentDependencies(
            llm_client=task_llm_client,
            settings=settings,
            preference_manager=preference_manager,
            conversation_manager=conversation_manager,
            intent_router=intent_router,
            behavior_tracker=behavior_tracker,
            behavior_recorder=behavior_recorder,
            torrent_selection_service=torrent_selection,
            search_aggregator=aggregator,
            release_group_tracker=release_group_tracker,
            database=db,
            downloader=downloader,
            settings_manager=settings_manager,
            action_event_store=action_event_store,
            chat_logger=detailed_logger.chat_logger,
            structured_logger=detailed_logger.structured_logger,
            category_registry=cat_registry,
            comms_registry=comms_registry,
            storage_monitor=storage_monitor,
            taste_profiler=taste_profiler,
        ))

        # --- Initialize tool catalog with domain ToolProviders ---
        providers = [
            DownloadToolProvider(
                downloader=downloader,
                scheduler=scheduler,
                database=db,
                search_aggregator=aggregator,
                settings_manager=settings_manager,
                category_registry=cat_registry,
            ),
            LibraryToolProvider(
                settings_manager=settings_manager,
                scheduler=scheduler,
                content_cleanup=content_cleanup,
                plex_client=plex_client,
                database=db,
                category_registry=cat_registry,
            ),
            PreferencesToolProvider(
                preference_manager=preference_manager,
                database=db,
                downloader=downloader,
                taste_profiler=taste_profiler,
            ),
            ResearchToolProvider(
                tmdb_client=tmdb_client,
                tvmaze_client=tvmaze_client,
                settings_manager=settings_manager,
                database=db,
            ),
            SchedulingToolProvider(
                prompt_scheduler=prompt_scheduler,
                scheduler=scheduler,
                settings_manager=settings_manager,
                supervisor=supervisor,
            ),
            WebToolProvider(
                web_reader=WebReader(),
                browser_runtime=browser_runtime,
                settings_manager=settings_manager,
                database=db,
                category_registry=cat_registry,
                prompt_scheduler=prompt_scheduler,
                searxng_manager=searxng_manager,
                llm_client=task_llm_client,
            ),
            CategoryToolProvider(
                category_registry=cat_registry,
                settings_manager=settings_manager,
                database=db,
                scheduler=scheduler,
                search_aggregator=aggregator,
                downloader=downloader,
                metadata_enricher=metadata_enricher,
                artwork_manager=artwork_manager,
            ),
            StorageToolProvider(storage_monitor=storage_monitor),
        ]
        tool_catalog = AgentToolCatalog(providers)
        tool_registry = tool_catalog.build_registry()

        # --- Set the full tool registry on the assistant ---
        assistant.set_tool_registry(tool_registry)

        # --- Wire the assistant back into PromptScheduler (breaks circular dep) ---
        prompt_scheduler.set_assistant(assistant)

        # --- Create app ---
        app = create_app(
            settings_manager=settings_manager,
            db=db,
            assistant=assistant,
            downloader=downloader,
            notifications=notifications,
            auth_service=auth_service,
            conversation_manager=conversation_manager,
            vector_store=vector_store if vector_store.is_initialized else None,
            behavior_tracker=behavior_tracker,
            behavior_recorder=behavior_recorder,
            suggestion_compiler=suggestion_compiler,
            recommender=recommender,
            release_group_tracker=release_group_tracker,
            category_registry=cat_registry,
            scheduler=scheduler,
            prompt_scheduler=prompt_scheduler,
            supervisor=supervisor,
            action_event_store=action_event_store,
            llm_manager=llm_manager,
            scanner=scanner,
            librarian=librarian,
            torrent_racer=torrent_racer,
            search_aggregator=aggregator,
            comms_registry=comms_registry,
            browser_runtime=browser_runtime,
            jackett_manager=jackett_manager,
            slskd_manager=slskd_manager,
            searxng_manager=searxng_manager,
            storage_monitor=storage_monitor,
            artwork_manager=artwork_manager,
            metadata_enricher=metadata_enricher,
            tvmaze_client=tvmaze_client,
        )

        # --- Start the web server first and wait for the socket to bind. ---
        # Heavy startup work such as library scans, metadata repair, artwork
        # refreshes, and suggestion compilation must never run before the UI/API
        # can answer health checks.  Otherwise the browser may see the machine as
        # unreachable even though the main process is alive.
        port = int(os.environ.get("LJS_PORT", settings.web_port))
        web_host = os.environ.get("LJS_HOST", "0.0.0.0")
        access_log_mode = os.environ.get("LJS_ACCESS_LOGS", "quiet").strip().lower()
        access_logs_enabled = access_log_mode not in {"0", "false", "off", "none"}
        config = uvicorn.Config(
            app,
            host=web_host,
            port=port,
            log_level="info",
            access_log=access_logs_enabled,
        )
        if access_logs_enabled and access_log_mode not in {"1", "true", "on", "verbose", "all"}:
            # The browser polls /api/system/logs and /api/storage/status while
            # the dashboard is open.  Keep uvicorn access logging useful by
            # hiding those successful heartbeat reads instead of burying real
            # warnings/errors under hundreds of identical 200 OK lines.
            install_quiet_polling_access_log_filter()
            logger.info(
                "Uvicorn access logs are quiet for polling endpoints "
                "(set LJS_ACCESS_LOGS=verbose to show every request)."
            )
        elif not access_logs_enabled:
            logger.info("Uvicorn access logs disabled by LJS_ACCESS_LOGS.")
        server = uvicorn.Server(config)
        web_task = supervisor.spawn_restartable("web_server", server.serve, TaskCriticality.CRITICAL)
        await _wait_for_web_server_ready(web_host, port, web_task)

        access_urls = _format_access_urls(web_host, port)
        logger.info("LJS web UI answered /api/live. Try: " + ", ".join(access_urls))
        if (
            getattr(settings.web_search, "enabled", True)
            and getattr(settings.web_search, "provider", "") == "searxng"
            and getattr(settings.web_search, "mode", "managed") == "managed"
        ):
            supervisor.spawn_one_shot(
                "searxng_managed_startup",
                _start_managed_searxng_after_ui(settings_manager, searxng_manager),
            )
        if getattr(settings.soulseek, "enabled", False) and getattr(settings.soulseek, "managed", True):
            supervisor.spawn_one_shot(
                "soulseek_managed_startup",
                _start_managed_soulseek_after_ui(settings_manager, slskd_manager),
            )
        if web_host in {"127.0.0.1", "localhost"}:
            logger.warning(
                "LJS_HOST is bound to localhost only; other devices on the LAN will not reach it. "
                "Unset LJS_HOST or set LJS_HOST=0.0.0.0 for LAN access."
            )

        # --- Start background services only after web readiness. ---
        scheduler.set_event_bus(getattr(app.state.deps, "event_bus", None))
        await scheduler.initialize()
        if getattr(settings.soulseek, "enabled", False):
            slskd_import_monitor = SlskdImportMonitor(
                settings_manager=settings_manager,
                database=db,
                category_registry=cat_registry,
                completion_handler=completion_handler,
            )
            supervisor.spawn_restartable(
                "slskd_import_monitor",
                slskd_import_monitor.run_forever,
                TaskCriticality.IMPORTANT,
            )
        supervisor.spawn_restartable(
            "event_loop_watchdog",
            lambda: _event_loop_watchdog(),
            TaskCriticality.IMPORTANT,
        )
        supervisor.spawn_one_shot(
            "deferred_startup_jobs",
            _run_deferred_startup_jobs(
                supervisor=supervisor,
                scheduler=scheduler,
                taste_profiler=taste_profiler,
                settings_manager=settings_manager,
            ),
        )

        # RSS monitor and chat bridges are useful, but they must not delay web
        # availability.  Start RSS with an empty feed set and let the category
        # watch-policy sync populate it.  The generic composition root does not
        # decide which TV/movie/music items are active enough to monitor.
        if settings.jackett_url and settings.jackett_api_key:
            from src.search.rss_monitor import RSSMonitor

            async def _on_rss_match(name: str, result, unit_label: str | None = None):
                """Turn an RSS match into a category-owned release event."""
                item = next((tracked for tracked in settings_manager.settings.tracked_items if tracked.key.lower() == name.lower()), None)
                if item:
                    candidate_title = getattr(result, "title", None)
                    if callable(candidate_title):
                        candidate_title = candidate_title()
                    if candidate_title is None:
                        candidate_title = str(result) if result is not None else ""
                    logger.info(f'RSS release event: {name} {unit_label or ""} candidate={candidate_title!r}')
                    await scheduler.handle_release_event(item, unit_label=unit_label, source_result=result, trigger="rss")
                else:
                    logger.warning(f"RSS matched '{name}' but it was not found in tracked items.")

            rss_monitor = RSSMonitor(
                feed_urls=[],
                item_names=[],
                supervisor=supervisor,
                on_match=_on_rss_match,
                category_registry=cat_registry,
                item_categories={},
                feed_targets={},
                max_feeds_per_cycle=8,
            )
            scheduler.set_rss_monitor(rss_monitor)
            rss_monitor.start()
            supervisor.spawn_one_shot("sync_category_watch_policies", scheduler.sync_all_category_watch_policies(reason="startup"))

        async def _start_comms_bridges() -> None:
            for bridge_info in comms_registry.list_bridges():
                await comms_registry.start_bridge(bridge_info["id"], settings, assistant, notifications)

        supervisor.spawn_one_shot("start_comms_bridges", _start_comms_bridges())

        logger.info(f"LJS Services started on port {port}. Waiting for instructions, Captain!")

        _shutting_down = False

        def _signal_handler(*args):
            nonlocal _shutting_down
            if _shutting_down:
                return
            _shutting_down = True
            logger.info("Shutdown signal received. Dropping anchor...")
            shutdown_event.set()

        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError: pass

        await shutdown_event.wait()

    finally:
        logger.info("Shutting down LJS services...")
        
        # 1. Signal web server to stop immediately (graceful exit)
        if 'server' in locals() and server:
            logger.info("Signaling web server to drop anchor...")
            server.should_exit = True
            
            # Remove from supervisor so it doesn't get cancelled prematurely
            if supervisor:
                # We can't easily "remove" without cancelling in the current supervisor API,
                # but we can check if it's the one we're waiting for.
                pass

            # Wait for server task to finish if it's supervised
            if supervisor and supervisor.is_alive("web_server"):
                logger.info("Waiting for web server to dock...")
                for _ in range(100): # Max 10 seconds
                    if not supervisor.is_alive("web_server"):
                        break
                    await asyncio.sleep(0.1)
                
                # If still alive, we might have to let the supervisor cancel it in step 2
                if supervisor.is_alive("web_server"):
                    logger.warning("Web server is taking too long to dock, will be forced.")
            else:
                await asyncio.sleep(0.5)

        # 2. Shutdown services in sequence with individual error handling
        # This ensures one hanging service doesn't block the rest from cleaning up.
        
        if scheduler:
            try:
                logger.info("Stopping scheduler...")
                scheduler.stop()
            except Exception as e:
                logger.error(f"Error stopping scheduler: {e}")

        if supervisor:
            try:
                logger.info("Shutting down task supervisor...")
                # We wait for supervised tasks with a timeout
                await asyncio.wait_for(supervisor.shutdown(), timeout=15)
            except asyncio.TimeoutError:
                logger.warning("Task supervisor shutdown timed out after 15s")
            except Exception as e:
                logger.error(f"Error during supervisor shutdown: {e}")

        if downloader:
            try:
                logger.info("Closing downloader...")
                await asyncio.wait_for(downloader.close(), timeout=15)
            except asyncio.TimeoutError:
                logger.warning("Downloader shutdown timed out after 15s")
            except Exception as e:
                logger.error(f"Error during downloader shutdown: {e}")

        if jackett_manager:
            try:
                logger.info("Stopping Jackett manager...")
                await asyncio.wait_for(jackett_manager.stop(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("Jackett manager shutdown timed out after 10s")
            except Exception as e:
                logger.error(f"Error stopping Jackett manager: {e}")

        if slskd_manager:
            try:
                logger.info("Stopping slskd manager...")
                await asyncio.wait_for(slskd_manager.stop(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("slskd manager shutdown timed out after 10s")
            except Exception as e:
                logger.error(f"Error stopping slskd manager: {e}")

        if db:
            try:
                logger.info("Closing database...")
                await asyncio.wait_for(db.close(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("Database shutdown timed out after 10s")
            except Exception as e:
                logger.error(f"Error closing database: {e}")
        
        # Cleanup other optional clients
        for name, closer in [
            ("plex", plex_client.close if 'plex_client' in locals() and plex_client else None),
            ("tmdb", tmdb_client.close if 'tmdb_client' in locals() and tmdb_client else None),
            ("browser", browser_runtime.close if 'browser_runtime' in locals() and browser_runtime else None),
            ("comms", comms_registry.stop_all if 'comms_registry' in locals() and comms_registry else None),
        ]:
            if closer:
                try:
                    logger.info(f"Closing {name} client...")
                    await asyncio.wait_for(closer(), timeout=10)
                except asyncio.TimeoutError:
                    logger.warning(f"Closing {name} client timed out after 10s")
                except Exception as e:
                    logger.warning(f"Shutdown: {name} cleanup failed: {e}")
        
        logger.info("Fair winds, Captain! LJS has shut down.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt. Shutting down LJS...")
    except SystemExit:
        pass
    except Exception as exc:
        logger.exception(f"Fatal LJS startup/runtime error: {exc}")
        raise
