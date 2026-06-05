"""Round 225 regression checks for Linux managed SearXNG uv/pip bootstrap.

The Linux installer previously accepted a partial uv-created virtualenv as an
installed runtime even when the venv had no pip. These tests are deterministic:
they do not download SearXNG, do not run uv, and do not start the sidecar.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search.web.searxng_manager import SearXNGManager


class Round225SearXNGLinuxBootstrapTests:
    """Verify partial installs are rejected and uv-created venvs are seeded."""

    def __init__(self) -> None:
        self._failures: list[str] = []

    def run(self) -> None:
        self._test_partial_uv_venv_is_not_installed()
        self._test_uv_venv_uses_seed_then_fallback()
        self._test_pip_bootstrap_has_uv_and_ensurepip_paths()
        if self._failures:
            raise AssertionError("\n".join(self._failures))
        print("round225_searxng_linux_uv_pip_bootstrap_tests: OK")

    def _test_partial_uv_venv_is_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manager = SearXNGManager(root / "svc", root / "state")
            source = manager._source_dir()
            source.mkdir(parents=True)
            (source / "searx").mkdir()
            python = manager._venv_python()
            python.parent.mkdir(parents=True)
            python.write_text("not a real python", encoding="utf-8")
            self._check(not manager.is_installed, "source + python without LJS ready marker must not be treated as installed")
            manager._mark_venv_ready()
            self._check(manager.is_installed, "ready marker should make the LJS-owned runtime complete")

    def _test_uv_venv_uses_seed_then_fallback(self) -> None:
        source = self._source_text()
        self._check('command.append("--seed")' in source, "uv virtualenv creation must request --seed first")
        self._check("_try_uv_venv(uv, seed=True)" in source, "manager should try seeded uv venv first")
        self._check("_try_uv_venv(uv, seed=False)" in source, "manager should keep a non-seeded uv fallback for older uv versions")

    def _test_pip_bootstrap_has_uv_and_ensurepip_paths(self) -> None:
        source = self._source_text()
        self._check('"pip", "install", "--python", str(python), "pip", "wheel", "setuptools"' in source, "uv pip bootstrap path should install pip without requiring pip first")
        self._check('"-m", "ensurepip", "--upgrade"' in source, "ensurepip should be the second bootstrap path")
        self._check("virtualenv was created without pip and pip bootstrap failed" in source, "missing pip should produce an explicit installer error")
        self._check("venv.partial_runtime_removed" in source, "repair/retry should remove a partial venv before recreating it")

    @staticmethod
    def _source_text() -> str:
        return (ROOT / "src/search/web/searxng_manager.py").read_text(encoding="utf-8")

    def _check(self, condition: bool, message: str) -> None:
        if not condition:
            self._failures.append(message)


if __name__ == "__main__":
    Round225SearXNGLinuxBootstrapTests().run()
