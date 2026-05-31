"""Round 188 regression checks for Soulseek storage readiness and login autostart."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_soulseek_storage_readiness_is_explicit_and_retryable() -> None:
    manager = read("src/integrations/slskd_manager.py")
    main = read("main.py")
    settings = read("src/core/domain_models/settings.py")
    require("_probe_payload_storage_ready" in manager, "slskd startup must preflight payload storage readiness")
    require("_plain_write_probe_directory" in manager, "slskd readiness probe must use a slskd-like plain create/delete probe")
    require("storage_unavailable" in manager, "slskd storage failures must use a distinct retryable status")
    require("Torrent writes may still work" in manager, "diagnostics must explain why torrent writes can differ from slskd startup")
    require("retry_delays = [0, 15, 30, 60, 120, 300]" in main, "managed slskd startup must retry storage-related mount races")
    require("storage_unavailable" in settings, "settings docs must include the storage_unavailable state")


def test_autostart_uses_real_launcher_and_logs() -> None:
    autostart = read("src/core/autostart.py")
    require("start-ljs.sh" in autostart, "autostart must generate a launcher wrapper")
    require("run.sh" in autostart, "autostart wrapper must call the real run.sh launcher")
    require("autostart.log" in autostart, "autostart must capture a dedicated log")
    require("StandardOutPath" in autostart and "StandardErrorPath" in autostart, "macOS LaunchAgent must capture stdout/stderr")
    require("launchctl" in autostart and "bootstrap" in autostart, "macOS LaunchAgent should be bootstrapped when possible")
    require("long-john-silver.service" in autostart and "systemctl" in autostart, "Linux must install a user systemd service as well as XDG autostart")
    require("sys.executable" not in autostart, "source checkout autostart must not bypass run.sh via direct python main.py")


if __name__ == "__main__":
    test_soulseek_storage_readiness_is_explicit_and_retryable()
    test_autostart_uses_real_launcher_and_logs()
    print("round188 soulseek storage/autostart tests passed")
