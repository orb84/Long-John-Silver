# Round 212 Local Agent Test Instructions — RSS Monitor / Release Event Recovery

## Goal
Verify that RSS release monitoring no longer crashes, no longer polls Jackett with an empty `/all` query, and no longer false-matches releases such as `Beyond the Wire` to the TV show `The Wire`.

## Required setup
Use the full Round 212 project archive. Preserve the user's normal config/data if testing as an upgrade, but make a backup first.

## Tests to run

1. Static/unit regression suite:

```bash
python3 scripts/round212_rss_monitor_release_event_tests.py
python3 -m compileall -q src scripts main.py
python3 scripts/round211_autostart_mobile_tests.py
python3 scripts/round210_language_policy_progressive_search_tests.py
python3 scripts/check_architecture.py
```

2. Runtime smoke:

```bash
./run.sh
```

Wait at least one RSS monitor cycle if practical, or temporarily reduce the RSS poll interval in a local throwaway config/test harness.

## What to verify in logs

Expected:

- No `RSS match callback failed: dictionary update sequence element #0 has length 1; 2 is required`.
- No Jackett RSS feed URL ending in `t=search&q=` with an empty query.
- RSS monitor starts with item-scoped feeds, not one broad empty `/all` feed.
- Matches log a real candidate title, not `<built-in method title of str object ...>`.
- `Wicked Attraction ... Beyond the Wire` must not trigger a release event for `The Wire`.

## Artifact/report requirements

The final testing report must include paths to every generated artifact/log reviewed, especially:

- `logs/ljs.log`
- `data/autostart/autostart.log` if autostart was involved
- any copied terminal output file for the commands above

