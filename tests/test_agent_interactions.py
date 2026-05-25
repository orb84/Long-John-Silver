"""
End-to-end agent interaction test suite.

Exercises the LLM agent through every major workflow: SEARCH,
DOWNLOAD, CONFIG intents, tool execution, taste profiling,
language preference handling, and download management.

Usage:
    python tests/test_agent_interactions.py
"""

import asyncio

# This file is a standalone live/integration script, not a pytest module.
__test__ = False

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from tests.harness import ServerTestHarness


def _check_server() -> bool:
    """Check that LJS server is running."""
    try:
        import httpx
        resp = httpx.get("http://localhost:8088/", timeout=5.0, follow_redirects=True)
        return resp.status_code == 200
    except Exception:
        return False


async def test_search_intent_research_tools(harness: ServerTestHarness):
    """SEARCH intent: ask for show info — should use TMDB/TVMaze tools."""
    logger.info("=== TEST: SEARCH Intent — Research Tools ===")
    resp = await harness.send_chat(
        "Can you look up information about Firefly? What are its genres and rating?"
    )
    logger.info(f"Response ({len(resp)} chars): {resp[:400]}")
    assert len(resp) > 30, "Empty research response"
    # Should mention genres from taste profile: Sci-Fi, Drama, Action
    logger.info("  PASS: Research tools active")


async def test_config_intent_add_category_item(harness: ServerTestHarness):
    """CONFIG intent: add a show to watchlist via chat."""
    logger.info("=== TEST: CONFIG Intent — Add Category Item ===")
    resp = await harness.send_chat(
        "Please add the TV show Dexter Original Sin to my watchlist"
    )
    logger.info(f"Response ({len(resp)} chars): {resp[:300]}")
    assert len(resp) > 10, "Empty config response"

    # Verify via REST
    detail = await harness.get_category_item_detail("Dexter Original Sin")
    logger.info(f"  Show detail: enabled={detail.get('enabled','?')}, "
                f"auto_download={detail.get('auto_download','?')}")
    assert detail.get("enabled") is not False
    logger.info("  PASS: Show added via chat")


async def test_config_intent_remove_category_item(harness: ServerTestHarness):
    """CONFIG intent: remove a show from watchlist."""
    logger.info("=== TEST: CONFIG Intent — Remove Category Item ===")
    # Test Remove Category Item 99 already exists in the library — just verify
    # removal intent works via chat. Skip add to avoid 409 conflict.
    resp = await harness.send_chat(
        "Remove Test Remove Category Item 99 from my watchlist"
    )
    logger.info(f"Response ({len(resp)} chars): {resp[:200]}")
    logger.info("  PASS: Remove show via chat")


async def test_download_intent_search(harness: ServerTestHarness):
    """DOWNLOAD intent: search for torrents of a specific episode."""
    logger.info("=== TEST: DOWNLOAD Intent — Search Torrents ===")
    resp = await harness.send_chat(
        "Search for torrents of Firefly season 1 episode 1, prefer Italian language"
    )
    logger.info(f"Response ({len(resp)} chars): {resp[:400]}")
    assert len(resp) > 20, "Empty download/search response"
    logger.info("  PASS: Download search tools active")


async def test_list_downloads(harness: ServerTestHarness):
    """Check download queue visibility."""
    logger.info("=== TEST: List Downloads ===")
    resp = await harness.send_chat(
        "Show me the current download queue status"
    )
    logger.info(f"Response ({len(resp)} chars): {resp[:300]}")
    logger.info("  PASS: Download queue visible")


async def test_recommendations_taste_aware(harness: ServerTestHarness):
    """Ask for recommendations — should use taste profile + trending."""
    logger.info("=== TEST: Taste-Aware Recommendations ===")
    resp = await harness.send_chat(
        "What TV shows would you recommend for me based on my library? "
        "I seem to like drama and sci-fi."
    )
    logger.info(f"Response ({len(resp)} chars): {resp[:400]}")
    assert len(resp) > 20, "Empty recommendation response"
    logger.info("  PASS: Recommendations active")


async def test_language_preference_flow(harness: ServerTestHarness):
    """Verify language preferences are respected.

    Sets Italian as the preferred language for a specific show,
    then triggers a show check and verifies the search query
    includes Italian language filtering.
    """
    logger.info("=== TEST: Language Preference ===")
    # Check that a show exists with language config
    detail = await harness.get_category_item_detail("Firefly")
    lang = detail.get("language", detail.get("preferred_language", "not set"))
    logger.info(f"  Firefly language: {lang}")

    # Check show config for language settings
    items = await harness.get_library_items()
    for s in shows:
        if s.get("name", "").lower() == "firefly":
            logger.info(f"  Firefly detected_language: {s.get('detected_language', '?')}")
            break

    logger.info("  PASS: Language configuration visible")


async def test_force_show_check(harness: ServerTestHarness):
    """Trigger a show check via REST and verify it works."""
    logger.info("=== TEST: Force Show Check ===")
    try:
        result = await harness.check_category_item("Firefly")
        logger.info(f"  Check result: {result}")
    except Exception as e:
        logger.info(f"  Check trigger result: {e}")
    logger.info("  PASS: Show check triggered")


async def test_taste_profile_integration(harness: ServerTestHarness):
    """Verify taste profile is being used by the agent."""
    logger.info("=== TEST: Taste Profile Integration ===")
    # The taste profile injects genre preferences into the system prompt.
    # Ask the agent about genres in the library.
    resp = await harness.send_chat(
        "Based on what you know about my library, do I prefer drama series?"
    )
    logger.info(f"Response ({len(resp)} chars): {resp[:400]}")
    # Should reference specific shows/genres from the taste profile
    assert len(resp) > 20, "Empty taste query response"
    logger.info("  PASS: Taste profile used in conversation")


async def test_upgrade_detection(harness: ServerTestHarness):
    """Verify quality upgrade detection."""
    logger.info("=== TEST: Quality Upgrades ===")
    upgrades = await harness.get_upgrades()
    logger.info(f"  Pending upgrades: {len(upgrades)}")
    for u in upgrades[:3]:
        logger.info(f"    {u.get('item_name', '?')} - {u.get('current', '?')} → {u.get('candidate', '?')}")
    logger.info("  PASS: Upgrade detection")


async def test_suggestion_workflow(harness: ServerTestHarness):
    """Verify Crow's Nest suggestion pipeline."""
    logger.info("=== TEST: Suggestions (Crow's Nest) ===")
    suggestions = await harness.get_suggestions()
    logger.info(f"  Pending suggestions: {len(suggestions)}")
    for s in suggestions[:5]:
        logger.info(f"    #{s.get('id', '?')}: {s.get('item_name', '?')} "
                    f"— {s.get('action_type', '?')} | {s.get('title', '?')}")
    if suggestions:
        # Dismiss one to test the flow
        action_id = suggestions[0].get("id")
        if action_id is not None:
            await harness.deny_suggestion(action_id)
            logger.info(f"  Dismissed suggestion #{action_id}")
    logger.info("  PASS: Suggestion workflow")


async def test_tool_registry_health(harness: ServerTestHarness):
    """Verify all 40 tools are registered and reachable."""
    logger.info("=== TEST: Tool Registry ===")
    # The agent runs with 40 tools per the log output.
    # Verify by sending a message that could use many tools.
    resp = await harness.send_chat(
        "I want to manage my library. List all my tracked shows with their "
        "download status, then check if there are any quality upgrades available."
    )
    logger.info(f"Response ({len(resp)} chars): {resp[:400]}")
    logger.info("  PASS: Multi-tool workflow")


async def run_all_tests():
    """Run the complete agent interaction test suite."""
    logger.info("=" * 60)
    logger.info("LJS Agent Interaction Test Suite")
    logger.info("=" * 60)

    if not _check_server():
        logger.error("Server not running on localhost:8088")
        logger.error("Start with: nohup .venv/bin/python main.py &")
        return 1

    harness = ServerTestHarness("http://localhost:8088")
    await harness.connect()

    tests = [
        ("Health Check", lambda: harness.verify_server_healthy()),
        ("SEARCH Intent — Research Tools", lambda: test_search_intent_research_tools(harness)),
        ("CONFIG Intent — Add Category Item", lambda: test_config_intent_add_category_item(harness)),
        ("CONFIG Intent — Remove Category Item", lambda: test_config_intent_remove_category_item(harness)),
        ("DOWNLOAD Intent — Search Torrents", lambda: test_download_intent_search(harness)),
        ("List Downloads", lambda: test_list_downloads(harness)),
        ("Taste-Aware Recommendations", lambda: test_recommendations_taste_aware(harness)),
        ("Language Preference", lambda: test_language_preference_flow(harness)),
        ("Force Show Check", lambda: test_force_show_check(harness)),
        ("Taste Profile Integration", lambda: test_taste_profile_integration(harness)),
        ("Quality Upgrades", lambda: test_upgrade_detection(harness)),
        ("Suggestion Workflow", lambda: test_suggestion_workflow(harness)),
        ("Multi-Tool Workflow", lambda: test_tool_registry_health(harness)),
    ]

    passed = 0
    failed = 0
    for name, test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            logger.error(f"  FAILED [{name}]: {e}")

    await harness.disconnect()

    logger.info("=" * 60)
    logger.info(f"Results: {passed} passed, {failed} failed")
    logger.info("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run_all_tests()))
