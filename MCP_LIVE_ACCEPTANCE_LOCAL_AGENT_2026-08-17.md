# LJS MCP Live Acceptance — Settings-Controlled Runtime

Use this on the normal LJS machine after installing this snapshot's declared
`requirements.txt`. This protocol verifies the real Streamable-HTTP MCP runtime
that is enabled from Compass. It must use the **same normally running LJS
process**; never start a second MCP/LJS runtime.

## 1. Dependency and static preflight

From the project root:

```bash
./run.sh update
# Windows: run.bat update
```

Then use the project's virtualenv Python explicitly. On macOS/Linux:

```bash
PY=.venv/bin/python
"$PY" -c 'import aiosqlite, mcp, httpx2, litellm; print("LJS_RUNTIME_IMPORTS_PASS")'
"$PY" -m compileall -q src scripts main.py
node --check src/web/static/js/components/settingsPanel.js
PYTHONPATH=. "$PY" scripts/mcp_control_plane_tests.py
PYTHONPATH=. "$PY" scripts/check_mcp_architecture.py
PYTHONPATH=. "$PY" scripts/round293_ella_search_selection_cancel_tests.py
PYTHONPATH=. "$PY" scripts/check_ai_intent_architecture.py
PYTHONPATH=. "$PY" scripts/check_ai_context_architecture.py
PYTHONPATH=. "$PY" scripts/check_security_architecture.py
PYTHONPATH=. "$PY" scripts/check_category_architecture.py
PYTHONPATH=. "$PY" scripts/check_public_docs.py
PYTHONPATH=. "$PY" scripts/check_model_facade_imports.py
PYTHONPATH=. "$PY" scripts/check_compatibility_shims.py
PYTHONPATH=. "$PY" scripts/check_architecture.py
PYTHONPATH=. "$PY" -m pytest -q
```

On Windows use `.venv\Scripts\python.exe` for the same Python commands. Stop
on the first failure. Do not substitute the system Python for the project venv.

Stop on the first failure. Do not weaken a gate.

## 2. Start LJS normally

Use `./run.sh` / `run.bat` (or the normal launcher for this installation). Open
Compass and confirm the ordinary application is healthy.

## 3. Enable MCP from Compass

Open **Compass → MCP — External LLM Control**.

1. Leave **LJS user binding** as `local` unless the owner deliberately wants an
   existing different LJS user.
2. Leave **Allow download / tracking actions** OFF for this first acceptance.
3. Turn **Enable MCP server** ON. The switch applies immediately.
4. Require the status to become **Running**. Copy the displayed **MCP address**
   and **Bearer token**. Use **Apply MCP Settings** only if you separately edit
   the user binding or action-grant controls.

If the status is **Enabled, not running**, preserve the exact displayed error and
STOP. If the error says the MCP runtime dependency is missing, run the launcher
`update` action, restart LJS once, and repeat this section. Do not install random
pins by hand.

The server address must be loopback, normally:

```text
http://127.0.0.1:8088/mcp
```

with the actual LJS port substituted when different.

## 4. Configure the MCP-capable LLM client

Add a Streamable-HTTP MCP server using the exact values shown in Compass. The
generic configuration shape is:

```json
{
  "transport": "streamable-http",
  "url": "http://127.0.0.1:8088/mcp",
  "headers": {
    "Authorization": "Bearer <token copied from Compass>"
  }
}
```

The MCP host/client must run on the same machine as LJS. Do not proxy `/mcp` to
LAN/public interfaces for this release.

## 5. Real SDK probe

In a second shell, pass the copied values only to the probe process:

```bash
PYTHONPATH=. .venv/bin/python scripts/mcp_live_acceptance.py \
  --url 'http://127.0.0.1:8088/mcp' \
  --token '<token copied from Compass>' \
  --evidence mcp_live_acceptance_evidence.json
```

Adjust only the port when required.

Expected final output:

```text
MCP_LIVE_ACCEPTANCE_PASS
```

The probe must verify authentication-before-catalog, loopback isolation, the
curated tool/resource catalog, **successful reads of every fixed `ljs://...`
resource**, bounded tool reads, exact capability denials, delegated conversation
continuity through a fresh MCP/HTTP client, and explicit `ljs.agent_close`
cleanup. It must not intentionally mutate application state. A failure while
reading a fixed resource is release-blocking even if `resources/list` succeeds.

## 6. Live switch proof

With the probe complete:

1. In Compass turn **Enable MCP server** OFF; the switch applies immediately.
2. Require status **Disabled**.
3. Re-run an MCP catalog request/probe and require the endpoint to be unavailable
   (the stable mount returns 503 while disabled).
4. Turn the switch ON again.
5. Require status **Running** without restarting LJS.
6. Reconnect the client and require catalog discovery to work again.

This proves Settings owns the actual runtime rather than only a persisted flag.

## Evidence

Return the probe evidence JSON, complete preflight output, the LJS log covering
enable/disable/re-enable, and the exact Compass runtime status if any step fails.
Never include the bearer token in retained evidence.
