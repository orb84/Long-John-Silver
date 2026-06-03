# Round 216 local-agent test instructions

Run these from the project root after unpacking the archive.

## Deterministic tests

```bash
python3 -m compileall -q src scripts main.py
PYTHONPATH=. python3 scripts/round216_release_watch_completion_recovery_tests.py
PYTHONPATH=. python3 scripts/round215_tv_release_watch_semantics_tests.py
PYTHONPATH=. python3 scripts/round214_coordinator_usage_audit_tests.py
PYTHONPATH=. python3 scripts/round213_category_watch_coordinator_tests.py
python3 scripts/check_architecture.py
```

Expected results:

- `round216 release-watch completion/recovery tests: PASS`
- `round215 tv release-watch semantics tests: PASS`
- `round214 coordinator usage audit tests: PASS`
- `round213 category item/watch coordinator tests: PASS`
- `check_architecture.py` reports `HARD findings: 0`.

## Runtime checks

1. Start LJS.
2. Open `/api/release-watches` while authenticated.
3. Confirm it returns JSON with `status`, `count`, `status_counts`, and `watches`.
4. Add an active TV show with a known future next episode.
5. Confirm the corresponding release watch is `pending` with a future `next_check_at`/`watch_start_at`.
6. Queue a release-watch download and then cancel/fail/remove the matching download before import.
7. After the queued-watch stale window or by manually invoking the scheduler retry job, confirm the watch becomes `failed_retryable` rather than staying permanently `queued`.
8. Queue and complete/import the matching unit. Confirm the watch becomes `completed`.

## Final report requirements

Your final report must include:

- Commands run and pass/fail status.
- The response body or saved artifact path for `/api/release-watches`.
- The release-watch row before queueing, after queueing, after cancel/fail recovery, and after successful import if exercised.
- Paths to any generated logs or test output artifacts.
