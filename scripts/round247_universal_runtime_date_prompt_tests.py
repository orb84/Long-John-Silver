"""Round 247 checks that every LJS LLM prompt receives runtime date context."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]

import sys
from types import SimpleNamespace

sys.path.insert(0, str(ROOT))
sys.modules.setdefault('litellm', SimpleNamespace(acompletion=AsyncMock()))

from src.ai.runtime_date_grounding import RuntimeDateGrounding
from src.core.models import LLMConfig
from src.llm_providers import LLMProviderManager
from src.llm_providers.client import LLMClient
from src.llm_providers.key_store import KeyStore
from src.llm_providers.registry import ProviderRegistry
from src.llm_providers.task_client import TaskLLMClient
from src.search.web.research_guidance import WebResearchPromptGuidance
from src.utils.runtime_prompt_context import RuntimePromptContext


class Check:
    """Small assertion collector for script-style regression tests."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, condition: bool, message: str) -> None:
        """Record a failed check without aborting the whole script."""
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        """Exit non-zero when any check failed."""
        if self.failures:
            print('Round 247 universal runtime date prompt failures:')
            for failure in self.failures:
                print(f' - {failure}')
            raise SystemExit(1)
        print('Round 247 universal runtime date prompt tests passed.')


def _run(coro: object) -> object:
    """Run an async test coroutine."""
    return asyncio.run(coro)


def _make_task_client() -> tuple[TaskLLMClient, Path]:
    """Create a task-aware client with a temporary key store."""
    handle = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
    handle.close()
    path = Path(handle.name)
    path.write_text('{}', encoding='utf-8')
    manager = LLMProviderManager(key_store_path=str(path))
    manager.registry.set_active_provider('openrouter')
    return TaskLLMClient(manager=manager, llm_config=LLMConfig(model='gpt-3.5-turbo')), path


def test_runtime_context_message_injection(check: Check) -> None:
    """Validate message injection, first-system preservation, and dedupe."""
    user_only = RuntimePromptContext.ensure_messages([{'role': 'user', 'content': 'hello'}])
    check.ok(user_only[0]['role'] == 'system', 'user-only prompts should receive a leading system date context')
    check.ok('CURRENT RUNTIME DATETIME:' in user_only[0]['content'], 'injected system context should include current datetime')
    check.ok('today, tomorrow, yesterday' in user_only[0]['content'], 'runtime guidance should explain relative-date use')

    strict_json = RuntimePromptContext.ensure_messages([
        {'role': 'system', 'content': 'Return strict JSON only.'},
        {'role': 'user', 'content': '{}'},
    ])
    check.ok(len(strict_json) == 2, 'existing system prompts should be augmented, not displaced')
    check.ok(strict_json[0]['content'].startswith('Return strict JSON only.'), 'original first system prompt should remain first')
    check.ok('CURRENT RUNTIME DATETIME:' in strict_json[0]['content'], 'existing system prompt should carry runtime date context')

    already_grounded = RuntimePromptContext.ensure_messages(strict_json)
    count = sum(str(message.get('content') or '').count('CURRENT RUNTIME DATETIME:') for message in already_grounded)
    check.ok(count == 1, 'runtime prompt context should not be duplicated')


def test_shared_prompt_and_payload_sources(check: Check) -> None:
    """Validate prompt text and tool-result payloads share the same helper."""
    prompt_context = WebResearchPromptGuidance.runtime_context()
    check.ok('CURRENT RUNTIME DATETIME:' in prompt_context, 'web research prompt should include runtime datetime')
    check.ok('CURRENT TIMEZONE:' in prompt_context, 'web research prompt should include timezone')
    check.ok('RUNTIME DATE/TIME RULES:' in prompt_context, 'web research prompt should explain how to use the date')

    payload = RuntimeDateGrounding.runtime_context()
    for key in ('current_datetime', 'current_date', 'current_year', 'timezone', 'rule'):
        check.ok(key in payload, f'runtime date payload should include {key}')
    check.ok('upcoming' in str(payload.get('rule', '')), 'runtime date payload should preserve tense guidance')


def test_task_llm_client_injects_before_provider_call(check: Check) -> None:
    """Validate the task-aware provider boundary injects date context."""
    client, path = _make_task_client()
    try:
        with patch('src.llm_providers.task_client.litellm') as mock_litellm:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)
            _run(client.completion('chat', [{'role': 'user', 'content': 'hello'}]))
            messages = mock_litellm.acompletion.call_args[1]['messages']
            check.ok(messages[0]['role'] == 'system', 'TaskLLMClient should inject a system context for user-only calls')
            check.ok('CURRENT RUNTIME DATETIME:' in messages[0]['content'], 'TaskLLMClient provider call should include runtime datetime')

        with patch('src.llm_providers.task_client.litellm') as mock_litellm:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)
            _run(client.completion('chat', [
                {'role': 'system', 'content': 'Return strict JSON only.'},
                {'role': 'user', 'content': '{}'},
            ]))
            messages = mock_litellm.acompletion.call_args[1]['messages']
            check.ok(messages[0]['content'].startswith('Return strict JSON only.'), 'TaskLLMClient should preserve the first system prompt')
            check.ok('CURRENT RUNTIME DATETIME:' in messages[0]['content'], 'TaskLLMClient should augment existing system prompts')
    finally:
        path.unlink(missing_ok=True)


def test_lower_level_llm_client_injects_before_provider_call(check: Check) -> None:
    """Validate the older provider client also injects runtime context."""
    handle = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
    handle.close()
    path = Path(handle.name)
    path.write_text('{}', encoding='utf-8')
    try:
        store = KeyStore(store_path=str(path))
        registry = ProviderRegistry(key_store=store)
        registry.set_active_provider('ollama_local')
        client = LLMClient(registry=registry)
        with patch('src.llm_providers.client.litellm') as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=MagicMock())
            _run(client.completion(messages=[{'role': 'user', 'content': 'hello'}], model='local-model'))
            messages = mock_litellm.acompletion.call_args[1]['messages']
            check.ok(messages[0]['role'] == 'system', 'LLMClient should inject a system context for user-only calls')
            check.ok('CURRENT RUNTIME DATETIME:' in messages[0]['content'], 'LLMClient provider call should include runtime datetime')
    finally:
        path.unlink(missing_ok=True)


def test_legacy_direct_litellm_paths_are_guarded(check: Check) -> None:
    """Validate known direct litellm compatibility paths apply the shared context."""
    direct_files = [
        ROOT / 'src/ai/intent_router.py',
        ROOT / 'src/ai/reasoning.py',
        ROOT / 'src/core/conversation.py',
    ]
    for path in direct_files:
        text = path.read_text(encoding='utf-8')
        check.ok('RuntimePromptContext.ensure_messages' in text, f'{path.name} should guard direct provider calls')


def main() -> None:
    """Run Round 247 regression checks."""
    check = Check()
    test_runtime_context_message_injection(check)
    test_shared_prompt_and_payload_sources(check)
    test_task_llm_client_injects_before_provider_call(check)
    test_lower_level_llm_client_injects_before_provider_call(check)
    test_legacy_direct_litellm_paths_are_guarded(check)
    check.finish()


if __name__ == '__main__':
    main()
