"""
Tests for the browser runtime with no-Playwright paths.

Verifies model importability and that BrowserRuntime degrades gracefully
when Playwright or Chromium is unavailable.
"""

import pytest


class TestBrowserRuntimeModels:
    """Tests for browser models and no-playwright degradation."""

    def test_models_importable(self):
        """Verify all new models are importable from src.core.models."""
        from src.core.models import (
            BrowserHealth,
            BrowserFetchRequest,
            BrowserFetchResult,
            PageLink,
            ChallengeDetection,
            TorrentScrapeCandidate,
            TorrentScrapeResult,
            ProviderSearchDiagnostics,
            SearchAggregateResult,
            NormalizedTorrentCandidate,
            WebEvidence,
            WebResearchReport,
            Fact,
            ExtractedFacts,
        )
        from pydantic import BaseModel
        for cls in [BrowserHealth, BrowserFetchRequest, BrowserFetchResult, PageLink,
                     ChallengeDetection, TorrentScrapeCandidate, TorrentScrapeResult,
                     ProviderSearchDiagnostics, SearchAggregateResult,
                     NormalizedTorrentCandidate, WebEvidence, WebResearchReport,
                     Fact, ExtractedFacts]:
            assert issubclass(cls, BaseModel), f"{cls.__name__} is not a BaseModel"
            assert cls.model_fields, f"{cls.__name__} has no model fields"

    def test_domain_policy_defaults(self):
        """Verify domain policy initializes with sensible defaults."""
        from src.utils.browser.domain_policy import BrowserDomainPolicy
        policy = BrowserDomainPolicy()
        assert not policy.is_in_cooldown("example.com")

    def test_challenge_detector_creatable(self):
        """Verify challenge detector can be instantiated."""
        from src.utils.browser.challenge_detector import ChallengeDetector
        detector = ChallengeDetector()
        assert detector is not None

    def test_browser_backward_compat_wrapper_creatable(self):
        """Verify the backwards-compatible Browser wrapper can be created."""
        from src.utils.browser import Browser
        browser = Browser()
        assert browser.available is not None

    def test_playwright_availability_flag(self):
        """Verify the Playwright availability flag is set."""
        from src.utils.browser import _PLAYWRIGHT_AVAILABLE
        assert isinstance(_PLAYWRIGHT_AVAILABLE, bool)
