# LJS Round 293 — Cancel Truth, Search Interaction, and Operation Observability

Date: 2026-08-11  
Baseline: Round 292 `search_truth_session_serialization_tv_tracking_lifecycle_cadence` package  
Primary incident: Ella Enchanted Italian movie search, user Stop/Cancel, later independent retry, and failed explicit candidate selection.

## 1. Executive verdict

The supplied Round 292 logs were initially misread as one roughly 37-minute Ella Enchanted request. That interpretation was wrong.

The correct chronology is:

1. At approximately **15:40:58 local Europe/Rome**, the user sent `Please find me a movie called Ella Enchanted in italian`.
2. The user reports pressing **Stop/Cancel almost immediately**. The Round 292 logs contain **no cancellation lifecycle event**, so the exact click timestamp cannot be recovered from those files.
3. Despite the user stopping the turn, backend search/provider work continued for several minutes. Old logs show Jackett activity continuing through the 15:43 minute range.
4. At approximately **16:17:46 local**, the user independently sent the same Ella request again. This was a new attempt, roughly 37 minutes after the first request; it was **not** continuation of one 37-minute foreground search.
5. The second attempt eventually surfaced an explicit Italian/English candidate and a language-unknown candidate.
6. The user replied `1` to choose the first candidate.
7. LJS did not preserve that exact user selection as authorization for the candidate's soft low-seeder warning. It refused the chosen candidate and then attempted unrelated alternatives before returning a theatrical `No candidates were queued` error.

Round 293 fixes the cancellation ownership and logging gap first, then the independent search/selection regressions exposed by the same incident.

## 2. What the old logs actually prove

### 2.1 There was no auditable Stop event

The old `chat.log` contains two independent Ella user messages but no event representing Stop/Cancel. The old `ljs.log` and detailed logs likewise contain no `cancel_requested`, `turn_cancelled`, `cancel_settled`, or `torrent_search_cancelled` event for the first attempt.

This means old support tooling could not distinguish:

- the user clicking Stop;
- the browser merely ceasing to wait;
- the server assistant task receiving cancellation;
- provider child tasks actually terminating;
- a later independent request.

That observability hole directly caused the incorrect 37-minute interpretation.

### 2.2 Provider work outlived the stopped turn

The first Ella request began around 15:40:58 local. After the user-reported Stop, Jackett work continued. One especially revealing sequence was the unrelated Swedish TMDB alias `Ella - Den förtrollade`:

- direct manual-parity probes finished or went empty;
- the aggregate child request nevertheless remained alive;
- an aggregate response was logged about **60.3 seconds** after that query began.

Round 292 already had logic intending to cancel a slow aggregate when the direct manual-parity probe had established an empty result. The defect was ownership: child tasks were cancelled in places but not reliably **awaited**, so the parent could leave provider work alive in the background.

### 2.3 The second attempt was slow for several independent reasons

For the later independent retry, the logs show several avoidable costs/failures:

- a categorized movie SEARCH still paid for an advisory pre-planner even though the ordinary tool loop already had the category and `search_media_torrents`;
- the model emitted `target_size_gb: null`, and the tool contract rejected that optional unset value as if it were an invalid number;
- a valid tool name could arrive with a terminal provider channel token such as `search_media_torrents<|channel|>` and be rejected;
- explicit Italian search continued into unrelated localized aliases, including Swedish title variants;
- movie search sufficiency was based partly on accumulating a large result count rather than whether the user's actual language constraint was satisfied;
- unknown-language rows remained peers even after explicit Italian evidence existed.

### 2.4 The user explicitly selected candidate 1, but queue authority was lost

The second attempt displayed an exact stable candidate. The user then replied `1`.

That later result-set selection should be the user's confirmation of **soft** warnings on that exact displayed candidate (for example very low seeders). Instead, the queue boundary independently asked for confirmation again. After failure, it also allowed fallback attempts against other cached candidates, which violated the user's explicit choice.

A hard constraint remains different: language mismatch, identity mismatch, or another hard request blocker cannot be overridden merely by selecting the row.

## 3. Round 293 architecture corrections

### 3.1 One operation identity across chat, LLM, tools, and search

Added `src/core/operation_trace.py`.

`OperationTraceContext` binds:

- `session_id`
- `turn_id`
- monotonic turn start
- `turn_elapsed_ms`

The existing `LLMActivityContext` now delegates its session/turn binding to this generic operation trace. This makes the trace category/tool/provider-neutral rather than an LLM-only mechanism.

The ordinary `logs/ljs.log` file sink uses `OperationTraceLogEnricher`, so nested lines from Jackett, metadata, tools, queueing, and LLM code inherit the same:

```text
session=<session-id> turn=<turn-id> elapsed_ms=<turn-relative-ms>
```

Background jobs that do not belong to a user turn receive `-` trace markers.

### 3.2 Explicit chat-turn lifecycle ledger

`DetailedLoggingSubsystem` now owns `ChatTurnAuditLogger` and writes:

- `logs/chat_turns.log`
- `logs/chat_turns.jsonl`

Each record contains stable session/turn identity, transport, state, turn-relative elapsed time, and a redacted detail field.

Relevant lifecycle events include:

- `turn_received`
- `turn_started`
- `turn_rejected_busy`
- `cancel_requested`
- `cancel_not_matched`
- `cancel_settled`
- `cancel_still_unwinding`
- `turn_cancelled`
- `turn_completed`
- `turn_failed`
- `transport_disconnected`

The logger keeps a bounded recent monotonic-start map so late `cancel_settled` acknowledgements still use the same turn clock even when `turn_cancelled` and transport acknowledgement arrive in different order.

### 3.3 Search lifecycle logging starts before provider I/O

`SearchLogger` now records:

- `torrent_search_started`
- `torrent_search_cancelled`
- `torrent_search_failed`
- completion under the historical `torrent_search_query` JSON event for compatibility, with explicit `terminal_state: completed`

Every record includes:

- `search_id`
- `session_id`
- `turn_id`
- `turn_elapsed_ms`
- `search_elapsed_ms`
- literal query
- category
- active provider list

The human search log labels completion explicitly as `torrent_search_completed`.

This makes a future sequence auditable as:

```text
Turn A received
Turn A started
Search X started
Turn A cancel requested
Search X cancelled
Turn A cancelled
Turn A cancel settled
...
Turn B received 37 minutes later
```

rather than inferring one duration from wall-clock gaps.

### 3.4 Stop is server-authoritative on both browser transports

The session-scoped `ChatTurnRegistry` remains the owner of exactly one assistant task per chat session.

WebSocket Stop now:

1. records `cancel_requested`;
2. publishes `stopping`;
3. cancels the matching server task;
4. waits up to a bounded settlement interval;
5. records `cancel_settled` or `cancel_still_unwinding` truthfully.

REST fallback now has a real authenticated `POST /api/chat/cancel` route using the same registry.

The browser no longer treats `AbortController.abort()` as server cancellation. It aborts the local REST response only after the server confirms the owning task has settled. If the cancel endpoint fails or cleanup is still unwinding, the UI remains visibly `stopping`/tracked instead of pretending the request ended while backend work continues.

### 3.5 Jackett child tasks cannot outlive their owning search

`JackettSearch.search()` now owns both:

- aggregate `/all/results` child;
- direct configured-indexer/manual-parity child.

On parent cancellation it cancels and awaits both children in `finally`.

The direct configured-indexer implementation likewise cancels **and awaits** selector children before returning.

A dependency-light regression also covers the old 60-second orphan shape: if direct manual-parity returns empty while the aggregate child is hanging, `JackettSearch.search()` cancels and awaits the aggregate instead of returning while that request continues logging in the background.

### 3.6 Categorized media SEARCH skips the advisory pre-planner

The old logs prove Ella was already resolved before the planner:

```text
Prepared agent context: intent=SEARCH category=movie ... search_media_torrents ...
```

Therefore a categorized SEARCH/DOWNLOAD whose registered tool surface contains `search_media_torrents` now runs the normal agent tool loop directly. It does not make an additional advisory LLM planning call first.

Generic/non-media SEARCH retains the planner where it can actually add execution value.

The search-loop contract tells the model to preserve literal constraints, use registered metadata/search tools, trust structured search evidence, and not replace successful category-owned search results with a generic web conclusion.

### 3.7 Optional JSON null is omission, not an invalid scalar

After required-field validation, optional schema arguments with JSON `null` are removed before scalar-type validation.

This means:

```json
{"target_size_gb": null}
```

behaves like the optional field was not supplied. Required fields still cannot be null.

### 3.8 Narrow terminal provider channel-token normalization

Known terminal channel-control leakage appended to an otherwise valid registered tool name is stripped before the normal tool allow-list check, for example:

```text
search_media_torrents<|channel|>
inspect_torrent_candidate<|channel|>commentary
```

This is intentionally narrow and terminal-only. Arbitrary unknown tool names remain blocked.

### 3.9 Explicit-language movie search stops on useful evidence

`CategoryTitleAuthority.query_titles_for_item()` now supports strict preferred-language query construction.

When the user explicitly requests Italian, the movie category uses:

- canonical/provider title;
- user/display title;
- translations associated with the requested locale;

and excludes arbitrary translations from unrelated locales. Therefore an Italian Ella request does not wander into the Swedish `Ella - Den förtrollade` alias simply because that alias exists in provider metadata.

Movie search sufficiency is also constraint-driven:

- explicit-language request: one provider/year-backed title-valid candidate that explicitly advertises the requested language is useful evidence;
- no explicit language: bounded candidate-volume threshold remains, reduced from the previous excessive value.

Once explicit requested-language candidates exist, unknown/wrong-language releases remain fallback evidence rather than peers that can displace the verified language match because of seed count.

### 3.10 Fresh acquisition SEARCH and DOWNLOAD share one goal-freshness boundary

Round 292 could suppress stale pending candidate context for a fresh SEARCH but still inherit the prior structured acquisition goal because `should_start_fresh_goal()` only honored DOWNLOAD.

Round 293 aligns these existing decisions. A concrete new acquisition request starts a fresh structured goal for either SEARCH or DOWNLOAD. Terse continuations such as:

- `search harder`
- a short quality refinement
- stable candidate/result handles
- `1`

remain attached to the current result set.

This reuses the existing generic acquisition-context freshness policy; it does not add category-specific phrase parsing.

### 3.11 Exact result-set selection confirms only that candidate's soft warnings

Cached search results now carry:

- stable `result_set_id`
- stable `candidate_id`
- origin user prompt
- whether the result set awaited user choice
- hard queue blockers
- soft manual-confirmation reasons

When a later user turn selects one displayed stable candidate, that provenance confirms soft warnings on that exact candidate. It does not override hard constraints.

Confirmation does not transfer to fallback candidates.

### 3.12 Explicit selected-candidate failure does not silently substitute another release

There are two different fallback situations:

- **automated first-turn/deterministic batch queueing:** an explicitly fallback-eligible operational failure may try another cached candidate, provided the alternate independently satisfies policy;
- **later explicit user choice of one displayed candidate:** the exact selected candidate is authoritative. If its queue attempt fails operationally, LJS reports that exact failure and does **not** silently substitute another cached release.

Confirmation/policy failures never trigger fallback spraying.

### 3.13 Plain operational language

Deterministic queue/error presentation no longer uses cargo/captain/parrot/pirate metaphors. Operational failures state directly whether a candidate was queued, blocked, required confirmation, or failed in the downloader.

## 4. Important changed files

Added:

- `src/core/operation_trace.py`
- `scripts/round293_ella_search_selection_cancel_tests.py`
- `tests/test_round293_ella_search_selection_cancel.py`

Key modified areas:

- `main.py`
- `src/ai/assistant.py`
- `src/ai/chat_presenter.py`
- `src/ai/chat_session_runner.py`
- `src/ai/download_context_policy.py`
- `src/ai/error_presenter.py`
- `src/ai/pending_actions.py`
- `src/ai/tool_contracts.py`
- `src/ai/tool_executor.py`
- `src/ai/tools/queue_download_support.py`
- `src/ai/tools/scheduling.py`
- `src/ai/tools/search_workspace.py`
- `src/core/categories/movie.py`
- `src/core/categories/title_authority.py`
- `src/llm_providers/activity.py`
- `src/search/aggregator.py`
- `src/search/jackett.py`
- `src/utils/detailed_logger.py`
- `src/web/app.py`
- `src/web/chat_turn_registry.py`
- `src/web/dependencies.py`
- `src/web/llm_diagnostics.py`
- `src/web/static/js/components/chatController.js`
- `src/web/static/js/components/llmActivityPanel.js`
- `AGENTS.md`
- `architecture.md`

## 5. Diagnostics UI changes

The existing dedicated LLM Diagnostics workspace now includes two additional bounded authenticated log views:

- **Turn lifecycle**
- **Searches**

These sit alongside LLM activity/context/raw-response/routing/application diagnostics. The goal is not to call provider-search data “LLM data,” but to make one user operation reconstructable in one support workspace using shared turn IDs.

## 6. Validation performed in this environment

### Passed

Dependency-light Round 293 executable incident harness:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python scripts/round293_ella_search_selection_cancel_tests.py
```

Result:

```text
ROUND293_ELLA_SEARCH_SELECTION_CANCEL_PASS
```

The harness covers, among other things:

- explicit Italian Ella search terminates on valid requested-language evidence;
- Swedish alias is excluded from that explicit-language query ladder;
- optional `null` tool fields are omitted;
- terminal channel-token normalization remains allow-list safe;
- soft versus hard queue policy;
- later exact result-set selection confirms only the chosen candidate;
- a policy/confirmation failure cannot spray fallback candidates;
- an operational failure after an explicit later candidate choice cannot silently substitute another release;
- session-level turn cancellation settles the server task;
- cancelling the Jackett parent settles aggregate/direct children and leaves no live `jackett-*` tasks;
- a direct-empty Jackett result cancels and awaits a hanging aggregate child instead of leaving it orphaned;
- turn/search lifecycle JSON logs share stable turn/search IDs and elapsed times;
- Loguru application lines inherit session/turn trace identity;
- fresh SEARCH/DOWNLOAD goal behavior;
- diagnostics/browser/source contracts and plain error presentation.

Historical executable incident harnesses passed:

```text
ROUND290_COMPLETE_READINESS_BUILD_IDENTITY_PASS
ROUND291_SILO_AIRING_FRONTIER_DIRECT_QUEUE_PASS
ROUND292_SEARCH_TRUTH_TRACKING_CADENCE_PASS
```

Static/runtime-independent checks passed:

```text
python -m compileall -q src scripts main.py
node --check src/web/static/js/components/chatController.js
node --check src/web/static/js/components/llmActivityPanel.js
python scripts/check_public_docs.py
python scripts/check_category_architecture.py
python scripts/check_ai_intent_architecture.py
python scripts/check_ai_context_architecture.py
python scripts/check_security_architecture.py
python scripts/check_model_facade_imports.py
python scripts/check_architecture.py
```

Architecture audit:

- HARD findings: **0**
- RISK findings: **168**
- ADVISORY findings: **421**

The 168 risk baseline is unchanged from the preceding rounds.

### Environment-blocked validation — do not represent as passed

This sandbox does not contain the project's declared `aiosqlite` dependency and cannot reach package indexes to install the missing environment.

Therefore:

```bash
pytest -q tests/test_round293_ella_search_selection_cancel.py
```

and:

```bash
python scripts/check_compatibility_shims.py
```

stop at import time with:

```text
ModuleNotFoundError: No module named 'aiosqlite'
```

This is an environment blocker, not a claimed application pass or failure. **No full pytest-suite pass is claimed for Round 293 in this sandbox.**

## 7. Required target-machine acceptance

Use the normal target-machine environment where LJS dependencies, database, NVIDIA endpoint, Jackett, and downloader are configured.

### 7.1 Provider/build sanity

Start LJS normally and first verify the accepted build identity exactly as established in Round 290. Do not judge Round 293 behavior while an older process owns the port.

### 7.2 Cancellation truth test

1. Open the web chat.
2. Send:

```text
Please find me a movie called Ella Enchanted in italian
```

3. Within a few seconds, press **Stop**.
4. The UI must enter `stopping` immediately.
5. It must not return to normal idle/send state until the server reports the task cancelled/settled.
6. No later Jackett/indexer work belonging to that Turn ID may continue after the terminal cancellation settles.

Inspect `logs/chat_turns.jsonl`. The exact interleaving of `turn_cancelled` and `cancel_settled` may vary across transports, but the same Turn ID must show explicit lifecycle truth including:

```text
turn_received
turn_started
cancel_requested
turn_cancelled
cancel_settled
```

or, if cleanup genuinely takes longer than the bounded synchronous wait:

```text
cancel_still_unwinding
```

followed by the eventual terminal task state. The UI must remain truthful while unwinding.

Inspect `logs/searches.jsonl`. Any active search under that Turn ID must have:

```text
torrent_search_started
...
torrent_search_cancelled
```

with the same Search ID. There must not later be a `terminal_state=completed` record for that same cancelled Search ID.

Inspect `logs/ljs.log`: nested provider/tool rows must contain the same `session=<...> turn=<...> elapsed_ms=<...>` lineage.

### 7.3 Independent retry test

After the cancellation has settled, send the same Ella request again as a **new** request.

Expected:

1. New Turn ID.
2. Fresh structured acquisition goal; no stale result set owns the new target.
3. Intent/category resolution identifies movie before execution.
4. No separate advisory planner LLM call before the ordinary category-owned media-search tool loop.
5. Optional unset model fields such as `target_size_gb: null` do not invalidate the tool call.
6. Search uses canonical/provider/requested-language title variants; it does not wander into the Swedish alias merely to accumulate result volume.
7. Once a provider/year-backed candidate explicitly advertises Italian, the query ladder returns useful results promptly instead of chasing an arbitrary candidate count.
8. If explicit Italian candidates exist, language-unknown candidates are not presented as equivalent peers merely because they have more seeders.

### 7.4 Exact candidate selection test

If the Italian candidate is shown as option 1, reply:

```text
1
```

Expected:

- the LLM resolves that visible stable candidate ID/result-set ID;
- its soft low-seeder warning is considered confirmed by the later explicit choice;
- hard language/identity blockers remain enforced;
- `queue_download` attempts that exact candidate;
- success is reported with the real queue/download receipt;
- if the exact backend queue attempt fails operationally, LJS reports that candidate's failure and **does not silently try another cached release**.

The user-facing message should be plain language, not themed error prose.

### 7.5 Follow-up continuity test

After a result set is visible, terse refinements such as:

```text
search harder
1080
1
```

must remain attached to the current structured result set/goal rather than becoming unrelated generic web research. A later fully specified new title request must start a fresh goal instead.

## 8. What remains intentionally unresolved

- The old Round 292 logs cannot reveal the exact timestamp at which the user clicked Stop, because that event simply was not logged. The user reports it was almost immediate; Round 293 is designed so future logs record it explicitly.
- No live target-machine call was performed in this sandbox against the user's real NVIDIA endpoint, Jackett instance, database, torrent client, or browser session.
- Full pytest and compatibility-shim validation require the normal declared Python environment and therefore must be completed on the target machine before production acceptance.
- The project retains its existing architecture-debt baseline. Round 293 did not attempt broad coordinator decomposition unrelated to this incident.

## 9. Acceptance recommendation

Round 293 should be treated as a **target-machine validation candidate**, not as accepted production merely because the dependency-light harness and architecture gates pass here.

The first live gate is cancellation truth. If pressing Stop produces continued provider logs under the same Turn ID after `cancel_settled`, stop testing and return the complete `logs/` bundle. If cancellation is clean, proceed to the independent Ella retry and exact candidate selection checks above.
