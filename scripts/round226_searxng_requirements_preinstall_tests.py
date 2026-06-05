"""Round 226 regression checks for current SearXNG editable install behavior.

SearXNG's setup.py imports the searx package while generating build metadata.
Current master imports runtime modules such as msgspec on that path, so clean
managed installs must install requirements.txt before running editable install
and must use --no-build-isolation for the editable step. These checks are
static/deterministic and do not download or run SearXNG.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class Round226SearXNGRequirementsPreinstallTests:
    """Verify SearXNG dependency preflight is encoded in the installer."""

    def __init__(self) -> None:
        self._failures: list[str] = []

    def run(self) -> None:
        self._test_requirements_are_installed_before_editable_package()
        self._test_editable_install_uses_no_build_isolation()
        self._test_import_verification_checks_msgspec_and_webapp()
        if self._failures:
            raise AssertionError("\n".join(self._failures))
        print("round226_searxng_requirements_preinstall_tests: OK")

    def _test_requirements_are_installed_before_editable_package(self) -> None:
        source = self._source_text()
        install_body = self._method_body(source, "_install_searxng_package")
        requirements_pos = install_body.find("_install_searxng_runtime_requirements")
        editable_pos = install_body.find("_install_searxng_editable_no_isolation")
        self._check(requirements_pos >= 0, "package install must call requirements preinstall")
        self._check(editable_pos >= 0, "package install must call editable install step")
        self._check(requirements_pos < editable_pos, "requirements must be installed before editable package build")
        self._check('"-r", str(requirements)' in source, "requirements.txt should be installed with pip/uv -r")
        self._check('"requirements.txt"' in source, "installer should explicitly reference SearXNG requirements.txt")

    def _test_editable_install_uses_no_build_isolation(self) -> None:
        source = self._source_text()
        self._check('"--no-build-isolation"' in source, "editable install must use --no-build-isolation after dependency preinstall")
        self._check('"-e",\n            str(self._source_dir())' in source or '"-e", str(self._source_dir())' in source, "editable install must still install the local SearXNG source")
        self._check("venv.editable_install_with_pip_failed_trying_uv" in source, "uv fallback should remain available for editable install")

    def _test_import_verification_checks_msgspec_and_webapp(self) -> None:
        source = self._source_text()
        self._check("import msgspec; import searx; import searx.webapp" in source, "installer should verify msgspec/searx/webapp imports before marking venv ready")
        self._check("_verify_searxng_imports()" in source, "venv ready marker must be written only after import verification")
        marker_pos = self._method_body(source, "_ensure_venv").find("_mark_venv_ready")
        verify_pos = self._method_body(source, "_ensure_venv").find("_install_searxng_package")
        self._check(verify_pos >= 0 and marker_pos > verify_pos, "ready marker should be written after package install/verification")

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
    Round226SearXNGRequirementsPreinstallTests().run()
