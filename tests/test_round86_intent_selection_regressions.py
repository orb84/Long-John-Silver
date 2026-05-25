"""Pytest wrapper for Round 86 intent/selection regressions."""

from scripts.round86_intent_selection_regression_tests import (
    test_ai_private_attribute_reads_are_defined,
    test_batch_recommendation_uses_seeders_for_equivalent_candidates,
    test_cached_candidate_resolver_has_category_registry_attribute,
    test_direct_category_download_plan_is_rewritten_to_generic_search,
    test_download_intent_exposes_only_generic_tools,
    test_followup_confirmation_routes_back_to_download_context,
    test_intent_tool_surfaces_remain_intent_specific,
)
