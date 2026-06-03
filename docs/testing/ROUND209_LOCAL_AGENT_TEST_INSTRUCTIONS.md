# Round 209 local testing instructions

These instructions are for the local testing agent. Follow the steps exactly and include the requested artifact paths in the final report.

## 1. Static validation

From the project root, run:

```bash
python3 -m compileall -q src scripts main.py
python3 scripts/round209_tv_pack_language_status_ui_tests.py
python3 scripts/round208_notification_delivery_ledger_tests.py
python3 scripts/check_architecture.py
```

Expected result:

- compileall exits with status 0.
- `round209 tv pack/language/status/ui tests: PASS` appears.
- `Round 208 notification delivery ledger tests passed` appears.
- `check_architecture.py` reports `HARD findings: 0`. Risk/advisory findings are acceptable but must be copied into the report summary.

## 2. Fresh-install UI smoke test

Start LJS with:

```bash
./run.sh
```

Open the web UI and verify:

- On an empty/fresh library, the global status header does not repeatedly flicker between Idle and Processing Suggestions.
- The Helm view keeps the message input visible inside the browser/frame without requiring page-level scrolling.
- The chat feed scrolls internally when there are many messages.

## 3. TV search behavior test

Use a fresh or empty install with media language preference set to English. In Helm, ask:

```text
Can you find me the first season of The Boys?
```

Expected behavior:

- The structured request should resolve title `The Boys`, season `1`.
- The TV search should attempt season-pack/complete-series candidates before per-episode fanout.
- Search logs should include `The Boys S01` and `The Boys Season 1`.
- Search logs should not include English-language fanout suffixes such as `The Boys S01 ENG` or `The Boys S01 English`.
- Search logs should not include Italian-language fanout suffixes such as `The Boys S01 ITA` or `The Boys S01 Italian` when the configured media preference is English.
- Results such as `The Boys S05`, `The Hardy Boys S01`, or single `S01E01` rows should not be treated as valid Season 1 pack candidates.
- Complete-series/multi-season packs that include Season 1 may be accepted, but they must require/select only the requested Season 1 files/folder.

## 4. Required final report contents

The final report must include:

- Pass/fail status for every command in section 1.
- A short summary of any `check_architecture.py` risk/advisory findings.
- Whether the empty-library status header flicker was observed.
- Whether the Helm input stayed visible without page-level scrolling.
- The exact search queries observed for the `The Boys` Season 1 request.
- Whether Italian/ITA or English/ENG suffix queries appeared despite English preference.
- Whether season-pack or complete-series candidates appeared before per-episode fallback.
- Whether Soulseek was invoked as a companion source, if enabled.

The final report must also list the paths to all generated artifacts/reports, not just copied metrics. Include these exact expected artifact paths if they exist:

- `logs/ljs.log`
- `logs/searches.log`
- `logs/llm_context.log`
- `logs/llm_responses.log`
- `logs/agent_round209_the_boys_s1_report.md`
- `logs/round209_architecture_check.txt`
- any screenshot/video path used to demonstrate Helm layout behavior

If a listed artifact does not exist, explicitly report `missing: <path>`.
