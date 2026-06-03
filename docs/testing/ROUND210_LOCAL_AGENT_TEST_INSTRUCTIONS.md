# Round 210 Local Agent Test Instructions

These instructions are for the local testing agent. Run them from the project root after extracting `ljs_round210_language_policy_progressive_search_fix.zip`.

## Deterministic checks

Run exactly:

```bash
python3 scripts/round210_language_policy_progressive_search_tests.py
python3 scripts/round209_tv_pack_language_status_ui_tests.py
python3 scripts/round207_tv_language_pack_safety_tests.py
python3 scripts/round208_notification_delivery_ledger_tests.py
python3 scripts/round206_architecture_recovery_tests.py
python3 scripts/round205_jackett_manual_search_parity_tests.py
python3 -m compileall -q src scripts main.py
python3 scripts/check_architecture.py
```

Expected deterministic results:

- `round210 language policy/progressive search tests: PASS`
- `round209 tv pack/language/status/ui tests: PASS`
- `round207_tv_language_pack_safety_tests: PASS`
- `Round 208 notification delivery ledger tests passed`
- `round206_architecture_recovery_tests: PASS`
- `round205_jackett_manual_search_parity_tests: PASS`
- `check_architecture.py` must report `HARD findings: 0`.

## Manual runtime checks

Use an install where TV preferred media language is English.

1. Launch LJS.
2. Ask for the first season of `The Boys`.
3. Inspect logs for generated TV queries.
4. Confirm English queries do not contain `ITA`, `Italian`, `ENG`, `English`, or `MULTI` suffixes.
5. Confirm `MULTI` / dual-audio releases may still appear as candidate titles if Jackett returns them naturally.
6. Confirm Jackett logs show aggregate and direct configured-indexer search starting in the same request window.
7. Confirm direct recovery logs can return an early useful batch and cancel remaining probes, rather than waiting for every configured indexer.

Repeat with TV preferred media language set to Italian:

1. Ask for the first season of `The Boys`.
2. Confirm query variants may include `ITA` and `Italian`.
3. Confirm no unrelated language token is introduced.

Repeat with TV preferred media language set to French:

1. Ask for the first season of `The Boys`.
2. Confirm query variants may include `FRE` and `French`.
3. Confirm no `ITA`, `Italian`, `SPA`, `Spanish`, or `MULTI` query token is introduced.

## Final report requirements

The final local-agent report must include:

- The exact command output for each deterministic check.
- The path to the full runtime log file used for manual validation.
- The exact query lines observed for English, Italian, and French checks.
- Whether `MULTI` appeared only in candidate titles/evidence, not as generated query text.
- The exact Jackett progressive-search lines showing early direct configured-indexer return, if observed.
- Paths to any generated test artifacts or reports.

Expected artifact/report paths to report back:

- `docs/testing/ROUND210_LOCAL_AGENT_TEST_INSTRUCTIONS.md`
- Runtime log path chosen by the local agent, for example `logs/ljs.log` or terminal capture path.
- Any captured manual validation transcript path, if the agent creates one.
