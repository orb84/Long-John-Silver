# LJS Round 297 Handover — MCP Static Resource Registration Fix

Date: 2026-08-17
Baseline: Round 296 MCP Settings/live-runtime project
Scope: repair the user-observed MCP enable failure; no expansion of public MCP authority.

## User-observed failure

Enabling MCP from Compass failed with:

```text
Resource 'ljs://status' has no URI template variables, but the handler declares a Context parameters. Context injection for static resources is not supported. Add a template variable to the URI or remove context parameter
```

This happened while `MCPServerAdapter` registered its resources, before the live
runtime could become active. All five LJS resources use fixed URIs, but their
handlers declared `ctx: Context`.

## Root cause

MCP Python SDK v2 distinguishes fixed/static resources from URI-template
resources. Fixed resources have no URI variables and their registered handler
must not request injected `Context`. LJS violated that registration contract for:

- `ljs://status`
- `ljs://capabilities`
- `ljs://library/summary`
- `ljs://downloads/active`
- `ljs://configuration/llm`

Round 296's dependency-light fake SDK registered those methods without validating
handler signatures, so the project gate missed the exact SDK failure.

## Code repair

`src/integrations/mcp_server.py`

- The five static resource handlers now take no handler parameters.
- They obtain the already-authenticated principal through
  `MCPRequestPrincipalContext.require()`.
- They then call the same protocol-neutral public control-plane services used by
  the MCP tool surface.
- Tool handlers keep SDK `Context` injection; only the fixed-resource contract
  changed.

Authentication still occurs at `MCPAuthenticationBoundary` before the MCP ASGI
application is entered. No static resource became anonymous and no capability
boundary was weakened.

## Regression protection

`scripts/mcp_control_plane_tests.py`

- The fake MCP SDK's `resource()` registrar now inspects fixed-resource handler
  signatures and fails if any parameter is declared. This reproduces the
  registration rule that caused the real failure.

`scripts/check_mcp_architecture.py`

- Added an independent AST guard that follows each fixed `resource()`
  registration to its handler and fails if that handler declares arguments
  beyond `self`.
- URI-template resources containing `{...}` are deliberately exempt, so future
  legitimate template parameters remain possible.

`scripts/mcp_live_acceptance.py`

- The real SDK acceptance probe now calls `client.read_resource()` for every
  fixed resource and requires non-empty content. This proves not only catalog
  registration but actual resource invocation and authenticated-principal
  propagation on the installed SDK.

`architecture.md`, `PROGRESS.md`, and the local acceptance runbook were updated
to make this boundary explicit.

## Validation performed here

PASS:

```text
python -m compileall -q src scripts main.py
python scripts/mcp_control_plane_tests.py
  -> MCP_CONTROL_PLANE_PASS
python scripts/check_mcp_architecture.py
  -> MCP_ARCHITECTURE_PASS
python scripts/round293_ella_search_selection_cancel_tests.py
  -> ROUND293_ELLA_SEARCH_SELECTION_CANCEL_PASS
python scripts/check_ai_intent_architecture.py
python scripts/check_ai_context_architecture.py
python scripts/check_security_architecture.py
python scripts/check_category_architecture.py
python scripts/check_public_docs.py
python scripts/check_model_facade_imports.py
python scripts/check_architecture.py
  -> 0 HARD findings
node --check src/web/static/js/components/settingsPanel.js
bash -n run.sh
```

The ChatGPT sandbox does not have `mcp>=2,<3` installed and cannot resolve the
package index, so it still cannot run the real MCP SDK itself. The corrected
real-machine runbook is `MCP_LIVE_ACCEPTANCE_LOCAL_AGENT_2026-08-17.md`; its
probe now reads every static resource and will catch both registration and
runtime resource-context failures.

## Expected owner-machine behavior

After replacing Round 296 with this tree and running the normal launcher/update
path if needed:

1. Open Compass -> MCP — External LLM Control.
2. Turn MCP on.
3. The previous static-resource `Context` error must no longer occur.
4. Status must become **Running**.
5. Run the real SDK acceptance protocol. `MCP_LIVE_ACCEPTANCE_PASS` is required
   before expanding the MCP surface.

If activation still fails, preserve the exact Compass error and LJS log. Do not
relax resource registration, authentication, or loopback restrictions.
