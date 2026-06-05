# Web Research Proactive Tracking Plan

**Status:** partially implemented in Round 223 as durable web-information watches plus scheduled assistant evaluation.  
**Scope:** use the general web-research layer to track news, rumors, release notes, patch notes, and public-source changes around category items and user-authored information watches.

## 1. Does this make sense?

Yes. It fits LJS well, because the project already has three pieces that should be reused instead of creating a new scheduler:

1. **Managed/general web research** — SearXNG discovers candidate public sources; `WebResearchService` fetches and stores evidence; category hooks interpret it.
2. **Category lifecycle policy** — categories already own recurring lifecycle decisions for tracked items.
3. **User scheduled assistant tasks** — `PromptScheduler` and the `create_scheduled_task` tool already support recurring `condition_check` and `scheduled_prompt` tasks with `interval_minutes`, `next_run_at`, `max_runs`, and failure backoff.

The right design is therefore not a new hidden crawler. Round 223 implements a thin information-watch layer that reuses those systems and remains bounded, opt-in, inspectable, and notification-driven.

## 2. Product shapes

### 2.1 Category item news watch

Example: the user is watching show X and wants occasional news/rumor updates.

A category item can declare or receive a web-information watch such as:

```text
category_id = tv
item_id = <tracked show id>
intent = news_and_rumor_watch
cadence = weekly
active_only = true
notify_policy = important_only
```

The TV category owns what “active” means. The generic scheduler must not decide whether a TV show is active, cancelled, between seasons, completed, or dormant.

Typical TV searches could include:

```text
<show title> next season news rumor
<show title> season <n+1> renewal cancellation release date
<show title> production update cast interview
```

SearXNG finds candidate sources, `WebResearchService` fetches pages, and the TV category interprets fetched evidence into signals such as:

```text
renewal_signal
cancellation_signal
next_season_rumor
production_update
release_window_reference
casting_or_creator_news
```

The LLM can then summarize only meaningful changes. It should not notify the user for every low-quality duplicate article.

### 2.2 User-authored recurring information watch

Example user prompt:

> Keep track of videogame X. I especially want to know when bug Y is patched or discussed.

This already maps naturally to the existing `create_scheduled_task` tool:

```json
{
  "task_type": "condition_check",
  "schedule_type": "recurring",
  "interval_minutes": 10080,
  "prompt": "Check public sources for videogame X, especially patch notes or developer discussions mentioning bug Y. Use web_research, fetch sources before presenting facts, and notify me only if there is a new credible update or a clear lack of updates after several checks.",
  "title": "Track videogame X bug Y",
  "max_runs": null
}
```

When it fires, `PromptScheduler` wraps it as a scheduled check and runs the normal assistant/tool chain. No new scheduler is needed for this first version.

## 3. Architecture

```text
Category lifecycle / user scheduled task
        ↓
Information-watch request
        ↓
WebResearchService / CategoryWebResearchService
        ↓
Fetched evidence + provenance
        ↓
LLM summarization/evaluation with source citations
        ↓
Notification only if policy says the update is worth surfacing
```

Boundary rules:

- This is **not media acquisition** and not a download search fallback.
- Search snippets are leads, not facts.
- Page extraction is required before durable facts or notifications.
- Category-specific meanings live in category hooks.
- User-authored recurring tasks run through `PromptScheduler`; category-owned automatic item watches run through category lifecycle/policy.
- The LLM may summarize/evaluate importance, but it must not silently mutate tracked items or queue downloads.

## 4. Data model proposal

Add an explicit watch table instead of hiding this inside settings text:

```text
web_information_watch
- id
- enabled
- owner_type              // user_task, category_item, system_suggestion
- user_id
- category_id
- item_id
- title
- intent
- query_template_json
- cadence_minutes
- next_run_at
- max_runs
- run_count
- notify_policy           // all_updates, important_only, digest_only, silent_archive
- min_confidence
- active_policy_json      // category-owned gating hints
- created_by              // user, category_lifecycle, assistant
- created_at
- updated_at
- last_run_at
- last_meaningful_update_at
- last_error
```

Add event rows for dedupe and explanation:

```text
web_information_watch_event
- id
- watch_id
- run_at
- status                  // no_change, update_found, error, skipped
- query_log_ids_json
- source_evidence_ids_json
- summary
- importance_score
- novelty_hash
- presented_to_user
- user_feedback           // useful, not_interested, too_frequent, stop
```

The existing `web_research_query_log`, `web_source_evidence`, and `category_fact_provenance` tables should continue to store the low-level provenance.

## 5. Scheduling strategy

### Phase A — reuse `PromptScheduler` for explicit user tasks

Do this first. It is already in the architecture and already exposed through `create_scheduled_task`.

For prompts like “keep track of X weekly,” the LLM creates a recurring `condition_check` with a bounded prompt. The prompt should explicitly say:

- use `web_research` / `category_web_research` when useful;
- fetch sources before presenting facts;
- summarize only credible new information;
- do not queue downloads;
- stop or adjust if the user asks.

### Phase B — category-owned item watches

Add category hooks:

```python
information_watch_policy(item, context) -> CategoryInformationWatchPolicy
build_information_watch_plan(item, watch, context) -> CategoryWebResearchPlan
interpret_information_watch_evidence(bundle, item, watch) -> InformationWatchInterpretation
```

For TV, the category can decide:

- whether a show is active enough to watch;
- whether weekly/monthly is appropriate;
- whether renewal/delay/next-season rumors matter;
- when to retire or lower cadence.

### Phase C — notification and feedback loop

User-facing cards should allow:

- “show me more like this”;
- “do not show rumors for this item”;
- “only official news”;
- “pause this watch”;
- “stop tracking this”; 
- “make it weekly/monthly.”

Feedback updates the watch row and/or category preference, not the generic web search layer.

## 6. LLM evaluation policy

The LLM is useful for ranking novelty and deciding whether a fetched evidence bundle is worth bothering the user with. The prompt should include:

- watch title and user objective;
- previous event summaries / novelty hashes;
- fetched evidence snippets with URLs/source kinds;
- category interpretation if available;
- notification policy.

The LLM should output a structured decision:

```json
{
  "notify": true,
  "importance_score": 0.78,
  "reason": "Credible developer patch notes mention bug Y as fixed in version 1.4.2.",
  "summary": "Patch 1.4.2 appears to fix bug Y...",
  "source_evidence_ids": [123, 124],
  "suggested_watch_update": {"cadence_minutes": 10080}
}
```

For low-confidence rumors:

```json
{
  "notify": false,
  "importance_score": 0.22,
  "reason": "Only forum speculation; no credible source or official discussion.",
  "summary": "No meaningful update found."
}
```

## 7. Default cadence

Recommended defaults:

- User-authored recurring information watch: weekly unless the user specifies otherwise.
- Active TV show next-season/rumor watch: weekly during active production/release windows, monthly otherwise.
- Official patch-note watch for games/software: weekly, or daily for a short user-requested high-interest period.
- Completed/dormant items: off unless user explicitly asks.

Cadences should jitter slightly to avoid all watches firing on startup or on the hour.

## 8. Safety, privacy, and cost controls

- Respect `web_search.enabled`.
- Respect provider health; if managed SearXNG is down, do not silently create a noisy loop.
- DuckDuckGo HTML fallback is degraded/off by default; only use it when explicitly allowed.
- Apply per-run budgets: max searches, max URLs fetched, max LLM tokens.
- Cache duplicate searches and dedupe source URLs.
- Store enough provenance to explain what was found, but avoid storing unlimited page bodies.
- Back off failed watches and persist `last_error`.

## 9. Scenario traces

### Scenario: user asks live about rumors for a show

```text
User: Any rumors about the next season of Show X?
Intent: SEARCH / factual research
Tool path: category_web_research(category_id=tv, item_id=Show X, intent=news_and_rumor_watch or delay_news_check)
Category: TV builds news/general searches
Web: SearXNG → candidate URLs → WebReader fetches pages → evidence bundle
Category: TV interprets renewal/release/delay/rumor signals
Assistant: summarizes with uncertainty and sources
No download search, no queueing, no item mutation
```

### Scenario: user asks generic information

```text
User: What is going on with videogame X bug Y?
Intent: SEARCH / factual research
Tool path: web_research(query="videogame X bug Y patch notes developer discussion", categories=[general, news])
Web: SearXNG first; DuckDuckGo HTML only if explicit degraded fallback is enabled
Assistant: reports fetched-source evidence or says no credible update was found
```

### Scenario: user asks to keep track weekly

```text
User: Keep track of videogame X and tell me when bug Y is patched.
Intent: CONFIG
Tool path: create_web_information_watch(intent=patch_note_watch, cadence_minutes=10080)
Scheduler: PromptScheduler runs the generated watch prompt weekly
Assistant on each run: calls run_web_information_watch, evaluates fetched evidence novelty, sends notification only if worthwhile, or replies LJS_NO_NOTIFICATION to suppress noise
```

## 10. Implementation phases

1. **Logging and scenario hardening** — done in Round 222: install/search/research paths log decisions and fallback use.
2. **User-authored watch prompt templates** — done in Round 223 with `create_web_information_watch` and generated scheduled prompts.
3. **Watch persistence tables** — done in Round 223 with `web_information_watch` and `web_information_watch_event`.
4. **Category watch hooks** — started in Round 223 with TV `news_and_rumor_watch` and `next_season_start_tracking`.
5. **Notification cards and feedback** — user can pause, stop, reduce, or increase watches.
6. **Digest mode** — group low-urgency updates instead of spamming.
7. **Lifecycle integration** — category lifecycle may propose/retire watches based on active state, but user opt-in/feedback wins.


### Scenario: user asks to track/download a future next season

```text
User: Find out when the next season of Show X starts and start downloading/tracking it.
Intent: DOWNLOAD/CONFIG hybrid
Tool path:
  1. metadata_lookup and/or category_web_research(category_id=tv, item_id=Show X, intent=next_season_start_tracking)
  2. track_category_item(category_id=tv, name=Show X) if the show is not already tracked
  3. create_web_information_watch(category_id=tv, item_id=Show X, intent=next_season_start_tracking, allow_download_queueing=true)
Future scheduled runs:
  - run_web_information_watch collects TV-owned evidence
  - LLM notifies only for meaningful/credible changes
  - if evidence indicates release/availability, the LLM must still use generic category download tools to search/select/queue
Forbidden shortcut:
  - web evidence or snippets must never directly create durable TV facts or queue a download
```

## Round 230 adjustment — LLM-planned category research

The public web-research path must not rely on category code enumerating every natural-language intent synonym.  The generic application should provide:

- bounded search/fetch budgets;
- managed provider startup and fallback diagnostics;
- provenance persistence;
- category context and category research contracts;
- source-quality warnings when results come from degraded fallback providers.

The LLM should map the user's actual wording to a category research strategy.  `category_web_research` therefore accepts a free-form semantic `intent` and a `query`/focus string.  `CategoryWebResearchService` first asks `LLMCategoryWebResearchPlanner` to turn that free-form objective plus the category's `web_research_contract()` into concrete searches.  Category deterministic plans remain only fallback/guardrail paths when the LLM planner is unavailable or invalid.

For example, `rumor_check`, `rumour scan`, `are there whispers`, and `look for creator interviews about season 6` are not separate application enums.  They are semantic inputs to the LLM planner.  The TV contract describes how TV evidence should be judged, but it should not need one branch per wording variant.

The assistant prompt now tells the model to preserve the user's exact public-research focus in `category_web_research.query`, and to treat exact category intent labels as optional hints rather than required enum values.  The tool result also returns a `source_quality.answer_policy` field so degraded fallback evidence cannot be mistaken for high-confidence official research.

## Round 231 prompt and planning guidance

Recurring web-information watches must inherit the same research rules as interactive turns. A scheduled prompt includes current runtime date context and reminds the LLM that old/undated sources are weak for current claims, social/forum chatter is unconfirmed unless corroborated, and no-change/no-official-word conclusions require suitable source coverage.

For watch creation and execution, the LLM should design the query plan instead of relying on hard-coded intent labels. The durable watch stores the user's objective and optional query plan; category watches pass that objective/query into category web research so the category/LLM planner can create current, source-diverse searches at run time.
