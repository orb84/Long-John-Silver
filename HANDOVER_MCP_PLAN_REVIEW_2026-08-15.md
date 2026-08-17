# Handover — MCP plan adversarial review (2026-08-15)

## Baseline

The authoritative code baseline is the user-supplied Round 293 archive. Mentions of Round 294 in the recovered transcript refer to crashed-session work that was never delivered and must not be treated as implemented.

## Work performed in this turn

- Read root `AGENTS.md` and `architecture.md` relevant sections.
- Inspected `ChatSessionRunner`, `AIAssistant`, `ToolRegistry`, `AgentToolPolicy`, both agent loops, `ToolExecutionContext`, `ChatTurnRegistry`, `OperationTraceContext`, `ConversationManager`, `ActionGateway`, action registration/composition, and current queue command path.
- Checked MCP 2026-07-28 official specification behavior: stateless core, explicit cross-request handles, Streamable HTTP, authorization guidance, MRTR, resources/tools, and Tasks extension.
- Checked official MCP Python SDK migration documentation: current Tasks extension runtime is not implemented there, so Tasks must not be a first-slice dependency.
- Ran the Round 293 incident harness and current architecture/AI intent/context guards. They pass.
- Wrote the refined implementation plan at `docs/plans/2026-08-15-mcp-control-plane-agent-delegation-refined-plan.md`.
- No application code was modified.

## Key decisions

1. MCP remains a thin adapter; LJS owns the public application control plane.
2. Agent delegation is primary; private agent tools are never exported wholesale.
3. Conversation, turn, mutation command, and future async work IDs remain separate.
4. Primary transport should be Streamable HTTP mounted in the existing LJS process.
5. Any stdio support must proxy to the running process, never bootstrap another LJS runtime.
6. Invocation principal/capabilities must be propagated into the inner agent's tool policy, not checked only at MCP entry.
7. Delegated agent calls default to `allow_actions=false`.
8. Public conversation handles are server-minted, durable, and principal-bound.
9. Initial clarification remains ordinary LJS conversation (`needs_input`), not an MCP MRTR dependency.
10. Do not expose generic MCP search/download tools in the first slice.
11. Do not invent an LJS Tasks clone. Add a protocol-neutral work coordinator only if real host testing proves it necessary.

## Baseline verification run

- `PYTHONPATH=. python scripts/round293_ella_search_selection_cancel_tests.py` — PASS
- `PYTHONPATH=. python scripts/check_architecture.py` — PASS (0 hard findings; existing risk/advisory findings remain)
- `PYTHONPATH=. python scripts/check_ai_intent_architecture.py` — PASS
- `PYTHONPATH=. python scripts/check_ai_context_architecture.py` — PASS

## Next implementation step

Start Phase 1 from the refined plan:

- add protocol-neutral `InvocationPrincipal` / `InvocationContext`;
- propagate them through `ChatTurnRequest` / assistant preparation / `ToolExecutionContext`;
- remove duplicated session-prefix source inference from both agent loops;
- intersect `AgentToolPolicy` with effective capabilities;
- preserve current behavior for trusted local web/comms principals;
- add regression/security tests before introducing any MCP SDK endpoint.
