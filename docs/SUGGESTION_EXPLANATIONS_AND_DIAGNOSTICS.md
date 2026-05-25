# Suggestion explanations and diagnostics

Suggestions are not just buttons. Every category-owned suggestion should carry a short human explanation and enough machine-readable evidence for the UI, logs, and assistant to answer: **why was this suggested?**

## Storage contract

The stable `suggested_actions` table remains generic. Category-specific rationale lives in `metadata_json` using this shape:

```json
{
  "explanation": "I found 9 aired episodes in the guide and 8 local episode records, so S01E09 looks missing.",
  "confidence": "high",
  "evidence": {
    "reason_code": "next_missing_episode",
    "provider": "tvmaze",
    "provider_episode_count": 9,
    "downloaded_episode_count": 8,
    "missing_episode_count": 1,
    "library_evidence_source": "episode_units"
  }
}
```

`src/core/suggestion_support.py` is the shared adapter that parses this metadata for HTTP responses, assistant tools, and prompt memory. New category workflows should use the same keys when possible instead of inventing their own UI-only shape.

## TV missing-episode diagnostics

The TV workflow now audits the three facts that matter before suggesting missing episodes:

1. which provider guide was used;
2. how many aired episodes the provider reported;
3. how many local downloaded episode units were matched.

It also checks canonical title aliases before declaring an episode missing. This prevents the common false positive where the scanner saved units under `Pluribus` but the tracked item was queried as `pluribus` or `Pluribus (2025)`.

When old installs have only a progress row and no per-episode unit rows yet, TV suggestions use that progress row as a conservative fallback and label the evidence as `progress_backfill`. This is intentionally visible in the explanation so the user and agent know the confidence is lower than a proper per-episode unit match.

## Logs and audit events

Suggestion compilation writes compact diagnostics in two places:

- application logs, for live debugging;
- `category_item_processing_events` with `event_type = 'suggestions_compiled'`, for durable post-hoc inspection.

The durable event includes raw/persisted suggestion counts plus each persisted suggestion's title, action type, priority, and metadata payload. This should make strange suggestions debuggable without relying on noisy verbose logs.

## Assistant access

The assistant can inspect pending suggestions with the read-only `suggestions_list` tool. Prompt memory also injects a compact pending-suggestions summary, so the agent can explain current recommendations without guessing.

Mutation actions are still separate:

- `suggestion_approve`
- `suggestion_deny`
- `suggestion_approve_all`

The agent should call `suggestions_list` before answering questions about why something is suggested.

## UI presentation

The suggestions panel shows:

- a group-level “why” line;
- action descriptions written from category evidence;
- evidence pills such as `9 aired`, `8 local`, `1 missing`, `episode_units`, or `progress_backfill`.

The UI should make a suggestion feel like a reasoned proposal, not an opaque command.
