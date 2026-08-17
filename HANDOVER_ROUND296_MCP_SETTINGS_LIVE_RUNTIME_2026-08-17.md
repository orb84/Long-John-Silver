# LJS Round 296 Handover — MCP Settings + Live Runtime Control

Date: 2026-08-17
Baseline: Round 295 `ljs_round295_mcp_security_truth_repair_2026-08-15.zip`

## User request

Expose MCP as a normal LJS Settings feature with a visible address, clear client/LLM-host configuration instructions, and an enable/disable switch. Investigate and fix why the Round-295 MCP implementation appeared not to be running.

## Root cause

Round 295 had the MCP implementation, but operational ownership was still hidden behind process environment variables that were read while FastAPI was constructed. MCP was disabled by default, no Compass Settings panel existed, no live runtime status was visible, and an in-app change could not start the SDK runtime.

A second practical update problem was also found: `run.sh`/`run.bat` decided whether Python dependencies needed reinstalling by comparing file modification times. ZIP extraction can preserve an older `requirements.txt` timestamp while `.venv/.deps_installed` is newer, so an updated project could skip installation of newly declared dependencies such as the MCP SDK and then look nonfunctional.

## Implemented architecture

### Canonical Settings ownership

`Settings.mcp` is now a real persisted application setting. It owns:

- enabled state;
- dedicated bearer token;
- MCP principal id;
- canonical LJS user binding;
- client id;
- bounded application capabilities.

MCP server enablement/token configuration is no longer owned by `LJS_MCP_*` process environment variables. Secret state is persisted only in the ignored local Settings file; the committed template carries no live token.

### Live switch without a second LJS runtime

`/mcp` is always mounted inside the existing FastAPI process through `MCPDynamicMount`.

The top-level LJS lifespan starts one `MCPRuntimeWorker`. That worker is the only task allowed to enter/exit the MCP SDK `session_manager.run()` async context. Settings requests send transition commands to the worker and wait for the observed result.

`MCPRuntimeController` owns the transaction around runtime + persisted Settings:

1. build and validate a detached candidate;
2. transition the live runtime;
3. persist the candidate;
4. on runtime failure, persistence failure, or request cancellation, restore the prior canonical Settings/runtime.

This avoids crossing SDK/AnyIO context ownership between unrelated HTTP request tasks and avoids spawning a second LJS application/runtime.

### Failure truth

The switch does not simply set a flag and claim success.

- Disabled runtime: `/mcp` stable mount returns 503.
- Successfully enabled runtime: Settings reports `running=true` only after the SDK session manager is active.
- Missing/broken SDK dependency: the mutation reports the actionable dependency error; startup from an already-enabled persisted configuration keeps LJS healthy and surfaces the error in Compass.
- Runtime replacement failure: previous runtime/token are restored.
- Settings persistence failure after candidate write: old persisted state and old runtime are restored.
- Cancelled Settings request after transition was queued: transition completes under the owner task and the controller restores previous truth before cancellation escapes.

## Compass UI

A new **MCP — External LLM Control** panel was added to the main Compass Settings UI.

It contains:

- live **Enable MCP server** switch (applies immediately);
- visible runtime state (`Running`, `Disabled`, or failure state);
- exact MCP address based on the actual running LJS port, normally `http://127.0.0.1:8088/mcp`;
- copyable dedicated bearer token;
- token regeneration;
- canonical LJS user binding;
- optional download/tracking action grant;
- copyable/generic Streamable-HTTP client configuration;
- explicit instructions explaining that the MCP URL belongs in the LLM application's **MCP / Tools / Integrations** configuration, not in the OpenAI/Ollama/provider base-URL field;
- explicit local-only warning.

The Settings API endpoints are:

- `GET /api/settings/mcp`
- `POST /api/settings/mcp`

Mutations still pass through `ActionGateway` as `settings_update_mcp`.

Generic `/api/settings` redacts the MCP bearer token.

## Dependency update reliability

`run.sh` and `run.bat` now compute the SHA-256 contents of `requirements.txt` and store/compare that hash in `.venv/.deps_installed`.

A dependency change therefore forces reinstall based on content, independent of ZIP or filesystem timestamps. Existing old/empty markers naturally mismatch and trigger one reinstall.

Normal recovery for a stale environment is:

```bash
./run.sh update
```

Windows:

```text
run.bat update
```

Then start LJS normally.

## Main files changed

- `.env.example`
- `config/settings.template.yaml`
- `src/core/domain_models/settings.py`
- `src/core/config.py`
- `src/integrations/mcp_configuration.py`
- `src/integrations/mcp_auth.py`
- `src/integrations/mcp_server.py`
- `src/integrations/mcp_runtime.py`
- `src/integrations/mcp_runtime_worker.py` (new)
- `src/web/app.py`
- `src/web/dependencies.py`
- `src/web/action_handlers/mcp.py` (new)
- `src/core/actions/registration.py`
- `src/web/routers/settings.py`
- `src/web/static/js/components/settingsPanel.js`
- `src/web/static/css/style.css`
- `main.py`
- `run.sh`
- `run.bat`
- `scripts/mcp_control_plane_tests.py`
- `scripts/check_mcp_architecture.py`
- `scripts/mcp_live_acceptance.py`
- `README.md`
- `SECURITY.md`
- `architecture.md`
- `PROGRESS.md`
- `MCP_LIVE_ACCEPTANCE_LOCAL_AGENT_2026-08-17.md` (new current protocol)

The old 2026-08-15 live acceptance document remains only as a superseded historical pointer.

## Dependency-light/runtime-transaction coverage added

`mcp_control_plane_tests.py` now proves, using a fake SDK adapter/session manager:

- Settings persistence/reload;
- content-hash launcher dependency detection;
- live enable/disable;
- automatic token creation;
- live token replacement;
- same-task enter/exit ownership for every SDK context;
- LIFO/non-nested runtime contexts;
- rollback after a cancelled Settings update;
- rollback after a failed runtime replacement;
- rollback after a Settings persistence error even when the candidate state was already observed as written.

The prior Round-295 authorization/capability/cancellation/handle/security checks remain.

## Final feasible validation in this environment

PASS:

```text
python -m compileall -q src scripts main.py
node --check src/web/static/js/components/settingsPanel.js
bash -n run.sh
PYTHONPATH=. python scripts/mcp_control_plane_tests.py
    MCP_CONTROL_PLANE_PASS
PYTHONPATH=. python scripts/check_mcp_architecture.py
    MCP_ARCHITECTURE_PASS
PYTHONPATH=. python scripts/round293_ella_search_selection_cancel_tests.py
    ROUND293_ELLA_SEARCH_SELECTION_CANCEL_PASS
PYTHONPATH=. python scripts/check_ai_intent_architecture.py
PYTHONPATH=. python scripts/check_ai_context_architecture.py
PYTHONPATH=. python scripts/check_security_architecture.py
PYTHONPATH=. python scripts/check_category_architecture.py
PYTHONPATH=. python scripts/check_public_docs.py
PYTHONPATH=. python scripts/check_model_facade_imports.py
PYTHONPATH=. python scripts/check_architecture.py
```

Architecture audit final summary:

```text
Files scanned:        376
HARD findings:        0
RISK findings:        168
ADVISORY findings:    423
```

The risk/advisory findings are review prompts dominated by pre-existing large legacy classes/methods/private-access warnings; no hard architecture violation is reported.

## Explicit environment limitation

This ChatGPT execution image does not contain the project's declared runtime packages:

```text
aiosqlite  MISSING
mcp        MISSING
httpx2     MISSING
litellm    MISSING
```

An isolated package-install attempt also failed because the environment cannot reach the package index.

Therefore these dependency-complete gates cannot be honestly claimed here:

- `check_compatibility_shims.py` stops on missing `aiosqlite` before the check can execute;
- `pytest` stops during `conftest.py` import on missing `aiosqlite`;
- a real MCP SDK Streamable-HTTP handshake cannot run without `mcp`/`httpx2`.

This is why `MCP_LIVE_ACCEPTANCE_LOCAL_AGENT_2026-08-17.md` is an explicit remaining real-machine gate. It uses the project's own virtualenv, full pytest/static checks, one normally running LJS process, Compass enable/disable, a real SDK client probe, fresh-client conversation continuity, authentication/capability checks, and switch-off/switch-on proof. It creates evidence in place and does not request any ZIP packaging.

## Expected owner workflow

1. Replace/update the project with this Round-296 tree.
2. Start through the normal launcher. Because `requirements.txt` content changed relative to an old environment marker, dependency reinstall should be triggered automatically; `./run.sh update` may be used explicitly.
3. Open Compass Settings.
4. Find **MCP — External LLM Control**.
5. Turn **Enable MCP server** ON.
6. Require the displayed status to become **Running**.
7. Copy the displayed address and token into the MCP/Tools/Integrations configuration of the local LLM application/agent host.
8. For formal acceptance, execute `MCP_LIVE_ACCEPTANCE_LOCAL_AGENT_2026-08-17.md` exactly and stop on the first failure.

Do not expand MCP to LAN/remote transport in this round. The current security contract is deliberately loopback-only.
