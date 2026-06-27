"""
Torrent racer for LJS.

Manages competing downloads for the same content. When a download has
few seeders or stalls, proactively starts alternative magnets from the
search results. When a winner emerges (progress threshold reached or
download completes), cancels the losers and cleans up their files.

Thresholds:
- MIN_SEEDERS_TO_START: If the primary result has fewer seeders than this,
  start alternatives immediately rather than waiting for a stall.
- RACE_WIN_PROGRESS: The progress percentage at which a download is declared
  the winner and all competitors are cancelled.
- STALL_SEEDERS_THRESHOLD: If download rate drops below this with fewer
  seeders than MIN_SEEDERS, the download is likely dead and alternatives
  should be started.
"""

import asyncio
from typing import TYPE_CHECKING
from loguru import logger
from src.core.task_supervisor import TaskSupervisor
from src.core.models import DownloadPriority, TaskCriticality
from src.core.security.path_policy import SafePathResolver, SecurityPolicyError

if TYPE_CHECKING:
    from src.core.downloader import DownloadManager
    from src.core.database import Database


MIN_SEEDERS_TO_START = 3
RACE_WIN_PROGRESS = 0.70
RACE_CHECK_INTERVAL_SECONDS = 15
MAX_CONCURRENT_RACERS = 3


class TorrentRacer:
    """Manages redundant downloads for low-seeder torrents."""

    def __init__(self, downloader: "DownloadManager", db: "Database", supervisor: TaskSupervisor) -> None:
        """Initialize the racer.

        Args:
            downloader: The DownloadManager instance.
            db: The Database instance.
            supervisor: TaskSupervisor.
        """
        self._downloader = downloader
        self._db = db
        self._supervisor = supervisor
        self._active_races: dict[str, dict] = {}

    async def start_race(
        self,
        item_name: str,
        primary_magnet: str,
        primary_title: str,
        primary_seeders: int | None = None,
        alternatives: list[dict] | None = None,
        selective_episodes: list[int] | None = None,
        selective_season: int | None = None,
        base_priority: DownloadPriority = DownloadPriority.NORMAL,
    ) -> str | None:
        """Start a redundant download race for a low-seeder torrent.

        If the primary result has enough seeders (>= MIN_SEEDERS_TO_START),
        just starts it normally without racing. If seeders are low, also starts
        up to MAX_CONCURRENT_RACERS alternatives.

        Args:
            item_name: Human-readable show name.
            primary_magnet: The LLM-selected magnet URI.
            primary_title: Title of the primary result.
            primary_seeders: Seeder count of the primary result. If None,
                race is started for all alternatives without seeding check.
            alternatives: List of dicts with 'magnet', 'title', 'seeders' keys.
            selective_episodes: Episode numbers for selective download.
            selective_season: Season number for selective download.
            base_priority: Priority to use for the primary download. When racing,
                the primary gets boosted to HIGH for bandwidth preference.

        Returns:
            The download ID of the primary result, or None if no magnet.
        """
        if not primary_magnet:
            return None

        # If seeders are good enough, just download normally — no race needed
        if primary_seeders is not None and primary_seeders >= MIN_SEEDERS_TO_START:
            logger.info(
                f"Race: {item_name} has {primary_seeders} seeders (>= {MIN_SEEDERS_TO_START}), "
                f"no race needed"
            )
            item = await self._downloader.add_magnet(
                primary_magnet, item_name=item_name,
                torrent_title=primary_title,
                priority=base_priority,
                selective_episodes=selective_episodes,
                selective_season=selective_season,
            )
            return item.id

        # Low seeders — start the primary as HIGH priority and race alternatives
        logger.info(
            f"Race: {item_name} has {primary_seeders} seeders, starting race "
            f"with {len(alternatives or [])} alternatives"
        )
        primary_item = await self._downloader.add_magnet(
            primary_magnet, item_name=item_name,
            torrent_title=primary_title,
            priority=DownloadPriority.HIGH,
            selective_episodes=selective_episodes,
            selective_season=selective_season,
        )
        primary_id = primary_item.id

        race_id = f"race_{primary_id}"
        competitor_ids = []

        if alternatives:
            # Start up to MAX_CONCURRENT_RACERS alternatives as LOW priority
            # so they queue behind the primary but still download
            for alt in alternatives[:MAX_CONCURRENT_RACERS - 1]:
                alt_magnet = alt.get("magnet")
                alt_title = alt.get("title", "unknown")
                if not alt_magnet or alt_magnet == primary_magnet:
                    continue
                try:
                    alt_item = await self._downloader.add_magnet(
                        alt_magnet, item_name=item_name,
                        torrent_title=alt_title,
                        priority=DownloadPriority.LOW,
                        selective_episodes=selective_episodes,
                        selective_season=selective_season,
                    )
                    competitor_ids.append({
                        "id": alt_item.id,
                        "magnet": alt_magnet,
                        "title": alt_title,
                    })
                    logger.info(f"Race: added alternative '{alt_title}' (id={alt_item.id})")
                except Exception as e:
                    logger.warning(f"Race: failed to add alternative '{alt_title}': {e}")

        # Track the race
        self._active_races[primary_id] = {
            "item_name": item_name,
            "primary_id": primary_id,
            "primary_magnet": primary_magnet,
            "competitors": competitor_ids,
            "winner_id": None,
        }

        # Start a background monitor that watches for a winner
        self._supervisor.spawn_restartable(
            f"race_monitor_{primary_id}",
            lambda: self._monitor_race(primary_id),
            TaskCriticality.IMPORTANT,
        )

        return primary_id

    async def _monitor_race(self, primary_id: str) -> None:
        """Background task that monitors a race and cancels losers.

        Polls all racers every RACE_CHECK_INTERVAL_SECONDS. When any
        download reaches RACE_WIN_PROGRESS, declares it the winner and
        cancels all others.
        """
        race = self._active_races.get(primary_id)
        if not race:
            return

        item_name = race["item_name"]
        competitor_ids = [c["id"] for c in race["competitors"]]
        all_ids = [primary_id] + competitor_ids

        try:
            while True:
                await asyncio.sleep(RACE_CHECK_INTERVAL_SECONDS)

                winner = None
                dead_ids = []

                for dl_id in all_ids:
                    item = await self._db.downloads.get_download(dl_id)
                    if not item:
                        dead_ids.append(dl_id)
                        continue

                    if item.status.value in ("complete", "seeding"):
                        winner = dl_id
                        break

                    if item.progress >= RACE_WIN_PROGRESS:
                        winner = dl_id
                        break

                    if item.status.value in ("failed", "cancelled"):
                        dead_ids.append(dl_id)

                if winner:
                    race["winner_id"] = winner
                    logger.info(
                        f"Race won by {winner} for '{item_name}' "
                        f"(progress threshold {RACE_WIN_PROGRESS:.0%} reached)"
                    )
                    # Cancel all non-winners
                    for dl_id in all_ids:
                        if dl_id != winner and dl_id not in dead_ids:
                            logger.info(f"Race: cancelling loser {dl_id} for '{item_name}'")
                            await self._cancel_and_cleanup(dl_id, item_name)
                    break

                # Remove dead downloads from tracking
                for dl_id in dead_ids:
                    if dl_id in all_ids:
                        all_ids.remove(dl_id)

                # If all downloads are dead, give up
                if not all_ids:
                    logger.warning(f"Race: all downloads failed for '{item_name}'")
                    break

        except asyncio.CancelledError:
            logger.debug(f"Race monitor cancelled for '{item_name}'")
        except Exception as e:
            logger.error(f"Race monitor error for '{item_name}': {e}")
        finally:
            self._active_races.pop(primary_id, None)

    async def _cancel_and_cleanup(self, download_id: str, item_name: str) -> None:
        """Cancel a download and attempt to clean up its partial files.

        Args:
            download_id: The download ID to cancel.
            item_name: Item name for logging.
        """
        try:
            await self._downloader.cancel_download(download_id)
            logger.info(f"Race: cancelled download {download_id} for '{item_name}'")
        except Exception as e:
            logger.warning(f"Race: failed to cancel download {download_id}: {e}")

        # Clean up partial files from disk
        try:
            item = await self._db.downloads.get_download(download_id)
            if item and item.file_path:
                from pathlib import Path
                file_path = Path(item.file_path)
                download_root = Path(getattr(self._downloader, "_download_dir", file_path.parent)).resolve()
                resolver = SafePathResolver(allowed_roots=[download_root])
                if file_path.exists():
                    resolver.safe_unlink(file_path, purpose="torrent_race.cleanup", move_to_trash=False)
                    logger.info(f"Race: deleted partial file {file_path}")
        except Exception as e:
            logger.debug(f"Race: could not clean up files for {download_id}: {e}")

    def get_race_status(self, primary_id: str) -> dict | None:
        """Get the status of an active race.

        Args:
            primary_id: The primary download ID.

        Returns:
            Race status dict with item_name, primary_id, competitors, winner_id,
            or None if no active race for that ID.
        """
        return self._active_races.get(primary_id)

    @property
    def active_race_count(self) -> int:
        """Number of currently active races."""
        return len(self._active_races)