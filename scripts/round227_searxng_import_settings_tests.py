"""Round 227 regression checks for SearXNG import verification settings.

Current SearXNG loads settings while importing searx.webapp and aborts if the
upstream default server.secret_key is still active. Clean managed installs must
verify webapp imports using an LJS-generated settings file passed through
SEARXNG_SETTINGS_PATH before marking the venv ready.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class Round227SearXNGImportSettingsTests:
    """Verify import verification cannot fall back to upstream default settings."""

    def __init__(self) -> None:
        self._failures: list[str] = []

    def run(self) -> None:
        self._test_webapp_import_uses_ljs_settings_path()
        self._test_run_checked_supports_explicit_env()
        self._test_verification_settings_are_temporary()
        if self._failures:
            raise AssertionError("\n".join(self._failures))
        print("round227_searxng_import_settings_tests: OK")

    def _test_webapp_import_uses_ljs_settings_path(self) -> None:
        source = self._source_text()
        body = self._method_body(source, "_verify_searxng_imports")
        self._check("_write_import_verification_settings()" in body, "webapp import verification must write a generated settings file first")
        self._check('env["SEARXNG_SETTINGS_PATH"] = str(settings_path)' in body, "verification must pass SEARXNG_SETTINGS_PATH explicitly")
        self._check("import msgspec; import searx; import searx.webapp" in body, "verification should still exercise searx.webapp import")
        self._check("env=env" in body, "verification command must receive the explicit environment")

    def _test_run_checked_supports_explicit_env(self) -> None:
        source = self._source_text()
        body = self._method_body(source, "_run_checked")
        self._check("env: dict[str, str] | None = None" in body, "_run_checked must accept an explicit environment")
        self._check("env=env" in body, "_run_checked must forward env to create_subprocess_exec")
        self._check("env_overrides" in body, "_run_checked should trace which env keys were overridden")

    def _test_verification_settings_are_temporary(self) -> None:
        source = self._source_text()
        self._check("def _write_import_verification_settings" in source, "manager must provide verification settings writer")
        self._check("def _import_verification_settings_path" in source, "manager must keep verification settings path separate")
        self._check('"import-verification-settings.yml"' in source, "verification settings should not overwrite final settings.yml")
        self._check("self._render_settings(cfg)" in self._method_body(source, "_write_import_verification_settings"), "verification settings must use normal renderer with random secret")

    @staticmethod
    def _source_text() -> str:
        return (ROOT / "src/search/web/searxng_manager.py").read_text(encoding="utf-8")

    @staticmethod
    def _method_body(source: str, name: str) -> str:
        marker = f"    async def {name}"
        start = source.find(marker)
        if start < 0:
            marker = f"    def {name}"
            start = source.find(marker)
        if start < 0:
            return ""
        next_method = source.find("\n    async def ", start + 1)
        next_sync = source.find("\n    def ", start + 1)
        ends = [pos for pos in (next_method, next_sync) if pos > start]
        end = min(ends) if ends else len(source)
        return source[start:end]

    def _check(self, condition: bool, message: str) -> None:
        if not condition:
            self._failures.append(message)


if __name__ == "__main__":
    Round227SearXNGImportSettingsTests().run()
