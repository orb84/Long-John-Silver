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
