# LJS Round 291 — TV Airing Frontier, Direct Queue Completion, and Human Responses

Date: 2026-07-13  
Baseline: Round 290 deployed-build truth package  
Primary incident: `can you please download for me the available episodes of the latest season of silo in italian ?`

## 1. Executive verdict

The assistant's bad answer was enabled by a contradictory tool contract, not by a lack of public release information alone.

LJS correctly resolved **Silo** as TV Season 3 and the exact fallback found current individual releases for **S03E01** and **S03E02**. However, the result sent to the main model exposed `expected_episode_count: 10`, where 10 was the provider catalogue/order size for the whole season. It did not expose an authoritative aired frontier, target-unit list, or complete two-unit queue recommendation. The model latched onto the loudest number, described ten episodes as available, and asked the user to choose between queueing and reviewing.

The follow-up queue itself then succeeded for two downloads, but `ToolOutcomeLedger` converted the successful status `queued` into a failure detail. The UI therefore claimed the operation was partial while simultaneously reporting two verified downloads and zero errors.

Round 291 corrects the evidence contract before the model, removes the unnecessary follow-up for a fully covered request, and makes final prose subordinate to the actual queue receipt.

## 2. Exact production-log findings

### 2.1 User request

```text
can you please download for me the available episodes of the latest season of silo in italian ?
```

The request was already sufficient: title, latest season, currently available units, media language, and download intent were explicit.

### 2.2 Search contradiction

The logs show:

- a broad range query for `Silo S03E01-E10`;
- exact fallback results for `S03E01` and `S03E02`;
- the query summary `S03E01, S03E02 (pack unavailable; individual episodes)`;
- top-level `expected_episode_count: 10`;
- no explicit `season_total_episode_count`, `aired_episode_count`, `target_unit_labels`, or complete batch action contract.

The main model then said:

```text
To fill your library with all 10 episodes...
```

That claim was unsupported by the actual released-unit evidence.

### 2.3 Unnecessary interaction

The model offered a menu asking whether to queue or review. That was unnecessary. The user's original instruction already authorized downloading all currently available Italian episodes. Once two safe candidates covered the two released targets, the correct next action was `queue_download`, not another confirmation.

### 2.4 False partial failure

The queue result contained:

- `status: queued`;
- `queued_count: 2`;
- two download identifiers;
- `errors: []`;
- `partial_failure: false`;
- a persisted successful command receipt.

The old outcome guard nevertheless created the detail `queue status was queued`, causing the false warning:

```text
The queue request completed only in part
```

### 2.5 Provider tool-name contamination

One model tool call arrived as:

```text
inspect_torrent_candidate<|channel|>commentary
```

The allow-list correctly rejected unknown tools, but this was a known provider control-token suffix attached to an otherwise valid tool name. Round 291 strips only that exact terminal provider token form before validation; arbitrary tool aliases remain blocked.

## 3. Architectural correction

### 3.1 One TV-owned release snapshot

Added `src/core/categories/tv_agent_availability.py` with `TVAgentAvailabilityFactsBuilder`.

For a season search, TV now publishes distinct facts:

| Field | Meaning |
|---|---|
| `season_total_episode_count` | Provider catalogue/order size, including unaired episodes |
| `aired_episode_count` | Number of episodes with dated release evidence up to the runtime date |
| `aired_unit_labels` | Released episode labels |
| `release_frontier_episode` | Highest aired episode number |
| `target_unit_count` | Released units still targeted by this request |
| `target_unit_labels` | Exact target labels |
| `requested_unit_scope` | Structured scope such as `available_units` |
| `season_release_state` | Currently airing, complete/released, or release count unknown |
| `expected_episode_count` | Compatibility bundle frontier; never the future catalogue total |

For this incident the intended packet is:

```json
{
  "season_total_episode_count": 10,
  "aired_episode_count": 2,
  "aired_unit_labels": ["S03E01", "S03E02"],
  "release_frontier_episode": 2,
  "target_unit_count": 2,
  "target_unit_labels": ["S03E01", "S03E02"],
  "requested_unit_scope": "available_units",
  "season_release_state": "currently_airing",
  "expected_episode_count": 2
}
```

When dated release evidence is unavailable, release coverage remains unknown. It does not fall back to the season's catalogue total merely to produce a number.

### 3.2 Search only to the released frontier

TV prepares the release snapshot before pack and exact fallback search.

For a currently airing season with E01/E02 released, pack-range queries may use:

```text
S03E01-E02
```

They must not use:

```text
S03E01-E10
```

Exact fallback labels are derived from aired/local category evidence only. A provider or indexer returning an apparent future `S03E03` does not make E03 part of the target.

### 3.3 Exact batch coverage

`SearchBatchRecommendationBuilder` now:

- runs after language, identity, seeder, quality, and queue-safety annotation;
- excludes blocked candidates;
- does not let a blocked or wrong-language bundle suppress safe direct episodes;
- filters groups to category-published `target_unit_labels`;
- selects one ranked candidate per exact target unit.

`SearchWorkspaceCompletionContractBuilder` no longer treats equal counts as proof of coverage. Recommended group labels must exactly equal target labels. E01 + E03 cannot satisfy an E01 + E02 target.

### 3.4 No redundant confirmation

When the workspace has one safe candidate for every current target and no unresolved quality choice, it emits:

```json
{
  "follow_up_required": false,
  "action_required": "queue_download",
  "queue_download_arguments": {"...": "stable IDs"}
}
```

The main model must perform the explicit queue tool call. Premature prose is suppressed and one structured follow-through prompt is injected. This is not hidden autoqueue: the model remains the action boundary, while the runtime prevents it from replacing a clear user instruction with an unnecessary menu.

The separate candidate-ranking LLM call is skipped for a complete deterministic batch. This avoids another slow or blank model call when category evidence already provides a safe complete action set.

### 3.5 Truthful final response

`ToolOutcomeLedger` now recognizes successful statuses such as `queued`, `already_active`, `complete`, and `succeeded` as non-errors.

A result with two verified successes and no errors is a full success. Prompt guidance requires brief human language, for example:

```text
Queued S03E01 and S03E02 of Silo in Italian.
```

The model should not expose result-set IDs, internal policies, receipt jargon, or a menu unless there is a real ambiguity or failure.

## 4. Important changed files

Added:

- `src/core/categories/tv_agent_availability.py`
- `tests/test_round291_silo_airing_frontier_and_human_flow.py`
- `scripts/round291_silo_airing_frontier_direct_queue_tests.py`

Modified:

- `src/core/categories/base.py`
- `src/core/categories/tv_agent.py`
- `src/core/scheduler_services.py`
- `src/ai/tools/search_workspace.py`
- `src/ai/tools/scheduling.py`
- `src/ai/tool_result_compactor.py`
- `src/ai/tool_outcome_guard.py`
- `src/ai/tool_executor.py`
- `src/ai/task_prompt_guidance.py`
- `src/ai/agent_loop.py`
- `src/ai/streaming_agent_loop.py`
- `architecture.md`
- historical executable expectations in Round 233 and Round 241, aligned with later fail-closed architecture

## 5. Regression coverage

Round 291 covers:

- catalogue total 10 versus released frontier 2;
- unknown release dates not becoming catalogue availability;
- pack query ending at E02 rather than E10;
- exact E01/E02 target labels reaching the workspace;
- complete Italian two-episode batch generation;
- blocked pack not suppressing eligible direct episodes;
- future/unrelated E03 being filtered from an E01/E02 batch;
- exact target-label equality rather than count-only completion;
- no candidate-ranker LLM call for a complete batch;
- no follow-up menu when the original request already authorizes queueing;
- queue follow-through enforced through the explicit LLM tool call;
- two successful queue receipts not being described as partial failure;
- known provider channel-token suffix normalization without weakening tool allow-listing.

## 6. Validation performed

Final pytest accounting:

- **1,225 tests collected**
- **1,201 passed**
- **24 intentionally skipped**
- **0 failed**

Focused affected set:

- 111 passed
- Round 291 incident file: 11 passed

Executable regressions passed for the relevant Round 233/241 and Round 280-291 chains, including Round 285 Silo identity/localized recall and Round 290 deployed-build truth. One pre-existing database-initialization test prints its complete `3 passed` summary and then retains a process/thread; it was isolated and its result verified, but that teardown leak is not claimed as fixed.

Static and architecture gates passed:

- Python compilation
- public documentation audit
- category architecture
- AI intent architecture
- AI context architecture
- security architecture
- model facade exports
- compatibility shims
- complete architecture audit

Architecture summary:

- 0 hard findings
- 168 risk findings, unchanged baseline
- 416 advisory findings
- 91 private-access reviews

## 7. Required target-machine acceptance test

After replacing the package, first verify the Round 290 build-identity contract so an older process cannot be mistaken for this build.

Send exactly:

```text
can you please download for me the available episodes of the latest season of silo in italian ?
```

Expected behavior:

1. The effective model displayed in LLM Diagnostics matches the configured route.
2. TV resolves the latest aired season through category-owned metadata.
3. Search facts distinguish total order from released target:
   - `season_total_episode_count: 10`
   - `aired_episode_count: 2`
   - `release_frontier_episode: 2`
   - `target_unit_labels: [S03E01, S03E02]`
4. No query uses `S03E01-E10` as the current range.
5. Exact Italian episode queries cover E01 and E02.
6. An indexer result naming E03 is not included merely because it exists.
7. The result contains a two-candidate batch and a completion contract requiring `queue_download`.
8. The assistant does not ask for confirmation or show a candidate menu.
9. The advisory candidate-ranker call is skipped for the complete batch.
10. The queue receipt reports two verified downloads and zero errors.
11. The final reply is brief human language, such as `Queued S03E01 and S03E02 of Silo in Italian.`
12. No false partial-failure card or message appears.

If metadata cannot establish current air dates, LJS must say release availability is unverified or take a bounded category-owned verification path. It must not substitute the catalogue total.

## 8. Remaining limitations

This environment cannot access the user's real TVMaze/TMDB credentials, Jackett indexers, local library database, torrent engine, configured NVIDIA model, or browser session. The full live acceptance test above remains required on the target machine.

The deterministic path intentionally does not queue a batch when quality trade-offs, language evidence, identity, queueability, or exact target coverage remain unresolved. In those cases a concise human clarification may still be correct.
