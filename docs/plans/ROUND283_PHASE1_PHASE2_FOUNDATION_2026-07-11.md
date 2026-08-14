# Round 283 — Phase 1 and Phase 2 Foundation

## Implemented

### Durable command and receipt model

- Extended `ActionCommand` with command, correlation, idempotency, actor, and timestamp fields.
- Extended `ActionResult` into an authoritative mutation receipt.
- Upgraded `ActionGateway` to record command start/completion, return typed failures, and replay successful short-window idempotent retries.
- Kept `action_events` as a compatibility projection.
- Added migration 112 with `action_command_receipts` and append-only `operational_events`.
- Routed assistant `queue_download` execution through the durable command contract.
- Added command receipt metadata to queue tool results.

### State authority and reconciliation foundation

- Added `StateAuthorityRegistry`, `AuthorityRule`, and typed `FactVerdict`.
- Encoded authorities for local presence, live activity, history, automation permission, candidate selection, mutation outcome, and provider availability.
- Added a read-only `DownloadStateReconciler` for completed-history/canonical-absence contradictions.

## Deliberately not completed in this slice

- UI actions and scheduler mutations have not all been migrated to domain-specific typed command schemas yet.
- Import, cancel, schedule, configuration, and category-item receipts still need explicit result adapters.
- The recovery inbox and repair commands are not yet exposed in the UI.
- The reconciler currently detects one high-value contradiction class only.
- Selective-pack lifecycle remains represented by existing queue receipts rather than a dedicated persisted state machine.

## Next implementation order

1. Add explicit receipt adapters for queue, cancel, import, schedule, configuration, and category-item mutation.
2. Migrate scheduler/watch queue actions through the same command executor.
3. Add operational-event entity descriptors and state transitions for downloads/imports.
4. Persist and expose reconciliation findings through a recovery-inbox repository/API.
5. Add preview and execute repair commands; never mutate directly from diagnostics.
6. Add active-row/live-handle, satisfied-watch, disconnected-drive, and orphan-result-set reconcilers.
7. Model selective bundle states from registration through canonical import verification.
