#!/usr/bin/env python3
"""Round 15 static regression checks for LLM-facing download control."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
downloads = (root / "src/ai/tools/downloads.py").read_text()
download_control = (root / "src/ai/tools/download_control.py").read_text()
download_support = (root / "src/ai/tools/download_support.py").read_text()
policy = (root / "src/ai/tool_policy.py").read_text()
prompt = (root / "src/ai/prompt_builder.py").read_text()
router = (root / "src/ai/intent_router.py").read_text()
tests = (root / "tests/test_agent_tools.py").read_text()

assert "class ManageDownloadsTool" in download_control, "LLM needs one generic download-control tool"
assert "name = \"manage_downloads\"" in download_control, "manage_downloads tool must be registered by name"
assert "move_before" in download_control and "move_after" in download_control and "move_top" in download_control, "Queue reordering actions must be exposed"
assert "confirmation_required" in download_control and "action == \"cancel\"" in download_control, "Cancel operations must be confirmation-gated"
assert "serialize" in download_support and "queue_position" in download_support and "health_state" in download_support, "Download reports must expose queue and health state"
assert "ManageDownloadsTool(downloader=self._downloader)" in downloads, "DownloadToolProvider must register manage_downloads"
assert "\"manage_downloads\"" in policy, "Tool policy must allow manage_downloads for download intent"
assert "list_downloads" in prompt and "manage_downloads" in prompt and "download report" in prompt, "Download prompt must teach report/control flow"
assert r"\bpause\b" in router and r"\bresume\b" in router and r"\bcancel\b" in router, "Intent router must classify queue-control commands as DOWNLOAD"
assert "test_manage_downloads_pauses_matched_download" in tests, "Agent tool tests must cover manage_downloads mutations"
print("round15 audit passed")
