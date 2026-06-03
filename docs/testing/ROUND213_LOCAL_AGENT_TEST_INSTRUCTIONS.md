# Round 213 Local Agent Test Instructions

## Goal

Validate that category item add/update now uses one coordinator and that TV RSS/release-watch behavior is category-owned and dynamic.

## Required commands

Run from the project root:

```bash
PYTHONPATH=. python3 scripts/round213_category_watch_coordinator_tests.py
PYTHONPATH=. python3 scripts/round212_rss_monitor_release_event_tests.py
PYTHONPATH=. python3 scripts/round211_autostart_mobile_tests.py
PYTHONPATH=. python3 scripts/round210_language_policy_progressive_search_tests.py
PYTHONPATH=. python3 scripts/round209_tv_pack_language_status_ui_tests.py
python3 -m compileall -q src scripts main.py
python3 scripts/check_architecture.py
```

## Manual runtime checks

1. Start LJS normally with Jackett configured.
2. Add a new TV show from the UI.
3. Confirm the log contains `Category item added/updated through coordinator: tv:<show>`.
4. Confirm no restart is needed before RSS/watch policy sync runs.
5. Confirm the log contains `Category watch policy sync complete` after the item is added.
6. Add or inspect an ended/completed show and confirm it is not placed in frequent RSS polling unless the TV category reports an active/next episode watch reason.
7. Add or inspect an active/returning show with a known next episode and confirm release-watch rows are created or retained for the next unit.

## Expected test report

The final test report must include:

- The exact commands run.
- Whether each command passed or failed.
- Any relevant log snippets for coordinator add/update and RSS/watch sync.
- Paths to all generated artifacts/reports, including this file path:
  - `docs/testing/ROUND213_LOCAL_AGENT_TEST_INSTRUCTIONS.md`
- Confirmation that `check_architecture.py` reports `HARD findings: 0`.
