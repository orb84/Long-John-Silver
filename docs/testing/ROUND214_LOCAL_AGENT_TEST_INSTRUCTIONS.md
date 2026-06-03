# Round 214 Local Agent Test Instructions

Run these commands from the project root.

## 1. Static coordinator audit

```bash
PYTHONPATH=. python3 scripts/round214_coordinator_usage_audit_tests.py
```

Expected output:

```text
round214 coordinator usage audit tests: PASS
```

This test confirms:

- `architecture.md` documents `CategoryItemCoordinator`.
- UI/agent category item actions delegate to the coordinator.
- library auto-discovery uses the coordinator without provider-heavy enrichment.
- no non-repair source file directly appends/removes `settings.tracked_items`.
- selected candidate bitrate preference updates use the coordinator.

## 2. Existing category-watch regression tests

```bash
PYTHONPATH=. python3 scripts/round213_category_watch_coordinator_tests.py
PYTHONPATH=. python3 scripts/round212_rss_monitor_release_event_tests.py
PYTHONPATH=. python3 scripts/round211_autostart_mobile_tests.py
PYTHONPATH=. python3 scripts/round210_language_policy_progressive_search_tests.py
```

All must pass.

## 3. Compile and architecture check

```bash
python3 -m compileall -q src scripts main.py
python3 scripts/check_architecture.py
```

Expected:

- compileall exits with code 0.
- `check_architecture.py` reports `HARD findings: 0`.

## Final report requirements

Report the exact paths of any generated logs or reports. At minimum include:

- `scripts/round214_coordinator_usage_audit_tests.py`
- `docs/testing/ROUND214_LOCAL_AGENT_TEST_INSTRUCTIONS.md`
- the terminal output for each command above
