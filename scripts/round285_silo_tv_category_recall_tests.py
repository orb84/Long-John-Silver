#!/usr/bin/env python3
"""Executable Round 285 regression gate for the Silo Italian-search incident."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_round285_tv_category_recall.py"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode:
        raise SystemExit(result.returncode)
    print("Round 285 Silo TV category/Italian recall tests passed")


if __name__ == "__main__":
    main()
