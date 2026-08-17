# LJS Progress

## 2026-08-15 — Round 295 MCP security/truth repair

Deep adversarial review of the first MCP slice found release-blocking authority
and truth defects. This repair preserves the control-plane architecture while
fixing those contracts at their canonical owners rather than adding MCP-specific
name filters.

Completed:

- provider/endpoint/API-key ownership separated; provider or endpoint changes
  cannot inherit an unrelated route secret; KeyStore secrets follow only the
  canonical provider endpoint, never operator/custom endpoint overrides;
- LLM settings mutation extracted to focused `LLMSettingsMutationService` and
  made persistence/runtime failure-atomic with rollback;
- `config.llm.probe` separated outbound provider probing from read access, and
  `config.llm.endpoint.write` separated endpoint authority from ordinary route
  writes;
- category action/workflow declarations now own explicit application
  capabilities; `risk_level` is no longer treated as authorization; concrete
  TV/movie/base/definition-backed mutations declare download/library/delete
  authority, while unknown mutations fail closed;
- hidden definition-backed metadata persistence now checks the originating tool
  execution context;
- local MCP v1 accepts only its dedicated token; generic Web JWT -> MCP admin
  widening removed; authentication occurs once at the ASGI boundary; unknown
  client origin fails closed;
- canonical MCP user/client binding added and enforced again on handle resolve;
  conversation handles now expire, are quota-bounded, periodically touched,
  rollback-cleaned on mint failure, and can be explicitly revoked by
  `ljs.agent_close`; revocation/expiry also deletes the private external session
  and conversation history; non-`local` configured users must already exist;
- per-principal delegated-turn admission and message-size bounds added;
- cancellation truth now distinguishes `not_running`, `cancelling`, settled
  `cancelled`, and `closed`;
- delegated failures return bounded `failed` outcomes; confirmation-required
  results preserve `needs_input`; structural evidence preserves observation
  order and only persisted receipt IDs;
- external status/library surfaces redact private exception details and host-local
  paths; external audit actors retain the concrete principal identity;
- provider activation and first-run setup now use the same canonical LLM route
  mutation service, so credential ownership cannot diverge between MCP and UI;
- category action truth tightened: non-implemented scan/consolidate actions are
  not LLM-visible; TV/movie delete has a concrete conditional-capability,
  token-bound workflow, while ordinary assistant policy keeps destructive tools
  hidden until a real pending-confirmation seam exists;
- live acceptance strengthened to use a fresh client for continuation, assert
  exact capability denials, include the 12-tool surface and close the handle.

The historical Round-294 adversarial review remains in the tree as evidence; it
is not rewritten to pretend these defects were never present.

Final validation in this environment:

- `python -m compileall -q src scripts main.py` — PASS
- `PYTHONPATH=. python scripts/mcp_control_plane_tests.py` — `MCP_CONTROL_PLANE_PASS`
- `PYTHONPATH=. python scripts/check_mcp_architecture.py` — `MCP_ARCHITECTURE_PASS`
- `PYTHONPATH=. python scripts/round293_ella_search_selection_cancel_tests.py` — `ROUND293_ELLA_SEARCH_SELECTION_CANCEL_PASS`
- AI intent/context, security, category, public-doc and model-facade guards — PASS
- `PYTHONPATH=. python scripts/check_architecture.py` — PASS, **0 HARD findings**

Dependency-complete execution is still an explicit local-machine gate rather
than a claimed result here. This sandbox does not contain `aiosqlite`, `mcp`,
`httpx2`, or `litellm`: `check_compatibility_shims.py` and `pytest` both stop at
import/collection on missing `aiosqlite`, and a real MCP SDK HTTP handshake
cannot be run without the MCP/runtime packages. `MCP_LIVE_ACCEPTANCE_LOCAL_AGENT_2026-08-15.md`
defines the exact dependency install, full pytest, same-process startup, fresh-client
continuity and real SDK handshake gate that must pass on the normal LJS machine.

## 2026-08-15 — MCP public control plane / agent delegation

Implemented the adversarially refined MCP plan over the delivered Round 293
baseline.

Completed:

- protocol-neutral `InvocationPrincipal` / `InvocationContext` capabilities;
- explicit private-agent tool capability metadata with fail-closed unknowns;
- capability filtering before LLM tool exposure and execution-time recheck;
- trusted first-party compatibility without session-prefix parsing in both loops;
- transport-neutral `ChatTurnRegistry` ownership;
- durable, opaque, principal/client-bound external conversation handles;
- `AgentDelegationService` over the existing `ChatSessionRunner`;
- structured stable result-set/candidate and persisted action-receipt evidence;
- bounded public status/library/download/diagnostics/LLM services;
- curated LLM configuration writes through `ActionGateway`, with credential
  fields stripped from the MCP mutation contract;
- opt-in official-SDK Streamable HTTP MCP adapter mounted in the existing
  FastAPI process;
- host-lifespan ownership of the MCP SDK session manager;
- loopback-only MCP network boundary plus bearer authentication;
- dedicated dependency-light MCP acceptance and architecture drift guards;
- `mcp>=2,<3` dependency and operator configuration documentation;
- fail-safe disabled-MCP configuration handling and explicit LLM tier/field allowlists;
- a real-SDK `scripts/mcp_live_acceptance.py` probe plus strict local-agent acceptance protocol for the dependency-complete machine.

Validation in the implementation environment:

- `python -m compileall -q src scripts main.py` — PASS
- `PYTHONPATH=. python scripts/mcp_control_plane_tests.py` — PASS
- `PYTHONPATH=. python scripts/check_mcp_architecture.py` — PASS
- `PYTHONPATH=. python scripts/round293_ella_search_selection_cancel_tests.py` — PASS
- `PYTHONPATH=. python scripts/check_ai_intent_architecture.py` — PASS
- `PYTHONPATH=. python scripts/check_ai_context_architecture.py` — PASS
- `PYTHONPATH=. python scripts/check_security_architecture.py` — PASS
- `PYTHONPATH=. python scripts/check_category_architecture.py` — PASS
- `PYTHONPATH=. python scripts/check_public_docs.py` — PASS
- `PYTHONPATH=. python scripts/check_model_facade_imports.py` — PASS
- `PYTHONPATH=. python scripts/check_architecture.py` — PASS with 0 HARD findings

Environment limitation: the sandbox does not contain the declared `mcp` or
`aiosqlite` packages and outbound package installation was unavailable, so a
live SDK HTTP handshake and the full pytest suite could not be executed here.
The dependency-light harness stubs only the SDK registration boundary; actual
live MCP interoperability remains the next local-machine acceptance gate after
installing `requirements.txt`.

Intentionally deferred:

- stdio proxy (only if a target client requires it);
- remote-network OAuth/TLS MCP;
- detached/durable work coordinator / MCP Tasks integration;
- generic structured acquisition APIs outside the LJS domain agent.
