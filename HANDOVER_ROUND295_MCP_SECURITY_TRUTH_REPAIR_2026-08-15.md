# LJS Round 295 — MCP Security / Truth Repair Handover

Date: 2026-08-15
Baseline: delivered Round 294 MCP control-plane snapshot, after the deep adversarial review.
Status: **implementation repair complete; all dependency-light/static project gates pass. Real dependency-complete pytest + MCP SDK HTTP acceptance remains a required machine-local gate because this sandbox lacks the declared runtime packages.**

## 1. Why this round exists

Round 294 established the right overall architecture — a protocol-neutral public control plane, a thin MCP adapter, `ChatSessionRunner` as semantic agent boundary, shared turn cancellation, opaque conversation handles, and `ActionGateway` for curated deterministic writes — but the adversarial review found release-blocking contract defects:

1. provider credentials could survive a provider/endpoint transition and be sent to the wrong endpoint;
2. category `risk_level` had been confused with authorization ownership, allowing download/destructive actions to inherit `config.write`;
3. public cancellation collapsed `not running` and `still unwinding` into false terminal `cancelled` truth;
4. generic LJS Web JWTs were widened into MCP administrator authority;
5. confirmation/evidence, user binding, handle lifecycle, network fail-closed behavior, concurrency and test coverage were weaker than the documentation claimed.

This round repairs those defects **at their canonical owners**, not with MCP tool-name blacklists or transport-specific workarounds.

## 2. Final architecture retained

The core direction remains:

```text
External MCP client
        |
        v
Local-only MCP Streamable HTTP adapter (/mcp)
        |
        v
Protocol-neutral LJS PublicControlPlane
        |
        +--> AgentDelegationService --> ChatSessionRunner --> AIAssistant --> private ToolRegistry
        |
        +--> bounded read services
        |
        +--> curated mutation services --> ActionGateway
```

MCP still does **not** own another LJS runtime, scheduler, downloader, conversation engine, tool registry, or domain orchestration layer.

## 3. Security / authorization repairs

### 3.1 Dedicated MCP credential only

Local MCP v1 now accepts only `LJS_MCP_TOKEN` (minimum 32 characters). Ordinary LJS Web JWTs are explicitly rejected instead of being widened to `admin`.

Authentication happens once at the MCP ASGI boundary and the validated `InvocationPrincipal` is propagated through `MCPRequestPrincipalContext`; individual tool handlers do not re-authenticate request headers.

`LocalMCPNetworkBoundary` fails closed for:

- non-loopback addresses;
- absent/unknown ASGI client origin.

The current local v1 supports one configured token/principal/client tuple at a time. Do not share it between unrelated external clients.

### 3.2 Explicit application capabilities, independent of risk

`CategoryActionDeclaration` and `CategoryWorkflowDeclaration` now carry explicit `invocation_capabilities_required`.

`risk_level`, `destructive`, and `requires_confirmation` are **not** treated as authorization domains.

Relevant capabilities include:

- `agent.delegate`, `agent.read`;
- `library.read`, `library.write`, `library.files.delete`;
- `downloads.read`, `downloads.write`;
- `tracking.write`;
- `config.write`;
- `config.llm.read`, `config.llm.probe`, `config.llm.write`, `config.llm.endpoint.write`;
- `diagnostics.read`;
- `admin`.

Unknown/unannotated mutating private tools fail closed to `admin` for constrained principals.

Enforcement remains defense-in-depth:

1. before tool definitions reach the LLM;
2. again at `ToolRegistry.execute()`;
3. delegated calls default `allow_actions=false` and remove mutation authority for that turn.

### 3.3 Category authorization truth

The earlier `risk_level="write" -> config.write` inference is gone.

Examples now resolve correctly:

- TV/movie download workflows -> `downloads.write`;
- category/library persistence -> `library.write`;
- deleting local library files -> additional `library.files.delete`;
- generic unknown mutation -> `admin` fail-closed.

Definition-backed workflows now receive the originating `ToolExecutionContext`, closing hidden-write cases where a nominally read workflow persists when internal-only identifiers are supplied.

### 3.4 Agent-visible category truth

A second review found category action declarations that were advertised despite lacking a concrete executor. Base/movie/TV scan/consolidate declarations that do not own a concrete category executor are no longer LLM-visible.

Movie/TV delete now has a real token-bound two-phase category workflow:

- untracking requires `library.write`;
- `delete_files=true` additionally requires `library.files.delete` **before path evidence is computed**;
- exact confirmation token is required for the destructive step.

Ordinary delegated assistant turns still do **not** advertise destructive category tools. `AIAssistant` has no canonical policy-level `confirmed=True` continuation state today, so exposing them would create an unreachable/double-confirmation contract. Explicit category callers can use the concrete workflow contract. A future agent-side destructive confirmation feature should add a real pending-confirmation seam rather than a prompt/name heuristic.

## 4. LLM credential / endpoint ownership repair

### 4.1 Canonical mutation service

LLM route mutation is centralized in `src/llm_providers/settings_mutation.py` as `LLMSettingsMutationService`.

It is now used by:

- normal settings updates;
- MCP `ljs.llm_set` through `ActionGateway`;
- provider activation;
- first-run LLM setup.

This avoids fixing MCP while leaving the same credential leak reachable through normal UI/setup paths.

### 4.2 Route-owned secrets

Provider or endpoint transitions clear inherited global/tier API keys unless an explicit replacement belongs to the newly configured route.

`ProviderCredentialPolicy` permits automatic provider KeyStore attachment only when the resolved endpoint is the provider preset's canonical endpoint. Registry/operator/custom endpoint overrides are honored for routing but never silently inherit the canonical provider secret.

The MCP mutation surface itself never accepts API-key fields.

### 4.3 Endpoint authority split

Public LLM capabilities are intentionally separate:

- `config.llm.read` — read configured/effective routing;
- `config.llm.probe` — perform outbound provider/model probing (`ljs.llm_test`);
- `config.llm.write` — ordinary model/provider/tier routing changes;
- `config.llm.endpoint.write` — additionally required for any public `api_base` change.

This prevents an ordinary configuration writer from redirecting future provider traffic to an arbitrary endpoint.

### 4.4 Persistence/runtime rollback

`LLMSettingsMutationService` computes on a detached candidate and preserves the identity of the live `Settings`/`LLMConfig` objects. Persistence and runtime reload are treated as one transaction boundary with best-effort restoration of:

- live configuration;
- saved settings;
- provider registry active provider;
- runtime assistant routing.

A failed runtime reload must not be reported as a successful durable configuration mutation.

## 5. Conversation / principal lifecycle repair

Opaque MCP conversation handles are now:

- high entropy and server minted;
- bound to principal **and client and canonical user**;
- rejected if the configured user binding changes;
- inactive-expiring (30-day default);
- quota-bounded (100 active per principal/client default);
- touched on use without write-on-every-request churn;
- rollback-cleaned if mint persistence fails;
- explicitly revocable via `ljs.agent_close`.

Revocation/expiry removes both the external handle and its private external session/conversation history. A non-`local` `LJS_MCP_USER_ID` must already exist; a typo cannot silently create a preference-less shadow user.

Delegated agent messages are bounded to 65,536 characters and provider-backed agent turns are admission-limited to 4 active turns per principal/client by default.

## 6. Cancellation truth repair

Public lifecycle states now preserve the shared `ChatTurnRegistry` truth:

- `not_running` — no matching active turn;
- `cancelling` — cancellation was requested but the task/children are still unwinding;
- `cancelled` — the owned turn has actually settled;
- `closed` — conversation handle has been revoked after any live turn settled.

This preserves the Round 293 cancellation-truth invariant rather than reintroducing optimistic terminal reporting at the MCP boundary.

## 7. Structured outcome repair

`InvocationEvidence` is ordered rather than set-based.

Delegated outcomes preserve stable structural evidence only:

- `result_set_ids`;
- `candidate_ids`;
- persisted `action_receipt_ids`.

`requires_confirmation`, `confirmation_required`, `needs_confirmation`, and clarification results propagate `needs_input` instead of being reported as complete.

Unhandled runner exceptions become bounded `failed` outcomes; raw private exception strings belong to diagnostics, not the default public agent result.

## 8. Public read/control hardening

- status failures return generic public health text rather than raw exception strings;
- canonical library objects are recursively stripped of host-local path fields;
- diagnostics remain bounded/redacted;
- `ljs.llm_test` is no longer ordinary read authority because it performs authenticated outbound network work;
- external audit actor identity retains the concrete principal rather than collapsing every external caller to one generic actor.

## 9. Current MCP public surface

Exactly 12 tools:

```text
ljs.agent_message
ljs.agent_cancel
ljs.agent_close
ljs.status
ljs.capabilities
ljs.library_list
ljs.library_get
ljs.downloads_list
ljs.llm_get
ljs.llm_test
ljs.llm_set
ljs.diagnostics_recent
```

Resources:

```text
ljs://status
ljs://capabilities
ljs://library/summary
ljs://downloads/active
ljs://configuration/llm
```

Still intentionally absent:

- stdio runtime owner;
- remote-network MCP;
- OAuth/TLS resource server;
- arbitrary `ActionGateway.execute(name,args)`;
- exported private `ToolRegistry`;
- public raw torrent/Soulseek/search/download micro-tools;
- invented long-work/MCP Tasks clone.

## 10. Tests added/strengthened

`scripts/mcp_control_plane_tests.py` now covers, without MCP/runtime dependencies:

- disabled/invalid configuration fail-closed behavior;
- dedicated-token-only auth and generic Web-JWT rejection;
- authenticate-once ASGI principal propagation;
- missing/non-loopback client fail-closed behavior;
- exact category capability ownership;
- unknown mutation fail-closed behavior;
- definition-backed hidden persistence denial;
- conditional file-delete authority;
- destructive category tools remaining hidden from ordinary assistant policy;
- cross-principal/client/**user** conversation-handle denial;
- handle quota/revoke/session cleanup and partial-mint rollback;
- delegation continuity and canonical user propagation;
- principal concurrency admission;
- structured failed outcomes;
- `not_running` / `cancelling` / `cancelled` lifecycle truth;
- explicit close/revoke lifecycle;
- ordered continuation evidence and persisted-receipt-only evidence;
- LLM secret/provider/endpoint ownership;
- provider activation and first-run setup using the same canonical route mutation;
- rollback behavior and preservation of live `LLMConfig` object identity;
- status/path redaction;
- exact MCP adapter catalog contract.

`scripts/mcp_live_acceptance.py` was also strengthened to:

- require 401 before catalog access without bearer auth;
- test non-loopback rejection where a local non-loopback address exists;
- require the exact 12-tool / 5-resource catalog;
- assert specific `config.llm.probe` and `config.llm.write` denial semantics;
- continue the delegated conversation through a **fresh MCP client and fresh HTTP client**;
- close/revoke the handle at the end;
- intentionally avoid application-state mutation in the first live gate.

## 11. Validation actually executed in this sandbox

Final feasible gate run after all Round 295 code/doc changes:

```text
python -m compileall -q src scripts main.py                              PASS
PYTHONPATH=. python scripts/mcp_control_plane_tests.py                   MCP_CONTROL_PLANE_PASS
PYTHONPATH=. python scripts/check_mcp_architecture.py                    MCP_ARCHITECTURE_PASS
PYTHONPATH=. python scripts/round293_ella_search_selection_cancel_tests.py ROUND293_ELLA_SEARCH_SELECTION_CANCEL_PASS
PYTHONPATH=. python scripts/check_ai_intent_architecture.py              PASS
PYTHONPATH=. python scripts/check_ai_context_architecture.py             PASS
PYTHONPATH=. python scripts/check_security_architecture.py               PASS
PYTHONPATH=. python scripts/check_category_architecture.py               PASS
PYTHONPATH=. python scripts/check_public_docs.py                         PASS
PYTHONPATH=. python scripts/check_model_facade_imports.py                PASS
PYTHONPATH=. python scripts/check_architecture.py                        PASS — HARD findings: 0
```

Architecture audit final summary:

```text
Files scanned: 374
HARD findings: 0
RISK findings: 168
ADVISORY findings: 423
```

The risk/advisory counts are the project's broad review prompts, dominated by pre-existing large classes/methods/private-access observations. No hard architecture violation is introduced by this round.

## 12. What could NOT be honestly executed here

This execution sandbox does not contain the project's declared runtime packages:

```text
aiosqlite  MISSING
mcp        MISSING
httpx2     MISSING
litellm    MISSING
```

Accordingly:

- `PYTHONPATH=. python scripts/check_compatibility_shims.py` stops on `ModuleNotFoundError: aiosqlite` before the check can run;
- `PYTHONPATH=. pytest -q` stops while loading `tests/conftest.py` on the same missing `aiosqlite` dependency;
- a real MCP v2 Streamable-HTTP handshake cannot be executed in this sandbox without `mcp`/`httpx2` and the normal LJS runtime dependencies.

A `pip install -r requirements.txt` attempt in this environment cannot reach the package index. **Do not reinterpret this as a passing live gate.**

## 13. Required machine-local acceptance before declaring live MCP complete

Use `MCP_LIVE_ACCEPTANCE_LOCAL_AGENT_2026-08-15.md` exactly.

The required sequence is:

1. install this snapshot's `requirements.txt` in the normal project virtualenv;
2. prove `import aiosqlite, mcp, httpx2, litellm`;
3. run the full provider-free/static gate list including compatibility shims and full pytest;
4. configure a newly generated read-only dedicated MCP token;
5. start **one normal LJS process** (no second MCP runtime);
6. run `scripts/mcp_live_acceptance.py` against the mounted `/mcp` endpoint;
7. preserve evidence and stop on the first failed prerequisite/gate.

PASS requires the real SDK probe to end with:

```text
MCP_LIVE_ACCEPTANCE_PASS
```

Do not grant write/download/endpoint/probe/admin capabilities merely to make the first live gate pass.

## 14. Recommended next step after that live gate

If the dependency-complete local gate passes, do one narrowly scoped write-capable acceptance using a disposable/known-safe configuration value and exact durable-receipt verification. Do **not** expand the MCP catalog first.

If the live gate fails, repair the observed real-runtime mismatch without weakening:

- dedicated-token auth;
- loopback enforcement;
- capability checks;
- route-owned credential policy;
- cancellation settlement truth;
- handle ownership/lifecycle;
- single-runtime architecture.
