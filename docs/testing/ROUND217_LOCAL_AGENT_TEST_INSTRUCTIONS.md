# Round 217 Local Agent Test Instructions

Run these checks from the project root after extracting the archive.

## Static/architecture checks

```bash
python3 -m compileall -q src scripts main.py
PYTHONPATH=. python3 scripts/round217_architecture_ui_mobile_tests.py
PYTHONPATH=. python3 scripts/round216_release_watch_completion_recovery_tests.py
PYTHONPATH=. python3 scripts/round215_tv_release_watch_semantics_tests.py
PYTHONPATH=. python3 scripts/round214_coordinator_usage_audit_tests.py
python3 scripts/check_architecture.py
```

Expected:

- `round217 architecture/ui/mobile tests: PASS`
- Existing Round 214-216 tests pass.
- `check_architecture.py` reports `HARD findings: 0`.

## Manual browser checks

1. Start the app with `./run.sh`.
2. Open the normal desktop UI and go to **Suggestions**.
3. Confirm there is an **Airing & Retry Watch** panel below the Suggestions card.
4. Confirm the panel loads `/api/release-watches`, shows count/status pills, and
   renders rows if watches exist. Empty state is acceptable on a fresh install.
5. Use a narrow/tall viewport or device emulator similar to the screenshot.
6. Confirm:
   - the top nav becomes icon-first and horizontally scrollable;
   - the Helm chat is a single-column panel;
   - Quick Actions/Fleet widgets do not squeeze the chat on mobile;
   - the chat message input remains visible without scrolling the whole page;
   - Suggestions and the release-watch panel are readable on mobile.

## Final report requirements

The final report must include paths to all generated test artifacts/logs, if any.
If no files are generated beyond terminal output, explicitly say so.
