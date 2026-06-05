#!/usr/bin/env python3
"""Round 232 prompt-guidance consistency checks."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai.prompt_builder import PromptBuilder
from src.ai.task_prompt_guidance import TaskPromptGuidance
from src.ai.reasoning import ReasoningPlanner
from src.core.models import Intent


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, cond: bool, msg: str) -> None:
        if not cond:
            self.failures.append(msg)

    def finish(self) -> None:
        if self.failures:
            print("Round 232 prompt guidance consistency failures:")
            for failure in self.failures:
                print(f" - {failure}")
            raise SystemExit(1)
        print("Round 232 prompt guidance consistency tests passed.")


def main() -> None:
    check = Check()

    operating = TaskPromptGuidance.operating_rules()
    check.ok("Use tools for facts/actions" in operating, "shared operating rules should require tools for facts/actions")
    check.ok("Do not invent" in operating, "shared operating rules should forbid invented facts/IDs/paths")

    search_prompt = PromptBuilder().build_system_prompt(Intent.SEARCH, active_category_id="tv")
    check.ok("LLM OPERATING RULES" in search_prompt, "main prompts should include shared operating rules")
    check.ok("CURRENT RUNTIME DATETIME" in search_prompt, "main prompts should include runtime date context")
    check.ok("metadata-only answers are insufficient" in search_prompt, "SEARCH guidance should reject metadata-only current-public answers")
    check.ok("PUBLIC WEB RESEARCH GUIDANCE" in search_prompt, "SEARCH prompt should include public web guidance")

    download_guidance = PromptBuilder()._task_guidance(Intent.DOWNLOAD)
    check.ok("TASK: Find/select torrents" in download_guidance, "DOWNLOAD task phrase should remain reachable")
    check.ok("candidate_id/result_set_id" in download_guidance, "DOWNLOAD guidance should prefer stable handles")
    check.ok("Public web evidence never directly authorizes a download" in download_guidance, "DOWNLOAD guidance should gate web evidence")

    config_guidance = PromptBuilder()._task_guidance(Intent.CONFIG)
    check.ok("prefer create_web_information_watch" in config_guidance, "CONFIG guidance should prefer watches for recurring public tracking")
    check.ok("create_scheduled_task" in config_guidance, "CONFIG guidance should preserve simple scheduled task path")

    planner_prompt = ReasoningPlanner()._build_plan_prompt(
        "Find current rumours about show X and track future downloads",
        Intent.DOWNLOAD,
        "",
        tool_schemas=[{"function": {"name": "category_web_research", "description": "research", "parameters": {"properties": {"query": {"type": "string"}}}}}],
    )
    check.ok("LLM OPERATING RULES" in planner_prompt, "advisory planner should receive shared operating rules")
    check.ok("Advisory planner rules" in planner_prompt, "advisory planner should receive shared planner contract")
    check.ok("Preserve exact user wording" in planner_prompt or "preserve" in planner_prompt.lower(), "planner should preserve user wording")

    scheduled = TaskPromptGuidance.scheduled_task_context("condition_check") + "\n\nUSER STORED TASK:\nCheck whether game X patch Y landed and notify only if yes."
    check.ok("CURRENT RUNTIME DATETIME" in scheduled, "scheduled prompts should include runtime date context")
    check.ok("LJS_NO_NOTIFICATION" in scheduled, "condition checks should include no-change sentinel")
    check.ok("USER STORED TASK" in scheduled, "scheduled wrapper should preserve original user prompt")
    scheduler_src = (ROOT / "src/core/prompt_scheduler.py").read_text(encoding="utf-8")
    check.ok("TaskPromptGuidance.scheduled_task_context" in scheduler_src, "PromptScheduler should reuse shared scheduled-task guidance")

    web_tools = (ROOT / "src/ai/tools/web.py").read_text(encoding="utf-8")
    check.ok("Free-form semantic watch objective" in web_tools, "web-information watch intent should be semantic, not enum-like")
    check.ok("not an enum" in web_tools, "category_web_research intent should remain semantic")

    scheduling = (ROOT / "src/ai/tools/scheduling.py").read_text(encoding="utf-8")
    check.ok("prefer" in scheduling and "create_web_information_watch" in scheduling, "scheduled-task tool schema should route public monitoring to watches")
    check.ok('"interval_minutes": task.interval_minutes,\n                    "interval_minutes": task.interval_minutes' not in scheduling, "scheduled task response should not duplicate interval_minutes within one payload")

    arch = (ROOT / "architecture.md").read_text(encoding="utf-8")
    check.ok("Round 232 LLM Prompt Guidance Rule" in arch, "architecture contract should document prompt guidance rule")

    check.finish()


if __name__ == "__main__":
    main()
