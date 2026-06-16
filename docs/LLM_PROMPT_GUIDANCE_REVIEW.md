# LJS LLM Prompt and Planning Guidance Review

**Round:** 232  
**Scope:** agent prompts, advisory planning prompts, scheduled-task prompts, web-information watch prompts, download-selection prompts, and tool schemas.

## Findings

1. **Guidance was scattered and uneven.**
   The main chat prompt, advisory planner, web-research planner, scheduler wrapper, watch wrapper, and individual tool schemas each had overlapping rules. Some surfaces received current-date/source-quality guidance while others did not.

2. **Some prompts were too large for smaller local models.**
   The download and configuration task prompts had accumulated long paragraphs. Smaller models are more reliable when they receive compact, repeated operating rules plus category-owned context rather than one giant block of feature history.

3. **Scheduled checks were under-specified.**
   Scheduled tasks did not consistently receive runtime date context, no-change notification rules, or the same evidence/action discipline as live chat turns.

4. **Watch intent descriptions still looked enum-like.**
   `create_web_information_watch.intent` used examples such as `news_and_rumor_watch`, which encouraged the LLM to think in exact labels instead of semantic objectives.

5. **Routing/planning guidance needed clearer action boundaries.**
   Queries such as “find out when X starts and start tracking/downloading it” combine research with a future action. The router/planner should treat that as action-oriented, while still requiring research/category/download proof before side effects.

## Changes made

- Added `src/ai/task_prompt_guidance.py` as the central generic task guidance source.
- Main assistant prompts now receive concise universal operating rules.
- SEARCH, DOWNLOAD, CONFIG, CHAT, and CLARIFY task guidance now comes from the shared helper.
- Advisory planner prompts now include the same concise operating rules and a compact planner contract.
- Scheduled prompts now include runtime datetime/date context, operating rules, condition-check no-notification semantics, and download side-effect restrictions.
- Web-information watch prompts now reuse scheduled-task guidance.
- `create_scheduled_task` schema now explains when to prefer `create_web_information_watch` and how to preserve exact condition triggers.
- `create_web_information_watch.intent` now explicitly says it is a free-form semantic objective, not an enum.
- Intent-router guidance now distinguishes pure information SEARCH from action-oriented DOWNLOAD/configuration requests.

## Current prompt architecture

```text
Generic LLM discipline
  -> src/ai/task_prompt_guidance.py

Public web research discipline
  -> src/search/web/research_guidance.py

Category-specific domain guidance
  -> src/core/categories/prompts/*.md
  -> category.web_research_contract()
  -> category search/download hooks

Main assistant prompt
  -> persona + response contract
  -> current date/runtime context
  -> generic operating rules
  -> user/category context
  -> web guidance when relevant
  -> concise intent guidance

Advisory planner
  -> current date/runtime context
  -> generic operating rules
  -> concise intent guide
  -> exact available tool schemas
  -> compact JSON contract

Scheduled tasks / watches
  -> current date/runtime context
  -> generic operating rules
  -> no-change / notification rules
  -> original stored user objective
```

## Rules preserved

- Generic code must not learn TV/movie/book/game semantics.
- Categories own domain rules, unit meanings, bundle semantics, and category-specific search/download guidance.
- The LLM should decide semantic intent and research strategy from user wording and category context.
- Deterministic code validates tools, budgets, IDs, provenance, and side-effect gates.
- Public web evidence can inform or trigger category/download work, but it never directly authorizes queueing.

## Future cleanup recommended

- Split large legacy scheduling/tool classes; they contain copied retry helper methods that make prompt/tool maintenance harder.
- Add a prompt snapshot test per routed intent with approximate token/character budgets.
- Add scenario traces for small-model behavior: pure reminder, condition check, future download tracking, replacement download, category scaffold, and current public information search.

## Round 233 download-search review addendum

The Round 233 Linux logs showed that torrent acquisition failures were not caused by web research. They came from three download-search prompt/tool mismatches:

1. **Literal title preservation** — the LLM shortened `A Knight of the Seven Kingdoms` to `A Knight the Seven Kingdoms`, which reduced indexer recall. Generic tool context now carries the current user prompt so `search_media_torrents` can repair a lossy title argument from the literal user span without hard-coding any title or category.
2. **TV language-query ordering** — exact episode searches stopped after broad non-language results, so Italian rows were never queried. TV now tries the configured/requested media language first for exact episode labels and uses a category-owned exact-episode query ladder before falling back to broad pack/season forms.
3. **TV pack naming** — season packs are often named by episode range (`S01E01-06`), not by `Complete` or `Pack`. TV pack search now prioritizes provider-derived episode-range queries, including language-tagged forms, before generic pack words.

Generic code still does not parse TV coordinates. It passes the current prompt and category-neutral search scope; the TV category owns title/unit/bundle interpretation and query schemas. Batch recommendations are suppressed for broad title-only searches so unrelated SxxEyy rows cannot be presented as a fake multi-unit plan.

## Round 247 update — universal runtime date prompt context

Round 246 made metadata and public-web tool results self-contained with runtime
date evidence.  The follow-up change moves the same current date/time guidance to
the LLM provider boundary too: `RuntimePromptContext.ensure_messages()` is called
by the task-aware client and the lower-level provider client before the prompt is
logged or sent.

That means helper prompts that do not pass through the main assistant
`PromptBuilder`—intent routing, summarization, candidate adjudication, taste
extraction, torrent ranking, category research planning, and legacy direct
provider paths—still receive the current datetime/date/year/timezone plus rules
for interpreting relative wording such as today, tomorrow, next, upcoming,
latest, current, and recent.

Prompt builders and tool-result date payloads now share the same source:
`src/utils/runtime_prompt_context.py`.  Do not add a new handcrafted current-date
paragraph to individual prompts; reuse the shared context helper or route the
call through `TaskLLMClient`.

## Round 250 update — torrent candidate ranking after language satisfaction

The torrent-candidate reviewer now receives compact deterministic fields such as
`language_preference_status`, `tv_request_fit`, and `availability_seeders`.
Prompts explicitly state that once requested scope and preferred/acceptable
language are satisfied, seeder availability outranks marginal bitrate or extra
audio tracks.  `ITA+ENG`/`MULTI` is acceptable for an English preference only as
fallback evidence, not as a reason to ask the user about Italian or promote a
weak swarm over much healthier English/language-unknown candidates.

TV scope filtering remains category-owned.  The generic search tool may invoke a
category payload hook, but it does not parse TV coordinates itself.


## Round 255 - Metadata-backed title authority for torrent search

- TV torrent/Soulseek searches should query from category-owned title authority before relying on the literal user string. Provider canonical names, original titles, alternative titles, and localized titles are now exposed as bounded query titles.
- Candidate validation first checks exact release-title containment against provider-known aliases. The older TV token-window fallback remains only for items with no metadata title authority.
- For localized-language requests, localized titles from providers may be included in the query ladder, but language tokens still come only from the requested/configured language.
- Public web research remains appropriate for title ambiguity when metadata providers cannot resolve a show, but it should supply title/source evidence, not bypass torrent candidate validation.
