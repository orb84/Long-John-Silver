# Round 211 Local Agent Test Instructions — Autostart + Mobile UI

## Goal
Verify that Round 211 fixes the user-level **Start LJS when I log in** path on macOS/Linux and that the main web UI behaves on narrow/mobile-sized frames.

## Important context
- Do **not** use Docker.
- Test from a normal user account, not root.
- The app should keep using `run.sh`; startup entries should not call `python main.py` directly.
- The generated startup wrapper is `data/autostart/start-ljs.sh` and the diagnostic log is `data/autostart/autostart.log`.
- Final report must include exact paths to all generated artifacts and logs listed below.

## Static checks
Run from the project root:

```bash
python3 scripts/round211_autostart_mobile_tests.py
python3 -m compileall -q src scripts main.py
python3 scripts/check_architecture.py
```

Expected result:
- `round211 autostart/mobile tests: PASS`
- compileall exits 0
- `check_architecture.py` reports `HARD findings: 0`

## macOS autostart manual check
1. Launch LJS normally:
   ```bash
   chmod +x run.sh
   ./run.sh
   ```
2. Open Compass / Application Startup.
3. Enable **Start LJS when I log in** and save.
4. Confirm these files exist:
   - `~/Library/LaunchAgents/com.longjohnsilver.ljs.plist`
   - `<PROJECT_ROOT>/data/autostart/start-ljs.sh`
   - `<PROJECT_ROOT>/data/autostart/autostart.log` may appear after bootstrap/login.
5. Inspect the plist:
   ```bash
   plutil -lint ~/Library/LaunchAgents/com.longjohnsilver.ljs.plist
   grep -n "start-ljs.sh\|RunAtLoad\|EnvironmentVariables" ~/Library/LaunchAgents/com.longjohnsilver.ljs.plist
   ```
6. Log out and log back in, or reboot.
7. After login, visit `http://127.0.0.1:8088/api/live`.
8. Inspect `<PROJECT_ROOT>/data/autostart/autostart.log`.

Expected macOS behavior:
- LJS starts after GUI login without manually running `run.sh`.
- `autostart.log` includes `===== LJS autostart =====` and `launching LJS via run.sh`.
- There is no endless duplicate-start loop. If the app was already running during enablement, duplicate attempts should exit due to the wrapper lock.

## Linux autostart manual check
1. Launch LJS normally:
   ```bash
   chmod +x run.sh
   ./run.sh
   ```
2. Open Compass / Application Startup.
3. Enable **Start LJS when I log in** and save.
4. Confirm these files exist:
   - `~/.config/autostart/long-john-silver.desktop`
   - `~/.config/systemd/user/long-john-silver.service`
   - `<PROJECT_ROOT>/data/autostart/start-ljs.sh`
5. Inspect entries:
   ```bash
   grep -n "Exec=\|start-ljs.sh\|X-GNOME-Autostart" ~/.config/autostart/long-john-silver.desktop
   grep -n "ExecStart=\|WorkingDirectory=\|Environment=" ~/.config/systemd/user/long-john-silver.service
   systemctl --user is-enabled long-john-silver.service || true
   ```
6. Log out and log back in, or reboot.
7. After login, visit `http://127.0.0.1:8088/api/live`.
8. Inspect `<PROJECT_ROOT>/data/autostart/autostart.log`.

Expected Linux behavior:
- LJS starts after login via either XDG autostart or user systemd.
- If both mechanisms fire, only one LJS instance should remain active because the wrapper lock prevents duplicates.
- `autostart.log` includes the user, PATH, project path, and final `run.sh` launch line.

## Mobile / responsive UI check
Use browser devtools device emulation or a real phone/tablet on the LAN.

Test sizes:
- 390 x 844
- 430 x 932
- 768 x 1024

Steps:
1. Open the web UI.
2. Navigate through The Helm, Hold, Booty, Suggestions, Sharing, Compass.
3. In The Helm, ensure the message input is visible without page-level scrolling.
4. Send a short chat message and ensure the feed scrolls internally.
5. Open a modal/detail view if available and confirm it fits inside the viewport.
6. Rotate once between portrait and landscape if testing on a real mobile device.

Expected mobile behavior:
- The header wraps instead of overflowing.
- Navigation scrolls horizontally if needed.
- The Helm chat input stays visible at the bottom of the chat panel.
- The message feed, not the full page, scrolls.
- Modals fit inside the viewport and scroll internally.

## Required final report artifacts
Your final report must include the exact paths to:
- `data/autostart/start-ljs.sh`
- `data/autostart/autostart.log`
- macOS: `~/Library/LaunchAgents/com.longjohnsilver.ljs.plist` if tested
- Linux: `~/.config/autostart/long-john-silver.desktop` if tested
- Linux: `~/.config/systemd/user/long-john-silver.service` if tested
- Any screenshots captured for mobile UI checks, with filenames/paths
- The output file/path where you saved command logs, if any

Also include:
- OS/version tested
- Whether login/reboot startup actually launched LJS
- Whether duplicate-start protection triggered
- Any mobile viewport where layout failed
