#!/usr/bin/env python3
"""Guardrails for compression-first LLM context assembly.

Normal long-context handling must not regress to drop-first/trim-first logic.
Dropping is allowed only in the explicit last-resort safety fallback inside
TokenBudgetManager.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN = {
    ROOT / "src/core/conversation.py": [
        "pop(0)",
        "while total_chars >",
        "trim oldest",
    ],
    ROOT / "src/ai/conversation_binding.py": [
        "pop(0)",
        "trim oldest",
    ],
}


def main() -> int:
    findings: list[str] = []
    for path, snippets in FORBIDDEN.items():
        text = path.read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet in text:
                findings.append(f"{path.relative_to(ROOT)} contains drop-first context marker: {snippet}")

    token_budget = (ROOT / "src/ai/token_budget.py").read_text(encoding="utf-8")
    required = [
        "compress_messages",
        "COMPRESSED EARLIER CONVERSATION CONTEXT",
        "last-resort",
        "raw_recent_context_percent",
    ]
    for marker in required:
        if marker not in token_budget:
            findings.append(f"src/ai/token_budget.py missing compression marker: {marker}")

    if findings:
        print("AI context architecture guard failed:")
        for finding in findings:
            print(" -", finding)
        return 1
    print("AI context architecture guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
