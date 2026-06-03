# Round 215 local-agent test instructions

You are testing `ljs_round215_tv_release_watch_hardening.zip`.

## Required deterministic checks

Run from the project root:

```bash
python3 -m compileall -q src scripts main.py
PYTHONPATH=. python3 scripts/round215_tv_release_watch_semantics_tests.py
PYTHONPATH=. python3 scripts/round214_coordinator_usage_audit_tests.py
PYTHONPATH=. python3 scripts/round213_category_watch_coordinator_tests.py
PYTHONPATH=. python3 scripts/round212_rss_monitor_release_event_tests.py
PYTHONPATH=. python3 scripts/round211_autostart_mobile_tests.py
PYTHONPATH=. python3 scripts/round210_language_policy_progressive_search_tests.py
PYTHONPATH=. python3 scripts/round209_tv_pack_language_status_ui_tests.py
python3 scripts/check_architecture.py
```

`check_architecture.py` may report RISK/ADVISORY size warnings.  HARD findings
must be zero.

## Manual/runtime check

1. Start LJS.
2. Add or update a TV show with a known future next episode.
3. Confirm a release-watch row is created with `watch_start_at` near the episode
   air date, not immediately due unless the air date has arrived.
4. Confirm no RSS feed is created for that future episode until the release
   window is close/open.
5. For an active episode whose release window is open, confirm retries happen on
   the configured interval and use the item language/quality requirements.
6. Confirm a notified candidate leaves the watch as `candidate_found`, and an
   auto-queued download leaves it as `queued`, not `completed`.

## Final report requirements

Your final report must include:

- The output of each command above.
- Any startup/runtime logs relevant to release watches.
- The path to any log file or artifact you created.
- The exact artifact filenames/paths expected:
  - `docs/project-history/ROUND215_TV_RELEASE_WATCH_HARDENING.md`
  - `docs/testing/ROUND215_LOCAL_AGENT_TEST_INSTRUCTIONS.md`
  - `scripts/round215_tv_release_watch_semantics_tests.py`
