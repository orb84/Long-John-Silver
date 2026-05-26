# Auto-start at system boot/login

LJS exposes launch-at-login as a single user-facing checkbox, available during first-run setup and later in Compass under **Application Startup**.

The feature is intentionally user-level and opt-in. It does not install a service, daemon, or privileged background agent. That keeps the behavior easy to reason about and easy to disable.

## Supported platforms

| Platform | Mechanism | Location |
| --- | --- | --- |
| macOS | LaunchAgent plist | `~/Library/LaunchAgents/com.longjohnsilver.ljs.plist` |
| Linux desktop sessions | freedesktop autostart entry | `~/.config/autostart/long-john-silver.desktop` |
| Windows | Current-user Run key | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` |

Headless Linux servers and Docker/container deployments should use an external supervisor such as systemd or the container runtime. The in-app checkbox intentionally handles login-session startup only.

The implementation lives in `src/core/autostart.py` behind `AutoStartManager`.

## User experience

Setup asks:

> Start LJS automatically when I log in

Compass exposes the same checkbox. Enabling it writes the OS entry immediately; disabling it removes the OS entry immediately. If the OS write fails, the backend returns a warning and stores the actual detected state instead of pretending the preference was applied.

## Design rules

1. **No admin privileges.** LJS only writes per-user startup entries.
2. **No hidden behavior.** The setting is persisted as `Settings.auto_start_at_login`, and the OS entry is reconciled best-effort on app launch.
3. **No platform logic in UI code.** Web actions call `AutoStartManager`; templates and JavaScript only deal with a checkbox.
4. **Packaged-app ready.** Source checkouts default to the source root containing `main.py` plus `sys.executable`; future app bundles can pass an explicit command to `AutoStartManager` without changing UI/actions.
5. **Stale entry detection.** Existing launch files/registry values are considered enabled only when they point at the current checkout/command. Moving the project and enabling the checkbox rewrites the entry instead of reporting a stale path as healthy.

## Startup reconciliation

When `auto_start_at_login` is true, `main.py` performs a best-effort `AutoStartManager().set_enabled(True)` early during launch. Failure is logged but does not stop manual startup. This protects users from accidentally losing the boot entry after moving/copying the project directory while keeping launch robust.

## Extension guidance

Packaged distributions should instantiate `AutoStartManager(command=[<packaged executable>], working_dir=<app data/root>)` from their composition root or installer action. Do not modify web handlers with packaging-specific paths.

If true background services are added later, keep them separate from this feature. A service/daemon has different lifecycle, permissions, logs, and update semantics than a user-level launch-at-login entry.
