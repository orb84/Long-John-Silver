# LJS Round 294 MCP / Public Control Plane — Deep Adversarial Review

**Date:** 2026-08-15  
**Reviewed baseline:** `ljs_round294_mcp_control_plane_agent_delegation_2026-08-15.zip`  
**Comparison baseline:** delivered Round 293 tree  
**Review mode:** hostile/read-only architecture, security, state-truth, concurrency, and acceptance review. **No application-code fixes were applied in this review.**

## Executive judgment

**Overall judgment: FAIL for enabling the new write-capable MCP/control-plane slice.**

The architectural direction remains good: MCP is a thin adapter over an application-owned control plane; it reuses the existing LJS FastAPI/runtime, `ChatSessionRunner`, shared turn registry, private `ToolRegistry`, and `ActionGateway`; it does not create a second scheduler/downloader/domain runtime; and capability enforcement exists both before LLM tool exposure and at execution.

The problem is not the basic architecture. The problem is that several **security and truth contracts at the new boundaries are wrong or incomplete**. Four findings are release-blocking:

1. **CRITICAL — LLM provider/API-key ownership can cross providers/endpoints through `ljs.llm_set`.** The MCP sanitizer correctly refuses incoming secrets, but the canonical settings mutation can retain the *old* global API key while changing provider and/or `api_base`. Because task execution prefers the global key before provider-scoped KeyStore lookup, a caller with `config.llm.write` can redirect later model traffic to another/custom endpoint while LJS still attaches the previous provider's secret.
2. **HIGH — `config.write` is a catch-all authorization bucket for declarative category writes/destructive actions.** Current TV/movie download, scan, and delete declarations are classified as `config.write`, not `downloads.write` / filesystem-specific capabilities. This is a real capability-confusion path into queueing and destructive category workflows.
3. **HIGH — public cancellation truth regresses Round 293.** `ljs.agent_cancel` returns terminal `cancelled` even when there was no matching active turn or when cancellation timed out and a child is still unwinding.
4. **HIGH — generic web JWTs become unrestricted MCP `ADMIN` principals.** The existing web JWT has no MCP audience/scope/role claim, but the MCP resolver maps any valid one to `ADMIN`, bypassing the dedicated MCP capability set and expanding a generic web credential into a new external-agent authority.

In addition, the live acceptance currently gives materially stronger confidence than it deserves: it does not prove continuity across a fresh MCP client/connection or restart, accepts *any* error as a successful write-authorization denial, does not exercise wire-level cancellation, and was not runnable in the review sandbox because the declared `mcp` and `aiosqlite` dependencies are absent.

**Recommended immediate operational state: keep `LJS_MCP_ENABLED=0`.** If the owner deliberately performs exploratory local testing before fixes, use only the dedicated token with read/delegate capabilities, keep `allow_actions=false`, and do not reuse a web JWT.

---

## Scope and method

I treated the Round 294 tree as untrusted evidence rather than relying on its implementation handover. I:

- read root `AGENTS.md` and `architecture.md` first;
- compared Round 294 against the delivered Round 293 tree to separate newly introduced behavior from pre-existing behavior newly exposed by MCP;
- traced authentication → principal → invocation context → tool-policy intersection → execution recheck;
- traced conversation handle mint/resolve → shared turn registry → cancellation → delegated public status;
- traced `ljs.llm_set` → public sanitizer → `ActionGateway` → `SettingsActionHandler` → runtime route resolution;
- traced category declarations → category tool factory → capability resolver → tool policy → category action/workflow execution;
- reviewed live acceptance for false positives and missing adversarial cases;
- ran the dependency-light architecture/security/regression gates and custom adversarial probes;
- verified the intended MCP ASGI mount/lifespan model against the current official MCP Python SDK documentation.

## What is genuinely good in the latest work

The latest work has several strong architectural decisions that should be preserved during repair:

- **MCP is an adapter, not the domain architecture.** `src/integrations/mcp_server.py` delegates to `PublicControlPlane` rather than exporting private agent tools or reconstructing domain orchestration.
- **No second LJS runtime.** MCP is mounted inside the existing FastAPI application (`src/web/app.py:160-195`). This avoids a second scheduler/downloader/database owner.
- **The host-lifespan pattern is structurally correct.** `MCPHostRuntime` runs the mounted MCP SDK session manager from the top-level FastAPI lifespan; the current MCP Python SDK explicitly requires this for mounted sub-apps.
- **Tool authorization is enforced twice.** Tool names are filtered before the LLM sees definitions and rechecked by `ToolRegistry.execute()` at execution time. That is the correct defense-in-depth shape.
- **`allow_actions=false` is the delegated default.** This is a valuable independent safety switch.
- **Unknown private tools fail closed for constrained principals.** That is much safer than automatically treating future tools as read-only.
- **Conversation handles are opaque and do not expose internal `session_id`.** They are high entropy and persisted separately.
- **The shared `ChatTurnRegistry` move is correct.** Turn ownership no longer belongs to the Web adapter; Web and MCP can use the same process-level cancellation authority.
- **No public generic ActionGateway escape hatch.** MCP does not expose `execute(any_action, any_args)` or the private `ToolRegistry` catalog.
- **The LLM public sanitizer does remove incoming API keys.** The credential problem is downstream provider ownership, not a failure to strip submitted secrets.
- **Round 293 cancellation/search regression tests still pass.** The new defect is specifically the public MCP translation of registry state, not the underlying registry/search ownership.

These strengths are why I recommend repairing the slice rather than abandoning it.

---

# Findings

## R294-01 — CRITICAL — Cross-provider/custom-endpoint API-key leakage through `ljs.llm_set`

**Classification:** pre-existing canonical settings bug, **newly security-relevant because Round 294 exposes it as an external control-plane mutation**.

### Evidence

`PublicLLMConfigurationService._sanitize_values()` intentionally allows `provider` and `api_base` but strips `api_key` (`src/core/public_control_plane.py`, around lines 230–268).

`SettingsActionHandler._apply_llm_route_fields()` mutates the existing global LLM object (`src/web/action_handlers/settings.py:57-83`):

- a supplied `api_base` replaces the endpoint;
- a supplied provider changes `active_provider`;
- if no `api_key` was supplied, it looks up a key for the new provider **only if one exists**;
- if no new-provider key exists, the pre-existing global `llm.api_key` is left untouched.

The task client later resolves credentials in this order (`src/llm_providers/task_client.py`, `_resolve_api_key`):

1. task/tier key;
2. **global `config.api_key`**;
3. provider-scoped KeyStore key.

It similarly prefers global `config.api_base` before the provider preset.

### Concrete failure

Assume provider A is configured with a global key. An MCP principal with `config.llm.write` calls `ljs.llm_set` with provider B and an attacker-controlled/custom `api_base`, but cannot and does not send an API key. LJS may retain provider A's global secret and then send it as a bearer credential to the newly selected endpoint on a later model call.

This is not merely “wrong model configuration”; it is a **secret-ownership violation and potential secret exfiltration path**.

### Required repair

Fix the canonical settings/runtime model, not just MCP:

- provider changes must never retain an API key owned by a different provider;
- preferably remove global cross-provider key fallback and resolve provider-owned credentials from KeyStore;
- if a global key field must remain for compatibility, attach provider ownership and clear it when ownership changes;
- separate custom endpoint mutation from ordinary model/provider selection (`config.llm.endpoint.write` or admin-only);
- validate endpoint scheme/host policy as appropriate;
- add regression tests proving provider A's key cannot be used after switching to provider B/custom endpoint.

**Release blocker.**

---

## R294-02 — HIGH — `config.write` authorizes downloads, scans, and destructive category actions

**Classification:** introduced by the Round 294 capability resolver interacting with pre-existing category declarations.

### Evidence

`AgentToolCapabilityResolver.for_tool()` (`src/ai/tool_capabilities.py:94-111`) does this for any declarative tool that lacks explicit application capabilities:

```text
risk_level in {write, destructive} -> CONFIG_WRITE
```

Current category contracts use `risk_level` for **mutation risk**, not configuration ownership. Examples in `src/core/categories/tv_workflows.py` include:

- `tv.download_next_missing_episode` — write;
- `tv.download_specific_episode` — write;
- `tv.download_season_pack` — write;
- `tv.download_missing_batch` — write;
- `tv.scan_library` — write;
- `tv.delete_item` — destructive.

Movie declarations include write/destructive workflows such as `movie.download_movie`, `movie.scan_library`, and `movie.delete_item`.

`CategoryActionDeclaration` and `CategoryWorkflowDeclaration` currently have no application-authorization capability field (`src/core/domain_models/categories.py:141-175`). `CategoryScopedTool` propagates only `risk_level`, so the new capability resolver classifies these writes as `config.write`.

`AgentToolPolicy` exposes category action tools under CONFIG (`src/ai/tool_policy.py:160-171`, `_category_action_tools` around 286-303), while `execute_category_action` itself is also listed as a generic CONFIG write. `BaseCategory.execute_action()` routes an action's declared `operation` into its concrete workflow. Therefore this is an executable authorization path, not dead metadata.

A dependency-light adversarial probe reproduced the classification:

```text
write_tool_required= ['config.write'] mutating= True
```

### Impact

A principal intended to change configuration can gain download/file-management authority when delegated actions are enabled. With user confirmation, the same semantic collision can extend to destructive category actions such as deleting media/files.

That violates the central Round 294 claim that application capabilities constrain the inner LJS agent.

### Required repair

Do **not** patch this with tool-name blacklists.

Add explicit application authorization metadata to the category contract, e.g. a typed `required_invocation_capabilities` on `CategoryActionDeclaration` / `CategoryWorkflowDeclaration`. Categories own whether an operation requires:

- `downloads.write`;
- a future `library.write` / `library.files.delete`;
- `tracking.write`;
- `config.write`;
- etc.

`risk_level` should remain confirmation/risk semantics and must not be treated as authorization-domain ownership. For constrained external principals, any write/destructive category declaration lacking explicit application capability metadata should fail closed.

**Release blocker.**

---

## R294-03 — HIGH — `ljs.agent_cancel` reports false terminal cancellation

**Classification:** introduced in Round 294 and contradicts Round 293 cancellation-truth semantics.

### Evidence

`ChatTurnRegistry.cancel_and_wait()` correctly distinguishes:

- no matching active turn;
- matching turn that settles;
- matching turn whose child boundary is still unwinding after the wait budget.

But `AgentDelegationService._cancel_result()` (`src/ai/agent_delegation.py:145-163`) always returns:

```text
status = AgentDelegationStatus.CANCELLED
```

Even when its own message says:

- “No matching active LJS agent turn was running.”
- “Cancellation was requested; a child boundary is still unwinding.”

Adversarial reproduction:

```text
cancel_no_active_status= cancelled
cancel_unsettled_status= cancelled
```

The current test only covers the happy “matched + settled” case.

### Impact

An outer agent can believe work has stopped when it never matched a turn or when work is still unwinding. That can produce unsafe retries, conflicting follow-up work, or false user claims.

### Required repair

Make cancellation truth explicit. Either add statuses such as `not_running`, `cancelling`, `cancelled`, or return fields such as:

```text
matched: bool
cancellation_requested: bool
settled: bool
```

Only emit terminal `cancelled` after the owning turn is actually settled. Add live and dependency-light tests for all branches.

**Release blocker.**

---

## R294-04 — HIGH — Any valid web JWT becomes MCP `ADMIN`

**Classification:** introduced in Round 294 MCP authentication.

### Evidence

`MCPPrincipalResolver._web_token_principal()` (`src/integrations/mcp_auth.py:62-78`) maps any valid existing web JWT to:

```text
capabilities={InvocationCapability.ADMIN}
```

The web JWT itself contains only username (`sub`) and expiry (`src/utils/auth.py:192-207`); it has no MCP audience, MCP scope, capability grant, or role. The user table has no role/capability field.

The Round 294 test explicitly **asserts** this behavior (`scripts/mcp_control_plane_tests.py`, `_authentication_contract`), so the green suite treats the unsafe scope expansion as expected behavior.

### Impact

This is best described as **credential-scope expansion**, not a claim that current LJS has a hidden lower-privilege web role. A generic browser/web credential becomes authority for the new external-agent/control-plane surface and bypasses `LJS_MCP_CAPABILITIES`. Because `ADMIN` also defeats fail-closed unknown-tool restrictions, the effect is broader than a dedicated least-privilege MCP token.

### Required repair

For the first local release, the safest contract is: **MCP accepts only `LJS_MCP_TOKEN`**. If web identities must later authenticate to MCP, issue or exchange for an MCP-scoped grant with explicit audience/capabilities. Do not infer `ADMIN` from possession of a generic web session JWT.

**Release blocker for the security claims of the current slice.**

---

## R294-05 — HIGH — `settings_update_llm` is not failure-atomic despite durable “truth” framing

**Classification:** pre-existing mutation design, newly exposed by `ljs.llm_set` and directly relevant to Round 294's receipt-truth promise.

### Evidence

`SettingsActionHandler.update_llm()` mutates the live settings object, calls `_sm.save(settings)`, and only **after persistence** calls `_assistant.update_settings(settings)` (`src/web/action_handlers/settings.py:45-58`). `AIAssistant.update_settings()` then performs multiple runtime updates, including persona reload and LLM client reconfiguration (`src/ai/assistant.py:370-380`), any of which can fail.

If runtime hot-reload raises after the save, `ToolRegistry.execute()` catches the exception and returns an error. `ActionGateway` then normalizes/persists a failed receipt. The durable setting may nevertheless already have changed.

Also, the handler mutates `self._sm.settings` in place before save, so even a persistence error can leave the in-memory settings object modified.

### Impact

The control plane can return “failed” while the configured state actually changed, which is precisely the mutation-truth ambiguity the gateway is supposed to avoid.

### Required repair

Introduce a prepare/validate/commit model or rollback:

- construct a detached candidate settings object;
- validate provider/model/endpoint and runtime reload feasibility;
- commit persistence and runtime state with rollback/explicit `uncertain` semantics if one side succeeds and the other fails;
- perform authoritative configured/effective read-back before returning success.

Add an adversarial fake assistant that throws after persistence and assert the receipt does not lie.

---

## R294-06 — MEDIUM/HIGH — Structured continuation evidence misses category confirmation state

`ToolResultEvidenceCollector` only sets `needs_input` when a mapping contains `clarification_required=True`. Category actions use `ActionReceipt(status="needs_confirmation", data={"requires_confirmation": True})`. The collector does not recognize that representation and does not expose its `action_id` as a persisted command receipt.

Adversarial reproduction:

```text
needs_confirmation_collected= False receipts= []
```

This means a delegated tool turn can structurally require user continuation while the public result says `complete` unless other intent routing happened to classify it as CLARIFY.

**Repair:** make tool results expose a typed continuation/evidence interface; do not recursively infer a growing list of ad-hoc dictionary keys. Preserve category confirmation semantics explicitly.

---

## R294-07 — MEDIUM — Structured evidence destroys chronology/relevance

`InvocationEvidence` stores result-set, candidate, and receipt IDs in sets. `ChatSessionRunner.collect_outcome()` sorts them lexicographically and truncates to fixed counts. Stable IDs may be correct, but ordering no longer represents occurrence, recency, candidate ranking, or causal receipt order.

For an outer agent, “which result set/action just happened?” can matter. A sorted set is not a semantic order.

**Repair:** ordered de-duplication at collection time, with explicit per-tool/result grouping where appropriate.

---

## R294-08 — MEDIUM — Dedicated MCP token loses the LJS user identity and therefore personalization

The dedicated principal has `user_id=None` (`src/integrations/mcp_auth.py:52-60`). `ConversationHandleService.mint()` calls `users.ensure_session(...)`; that repository creates/returns the reserved `local` user when no user was supplied. But the handle service ignores the returned session row and returns `user_id=principal.user_id`, still `None` (`src/core/conversation_handle.py`).

The assistant uses `user_id` for per-user preferences, behavior profile, taste context, and behavior recording. The dedicated-token path—the path that should be the safer least-privilege MCP path—therefore behaves less like the owner's normal LJS agent.

**Repair:** define user binding explicitly for dedicated principals. At minimum, use the resolved session user ID rather than throwing it away; ideally configure/map each MCP principal to a real LJS user without coupling security identity and display username.

---

## R294-09 — MEDIUM — “principal/client-bound” handles are only credential/principal-bound in the actual MCP adapter

`ConversationHandleService.resolve()` correctly checks both `principal_id` and `client_id`. However both dedicated and web-JWT MCP principals set `client_id = principal_id` (`src/integrations/mcp_auth.py:52-78`). Multiple client processes using the same token/JWT are indistinguishable and can resume the same handle if it leaks/is shared.

The documentation says handles are bound to both principal **and client identity**, which is stronger than the implementation.

**Repair:** either document this honestly as credential/principal binding for local v1, or introduce a server-authenticated client identity/registration. Do not trust a caller-provided “client id” header as an identity assertion.

---

## R294-10 — MEDIUM — Authentication is performed twice and the authoritative principal is discarded

`MCPAuthenticationBoundary` authenticates every HTTP request before MCP protocol handling, which is good, but discards the resolved principal. Every tool/resource handler then calls `_principal(ctx)` and re-runs authentication using `ctx.headers`.

This produces two authentication sources of truth, duplicate web-JWT database lookup, and dependence on transport header normalization. The current MCP SDK explicitly documents `Context.headers` as client-supplied input and warns not to treat a header itself as an identity assertion.

**Repair:** authenticate once at the ASGI/auth boundary and propagate an immutable validated principal through request state/context to the tool handler. Capability checks still remain inside application services.

---

## R294-11 — MEDIUM — Local-only network guard fails open when ASGI client identity is absent

`LocalMCPNetworkBoundary._is_loopback(None)` returns `True` (`src/integrations/mcp_network.py:22-25`). For a security boundary, an unknown client address should not be treated as loopback.

Adversarial reproduction:

```text
network_none_loopback= True
```

The reverse-proxy hazard is documented (“do not expose through a reverse proxy”), which is good, and the MCP SDK's own localhost Host/Origin protection provides defense in depth. But the application boundary should still fail closed.

**Repair:** `None`/malformed client identity => reject unless an explicitly trusted in-process transport path is used.

---

## R294-12 — MEDIUM — Conversation handles have no expiry, quota, close/revoke surface, or cleanup policy

The migration stores `created_at`, `last_active_at`, and `revoked_at`, and the repository has a `revoke()` method, but the service/public surface exposes no close/revoke operation, no TTL, no expiry, no reaper, and no per-principal quota. Every new `agent_message` without a handle creates a durable internal session plus a durable external-handle row.

A buggy or malicious authenticated local agent can grow durable state indefinitely. `resolve()` also writes/commits `last_active_at` on every continuation.

**Repair:** retention/TTL, max open handles per principal, explicit close/revoke, cleanup job, and touch throttling.

---

## R294-13 — MEDIUM — No application-level message-size, turn-rate, or per-principal concurrency limit

There is one-live-turn-per-**conversation**, but an authenticated principal can mint many conversations and run them in parallel. `AgentDelegationService.send_message()` accepts an arbitrary Python string without an application char/token ceiling or per-principal admission control.

This is a local-only endpoint, but local agents are exactly the kind of automation that can accidentally produce unbounded parallel provider cost/load.

**Repair:** principal-scoped concurrent-turn limit, bounded request size, rate/admission control, conversation quota.

---

## R294-14 — MEDIUM — `llm_test` is network egress but requires only `config.llm.read`

`PublicLLMConfigurationService.test()` is authorized with `CONFIG_LLM_READ` and calls `get_models_for_provider(... force_refresh=True)`. `ModelCatalog._fetch_models()` performs a real outbound HTTP request to the provider's models endpoint and may attach the provider's stored API key.

So the default read-capable MCP principal can actively force authenticated provider traffic. That may be an intended diagnostic capability, but it is not semantically a pure configuration read.

**Repair:** separate `config.llm.probe` / diagnostics capability, or make ordinary reads use cache-only state and reserve forced network probes for an explicit capability.

---

## R294-15 — MEDIUM — Public delegated result model has no structured failure state

`AgentDelegationStatus` has only `complete`, `needs_input`, `busy`, and `cancelled`. `AgentDelegationService._await_turn()` catches only `CancelledError`; an ordinary runner failure escapes as an MCP/protocol exception rather than a stable application outcome.

**Repair:** decide deliberately whether application failures are MCP errors or a typed `failed` result. If the latter, include a safe error code and recoverability marker, not raw exception prose.

---

## R294-16 — MEDIUM — Audit actor collapses all external principals to `external_agent`

`ToolExecutionContextFactory` sets `actor="external_agent"` for every untrusted principal while separately storing `principal_id`. Mutation tools that use actor/source in `ActionCommand` can therefore lose the actual external identity in the canonical actor field.

**Repair:** use a stable actor such as `external_agent:<principal_id>` or the principal ID itself, while keeping source/client in their own fields.

---

## R294-17 — LOW/MEDIUM — Public status can expose raw internal exception strings

`PublicStatusService.get()` places `str(exc)` directly into the public `critical` list when storage-report construction fails. `status.read` is part of the default MCP capability set.

**Repair:** public status should return a generic degraded/error code; detailed redacted diagnostic text belongs behind `diagnostics.read`.

---

## R294-18 — MEDIUM — `library_get` can expose exact local file paths

`PublicLibraryService.get_item()` returns the full canonical object rather than a redacted external view. Canonical category units include fields such as `file_path`. This is not the same as the bounded summary resource, and the refined plan only explicitly prohibited raw filesystem paths in MCP **resources**, so this is not a direct plan violation. It is nevertheless sensitive local-machine information on a generic `library.read` tool.

**Repair:** decide and document whether exact paths are intentionally part of `library.read`. If not, add an external canonical projection that preserves semantic library truth while removing machine paths.

---

## R294-19 — MEDIUM — Conversation mint is not transactional and continuation touches commit every request

`ConversationHandleService.mint()` first commits an internal session through `ensure_session()`, then separately commits the external handle. If handle persistence fails, the internal session remains orphaned. `resolve()` then performs a DB update+commit on every successful continuation solely to touch `last_active_at`.

**Repair:** transactionally mint the session+handle where practical; throttle last-active writes.

---

## R294-20 — DEPLOYMENT CONSTRAINT — Chat turn ownership is process-local

The shared `ChatTurnRegistry` is intentionally an in-memory process-local lock. Current `main.py` creates a normal single `uvicorn.Server` without worker fan-out, so this is **not a current single-process defect**.

However, if LJS is later run with multiple ASGI workers, the same durable conversation handle can be served by different workers and one-live-turn-per-conversation is no longer guaranteed. Modern MCP 2026-07-28 HTTP requests are sessionless, so transport affinity does not solve application turn ownership.

**Repair:** explicitly document “single worker only” for MCP until a DB/distributed lease owns turn concurrency, or implement such a lease before multi-worker deployment.

---

# Acceptance / test-quality findings

## T-01 — HIGH — Live continuity test does not test reconnect continuity

`scripts/mcp_live_acceptance.py` creates one `Client` and performs both delegated turns on that same client. The refined plan specifically required that a **new HTTP connection** can continue the same server-minted conversation handle.

For the modern 2026-07-28 MCP path, requests are protocol-sessionless, so persistence should work—but the acceptance still needs to prove the application guarantee and any legacy/fallback behavior.

**Required test:** call 1 using client/transport A; close A; create fresh HTTP transport/client B; continue using the same `conversation_id`. Then restart LJS and repeat a continuation to prove DB durability.

## T-02 — MEDIUM — Write-denial live test accepts the wrong failure as success

`_check_read_only_write_denial()` marks denial successful for **any** MCP `is_error` result or any `MCPError`. Schema errors, server failures, DB errors, or handler exceptions can therefore produce a false PASS.

**Required test:** assert the specific capability-denied error/code/structured shape.

## T-03 — HIGH — No live/wire-level cancellation acceptance

The catalog test proves `ljs.agent_cancel` exists, but the live script never calls it. The dependency-light test covers only a fake runner's happy settled case and therefore missed R294-03.

**Required cases:** matching settled turn; matching but still-unwinding turn; wrong turn ID; no active turn; cancellation of the owning HTTP/tool request; child search/download cancellation propagation.

## T-04 — LOW — Live download count evidence is always wrong

`_count_items()` checks `items`, `downloads`, `results`, or `count`. `ljs.downloads_list` returns `{active: [...], total: ...}`. The evidence therefore reports zero downloads even when active downloads were returned.

## T-05 — HIGH — Capability tests do not test category-semantic ownership

The dependency-light capability test verifies only representative generic names (`web_search`, `queue_download`, unknown tool). It never builds the current category declarations and checks that every write/destructive action maps to the correct application capability. That is why R294-02 passed all green gates.

**Required gate:** enumerate every registered tool under a constrained context and assert explicit capability ownership for every mutating tool; category write/destructive declarations without explicit application capability should fail the test.

## T-06 — HIGH — Auth test encodes unrestricted web JWT as success

The authentication contract explicitly asserts that a web JWT contains `ADMIN`, so it cannot catch R294-04.

## T-07 — RELEASE ACCEPTANCE GAP — Real MCP SDK/runtime was not executable in this environment

The review environment lacks both `mcp` and `aiosqlite`. Full pytest collection stops at `ModuleNotFoundError: aiosqlite`, and the real MCP adapter cannot be instantiated. The dependency-light MCP harness and static guards are valuable but cannot prove real SDK registration, request-context behavior, middleware composition, or wire interoperability.

The current official SDK documentation does support the chosen **shape**—mounted Streamable HTTP with the top-level host entering `session_manager.run()`—so I found no design reason to undo that wiring. It still requires actual dependency-complete live acceptance before release.

---

# Validation evidence from this review

The following existing gates pass on the reviewed Round 294 tree:

```text
MCP_CONTROL_PLANE_PASS
MCP_ARCHITECTURE_PASS
ROUND293_ELLA_SEARCH_SELECTION_CANCEL_PASS
AI intent architecture guard passed
AI context architecture guard passed
Security architecture guard passed
Category architecture guard passed
Public documentation audit passed
Model facade import audit passed
check_architecture.py: 0 HARD findings
python -m compileall -q src scripts main.py: PASS
```

The general architecture audit still reports many pre-existing RISK/ADVISORY size findings, especially `AIAssistant`, agent loops, `PlanCoordinator`, and `PlanExecutor`, but **the new MCP/control-plane slice did not create a new God-class architecture**. The latest work is mostly composed from small collaborators.

Full pytest collection cannot start here:

```text
ModuleNotFoundError: No module named 'aiosqlite'
```

The real MCP SDK is also absent:

```text
ModuleNotFoundError: No module named 'mcp'
```

Custom adversarial probes reproduced:

```text
write_tool_required= ['config.write'] mutating= True
cancel_no_active_status= cancelled
cancel_unsettled_status= cancelled
network_none_loopback= True
needs_confirmation_collected= False receipts= []
```

---

# Root-cause assessment

The latest implementation did **not** fail because the MCP plan was fundamentally wrong. It failed in a more instructive way: the new external boundary forced several older, loosely typed concepts to become security contracts before those concepts were ready.

The most important root causes are:

1. **Risk classification was reused as authorization classification.** `risk_level="write"` answers “does this mutate?”; it does not answer “which principal capability owns this mutation?” Conflating those two concepts creates the `config.write` escape.
2. **Provider routes and secrets are not ownership-coupled.** A single global `api_key` / `api_base` fallback allows provider identity to drift away from credential/endpoint identity.
3. **Public state vocabulary is too small.** Collapsing cancellation outcomes into one `cancelled` enum and delegated failures into transport exceptions loses truth that the lower layers already know.
4. **The adapter duplicated authentication rather than carrying authenticated identity.** Authentication should yield one immutable principal; handlers should consume it, not reconstruct it from client headers.
5. **The acceptance suite tests the happy architecture, not adversarial boundary semantics.** The guards prove “the intended classes are present and the generic happy path works,” but not “every current category write is correctly scoped” or “failure branches remain truthful.”

These are repairable without abandoning the control-plane architecture.

---

# Recommended repair order

## Phase A — Close security/truth blockers before any live write testing

1. Remove generic web-JWT → MCP ADMIN authentication; dedicated token only for v1, or implement explicit MCP-scoped grants.
2. Add explicit application capabilities to category action/workflow declarations and fail closed for constrained external writes missing that metadata.
3. Fix provider/API-key/api-base ownership in the canonical LLM settings/runtime. Add explicit custom-endpoint authorization.
4. Fix cancellation public states so terminal truth matches `ChatTurnRegistry`.
5. Fix LLM mutation failure atomicity/read-back semantics.

Keep MCP disabled while these are unresolved.

## Phase B — Make agent-to-agent continuation truth complete

1. Replace recursive ad-hoc evidence inference with typed tool-result evidence/continuation interfaces.
2. Preserve ordered result/receipt evidence.
3. Add a structured failed outcome or a deliberate, documented MCP-error contract.
4. Preserve actual external principal identity in audit actor fields.
5. Bind dedicated MCP principals to an LJS user so preferences/taste/behavior match normal agent behavior.

## Phase C — Harden lifecycle and admission control

1. Fail closed when client network identity is missing.
2. Authenticate once and propagate the validated principal.
3. Add conversation TTL/quota/revoke/cleanup.
4. Add principal-level message-size/concurrency/rate limits.
5. Keep single-worker deployment explicit until turn ownership is distributed.
6. Decide whether `library.read` may expose exact local paths.
7. Separate `llm_test` network probing from pure configuration read.

## Phase D — Replace the current acceptance with an adversarial live gate

A dependency-complete local run should prove at minimum:

- exact MCP catalog and auth-before-catalog;
- dedicated read-only token cannot mutate;
- denial is specifically capability denial;
- every registered mutating private/category tool has explicit correct application capability ownership;
- `config.write` cannot queue downloads, delete library files, or perform unrelated mutations;
- provider changes never reuse another provider's API key;
- custom endpoint changes cannot receive a previously stored unrelated key;
- new MCP client/connection can resume a conversation handle;
- LJS restart can resume the same handle;
- principal A cannot resume principal B's handle;
- matching/no-match/unsettled cancellation states are truthful;
- live cancellation propagates into the Round 293 child search cancellation tree;
- `needs_confirmation` becomes structured `needs_input`/continuation state;
- dedicated-token delegation sees the intended user's preferences/taste context;
- no secret/path leakage outside the explicitly authorized projection;
- quotas/limits reject abusive conversation/message concurrency;
- full pytest and all architecture guards pass.

Only after that gate should write-capable MCP be enabled.

---

# Final recommendation

**Preserve the architecture, reject the current release state.**

I would not revert the public-control-plane concept, `ChatSessionRunner` delegation, shared `ChatTurnRegistry`, opaque handles, or the thin MCP adapter. Those are directionally correct and relatively clean.

I would, however, treat the current Round 294 as a **review-failed implementation candidate**. The capability model and LLM provider credential ownership need correction before MCP becomes an enabled control surface. The cancellation/evidence/acceptance gaps then need to be repaired before the system can truthfully claim that an outer agent receives authoritative state rather than optimistic status.

The safest next engineering step is a focused **Round 294R repair** against the blocker set above, followed by a fresh adversarial review and then the dependency-complete local MCP live gate.
