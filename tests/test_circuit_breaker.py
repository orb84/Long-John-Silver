"""
Tests for the circuit breaker utility.

Verifies that CircuitBreaker correctly transitions between states,
rejects calls when open, and recovers after the backoff period.
"""

import asyncio
import time
import pytest
from src.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    STATE_CLOSED,
    STATE_OPEN,
    STATE_HALF_OPEN,
)


class TestCircuitBreakerStates:
    """Tests for circuit breaker state transitions."""

    @pytest.mark.asyncio
    async def test_starts_closed(self):
        breaker = CircuitBreaker("test")
        assert breaker.state == STATE_CLOSED

    @pytest.mark.asyncio
    async def test_stays_closed_on_success(self):
        breaker = CircuitBreaker("test")

        async def ok():
            return "result"

        result = await breaker.call(ok)
        assert result == "result"
        assert breaker.state == STATE_CLOSED
        assert breaker.success_count == 1

    @pytest.mark.asyncio
    async def test_opens_after_failure_threshold(self):
        breaker = CircuitBreaker("test", failure_threshold=3)

        async def fail():
            raise ValueError("boom")

        for i in range(3):
            with pytest.raises(ValueError):
                await breaker.call(fail)

        assert breaker.state == STATE_OPEN
        assert breaker.failure_count == 3

    @pytest.mark.asyncio
    async def test_rejects_calls_when_open(self):
        breaker = CircuitBreaker("test", failure_threshold=1)

        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await breaker.call(fail)

        # Now open — should reject
        async def ok():
            return "should not run"

        with pytest.raises(CircuitOpenError):
            await breaker.call(ok)

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_recovery(self):
        breaker = CircuitBreaker("test", failure_threshold=1, recovery_seconds=0.01)

        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await breaker.call(fail)

        # Wait for recovery period
        await asyncio.sleep(0.05)

        # Should be half_open now
        assert breaker.state == STATE_HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_closes_on_success(self):
        breaker = CircuitBreaker("test", failure_threshold=1, recovery_seconds=0.01)

        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await breaker.call(fail)

        await asyncio.sleep(0.05)

        async def ok():
            return "healed"

        result = await breaker.call(ok)
        assert result == "healed"
        assert breaker.state == STATE_CLOSED

    @pytest.mark.asyncio
    async def test_half_open_reopens_on_failure(self):
        breaker = CircuitBreaker("test", failure_threshold=1, recovery_seconds=0.01)

        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await breaker.call(fail)

        await asyncio.sleep(0.05)

        # Half-open test call fails — re-opens
        with pytest.raises(ValueError):
            await breaker.call(fail)

        assert breaker.state == STATE_OPEN
        # Backoff should have doubled
        assert breaker._backoff_multiplier == 2.0

    @pytest.mark.asyncio
    async def test_reset(self):
        breaker = CircuitBreaker("test", failure_threshold=1)

        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await breaker.call(fail)

        assert breaker.state == STATE_OPEN
        breaker.reset()
        assert breaker.state == STATE_CLOSED
        assert breaker.failure_count == 0