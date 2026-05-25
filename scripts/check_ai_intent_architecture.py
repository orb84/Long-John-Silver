#!/usr/bin/env python3
"""Guardrails for LLM-owned intent/follow-up routing.

This script intentionally scans for the regression class that caused Round 87:
natural-language phrase lists or helper functions that classify user replies as
DOWNLOAD without asking the LLM and without structured pending-action context.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SNIPPETS = [
    "INTENT_KEYWORDS",
    "_is_torrent_candidate_followup",
    "selection_keywords",
    "Fast-routed intent",
    "Detected torrent candidate follow-up",
    "Forcing Intent.DOWNLOAD",
]

# Tool descriptions/docs can contain examples. The guard focuses on executable AI
# routing/planning code.
SCAN_PATHS = [ROOT / "src" / "ai"]


def main() -> int:
    findings: list[str] = []
    for base in SCAN_PATHS:
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for snippet in FORBIDDEN_SNIPPETS:
                if snippet in text:
                    findings.append(f"{path.relative_to(ROOT)} contains forbidden intent heuristic marker: {snippet}")
    if findings:
        print("AI intent architecture guard failed:")
        for finding in findings:
            print(" -", finding)
        return 1
    print("AI intent architecture guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
