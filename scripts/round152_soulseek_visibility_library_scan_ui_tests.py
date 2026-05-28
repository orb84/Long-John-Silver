"""Round 152 regression checks for Soulseek visibility, scan throttling, and library UI."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def test_soulseek_transfers_are_visible() -> None:
    client = read("src/integrations/slskd_client.py")
    view = read("src/integrations/slskd_transfer_view.py")
    router = read("src/web/routers/downloads.py")
    tools = read("src/ai/tools/downloads.py")
    list_support = read("src/ai/tools/download_list_support.py")
    soulseek = read("src/ai/tools/soulseek.py")
    require("def download_transfers" in client, "slskd client should expose transfer listing")
    require("SHADOW_PREF_KEY" in view and "add_shadow_transfer" in view, "Soulseek queueing should create immediate UI shadow rows")
    require("SlskdTransferReadModel" in router and "rows.extend(await self._soulseek_rows" in router, "Downloads API should include Soulseek rows")
    require("DownloadListReportService(self._downloader, self._settings_manager, self._database)" in tools and "SlskdTransferReadModel" in list_support, "list_downloads should include Soulseek rows")
    require("visible_download" in soulseek and "mirrored in the LJS Downloads view" in soulseek, "enqueue tool should expose visible Soulseek transfer rows")


def test_metadata_repair_no_longer_forces_recent_startup_scan() -> None:
    scheduler = read("src/core/scheduler.py")
    settings = read("src/core/domain_models/settings.py")
    require("last_media_metadata_repair_at" in settings, "settings should persist metadata repair throttle")
    require("recent library scan exists; no startup full scan needed" in scheduler, "metadata repair should defer when a recent scan exists")
    require("7 * SECONDS_PER_DAY" in scheduler, "metadata repair should be weekly-throttled")


def test_library_sections_and_view_modes() -> None:
    panel = read("src/web/static/js/components/bootyPanel.js")
    require("this._categories.forEach" in panel, "BootyPanel should render all category sections, not only categories with items")
    require("No ${displayName} items yet" in panel, "empty categories should be visible with an explanatory message")
    require("_setCategoryViewMode" in panel and "category-view-${viewMode}" in panel, "category sections should support list/icon view modes")
    require("_categoryIcon" in panel and "fa-music" in panel and "fa-book-open" in panel, "library category icons should include non-TV/movie categories")


def test_soulseek_ui_actions_are_not_torrent_actions() -> None:
    ui = read("src/web/static/js/components/downloadManagerUI.js")
    require("const isSoulseek" in ui, "download UI should recognize Soulseek rows")
    require("Clear Soulseek transfer" in ui and "Cancel Soulseek transfer" in ui, "Soulseek rows should expose slskd clear/cancel controls, not disabled torrent placeholders")
    require("dl.status === 'paused'" in ui and "} else if (isPending)" in ui, "torrent pause/resume controls should remain outside the Soulseek action branch")
    require("Soulseek is peer-to-peer through one remote user" in ui, "Soulseek cards should explain non-swarm semantics")


def main() -> None:
    test_soulseek_transfers_are_visible()
    test_metadata_repair_no_longer_forces_recent_startup_scan()
    test_library_sections_and_view_modes()
    test_soulseek_ui_actions_are_not_torrent_actions()
    print("Round 152 Soulseek visibility / scan / library UI tests passed")


if __name__ == "__main__":
    main()
