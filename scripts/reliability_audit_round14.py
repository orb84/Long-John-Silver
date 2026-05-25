#!/usr/bin/env python3
"""Round 14 static regression checks for Hold filtering and release title propagation."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
ui = (root / "src/web/static/js/components/downloadManagerUI.js").read_text()
hold = (root / "src/web/static/js/components/holdPanel.js").read_text()
scheduler = (root / "src/core/scheduler.py").read_text()
download_tool = (root / "src/ai/tools/downloads.py").read_text()
queue_support = (root / "src/ai/tools/queue_download_support.py").read_text()
css = (root / "src/web/static/css/style.css").read_text()

assert "_statusGroup(dl)" in ui, "Hold UI must group downloads by normalized status"
assert "if (filterVal === 'downloading') return group === 'downloading';" in ui, "Downloading filter must not include queued/paused items"
assert "download-state-section" in ui and "download-state-header" in ui, "All view must render separated status groups"
assert "dl-torrent-title" in ui, "Cards must display a full torrent/release title line"
assert "dl.torrent_title" in ui and "Torrent release name not available yet" in ui, "Title fallback must not repeat item name"
assert "data-filter': 'queued'" in hold and "data-filter': 'paused'" in hold, "Hold filters must expose queued and paused states"
assert "torrent_title: str = \"\"" in scheduler, "Scheduler queue_download must accept torrent_title"
assert "torrent_title=torrent_title" in scheduler, "Scheduler must persist torrent_title via downloader.add_magnet"
assert ("torrent_title=candidate.get(\"title\")" in download_tool or "\"torrent_title\": candidate.get(\"title\")" in queue_support), "Candidate queues must preserve the full torrent title"
assert ".download-card.dl-state-downloading" in css and ".download-card.dl-state-paused" in css, "Status groups need distinct styling"
print("round14 audit passed")
