# LJS Round 292 — Search Truth, Session Serialization, TV Tracking, and Lifecycle Cadence

Date: 2026-08-10  
Baseline: Round 291 TV release-frontier/direct-queue package  
Primary evidence: user-supplied `logs 2.zip`, covering approximately 2026-08-04 07:14 through 2026-08-10 22:15 local log time.

## 1. Executive verdict

The current regressions were not caused by weak torrent indexers.  They came from several later LJS layers weakening already-good category/provider evidence and from background coordinators doing work before consulting the lifecycle ledger.

The clearest reproduction is Oppenheimer.  Jackett returned dozens of correct Italian releases in under two seconds, including ordinary names such as:

- `Oppenheimer (2023) BluRay 1080p.H264 Ita Eng AC3 5.1 Sub Ita Eng ...`
- `Oppenheimer 2023 IMAX 4K HDR DV 2160p BDRip Ita Eng x265 NAHOM`
- many other explicit ITA/ITA+ENG results.

Yet the final LLM workspace contained only two language-unknown YTS releases.  LJS then told the user no Italian result was available.  This was a post-provider filtering failure.

Round 292 fixes that authority loss, prevents overlapping chat turns from crossing result state, restores structured media-search continuity for terse follow-ups, stops movie Soulseek from blocking a successful torrent search, makes new active TV shows default episode tracking on from provider lifecycle truth, and moves lifecycle due checks ahead of expensive per-item work.

## 2. Log evidence

### 2.1 Oppenheimer provider recall was excellent

`searches.jsonl` shows the first request produced:

- `Oppenheimer 2023 ITA`: 33 raw / 32 deduped / 32 accepted, provider elapsed ~784 ms.
- `Oppenheimer ITA`: 43 raw / 41 deduped / 41 accepted.
- `Oppenheimer 2023`: 49 accepted.
- `Oppenheimer`: 50 accepted on the first pass and up to 96 accepted on the overlapping second pass.

The raw pool visibly contained many Italian 1080p/2160p candidates.

At 20:58:16, however, `SEARCH_MEDIA_TORRENTS_WORKSPACE_AUDIT` contained only two clean candidates:

- `Oppenheimer 2023 2160p bluray YTS`
- `Oppenheimer 2023 1080p bluray YTS`

Both had empty language evidence.  That is the false-negative pool the agent described to the user.

### 2.2 Movie Soulseek added minutes after torrents were already available

For the Oppenheimer request:

- torrent discovery began around 20:55:14;
- strong Jackett results were available by ~20:55:16;
- the foreground Soulseek companion was started before torrent search;
- Soulseek recovery/query work continued through roughly 20:57:48–20:57:57;
- the search workspace was not emitted until 20:58:16.

A movie torrent request with dozens of validated results therefore waited about three minutes for an independent companion source that was not needed.

### 2.3 Two user turns overlapped in one chat session

`chat.log` records:

- 18:55:02 UTC: `please download for me oppenheimer in italian`
- 18:57:10 UTC: `download oppenheimer in italian pleae`
- first assistant answer only at 18:58:22 UTC.

The old “one active turn” guard was scoped to one WebSocket connection.  A reconnect/new socket/REST fallback could start another assistant task for the same `session_id`.  This can cross pending result sets and explains the class of failures where the visible turn says nothing was found while older work continues in the background.

### 2.4 “Search harder” fell out of the acquisition workflow

The 18:59:28 follow-up was routed to `SEARCH`.  The generic SEARCH planner produced a `web_search("Oppenheimer 2023 Italian torrent")` step and returned raw web URLs/magnets.  Those rows had no LJS result-set/candidate IDs and were therefore not queueable later.  When the user answered `1080`, the subsequent queue step had no valid candidate workspace and returned `No candidates were queued.`

A terse acquisition follow-up must continue the structured category/result-set workflow, not switch to generic web research.

### 2.5 Background lifecycle churn was enormous

Across the supplied `ljs.log` window:

- `Discovery: skipping ... (category background discovery disabled)`: **18,524** lines across ~120 distinct items.  Many stable movies appear ~156 times, approximately once per hour.
- `TV suggestion audit ...`: **2,632** lines across 24 TV items.  House of the Dragon appears 122 times; many other shows exceed 100 audits.
- the forced full library scan ran hourly and repeatedly caused library/suggestion invalidation work.

The scheduler was consulting category no-op/background policy only after it had already iterated/reconciled each item.

### 2.6 New/active TV shows were explicitly defaulted off

The Round 291 model/category path represented new TV `auto_download` as false in multiple layers.  Logs then show real House of the Dragon Season 3 RSS matches followed by:

`background search disabled by item policy`

The UI text also stated that TV automation was “Off by default for library shows.”  This was intentional code behavior, not a checkbox-rendering bug.

## 3. Root causes and corrections

### 3.1 One-word movie identity was re-parsed with a tiny suffix whitelist

`CategoryTitleAuthority` treated a one-word title such as `Oppenheimer` as unsafe unless every token after the title was a tiny known release suffix.  Normal release strings containing group names, IMAX, punctuation, richer audio descriptors, and similar metadata failed.  Minimal YTS names happened to survive.

**Correction:** a category/provider-verified movie year now acts as a disambiguating identity boundary.  `Oppenheimer 2023 ...` is accepted as the requested movie while unrelated `Oppenheimer The Real Story 2023 ...` remains rejected.  Without verified year evidence, the conservative one-word behavior remains.

`MovieCategory` publishes a category-owned `title_identity` verdict into each candidate payload.  Generic workspace filtering consumes that verdict rather than repeating a weaker title-only interpretation.

### 3.2 Movie query fanout stopped for the wrong reason

The old movie search accumulated a large identity-valid pool before stopping and could continue into poor aliases.  A simple fixed identity count is also insufficient when the user requested a language because an indexer can ignore the query language token.

**Correction:** movie search stops once it has a useful canonical pool (20+ identity-valid rows) **and**, when a language was requested, at least one row carries explicit preferred-language evidence.  Otherwise the bounded movie-owned query ladder continues.  The returned query summary contains only queries actually executed.

### 3.3 Soulseek was a foreground blocker for torrent-rich movies

**Correction:** categories may publish `soulseek_source_strategy()`.  Movies choose `fallback_if_primary_empty`.  If torrent search returns candidates, the response returns immediately with Soulseek marked `not_needed_primary_sufficient`; Soulseek is attempted only when primary torrent discovery is empty.  Categories that need parallel source discovery keep the existing parallel mode.

### 3.4 Acquisition follow-ups lost category/result-set authority

**Correction:** `AgentGoalStateManager.active_goal()` exposes the persisted structured goal.  When a SEARCH/DOWNLOAD follow-up has no newly resolved category but an active acquisition result set exists, `AIAssistant` reuses that category.  SEARCH acquisition continuation gets a compact seven-tool surface:

- `enquire_about_media`
- `metadata_lookup`
- `search_media_torrents`
- `inspect_torrent_candidate`
- `get_storage_status`
- `list_downloads`
- `category_web_research`

It does not get queue mutation authority.  Public web research remains evidence only; it cannot manufacture queueable candidate IDs.

### 3.5 Chat serialization was connection-scoped instead of session-scoped

**Correction:** new `src/web/chat_turn_registry.py` is process-local authority for one live assistant task per `session_id`.  Both `/ws/chat` and `/api/chat` acquire the same registry.  A second connection or REST fallback sees the existing turn as busy; cancellation/release is turn-specific.

### 3.6 TV tracking was hard-coded false in multiple layers

**Correction:** `TvShowItem.auto_download` is genuinely tri-state.  Library discovery creates TV items with `None`.  At the TV watch-policy boundary, if lifecycle truth is missing, the category performs one bounded TVMaze lifecycle lookup and persists it.  Then:

- active / returning / in-production / running / known next episode -> initial `auto_download=True`;
- ended / cancelled / finished -> initial `False`;
- unknown -> remains `None`.

An explicit existing user boolean is never overwritten.  `CategoryWatchPlan.item_updates` carries the category-owned default back through generic scheduler persistence.  UI help now states that newly discovered still-active shows default on.

Important migration note: an old persisted `False` from earlier releases is indistinguishable from a deliberate historical user choice.  Round 292 does **not** flip existing false values automatically, because doing so could override a real opt-out.  The corrected default applies reliably to newly discovered/unset shows; explicit users choices remain authoritative.

### 3.7 Lifecycle gating happened after item work

**Correction:** `CategoryLifecycleEngine.scheduled_work_is_due()` is a cheap ledger-only preflight.  A clean future-dated row returns false without canonical object construction, fingerprinting, metadata/provider calls, suggestions, taste, or LLM work.

The hourly tracked-item job now checks this before paused-state repository access and runtime reconciliation.  Suggestion compilation also performs the same preflight before full fingerprint/workflow evaluation.  First-time `reconcile_item()` reads the existing ledger before computing fingerprints.

Stable present movies now use a 180-day lifecycle/upgrade cadence.  Missing movies retain a 14-day check and metadata-incomplete movies a 7-day repair cadence.

### 3.8 Hourly full library crawl was redundant

**Correction:** the forced full scan is now a **daily safety net**.  The existing 120-second filesystem watcher remains, as does targeted reconciliation after managed imports.  Real filesystem changes still trigger prompt scans; unchanged libraries no longer get a forced full crawl every hour.

## 4. Changed files

Added:

- `src/web/chat_turn_registry.py`
- `tests/test_round292_search_truth_tracking_cadence.py`
- `scripts/round292_search_truth_tracking_cadence_tests.py`
- this handover

Modified:

- `architecture.md`
- `src/ai/assistant.py`
- `src/ai/goal_state.py`
- `src/ai/task_prompt_guidance.py`
- `src/ai/tool_policy.py`
- `src/core/categories/movie.py`
- `src/core/categories/title_authority.py`
- `src/core/categories/tv.py`
- `src/core/categories/tv_context.py`
- `src/core/categories/watch.py`
- `src/core/category_lifecycle.py`
- `src/core/domain_models/media.py`
- `src/core/scheduler.py`
- `src/core/scheduler_services.py`
- `src/web/app.py`
- `src/web/static/js/components/categoryItemDetailModal.js`

## 5. Validation completed in this sandbox

Passed:

```bash
PYTHONPATH=. python scripts/round292_search_truth_tracking_cadence_tests.py
python -m compileall -q src scripts main.py tests/test_round292_search_truth_tracking_cadence.py
node --check src/web/static/js/components/categoryItemDetailModal.js
python scripts/check_public_docs.py
python scripts/check_category_architecture.py
python scripts/check_ai_intent_architecture.py
python scripts/check_ai_context_architecture.py
python scripts/check_security_architecture.py
python scripts/check_model_facade_imports.py
python scripts/check_architecture.py
```

Dependency-light incident harness result:

`ROUND292_SEARCH_TRUTH_TRACKING_CADENCE_PASS`

Architecture audit:

- files scanned: 353
- hard findings: **0**
- risk findings: **168**
- advisory findings: 418
- private-access reviews: 91

Round 291 baseline audit was 0 hard / 168 risk, so the risk baseline did not increase.

### Validation limitation — do not misrepresent this

The sandbox does not contain `aiosqlite` or `litellm`, and package-network access is unavailable.  Installing the declared requirements fails at package resolution.  Therefore the normal pytest environment cannot be constructed here.

The following are blocked at import time by missing `aiosqlite`, not by a project assertion failure:

```bash
pytest -q tests/test_round292_search_truth_tracking_cadence.py
python scripts/check_compatibility_shims.py
```

No full-suite pass is claimed for Round 292 from this sandbox.

## 6. Required target-machine acceptance

Run from the unpacked Round 292 project in the normal LJS Python environment.

### 6.1 Code/tests first

```bash
python -m pytest -q tests/test_round292_search_truth_tracking_cadence.py
python scripts/round292_search_truth_tracking_cadence_tests.py
python scripts/check_compatibility_shims.py
python scripts/check_public_docs.py
python scripts/check_category_architecture.py
python scripts/check_ai_intent_architecture.py
python scripts/check_ai_context_architecture.py
python scripts/check_security_architecture.py
python scripts/check_model_facade_imports.py
python scripts/check_architecture.py
```

Then run the complete project suite according to the normal project command.  Stop and return the first real failure with its full traceback; do not edit code on the test machine.

### 6.2 Oppenheimer live search

Send exactly:

`please download for me oppenheimer in italian`

Expected:

1. movie identity resolves to Oppenheimer (2023);
2. first Jackett query should be `Oppenheimer 2023 ITA` or the equivalent category-owned preferred-language query;
3. normal `... Ita ... IMAX ... <release group>` rows must survive movie title validation;
4. if that first query yields a strong preferred-language pool, search should stop without low-value alias fanout;
5. successful torrent discovery must not wait for a foreground Soulseek search;
6. final workspace should contain several explicit Italian candidates, not only the two language-unknown YTS rows;
7. the assistant should choose/queue according to normal quality/language/seed rules instead of claiming Italian releases do not exist.

Inspect logs for `MOVIE_SEARCH_FILTER_AUDIT`, `SEARCH_MEDIA_TORRENTS_WORKSPACE_AUDIT`, and the source strategy.  A torrent-rich movie search should report Soulseek as `not_needed_primary_sufficient`.

### 6.3 Acquisition follow-up continuity

After a media candidate workspace exists, say:

`search harder`

Expected:

- it remains attached to the same category/result-set acquisition goal;
- it invokes/refines `search_media_torrents` rather than replacing the workspace with raw web magnets;
- any later choice such as `1080` refers to stable LJS candidate/result-set IDs.

### 6.4 Same-session overlap

Start a deliberately slow chat request, then from another tab/reconnected socket using the same chat session try to send a second request.

Expected: second turn is rejected as busy until the first finishes or is stopped.  No second assistant task should start for that session.

### 6.5 New active TV tracking default

Add/scan a **new** TV show whose provider metadata says it is active/returning and has future episodes.

Expected:

- initial item may transiently be `auto_download=null` during cheap scan ingestion;
- TV watch-policy sync resolves lifecycle metadata once;
- the persisted item becomes `auto_download=true`;
- the detail checkbox renders enabled;
- release watches/RSS searches are allowed;
- explicitly turning the checkbox off persists false and later syncs do not turn it back on.

Also test an ended show: it should default off.

### 6.6 Background cadence

Let the scheduler run for more than one hourly update interval.

For stable present movies whose lifecycle ledger is future-dated:

- no per-movie `Discovery: skipping ...` flood should recur hourly;
- no provider/LLM/canonical reconciliation should happen merely because the hourly scheduler ticked;
- the item should remain dormant until explicit invalidation or its long due date.

TV suggestions should likewise reuse valid lifecycle/suggestion state rather than rebuilding every hour.  The forced whole-library scan should no longer run hourly; it is daily, with filesystem changes still detected by the 120-second watcher.

## 7. Remaining considerations

- Existing historical TV items already persisted as `auto_download=False` cannot be safely auto-migrated because old releases did not record whether false came from the old blanket default or an explicit user opt-out.  Round 292 deliberately preserves those booleans.  Newly unset/discovered items use the corrected lifecycle default.
- The hourly scheduler still iterates tracked-item envelopes to perform a cheap lifecycle-ledger due check.  This is intentional and local; it is no longer equivalent to doing category/provider work for every item.
- Active release watches may legitimately poll more frequently than ordinary lifecycle checks because they represent concrete expected releases.  That is separate from the eliminated all-library hourly churn.
- Full dependency-backed pytest and live Jackett/TVMaze/slskd/downloader acceptance must be completed on the target machine.

## 8. Acceptance recommendation

Do not judge Round 292 by whether “search eventually found something.”  Accept it only if the logs prove that provider truth survives into the final workspace, a successful primary movie search is not held behind Soulseek, same-session turns cannot overlap, new active TV items resolve to tracking-on, and stable movies remain dormant between lifecycle due dates.
