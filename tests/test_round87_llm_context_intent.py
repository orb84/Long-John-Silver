"""Round 87 pytest wrapper for LLM-owned intent and context-budget traces."""

import pytest

from scripts.round87_llm_context_intent_tests import (
    test_intent_is_llm_owned_with_pending_context,
    test_plan_language_binding_does_not_parse_user_language_words,
    test_token_budget_compacts_without_losing_core_messages,
)


@pytest.mark.asyncio
async def test_round87_intent_pending_context():
    await test_intent_is_llm_owned_with_pending_context()


@pytest.mark.asyncio
async def test_round87_plan_language_binding():
    await test_plan_language_binding_does_not_parse_user_language_words()


def test_round87_token_budget():
    test_token_budget_compacts_without_losing_core_messages()
