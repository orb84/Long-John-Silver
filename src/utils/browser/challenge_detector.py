"""
Challenge detector for LJS browser automation.

Inspects rendered page content and classifies blocking states:
Cloudflare challenges, captcha widgets, consent overlays,
age gates, and HTTP block pages.
"""

from loguru import logger
from src.core.models import BrowserFetchResult, ChallengeDetection


class ChallengeDetector:
    """Detects Cloudflare, captcha, consent, and interstitial pages.

    Inspects a BrowserFetchResult and returns a structured
    ChallengeDetection with the challenge type, confidence,
    and the specific indicators that were matched.
    """

    CLOUDFLARE_KEYWORDS = [
        "checking your browser",
        "just a moment",
        "cloudflare",
    ]

    ISP_BLOCK_KEYWORDS = [
        "avviso",
        "copyright",
        "access denied",
        "blocked by",
    ]

    CAPTCHA_KEYWORDS = [
        "captcha",
        "are you human",
        "i am not a robot",
        "recaptcha",
        "hcaptcha",
    ]

    CONSENT_KEYWORDS = [
        "cookie consent",
        "accept cookies",
        "we use cookies",
    ]

    AGE_GATE_KEYWORDS = [
        "enter your age",
        "you must be",
        "age verification",
        "are you over",
        "date of birth",
    ]

    JS_INTERSTITIAL_KEYWORDS = [
        "enable javascript",
        "please enable javascript",
        "javascript is disabled",
    ]

    def inspect(self, page_result: BrowserFetchResult) -> ChallengeDetection:
        """Classify rendered page blocking or interstitial state.

        Args:
            page_result: The structured fetch result to inspect.

        Returns:
            ChallengeDetection with is_challenge, type, confidence, and matched indicators.
        """
        body_lower = (page_result.title + " " + page_result.text).lower()

        indicators: list[str] = []

        cf_matches = [kw for kw in self.CLOUDFLARE_KEYWORDS if kw in body_lower]
        if cf_matches:
            indicators.extend(cf_matches)
            return ChallengeDetection(
                is_challenge=True,
                challenge_type="cloudflare",
                confidence=0.9,
                indicators=indicators,
            )

        captcha_matches = [kw for kw in self.CAPTCHA_KEYWORDS if kw in body_lower]
        if captcha_matches:
            indicators.extend(captcha_matches)
            return ChallengeDetection(
                is_challenge=True,
                challenge_type="captcha",
                confidence=0.85,
                indicators=indicators,
            )

        consent_matches = [kw for kw in self.CONSENT_KEYWORDS if kw in body_lower]
        if consent_matches:
            indicators.extend(consent_matches)
            return ChallengeDetection(
                is_challenge=True,
                challenge_type="consent",
                confidence=0.7,
                indicators=indicators,
            )

        age_matches = [kw for kw in self.AGE_GATE_KEYWORDS if kw in body_lower]
        if age_matches:
            indicators.extend(age_matches)
            return ChallengeDetection(
                is_challenge=True,
                challenge_type="age_gate",
                confidence=0.7,
                indicators=indicators,
            )

        js_matches = [kw for kw in self.JS_INTERSTITIAL_KEYWORDS if kw in body_lower]
        if js_matches:
            indicators.extend(js_matches)
            return ChallengeDetection(
                is_challenge=True,
                challenge_type="javascript_interstitial",
                confidence=0.8,
                indicators=indicators,
            )

        if page_result.status == 403:
            return ChallengeDetection(
                is_challenge=True,
                challenge_type="block",
                confidence=0.6,
                indicators=[f"HTTP 403 Forbidden"],
            )

        if page_result.status == 429:
            return ChallengeDetection(
                is_challenge=True,
                challenge_type="rate_limit",
                confidence=0.8,
                indicators=[f"HTTP 429 Too Many Requests"],
            )

        return ChallengeDetection(
            is_challenge=False,
            challenge_type=None,
            confidence=1.0,
            indicators=[],
        )
