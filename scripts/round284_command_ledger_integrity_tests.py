"""Executable Round 284 regression entry point."""

from __future__ import annotations

import subprocess
import sys


class Round284CommandLedgerChecks:
    """Run the adversarial command-ledger regression module."""

    @staticmethod
    def run() -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_round284_command_ledger_integrity.py"],
            check=False,
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
        print("ROUND284_COMMAND_LEDGER_INTEGRITY_PASS")


if __name__ == "__main__":
    Round284CommandLedgerChecks.run()
