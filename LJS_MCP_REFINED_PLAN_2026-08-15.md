# LJS MCP Control Plane / Agent Delegation — Adversarially Refined Plan

**Date:** 2026-08-15  
**Implementation baseline:** delivered Round 293 archive only  
**Status:** reviewed plan; no MCP implementation has been applied yet

## 1. Baseline and authority

References in the recovered transcript to “Round 294” describe work that was being discussed or attempted in the crashed session and was never delivered. They are therefore **design intent only**, not repository fact.

The attached Round 293 project is the authoritative implementation baseline. Its relevant existing boundaries are:

- `ChatSessionRunner`: canonical transport-neutral interactive assistant-turn boundary.
- `AIAssistant`: LJS domain agent and context/orchestration owner.
- `ToolRegistry`: private LJS agent tool surface.
- `ChatTurnRegistry`: process-local ownership of one cancellable active turn per chat session.
- `ConversationManager` + pending result-set state: persistent session conversation/acquisition continuity.
- `ActionGateway`: durable mutation command/receipt boundary, but still only partially universalized across the application.
- `OperationTraceContext`: trace correlation for `session_id` + `turn_id`; it is not a durable generic “operation” subsystem.

## 2. Verdict on the recovered MCP proposal

The central idea **passes**: MCP should primarily expose LJS as a specialized delegable domain agent, with a deliberately small deterministic control/read surface. It should not export the private `ToolRegistry` or force an external LLM to reconstruct LJS domain orchestration.

However, the recovered plan is **not implementation-ready unchanged**. The following corrections are required first.

## 3. Adversarial findings

### A. Do not collapse conversation, turn, command, and future async work into one “operation” concept

Round 293 already has three distinct authorities:

1. **Conversation** (`session_id` today; public future handle `conversation_id`) — persistent semantic continuity, history, pending result sets, acquisition goals.
2. **Turn** (`turn_id`) — one foreground assistant execution with cancellation ownership.
3. **Command** (`command_id` / `correlation_id`) — durable mutation attempt and receipt.

A future long-running async handle is a fourth concept. It may reference a conversation, turn, and commands, but it must not replace them.

Do **not** call the future durable async handle `operation_id` without first resolving the existing `ToolExecutionContext.operation_id`, which currently carries an LLM tool-call identity. Prefer `work_id` or protocol-native task identity for future async work.

### B. MCP Tasks must not become LJS's domain architecture

MCP 2026-07-28 has a stateless core and an official Tasks extension for long-running work. That is a useful adapter target, not an application-domain abstraction.

The official Python SDK migration documentation currently states that its 2026 Tasks extension runtime is not implemented. Therefore:

- first implementation should support ordinary request-scoped agent execution with progress/cancellation;
- LJS should not invent a parallel fake Tasks framework merely to mimic the extension;
- if real host/client testing later proves a need for detached/durable work, introduce a protocol-neutral `WorkCoordinator`, then adapt it to MCP Tasks when the Python SDK/runtime supports it.

### C. A naïve stdio server must not boot a second LJS runtime

A stdio MCP process is commonly client-launched. Starting normal LJS inside it would risk a second scheduler, downloader/runtime owner, managed sidecars, caches, watchers, and concurrent mutations against the same data.

Therefore:

- **Primary MCP server transport:** Streamable HTTP mounted in the already-running LJS FastAPI process, sharing the exact live `AIAssistant`, `ChatTurnRegistry`, `ActionGateway`, database, downloader, and service instances.
- **Optional stdio support:** a thin proxy into the running LJS MCP/control endpoint. It must never initialize a second domain runtime.

### D. Invocation identity is currently too implicit for MCP authorization

Today agent tools derive `source` by parsing `session_id` prefixes in both agent loops. `ChatSessionRunner` carries `session_id`/`user_id`, but not a first-class authenticated principal or effective capabilities.

This is insufficient for an external-agent boundary.

Introduce protocol-neutral invocation identity:

```text
InvocationPrincipal
- principal_id
- user_id
- client_id
- source
- granted_capabilities

authentication context remains transport-owned
```

and an invocation context containing the principal plus conversation/turn metadata.

Source/capabilities must be propagated explicitly. Remove duplicated session-prefix parsing from both agent loops.

### E. Capability checks must constrain the *inner* LJS agent, not only the MCP wrapper

Checking scope only before `ljs.agent_message` would create a confused-deputy gap: a read-only external principal could ask the LJS agent to perform a mutation, and the inner agent would still see its normal write tools.

Effective capabilities must be applied when building the LJS agent's allowed tool set **and** rechecked at execution/mutation boundaries.

Recommended initial capabilities:

```text
agent.delegate
library.read
downloads.read
downloads.write
tracking.write
config.llm.read
config.llm.write
diagnostics.read
admin
```

The exact names may be refined during implementation, but permissions must describe application capabilities, not MCP method names.

### F. `allow_actions` must be explicit for delegated agent calls

A high-level delegated agent tool can hide nested mutations from the outer host's normal per-tool human-in-the-loop UX. Therefore the public agent call should default to read-only delegation:

```text
ljs.agent_message(
    message,
    conversation_id?,
    allow_actions=false
)
```

The effective inner capability set is the intersection of:

- credential/principal capabilities; and
- the delegation mode requested for this call.

Setting `allow_actions=true` never grants capabilities the principal does not already have.

### G. Public conversation handles must be server-minted and principal-bound

Do not accept arbitrary internal `session_id` values from MCP clients.

`ConversationHandleService` should mint an opaque high-entropy `conversation_id`, persist its binding to principal/client and the internal LJS session, and validate that binding on every continuation. A new HTTP connection must be able to continue the same conversation by passing the handle.

This directly matches MCP 2026-07-28's stateless request model: state that spans requests is referenced by explicit identifiers carried on each request.

### H. Do not misuse MCP `input_required`

MCP 2026-07-28 gives `resultType: input_required` a specific Multi Round-Trip Requests meaning. LJS already has its own ordinary conversational clarification model.

Initial MCP delegation should return a normal completed tool result such as:

```json
{
  "status": "needs_input",
  "conversation_id": "...",
  "message": "Which release do you mean?"
}
```

The outer agent then sends another `agent_message` with that conversation handle.

Later, the MCP adapter may optionally translate a suitable LJS clarification into MRTR when host interoperability is proven. The LJS domain model must not depend on MRTR.

### I. `ChatSessionRunner` needs a typed outcome seam, not an MCP-only parallel agent path

The recovered proposal correctly says MCP should enter through the same semantic boundary as other chat surfaces. But `ChatSessionRunner` currently emits only `status`, `token`, and `done`, while `AIAssistant.run_stream_events()` currently wraps every chunk as plain text.

MCP needs structured authority without parsing final prose. Introduce a transport-neutral result/event seam shared by all surfaces, for example:

```text
ChatTurnOutcome
- conversation/session handle
- turn_id
- status: complete | needs_input | cancelled | failed
- final_text
- stable result-set/candidate handles when relevant
- authoritative mutation receipt references produced during the turn
```

Do not duplicate the agent loop for MCP. Extend the existing shared runner/agent execution path so MCP can consume structure while web/Discord may continue rendering text.

### J. Do not expose `ToolRegistry` as the public MCP catalog

The private registry contains orchestration primitives and internal implementation details. Exporting it would recreate distributed orchestration and make future internal refactors public compatibility obligations.

MCP gets a separate, small public capability catalog implemented over stable application services.

### K. `ActionGateway` is the right mutation direction, but not yet a universal generic public executor

The architecture document already describes migration toward `ActionGateway`, but the code is still transitional:

- many UI action handlers are registered from `src/web/action_handlers`;
- the web composition root wires the gateway to the assistant `ToolRegistry`;
- some agent tools, notably `queue_download`, create their own command gateway/receipt path;
- direct registry execution does not currently carry a fully authenticated `ToolExecutionContext` through `ActionGateway`.

Therefore MCP must **not** expose “execute any registered action” or `registered_actions` as its public API.

The MCP control facade should expose only explicitly curated commands and send them through the canonical command path. Security/context plumbing should be improved incrementally rather than pretending every historical action is already uniformly migrated.

### L. Full-library resources are too coarse

Avoid a giant `ljs://library` resource. It can be expensive, leak more data than required, and flood model context.

Use bounded summary resources and parameterized query tools/resource templates instead.

### M. Do not add public raw `search` / `download` tools in the first slice

They are exactly where orchestration duplication can re-enter.

For semantic acquisition requests, delegate to the LJS agent. If a later non-LLM automation genuinely needs deterministic structured acquisition, design that contract separately and deliberately; do not export private agent tools by convenience.

## 4. Refined architecture

```text
External Agent / MCP Host
          |
          v
+----------------------------+
| Thin MCP Adapter            |
| - Streamable HTTP primary   |
| - protocol/auth translation |
+-------------+--------------+
              |
              v
+--------------------------------------------------+
|            LJS Public Control Plane              |
|                                                  |
|  Principal/Capability Policy                     |
|  ConversationHandleService                       |
|                                                  |
|  +--------------------+  +--------------------+  |
|  | Agent Delegation   |  | Query Services     |  |
|  | Service            |  | bounded/read-only  |  |
|  +---------+----------+  +---------+----------+  |
|            |                       |             |
|            v                       v             |
|   ChatSessionRunner          canonical read      |
|            |                 models/services     |
|            v                                     |
|      AIAssistant                                  |
|            |                                     |
|      private ToolRegistry                        |
|                                                  |
|  +--------------------+                          |
|  | Curated Commands   |                          |
|  +---------+----------+                          |
|            |                                     |
|            v                                     |
|       ActionGateway                              |
+------------+-------------------------------------+
             |
             v
       canonical LJS services
```

MCP is an adapter. The control plane is application-owned and protocol-neutral.

## 5. Identity/lifecycle model

### `conversation_id`

- server-minted, durable application handle;
- bound to principal/client;
- maps to LJS persistent conversation memory and pending-action state;
- survives MCP transport reconnects and LJS restart.

### `turn_id`

- existing foreground assistant execution identity;
- owned by `ChatTurnRegistry`;
- cancellation target for a live agent turn;
- not a conversation and not a durable job.

### `command_id` / `correlation_id`

- existing durable mutation identities;
- authoritative receipts establish whether a state change occurred.

### future `work_id`

- add only if detached/durable async execution is proven necessary;
- may reference conversation/turn/command IDs;
- maps to MCP Tasks only in the adapter when supported.

## 6. Proposed first public MCP surface

Keep names subject to final SDK naming constraints, but the semantic surface should remain small.

### Agent

`ljs.agent_message`

Inputs:

- `message`
- optional `conversation_id`
- `allow_actions` default `false`

Returns structured content including:

- `conversation_id`
- `turn_id`
- `status` (`complete`, `needs_input`, `cancelled`, `failed`)
- `message`
- relevant stable result-set/candidate handles
- authoritative command-receipt references when mutations occurred

### Read/control tools

- `ljs.status`
- `ljs.library_query`
- `ljs.library_get`
- `ljs.downloads_list`
- `ljs.llm_get`
- `ljs.llm_test`
- `ljs.llm_set`
- `ljs.diagnostics_recent`

Optional explicit cancellation can be added as `ljs.agent_cancel(conversation_id, turn_id)` if client cancellation alone is insufficient in real host testing.

### Resources

- `ljs://status`
- `ljs://capabilities`
- `ljs://library/summary`
- `ljs://downloads/active`
- `ljs://configuration/llm`

Sensitive resources must be authorized and use conservative private/no-cache semantics.

### Explicitly **not** in first slice

- private `ToolRegistry` export;
- generic `execute_action(name, args)` escape hatch;
- raw torrent/Soulseek/web-search MCP micro-tools;
- public `search`/`download` orchestration tools;
- MCP prompts that expose internal LJS system prompts/skills;
- fake LJS Tasks clone;
- stdio process that initializes a second LJS runtime.

## 7. Implementation sequence

### Phase 0 — Freeze baseline and add MCP architecture tests

- Preserve Round 293 behavior.
- Add architecture guard that MCP code cannot import/use private agent tool names as its public catalog.
- Add a test that any stdio adapter/proxy cannot instantiate the normal LJS runtime/composition root.
- Record current passing Round 293/AI architecture harnesses.

### Phase 1 — First-class invocation identity and capability propagation

- Add `InvocationPrincipal` and `InvocationContext` in a protocol-neutral core/application module.
- Add explicit source/principal/capability context to `ChatTurnRequest` and assistant preparation.
- Add principal/capability fields to `ToolExecutionContext` as needed.
- Extract one shared tool-context factory; remove duplicated session-prefix source parsing from both agent loops.
- Extend `AgentToolPolicy` so the allowed tool set is intersected with effective capabilities.
- Add execution-boundary capability rejection as defense in depth.
- Existing web/local bridges receive a trusted local principal preserving current behavior.

**Gate:** existing chat surfaces and Round 293 pass unchanged; read-only delegated principal cannot expose or execute a write tool.

### Phase 2 — Conversation handle + typed agent delegation service

- Add `ConversationHandleService` with durable principal binding.
- Add `AgentDelegationService` over `ChatSessionRunner`.
- Add/extend typed `ChatTurnOutcome` / agent event collection without creating a second loop.
- Make public conversation handles independent of MCP connection/process identity.
- Keep pending result-set/candidate continuation exactly on the existing conversation state.

**Gate:** first request returns a handle; a second request on a fresh transport connection can continue by stable candidate ID/result-set context; a different principal cannot reuse the handle.

### Phase 3 — Protocol-neutral public query/command facade

- Add focused query services for status, bounded library lookup, active downloads, diagnostics, and effective LLM configuration.
- Add curated command methods only for required deterministic controls (initially LLM configuration is the best vertical slice).
- Send mutations through `ActionGateway`; never expose arbitrary gateway action names.
- Return configured + effective LLM routes and durable command receipt/revision information.

**Gate:** no web-router dependency is required by the public facade's interface; reads are bounded; `llm_set` produces an authoritative command result and effective-route readback.

### Phase 4 — Embedded Streamable HTTP MCP adapter

- Add official MCP Python SDK dependency/version pin compatible with 2026-07-28.
- Mount the MCP endpoint into the existing LJS FastAPI process.
- Adapter only translates protocol calls to the public control plane.
- Resolve principal per request; never infer identity/capabilities from connection state.
- Wire MCP request cancellation to the existing `ChatTurnRegistry`/runner cancellation chain.
- Advertise only the curated public tools/resources.

**Gate:** MCP Inspector/SDK modern-mode interoperability; repeated requests over new connections preserve conversation only through explicit handle; cancelling the request settles the owning LJS turn and leaves no provider child activity.

### Phase 5 — Optional stdio proxy

Only if a target client requires stdio:

- implement a small proxy that speaks MCP stdio and forwards to the running LJS endpoint/control API;
- credential comes from environment/local config;
- proxy contains no scheduler/downloader/database/domain bootstrap.

**Gate:** process inspection/test proves one LJS runtime only.

### Phase 6 — Async/durable work only if real interoperability requires it

First test long searches through ordinary MCP calls with progress + cancellation.

If hosts impose practical request-duration limits:

- add protocol-neutral `WorkCoordinator` with explicit ownership/state/cancellation;
- do not reuse conversation, turn, or command IDs as work IDs;
- map to MCP Tasks when the Python SDK supports the 2026 extension;
- until then, do not implement a near-copy of `tasks/get` under LJS names unless a concrete host requirement justifies it.

## 8. Security acceptance cases

1. A read-only principal asks the delegated LJS agent to download/delete/change config: write tools are absent and execution is blocked if attempted.
2. `allow_actions=false` blocks writes even for an admin principal.
3. `allow_actions=true` cannot exceed the credential's granted capabilities.
4. Conversation handle from principal A is rejected for principal B.
5. Client-supplied fake `source`, `user_id`, internal session ID, command ID, or scope cannot impersonate a trusted source.
6. Diagnostics output remains bounded/redacted and requires `diagnostics.read`.
7. No MCP resource exposes raw local filesystem paths or secrets by default.
8. Mutating MCP calls return command receipt truth; final LJS prose cannot establish mutation success by itself.

## 9. End-to-end acceptance scenarios

### Agent continuity / exact selection

1. MCP call: “Find X in Italian.”
2. LJS returns a stable conversation handle and result-set/candidate handles.
3. New MCP connection: same conversation handle, “take the first one.”
4. Existing LJS pending-action provenance identifies the exact candidate.
5. If actions are permitted, queue mutation returns a persisted command receipt.
6. No alternate candidate is silently substituted after exact-selection failure.

### Cancellation

1. Start a deliberately slow search through `ljs.agent_message`.
2. Cancel the MCP request.
3. Same `turn_id` is cancelled through `ChatTurnRegistry`.
4. Jackett/direct children are cancelled and awaited.
5. Turn/search audit reaches a settled terminal state.
6. No later provider log belongs to the cancelled turn.

### Stateless transport continuity

1. Create conversation on HTTP connection A.
2. Close A.
3. Continue on connection B using only the explicit `conversation_id`.
4. Continuation succeeds.
5. Same request without handle does not inherit A's conversation state.

### LLM control truth

1. `ljs.llm_get` returns configured and effective routes/revision.
2. `ljs.llm_set` applies a valid route via the command boundary.
3. The result includes durable receipt status.
4. Immediate `ljs.llm_get` reflects the actual effective route, not only persisted settings text.

## 10. Regression gates for each MCP implementation round

At minimum:

```bash
PYTHONPATH=. python scripts/round293_ella_search_selection_cancel_tests.py
PYTHONPATH=. python scripts/check_architecture.py
PYTHONPATH=. python scripts/check_ai_intent_architecture.py
PYTHONPATH=. python scripts/check_ai_context_architecture.py
```

Add dedicated MCP tests for every phase above. No implementation phase passes if it breaks the existing exact-selection/cancellation truth from Round 293.

## 11. Final recommendation

Proceed with MCP, but implement the **application control plane and invocation identity first**, not the protocol adapter first.

The key architectural rule is:

> MCP may expose LJS's public capabilities, but it must never become a second owner of LJS domain reasoning, conversation semantics, mutation truth, or background runtime.

That keeps the excellent part of the recovered idea—agent-to-agent delegation—while avoiding a second orchestration stack, an accidental second LJS process, and a premature generic “operation” subsystem.
