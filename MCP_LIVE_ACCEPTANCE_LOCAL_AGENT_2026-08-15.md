# LJS MCP Live Acceptance — Local Agent Protocol

Use this only on the normal LJS machine after installing this snapshot's
`requirements.txt`.

## Goal

Prove the real MCP v2 Streamable-HTTP transport and the repaired Round-295
security/truth contracts against the **same normally running LJS process**:
outer-boundary authentication, loopback isolation, exact public catalog,
read/probe authorization separation, persistent conversation continuity across
a fresh client connection, and explicit handle close/revocation. This gate must
not intentionally mutate LJS application state.

## Hard rules

- Work from the delivered snapshot without editing application code first.
- Do **not** start a separate MCP server process or bootstrap a second LJS runtime.
- Do **not** add write, endpoint-write, probe, download, tracking or admin
  capabilities for this first live gate.
- Do **not** disable authentication, loopback enforcement, transport security,
  cancellation guards, or architecture checks to make a test pass.
- Use a newly generated dedicated MCP token. Ordinary LJS Web JWTs are **not**
  valid MCP credentials in this version.
- Stop on the first failed prerequisite/command and preserve exact output. Do not
  repair code while executing the acceptance protocol.

## Prerequisites

From the project root:

```bash
python --version
python -m pip install -r requirements.txt
python -c "import aiosqlite, mcp, httpx2, litellm; print('MCP_DEPS_OK')"
```

Expected: dependency installation succeeds and the import command prints exactly
`MCP_DEPS_OK`.

If installation/import fails: **STOP** and return the complete command/output.
Do not modify dependency pins ad hoc.

## Provider-free/static preflight

Run exactly:

```bash
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
PYTHONPATH=. python scripts/check_compatibility_shims.py
PYTHONPATH=. python scripts/check_architecture.py
PYTHONPATH=. pytest -q
```

Expected:

- `MCP_CONTROL_PLANE_PASS`
- `MCP_ARCHITECTURE_PASS`
- `ROUND293_ELLA_SEARCH_SELECTION_CANCEL_PASS`
- AI/security/category/docs/model-facade/compatibility checks pass
- architecture audit reports `HARD findings: 0`
- pytest passes

The dependency-light MCP gate specifically covers the previously missed hostile
cases: category capability ownership, hidden read-path persistence, provider
secret/endpoint ownership and rollback, generic Web-JWT rejection, missing
client fail-closed behavior, handle quotas/revocation/user binding, admission
limits, `not_running`/`cancelling`/`cancelled` truth, confirmation continuation,
ordered receipt evidence, failed-turn error redaction, and path/status redaction.

If any command fails: **STOP**. Preserve output and do not start LJS.

## Configure a dedicated read-only MCP credential

Generate a token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set:

```bash
export LJS_MCP_ENABLED=1
export LJS_MCP_TOKEN='<generated-token>'
export LJS_MCP_PRINCIPAL_ID='mcp-live-acceptance'
export LJS_MCP_USER_ID='local'
export LJS_MCP_CLIENT_ID='mcp-live-acceptance-client'
export LJS_MCP_CAPABILITIES='agent.delegate,agent.read,status.read,library.read,downloads.read,config.llm.read,diagnostics.read'
```

Do **not** add `library.write`, `library.files.delete`, `downloads.write`,
`tracking.write`, `config.write`, `config.llm.probe`, `config.llm.write`,
`config.llm.endpoint.write`, or `admin`.

If `local` is not the canonical user whose normal preferences/history should be
used on this installation, set `LJS_MCP_USER_ID` to the exact id of an **existing**
LJS user before starting. Non-`local` ids are fail-closed if they do not already
exist. Do not change it during the run.

## Start LJS normally

Use the same ordinary project launch command used for this snapshot. Do not
create a second MCP process. Keep the LJS runtime log visible.

Confirm the ordinary UI loads before proceeding. If startup/migration fails:
**STOP** and return the complete startup log.

## Run the real MCP probe

In a second shell, with the same token available:

```bash
export LJS_MCP_URL='http://127.0.0.1:8088/mcp'
PYTHONPATH=. python scripts/mcp_live_acceptance.py \
  --url "$LJS_MCP_URL" \
  --token "$LJS_MCP_TOKEN" \
  --evidence mcp_live_acceptance_evidence.json
```

Adjust only the port if the normal LJS Web port differs.

Expected final output:

```text
MCP_LIVE_ACCEPTANCE_PASS
evidence=<absolute path>/mcp_live_acceptance_evidence.json
```

The probe must establish all of these:

1. no-token HTTP access is rejected with 401 **before MCP catalog access**;
2. a discoverable non-loopback IPv4 request is rejected with 403;
3. real `tools/list` exactly matches the curated **12-tool** surface, including
   `ljs.agent_close`;
4. real `resources/list` exactly matches the curated 5-resource surface;
5. structured status/capabilities/library/download/LLM read calls succeed;
6. the read-only credential's `ljs.llm_test` attempt is denied specifically for
   missing `config.llm.probe` (not by an unrelated failure);
7. the read-only credential's `ljs.llm_set` attempt is denied specifically for
   missing `config.llm.write`; the test payload uses the already-configured route
   so an authorization regression still does not intentionally alter routing;
8. `ljs.agent_message` returns a server-minted opaque `conversation_id`;
9. continuation through a **fresh MCP client and fresh HTTP client** uses the
   previous conversation memory and returns the same handle;
10. `ljs.agent_close` reports `closed`, after which the handle is revoked and
    its private external session/history lifecycle is cleaned.

Conversation continuity needs a working configured LLM/provider. If the provider
is unavailable, **STOP** and report a provider/runtime blocker rather than
weakening the test.

Wire-level cancellation races are intentionally not made a flaky live gate. The
provider-free MCP acceptance test deterministically exercises no-match,
still-unwinding, settled and owning-request cancellation; the Round-293 incident
harness verifies child search cancellation propagation. If a manual live
cancellation is additionally exercised, preserve its evidence but do not change
the pass/fail definition above.

## Evidence to return

Return:

- `mcp_live_acceptance_evidence.json`;
- complete stdout/stderr from preflight and live probe;
- LJS startup/runtime log covering the probe window;
- tree-diff evidence showing the acceptance run did not modify application
  source;
- whether the non-loopback check ran or was skipped solely because no
  non-loopback IPv4 was discoverable.

Never include the bearer token in evidence; redact it if shell/runtime logs echo
it.

## Success condition

PASS only when every provider-free/static gate passes and
`scripts/mcp_live_acceptance.py` ends with `MCP_LIVE_ACCEPTANCE_PASS` against the
same normally running LJS process. Any prerequisite, static gate, exact
capability-denial, catalog, continuity, or close/revocation failure is a FAIL.
