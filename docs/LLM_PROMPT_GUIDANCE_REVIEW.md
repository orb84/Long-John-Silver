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
