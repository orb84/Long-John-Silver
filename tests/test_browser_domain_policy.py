"""
Tests for browser domain policy.

Verifies domain semaphore enforcement, cooldown tracking,
and escalation of cooldown duration on repeated failures.
"""

import asyncio
import pytest
from src.utils.browser.domain_policy import BrowserDomainPolicy


class TestBrowserDomainPolicy:
    """Tests for domain-level throttling and cooldowns."""

    @pytest.mark.asyncio
    async def test_acquire_returns_domain(self):
        policy = BrowserDomainPolicy()
        result = await policy.acquire("https://example.com/page")
        assert result == "example.com"
        policy.release("example.com")

    @pytest.mark.asyncio
    async def test_same_domain_serialized(self):
        policy = BrowserDomainPolicy(domain_max_pages=1, global_max_pages=5)
        domain = await policy.acquire("https://example.com/a")
        assert domain == "example.com"

        acquired = []

        async def try_acquire():
            result = await policy.acquire("https://example.com/b")
            acquired.append(result)
            if result == "example.com":
                policy.release("example.com")

        task = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.01)
        assert len(acquired) == 0

        policy.release("example.com")
        await asyncio.sleep(0.01)
        assert len(acquired) == 1
        assert acquired[0] == "example.com"
        await task

    @pytest.mark.asyncio
    async def test_different_domains_concurrent(self):
        policy = BrowserDomainPolicy(domain_max_pages=1, global_max_pages=5)

        domain_a = await policy.acquire("https://example.com/page")
        domain_b = await policy.acquire("https://other.org/page")
        assert domain_a == "example.com"
        assert domain_b == "other.org"

        policy.release("example.com")
        policy.release("other.org")

    @pytest.mark.asyncio
    async def test_cooldown_blocks_requests(self):
        policy = BrowserDomainPolicy(challenge_cooldown_seconds=1)
        await policy.acquire("https://blocked.com/a")
        policy.release("blocked.com")

        policy.record_challenge("blocked.com")
        assert policy.is_in_cooldown("blocked.com")

        result = await policy.acquire("https://blocked.com/b")
        assert result == "cooldown"

    @pytest.mark.asyncio
    async def test_cooldown_expires(self):
        policy = BrowserDomainPolicy(challenge_cooldown_seconds=0.01)
        policy.record_challenge("temp.com")
        assert policy.is_in_cooldown("temp.com")

        await asyncio.sleep(0.05)
        assert not policy.is_in_cooldown("temp.com")

    @pytest.mark.asyncio
    async def test_record_success_resets_failure_count(self):
        policy = BrowserDomainPolicy()
        policy.record_failure("example.com")
        policy.record_failure("example.com")
        assert policy._domain_failure_counts.get("example.com", 0) == 2

        policy.record_success("example.com")
        assert policy._domain_failure_counts.get("example.com", 0) == 0

    @pytest.mark.asyncio
    async def test_repeated_failures_escalate_cooldown(self):
        policy = BrowserDomainPolicy(
            challenge_cooldown_seconds=1,
            repeated_cooldown_seconds=5,
        )
        policy.record_failure("bad.com")
        policy.record_failure("bad.com")
        count = policy._domain_failure_counts["bad.com"]
        assert count == 2
