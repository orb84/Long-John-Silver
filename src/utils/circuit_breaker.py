"""
Circuit breaker for LJS.

Protects external service calls (LLM, API) from cascading failures.
When failures exceed a threshold, the circuit opens and rejects calls
immediately with exponential backoff before retrying. This prevents
hammering a failing API with retries that will never succeed.
"""

import time
from typing import Any, Callable
from loguru import logger


# Circuit states
STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"

# Default thresholds
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_RECOVERY_SECONDS = 60
DEFAULT_MAX_BACKOFF_SECONDS = 300


class CircuitBreaker:
    """Protects a service from cascading failures with open/half-open/closed states.

    In CLOSED state, calls pass through normally. When failures exceed
    the threshold, the circuit transitions to OPEN and immediately rejects
    all calls. After the recovery period, it transitions to HALF_OPEN and
    allows one test call. If that succeeds, the circuit closes again; if it
    fails, the circuit re-opens with exponential backoff.

    Typical usage:
        breaker = CircuitBreaker("llm")
        result = await breaker.call(some_async_function, arg1, arg2)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        recovery_seconds: float = DEFAULT_RECOVERY_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    ):
        """Initialize the circuit breaker.

        Args:
            name: Human-readable name for logging (e.g., "llm", "tmdb").
            failure_threshold: Number of consecutive failures before opening.
            recovery_seconds: Seconds to wait in OPEN state before trying HALF_OPEN.
            max_backoff_seconds: Maximum backoff duration (caps exponential growth).
        """
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._state = STATE_CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._backoff_multiplier = 1.0
        self._success_count = 0

    @property
    def state(self) -> str:
        """Current circuit state: closed, open, or half_open."""
        if self._state == STATE_OPEN:
            # Check if recovery period has elapsed
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_seconds * self._backoff_multiplier:
                logger.info(
                    f"CircuitBreaker[{self._name}]: recovery period elapsed, "
                    f"transitioning to HALF_OPEN"
                )
                self._state = STATE_HALF_OPEN
        return self._state

    @property
    def failure_count(self) -> int:
        """Number of consecutive failures in the current cycle."""
        return self._failure_count

    @property
    def success_count(self) -> int:
        """Total successful calls since last reset."""
        return self._success_count

    async def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute an async function through the circuit breaker.

        If the circuit is OPEN, raises CircuitOpenError immediately.
        If HALF_OPEN, allows one call; success closes the circuit,
        failure re-opens it with increased backoff.

        Args:
            fn: Async callable to execute.
            *args: Positional arguments for fn.
            **kwargs: Keyword arguments for fn.

        Returns:
            The result of fn(*args, **kwargs).

        Raises:
            CircuitOpenError: When the circuit is OPEN and rejecting calls.
        """
        current_state = self.state

        if current_state == STATE_OPEN:
            raise CircuitOpenError(
                f"CircuitBreaker[{self._name}] is OPEN — rejecting call. "
                f"Retry after {self._recovery_seconds * self._backoff_multiplier:.0f}s "
                f"({self._failure_count} consecutive failures)"
            )

        try:
            result = await fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise

    def _on_success(self) -> None:
        """Record a successful call; close the circuit if HALF_OPEN."""
        self._success_count += 1
        if self._state == STATE_HALF_OPEN:
            logger.info(f"CircuitBreaker[{self._name}]: test call succeeded, closing circuit")
            self._state = STATE_CLOSED
            self._backoff_multiplier = 1.0
        self._failure_count = 0

    def _on_failure(self, error: Exception) -> None:
        """Record a failure; open the circuit if threshold is reached."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == STATE_HALF_OPEN:
            # Test call failed — re-open with increased backoff
            self._backoff_multiplier = min(
                self._backoff_multiplier * 2.0,
                self._max_backoff_seconds / self._recovery_seconds,
            )
            logger.warning(
                f"CircuitBreaker[{self._name}]: test call failed in HALF_OPEN, "
                f"re-opening with backoff {self._backoff_multiplier:.1f}x"
            )
            self._state = STATE_OPEN
        elif self._failure_count >= self._failure_threshold:
            # Exceeded threshold — open the circuit
            logger.warning(
                f"CircuitBreaker[{self._name}]: {self._failure_count} consecutive failures, "
                f"opening circuit (threshold={self._failure_threshold})"
            )
            self._state = STATE_OPEN

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED state."""
        self._state = STATE_CLOSED
        self._failure_count = 0
        self._backoff_multiplier = 1.0
        logger.info(f"CircuitBreaker[{self._name}]: manually reset to CLOSED")


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is in OPEN state and rejecting calls."""
    pass