# Handover — Round 294 MCP Deep Adversarial Review

**Date:** 2026-08-15  
**Code state:** application source is unchanged from the delivered Round 294 MCP build. This handover records review findings only.

## Verdict

**FAIL for enabling write-capable MCP. Keep `LJS_MCP_ENABLED=0` pending repair.**

The architecture is worth preserving: thin MCP adapter, protocol-neutral public control plane, existing FastAPI/runtime, shared `ChatSessionRunner`/`ChatTurnRegistry`, private `ToolRegistry`, and `ActionGateway` reuse are all sound directions.

## Release blockers

1. **CRITICAL — LLM provider secret ownership:** `ljs.llm_set` can change provider/custom `api_base` while the canonical settings layer retains the old global API key. Task route resolution prefers that global key, creating a potential cross-provider/custom-endpoint secret leak.
2. **HIGH — capability collision:** Round 294 maps any declarative `risk_level=write/destructive` tool without explicit app capabilities to `config.write`. Current TV/movie download, scan, and delete actions/workflows therefore receive the wrong authorization domain.
3. **HIGH — cancellation truth:** `AgentDelegationService._cancel_result()` reports `cancelled` for no-match and still-unwinding cases.
4. **HIGH — web JWT scope expansion:** any valid existing web JWT becomes an MCP `ADMIN` principal despite having no MCP audience/scope/capability claims.

## Important majors after blockers

- LLM settings mutation is not failure-atomic: persistence happens before runtime reload; a failed receipt can coexist with a changed persisted state.
- `ToolResultEvidenceCollector` misses category `needs_confirmation`, so delegated continuation truth is incomplete.
- evidence IDs are sets sorted lexicographically, losing chronology/relevance.
- dedicated token carries no LJS `user_id`; `ConversationHandleService` discards the reserved `local` user returned by `ensure_session`, so per-user preferences/taste/behavior can disappear.
- handle “client binding” is not distinct in MCP because `client_id == principal_id`.
- authentication is run twice; handlers rebuild identity from `ctx.headers` instead of consuming the already validated principal.
- local network guard treats missing ASGI client identity as loopback.
- no handle TTL/quota/revoke public API/cleanup; no principal-level message/concurrency limit.
- `llm_test` forces authenticated provider network egress under a read capability.
- delegated result has no typed failed state; audit actor collapses all external principals to `external_agent`.
- exact `library_get` can expose canonical local file paths; decide whether that is intended for `library.read`.
- turn ownership is process-local; current `main.py` is single-worker, so document that constraint until distributed leases exist.

## Acceptance gaps

- live continuity test uses one MCP `Client`, not a fresh connection/client and not an LJS restart;
- write-denial passes on any error rather than the specific capability-denied contract;
- live script never exercises `ljs.agent_cancel`;
- download-count evidence ignores the actual `active` field;
- no gate enumerates current category mutating tools and verifies semantic capability ownership;
- auth test explicitly asserts web JWT -> ADMIN;
- real MCP SDK and full pytest were not runnable in the review sandbox (`mcp` and `aiosqlite` missing).

## Green evidence

- `MCP_CONTROL_PLANE_PASS`
- `MCP_ARCHITECTURE_PASS`
- `ROUND293_ELLA_SEARCH_SELECTION_CANCEL_PASS`
- AI intent/context guards PASS
- security/category/docs/model-facade guards PASS
- `check_architecture.py`: 0 HARD findings
- `compileall`: PASS

Custom red-team probes reproduced wrong category capability, false cancellation states, fail-open missing client identity, and missed `needs_confirmation` evidence.

## Recommended next work

Create a focused **Round 294R repair**, in this order:

1. provider-secret/endpoint ownership;
2. explicit category application capability metadata;
3. MCP auth scope (dedicated token only first, or explicit MCP-scoped grants);
4. truthful cancellation states;
5. LLM mutation failure atomicity;
6. typed continuation/evidence + dedicated-user binding;
7. handle/admission/network/auth hardening;
8. adversarial dependency-complete live gate including reconnect+restart continuity and wire cancellation.

See `LJS_ROUND294_MCP_DEEP_ADVERSARIAL_REVIEW_2026-08-15.md` for full evidence and remediation details.
