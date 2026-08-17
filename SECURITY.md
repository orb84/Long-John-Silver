# LJS Security Model

LJS treats every assistant request, user-provided path, downloaded filename, metadata field, and database path as untrusted until runtime policy validates it.

## Core guarantees

- The assistant is not given a generic shell tool.
- Shell commands must go through `CommandPolicy` as structured argv with `shell=False`.
- Package-install commands require explicit approval from a UI/system action.
- Filesystem mutations must go through `SafePathResolver`.
- Category operations are scoped to the category library root and download roots.
- Deletes are permanently removed by default after safe-path validation and any required confirmation. Explicit quarantine remains available only for workflows that deliberately request recoverability; routine download cleanup must never hide media in `.ljs-trash` inside the downloads folder.
- Risky/destructive actions can produce a two-phase confirmation receipt with exact affected paths.
- Security-sensitive operations can be written to `./data/security_audit.jsonl`.
- `scripts/check_security_architecture.py` fails CI if raw unsafe primitives are reintroduced outside `src/core/security/`.
- Managed autostart, slskd, and SearXNG operations use the same capabilities; service managers do not receive exemptions for direct process execution, recursive deletion, copying, or rollback.
- State-changing assistant/UI actions use durable command receipts. If mutation execution may have happened but its receipt cannot be persisted, callers receive an `uncertain` failure and must verify live state before retrying.
- Durable operational payloads are sanitized and bounded so tracker URLs, passkeys, credentials, tokens, and URL query strings do not enter receipts or support bundles.

## Running LJS defensively

Run the process as a dedicated non-root user. In Docker/Podman, mount only the intended library/download/data directories as writable volumes. Keep host system folders read-only or unmounted. App-level guards are important, but OS-level permissions are the final blast-radius limit.

## Expected safe path pattern

```python
resolver = SafePathResolver.for_category(category, settings)
safe_target = resolver.ensure_destination(target, purpose="movie.organize.target")
resolver.safe_move(source, safe_target, purpose="movie.organize.move")
```

## Expected safe subprocess pattern

```python
result = CommandPolicy().run_sync(
    ["ffprobe", "-v", "quiet", "-print_format", "json", str(path)],
    purpose="media.ffprobe",
    capture_output=True,
    text=True,
    timeout=10,
)
```

Do not use `shell=True`, `os.system`, raw `subprocess.run`, raw `Path.unlink`, raw `shutil.rmtree`, or raw `shutil.move` in application code.

## Local MCP control-plane boundary

MCP is disabled by default and is not a second trusted shell/control channel.
The current implementation is intentionally **local-only**:

- `/mcp` is mounted inside the existing LJS FastAPI process, so there is only one
  scheduler/downloader/domain runtime and one assistant authority.
- `LocalMCPNetworkBoundary` rejects non-loopback clients **and requests whose
  client origin is missing/unknown**, even if the ordinary LJS UI listens on
  `0.0.0.0` for LAN use.
- Every MCP HTTP request is authenticated once at the outer ASGI boundary. The
  validated immutable principal is propagated inward; individual tool handlers
  do not re-authenticate client headers.
- Local MCP v1 accepts only a dedicated `LJS_MCP_TOKEN` of at least 32
  characters. Ordinary LJS Web JWTs are deliberately **not** widened into MCP
  credentials or administrator authority.
- `LJS_MCP_USER_ID` binds the dedicated principal to the canonical LJS user used
  by delegated conversation context. The reserved `local` identity may be created
  by the local-session authority; any other configured id must already exist.
  `LJS_MCP_CLIENT_ID` participates in handle ownership. Local v1 configures one
  dedicated token/principal/client tuple at a time; do not share it across
  unrelated local clients.
- Credential capabilities are explicit and default to read/delegate only.
  Delegated agent calls independently default to `allow_actions=false`.
- Capability filtering occurs before private tool definitions reach the LLM and
  is repeated at `ToolRegistry.execute()`.
- Category authorization is explicit application metadata. `risk_level` and
  confirmation/destructive labels do not imply a capability. Concrete download,
  library-write, file-delete, tracking and configuration workflows declare the
  authority they require; unknown future mutations fail closed to constrained
  principals. Destructive category actions are not advertised to ordinary
  delegated assistant turns until a real assistant pending-confirmation seam
  exists; explicit category callers use the workflow's token-bound confirmation.
- Definition-backed workflows receive the invocation/tool context so a nominal
  read path cannot smuggle a hidden persistence mutation (for example by adding
  scheduler-only identifiers).
- External callers never supply internal LJS `session_id` values. Opaque
  `conversation_id` handles are high-entropy, server-minted, persisted, bound to
  principal/client/user, inactivity-expiring, quota-bounded, and revocable with
  `ljs.agent_close`. Revocation/expiry also cleans the private external session and
  its conversation history.
- Delegated provider-backed turns have a per-principal/client concurrency
  admission limit (4 by default), and delegated messages are bounded to 65,536
  characters.
- Cancellation truth is not optimistic: `cancelled` means the owned turn has
  settled; an unwinding child reports `cancelling`; a missing live turn reports
  `not_running`.
- MCP does not export the private `ToolRegistry`, arbitrary `ActionGateway`
  action names, raw torrent/Soulseek/web-search micro-tools, API-key mutation,
  or filesystem paths from canonical library objects.
- LLM provider probing is separate from read authority
  (`config.llm.probe`). Ordinary LLM routing writes require
  `config.llm.write`; endpoint changes additionally require
  `config.llm.endpoint.write`.
- Incoming MCP LLM mutations cannot set top-level or tier API keys. Canonical
  route mutation clears route-incompatible secrets, rolls back persistence and
  runtime configuration on failure, and auto-attaches stored provider keys only
  to the provider's canonical endpoint—not to operator/custom endpoint
  overrides.
- Delegated mutation evidence includes only structurally observed IDs of command
  receipts whose `receipt_persisted` value is true. Assistant prose is never
  mutation proof; confirmation-required results remain `needs_input`.
- Status/library/diagnostic surfaces are bounded and redact raw private exception
  text, secrets, and host-local paths as appropriate.

To generate a dedicated local MCP token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Do not expose the current `/mcp` endpoint through a reverse proxy or public/LAN
address. Standards-compliant remote MCP requires a separately designed TLS and
OAuth 2.1 resource-server deployment; that is intentionally not claimed by this
local transport.
