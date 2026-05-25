"""Pytest wrapper for the Round 84 plan coordinator regression traces."""

from scripts.round84_plan_coordinator_regression_tests import main


def test_round84_plan_coordinator_regressions() -> None:
    main()
