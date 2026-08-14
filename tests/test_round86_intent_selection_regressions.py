"""Pytest wrapper for Round 86 intent/selection regressions."""

import pytest

from scripts import round86_intent_selection_regression_tests as traces


def test_ai_private_attribute_reads_are_defined():
    traces.test_ai_private_attribute_reads_are_defined()


def test_batch_recommendation_uses_seeders_for_equivalent_candidates():
    traces.test_batch_recommendation_uses_seeders_for_equivalent_candidates()


def test_cached_candidate_resolver_has_category_registry_attribute():
    traces.test_cached_candidate_resolver_has_category_registry_attribute()


def test_direct_category_download_plan_is_rewritten_to_generic_search():
    traces.test_direct_category_download_plan_is_rewritten_to_generic_search()


def test_download_intent_exposes_only_generic_tools():
    traces.test_download_intent_exposes_only_generic_tools()


@pytest.mark.asyncio
async def test_pending_context_drives_llm_routing_without_phrase_helper():
    await traces.test_pending_context_drives_llm_routing_without_phrase_helper()


def test_intent_tool_surfaces_remain_intent_specific():
    traces.test_intent_tool_surfaces_remain_intent_specific()
