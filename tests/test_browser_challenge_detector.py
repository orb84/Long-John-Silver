"""
Tests for the browser challenge detector.

Verifies that ChallengeDetector correctly classifies Cloudflare,
captcha, consent, age gate, JavaScript interstitial pages, and
correctly returns no-challenge for normal pages.
"""

import pytest
from src.core.models import BrowserFetchResult, ChallengeDetection
from src.utils.browser.challenge_detector import ChallengeDetector


def _make_result(title="", text="", status=200):
    return BrowserFetchResult(
        ok=True,
        url="https://example.com/page",
        final_url="https://example.com/page",
        status=status,
        title=title,
        text=text,
    )


class TestChallengeDetector:
    """Tests for ChallengeDetector challenge classification."""

    def test_normal_page_returns_no_challenge(self):
        detector = ChallengeDetector()
        result = _make_result(title="My Page", text="Hello world, this is a normal page.")
        detection = detector.inspect(result)
        assert not detection.is_challenge
        assert detection.challenge_type is None

    def test_cloudflare_just_a_moment_detected(self):
        detector = ChallengeDetector()
        result = _make_result(
            title="Just a moment...",
            text="Checking your browser before accessing the site. DDoS protection by Cloudflare.",
        )
        detection = detector.inspect(result)
        assert detection.is_challenge
        assert detection.challenge_type == "cloudflare"
        assert detection.confidence >= 0.8

    def test_cloudflare_avviso_detected(self):
        detector = ChallengeDetector()
        result = _make_result(
            title="Attention Required",
            text="Attendere prego... Verifica della sicurezza... Avviso di Cloudflare.",
        )
        detection = detector.inspect(result)
        assert detection.is_challenge
        assert detection.challenge_type == "cloudflare"

    def test_captcha_detected(self):
        detector = ChallengeDetector()
        result = _make_result(
            title="Verify",
            text="Please complete the captcha to prove you are human.",
        )
        detection = detector.inspect(result)
        assert detection.is_challenge
        assert detection.challenge_type == "captcha"

    def test_hcaptcha_detected(self):
        detector = ChallengeDetector()
        result = _make_result(
            title="Security Check",
            text="hCaptcha: I am not a robot. Please verify.",
        )
        detection = detector.inspect(result)
        assert detection.is_challenge
        assert detection.challenge_type == "captcha"

    def test_cookie_consent_detected(self):
        detector = ChallengeDetector()
        result = _make_result(
            title="Welcome",
            text="This site uses cookies. Click accept cookies to continue browsing.",
        )
        detection = detector.inspect(result)
        assert detection.is_challenge
        assert detection.challenge_type == "consent"

    def test_age_gate_detected(self):
        detector = ChallengeDetector()
        result = _make_result(
            title="Age Verification",
            text="You must be 18 years or older. Please enter your date of birth.",
        )
        detection = detector.inspect(result)
        assert detection.is_challenge
        assert detection.challenge_type == "age_gate"

    def test_javascript_interstitial_detected(self):
        detector = ChallengeDetector()
        result = _make_result(
            title="Error",
            text="This site requires JavaScript. Please enable JavaScript in your browser.",
        )
        detection = detector.inspect(result)
        assert detection.is_challenge
        assert detection.challenge_type == "javascript_interstitial"

    def test_403_block_detected(self):
        detector = ChallengeDetector()
        result = _make_result(status=403, title="Forbidden", text="")
        detection = detector.inspect(result)
        assert detection.is_challenge
        assert detection.challenge_type == "block"

    def test_429_rate_limit_detected(self):
        detector = ChallengeDetector()
        result = _make_result(status=429, title="Too Many Requests", text="")
        detection = detector.inspect(result)
        assert detection.is_challenge
        assert detection.challenge_type == "rate_limit"

    def test_cloudflare_checked_before_other_types(self):
        detector = ChallengeDetector()
        result = _make_result(
            title="Just a moment...",
            text="Checking your browser... captcha verification... accept cookies",
        )
        detection = detector.inspect(result)
        assert detection.is_challenge
        assert detection.challenge_type == "cloudflare"

    def test_indicators_recorded(self):
        detector = ChallengeDetector()
        result = _make_result(
            title="Just a moment...",
            text="Checking your browser before accessing the site.",
        )
        detection = detector.inspect(result)
        assert len(detection.indicators) >= 1
        assert "checking your browser" in detection.indicators
