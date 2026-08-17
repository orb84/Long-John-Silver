# LJS MCP Control Plane / Agent Delegation — Implementation Handover

**Date:** 2026-08-15  
**Authoritative input baseline:** delivered Round 293 project archive  
**Important:** references to “Round 294” in the recovered chat transcript described work from a crashed session that was never delivered. They were treated as design intent only, never as code baseline.

## 1. State of this snapshot

The adversarially refined MCP plan has been implemented over Round 293 through the first production-shaped local MCP slice.

This snapshot contains:

- protocol-neutral invocation identity and capability propagation;
- external agent delegation through the canonical `ChatSessionRunner` / `AIAssistant` path;
- one shared transport-neutral `ChatTurnRegistry` for cancellation ownership;
- durable opaque external conversation handles;
- capability filtering both before private tools reach the LLM and again at execution;
- structured stable result/candidate and persisted command-receipt evidence;
- bounded protocol-neutral status/library/download/diagnostic/LLM control services;
- a thin MCP v2 Streamable-HTTP adapter mounted into the existing LJS FastAPI runtime;
- local-only network enforcement and bearer authentication at the ASGI boundary;
- a curated MCP public surface, with no private `ToolRegistry`, raw search/download orchestration, or arbitrary action-execution escape hatch;
- documentation, migration, architecture drift checks and dependency-light MCP acceptance tests.

This is **not** claimed as live MCP-client accepted in this sandbox because the sandbox lacks the declared `mcp` and `aiosqlite` dependencies and cannot install packages from the network. See §8.

## 2. Architectural decisions implemented

### 2.1 MCP is an adapter, not the domain architecture

The new protocol-neutral path is:

```text
External MCP host
    -> MCPServerAdapter
    -> PublicControlPlane
       -> AgentDelegationService -> ChatSessionRunner -> AIAssistant -> private ToolRegistry
       -> bounded query services -> canonical LJS reads
       -> curated command service -> ActionGateway
```

No second LJS application/runtime is started for MCP.

### 2.2 State domains remain distinct

The implementation deliberately does not invent a generic “operation” object.

- `conversation_id`: durable opaque external continuation handle;
- `turn_id`: one foreground agent turn, owned by `ChatTurnRegistry`;
- `command_id` / `correlation_id`: durable mutation truth from `ActionGateway`;
- search/result-set/candidate IDs: existing stable acquisition-result identity.

They can reference one another in results, but no ID is repurposed to mean another lifecycle.

### 2.3 Explicit invocation identity

New protocol-neutral models live in:

- `src/core/domain_models/invocation.py`
- `src/core/invocation.py`

An `InvocationPrincipal` carries principal/client/user/source/capabilities/trust. `InvocationContext` carries that principal plus conversation, turn, `allow_actions`, and shared structured invocation evidence.

Existing first-party chat remains trusted by default through the central `InvocationContextResolver`; the two agent loops no longer independently infer security identity from session-ID formatting.

### 2.4 Confused-deputy protection reaches the inner agent

Capability enforcement is not only an MCP-front-door check.

`AgentToolCapabilityResolver` assigns explicit application capabilities to the private agent tool surface. `ToolRegistry`:

1. filters unauthorized tools before their definitions are sent to the LLM;
2. re-checks authorization on execution;
3. applies `allow_actions=false` as an independent mutation brake;
4. fails unknown externally constrained tools closed.

Trusted legacy first-party execution preserves the pre-MCP surface.

### 2.5 Conversation handles are opaque and principal-bound

Migration:

- `migrations/114_external_conversation_handles.sql`

Repository/service:

- `src/core/repositories/conversation_handle.py`
- `src/core/conversation_handle.py`

External callers never provide raw LJS `session_id` values. LJS mints high-entropy `conversation_id` handles, maps them privately to an internal session, persists them, and requires both principal and client bindings to match on resume.

### 2.6 Cancellation reuses Round 293 ownership

`ChatTurnRegistry` moved from Web ownership to the AI execution layer:

- canonical: `src/ai/chat_turn_registry.py`
- compatibility re-export: `src/web/chat_turn_registry.py`

Web and MCP therefore cancel the same owning task. The external cancellation acceptance test proves that the runner observes `CancelledError` and the registry has no active turn afterward.

### 2.7 Structured result truth, not prose parsing

`src/ai/tool_result_evidence.py` recursively reads only structural fields from actual tool results and records:

- `result_set_id`;
- `candidate_id` / `candidate_ids`;
- `command_receipt.command_id` only when `receipt_persisted == True`;
- explicit `clarification_required` state.

No regex, user-language keyword logic, or parsing of assistant prose is used.

`ChatTurnOutcome` now exposes bounded structured IDs alongside the final human message.

### 2.8 Public control plane is curated

Protocol-neutral services are in:

- `src/core/public_control_plane.py`
- `src/core/public_control_plane_facade.py`

Current public MCP tools:

```text
ljs.agent_message
ljs.agent_cancel
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

Current resources:

```text
ljs://status
ljs://capabilities
ljs://library/summary
ljs://downloads/active
ljs://configuration/llm
```

Not exposed:

- private `ToolRegistry`;
- arbitrary `ActionGateway.execute(name,args)`;
- raw torrent/Soulseek/web search micro-tools;
- direct generic acquisition/download orchestration;
- API-key mutation;
- remote MCP;
- stdio server that boots a second LJS runtime.

### 2.9 LLM mutation remains canonical

`ljs.llm_set` uses the existing `settings_update_llm` action through the shared `ActionGateway` with `ActionSource.EXTERNAL`.

The public mutation contract uses explicit allowlists. Credential fields are removed both at the top level and inside tier payloads. Only the real `lightweight`, `standard`, and `heavy` tier names are accepted. The dependency-light acceptance harness verifies that top-level and nested `api_key` values never reach `ActionGateway`.

### 2.10 Initial transport is actually local-only

The normal LJS web process can bind `0.0.0.0`; therefore merely documenting MCP as local was insufficient.

The MCP app is wrapped with:

1. `LocalMCPNetworkBoundary` — rejects non-loopback network clients;
2. `MCPAuthenticationBoundary` — requires a valid bearer before any MCP protocol handling/catalog enumeration;
3. `MCPPrincipalResolver` — maps either the dedicated MCP token or a valid existing LJS web JWT to a protocol-neutral principal.

The dedicated token is optional but, when MCP is enabled and the token is configured, must be at least 32 characters. Disabled MCP ignores stale invalid MCP-only capability/token settings so the opt-in integration cannot break ordinary LJS startup while off.

Remote-network MCP with standardized OAuth/TLS is intentionally deferred rather than simulated.

## 3. MCP SDK integration

Dependency added:

```text
mcp>=2,<3
```

Adapter/runtime:

- `src/integrations/mcp_server.py`
- `src/integrations/mcp_runtime.py`
- `src/integrations/mcp_configuration.py`
- `src/integrations/mcp_auth.py`
- `src/integrations/mcp_network.py`

`src/web/app.py` constructs the adapter from the already-live LJS dependencies and mounts it only when `LJS_MCP_ENABLED=1`.

The mounted MCP sub-app is created with `streamable_http_path="/"`, so mount path `/mcp` is the public MCP endpoint. The top-level FastAPI lifespan owns `session_manager.run()`; no sub-app lifespan is relied upon.

## 4. Operator configuration

Documented in `.env.example`, `README.md`, and `SECURITY.md`.

Typical local read/delegate configuration:

```dotenv
LJS_MCP_ENABLED=1
LJS_MCP_TOKEN=<strong token>
LJS_MCP_PRINCIPAL_ID=my-local-agent
LJS_MCP_CAPABILITIES=agent.delegate,agent.read,status.read,library.read,downloads.read,config.llm.read,diagnostics.read
```

Generate a token with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then connect to:

```text
http://127.0.0.1:8088/mcp
```

with:

```text
Authorization: Bearer <token>
```

The port follows ordinary LJS configuration (`LJS_PORT` / `settings.web_port`).

Delegated semantic mutations require **both** an appropriate write capability on the principal and `allow_actions=true` on that `ljs.agent_message` call.

## 5. Main files added

```text
migrations/114_external_conversation_handles.sql
src/core/domain_models/invocation.py
src/core/invocation.py
src/core/conversation_handle.py
src/core/repositories/conversation_handle.py
src/core/public_control_plane.py
src/core/public_control_plane_facade.py
src/ai/tool_context.py
src/ai/tool_capabilities.py
src/ai/tool_result_evidence.py
src/ai/chat_turn_registry.py
src/ai/agent_delegation.py
src/integrations/mcp_configuration.py
src/integrations/mcp_auth.py
src/integrations/mcp_network.py
src/integrations/mcp_server.py
src/integrations/mcp_runtime.py
scripts/mcp_control_plane_tests.py
scripts/check_mcp_architecture.py
scripts/mcp_live_acceptance.py
tests/test_invocation_capabilities.py
MCP_LIVE_ACCEPTANCE_LOCAL_AGENT_2026-08-15.md
PROGRESS.md
```

Important existing files updated include:

```text
src/ai/assistant.py
src/ai/agent_loop.py
src/ai/streaming_agent_loop.py
src/ai/chat_session_runner.py
src/ai/tool_registry.py
src/ai/tool_executor.py
src/ai/category_tool_factory.py
src/ai/tools/downloads.py
src/core/database.py
src/core/models.py
src/core/domain_models/agent.py
src/core/domain_models/enums.py
src/web/app.py
src/web/chat_turn_registry.py
requirements.txt
.env.example
README.md
SECURITY.md
architecture.md
docs/CODEBASE_ARCHITECTURE_MAP.md
```

## 6. Verification completed in this environment

PASS:

```text
python -m compileall -q src scripts main.py
PYTHONPATH=. python scripts/mcp_control_plane_tests.py
PYTHONPATH=. python scripts/check_mcp_architecture.py
PYTHONPATH=. python scripts/round293_ella_search_selection_cancel_tests.py
PYTHONPATH=. python scripts/check_ai_intent_architecture.py
PYTHONPATH=. python scripts/check_ai_context_architecture.py
PYTHONPATH=. python scripts/check_security_architecture.py
PYTHONPATH=. python scripts/check_category_architecture.py
PYTHONPATH=. python scripts/check_public_docs.py
PYTHONPATH=. python scripts/check_model_facade_imports.py
PYTHONPATH=. python scripts/check_architecture.py
```

`check_architecture.py` reports **0 HARD findings**. Its existing RISK/ADVISORY output remains primarily the known large-class/method/private-access review backlog; the new MCP/control-plane classes do not introduce findings in the grep-filtered audit.

The dependency-light MCP harness specifically covers:

- migration syntax/schema shape;
- default read/delegate capabilities;
- invalid enabled capability config fails closed;
- disabled MCP remains inert despite stale invalid MCP-only settings;
- weak enabled dedicated token rejected;
- private-tool pre-LLM filtering and execution re-check;
- unknown constrained tools fail closed;
- trusted first-party compatibility;
- dedicated token and existing web-JWT authentication;
- auth before MCP protocol handling;
- opaque principal/client-bound conversation handles;
- continuation using the same private canonical session;
- shared turn cancellation and cleanup;
- structured result/candidate/persisted receipt evidence;
- loopback-only network enforcement;
- exact curated MCP tool/resource catalog;
- `streamable_http_path="/"`;
- LLM public sanitizer removes top-level/nested secrets and unknown tiers.

## 7. Architecture guardrails added

`scripts/check_mcp_architecture.py` prevents the MCP adapter from drifting toward:

- private `ToolRegistry` imports;
- private agent tool-package imports;
- scheduler/downloader/main runtime imports;
- public raw search/download tools;
- generic execute-action/tool-execute escape hatches;
- stdio as the primary server path;
- any later stdio module booting `create_app`/scheduler as a second runtime.

## 8. Verification limitation / next acceptance gate

This execution sandbox does not have `aiosqlite` or `mcp` installed. Outbound package installation is unavailable.

Consequences:

- `scripts/check_compatibility_shims.py` cannot import because `aiosqlite` is missing;
- `pytest` cannot load `tests/conftest.py` because `aiosqlite` is missing;
- a real SDK `Client`/Inspector Streamable-HTTP handshake cannot be executed because `mcp` is missing.

Those are environment/dependency blockers, not observed assertion failures.

A ready-to-run acceptance probe and strict local-agent protocol are included as `scripts/mcp_live_acceptance.py` and `MCP_LIVE_ACCEPTANCE_LOCAL_AGENT_2026-08-15.md`.

On the normal LJS machine, after installing `requirements.txt`, the remaining acceptance should be:

1. run the full existing pytest/compatibility suite;
2. enable MCP with a generated token and default read-only capabilities;
3. start LJS normally — **do not start a second MCP/LJS process**;
4. connect an MCP v2 client/Inspector to `http://127.0.0.1:<port>/mcp` with the bearer token;
5. verify `tools/list` and `resources/list` match the curated surface;
6. call `ljs.status`, `ljs.library_list`, and `ljs.llm_get`;
7. call `ljs.agent_message` and preserve the returned `conversation_id` across a second MCP request/new HTTP connection;
8. verify a read-only principal cannot make the delegated agent see or execute mutating tools even when prompted maliciously;
9. verify `allow_actions=false` still blocks writes on a write-capable principal;
10. with a deliberately write-capable test principal, verify a known mutation returns authoritative receipt IDs and is visible in canonical LJS state;
11. start a long search turn and cancel it through `ljs.agent_cancel`, confirming Round 293 child search/provider cancellation settles;
12. verify non-loopback access receives 403;
13. verify no-token/invalid-token access receives 401 before tool/resource enumeration.

Do not weaken the loopback/auth boundary merely to make an external LAN host connect. Remote MCP is a separate OAuth/TLS design phase.

## 9. Intentionally deferred

Not bugs / not unfinished accidental scaffolding:

- stdio proxy — add only if a target host actually requires it, and make it a thin proxy to the running LJS control plane;
- remote OAuth/TLS MCP;
- protocol-neutral detached/durable work coordinator;
- MCP Tasks adapter after there is a real LJS long-work need and SDK/runtime support is appropriate;
- generic structured acquisition/search/download APIs that would duplicate LJS agent orchestration.

## 10. Resume guidance

Treat this snapshot, not the lost “Round 294”, as the new implementation baseline.

Before extending the public surface:

1. preserve `ChatSessionRunner` as the semantic agent boundary;
2. preserve `ActionGateway` as the durable deterministic-mutation boundary;
3. do not export private `ToolRegistry` entries;
4. do not infer security capability from tool names or human-language keywords;
5. do not conflate conversation, turn, command, search-result, and future detached-work identities;
6. require any new external write to have explicit capability metadata and authoritative mutation truth;
7. run `scripts/mcp_control_plane_tests.py` and `scripts/check_mcp_architecture.py` after every MCP/control-plane change.
