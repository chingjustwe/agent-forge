"""P3a-P1: RetryPolicy + CircuitBreaker — fault recovery.

Replaces the hardcoded retry logic in ``HarnessRuntime._run_with_retry``.
``RetryPolicy`` computes exponential backoff with jitter; ``CircuitBreaker``
tracks consecutive failures and opens after a threshold to prevent
cascading retries against a degraded upstream.

The runtime calls ``CircuitBreaker.can_execute()`` before each attempt,
``record_success()`` / ``record_failure()`` after, and ``RetryPolicy.backoff()``
to compute the sleep delay between retries.
"""
from __future__ import annotations

import random
import time
from typing import Literal

from pydantic import BaseModel, Field


class RetryableError(Exception):
    """Raised when an adapter fails with a retryable exception."""


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open and blocks execution."""


class RetryPolicy(BaseModel):
    """Configurable retry policy with exponential backoff + jitter."""

    max_retries: int = Field(default=3, ge=0)
    base_delay: float = Field(default=1.0, ge=0.0)
    max_delay: float = Field(default=30.0, ge=0.0)
    retryable_exceptions: list[str] = Field(
        default_factory=lambda: ["TimeoutError", "ConnectionError"]
    )

    def backoff(self, attempt: int) -> float:
        """Exponential backoff with jitter.

        ``attempt`` is 1-based (first retry → attempt=1).
        Returns a delay in seconds, capped at ``max_delay``.
        """
        if attempt < 1:
            return 0.0
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        # Jitter: randomize between 50% and 100% of the computed delay
        # to avoid thundering-herd on simultaneous retries.
        return delay * (0.5 + random.random() * 0.5)

    def is_retryable(self, exc: Exception) -> bool:
        """Check whether ``exc`` is in the retryable exceptions list."""
        exc_type_names = {type(exc).__name__}
        for base in type(exc).__mro__[1:]:
            if base is object or base is BaseException:
                break
            exc_type_names.add(base.__name__)
        return bool(exc_type_names & set(self.retryable_exceptions))


class CircuitBreaker:
    """3-state circuit breaker for adapter fault isolation.

    - **closed**: requests flow normally; failures increment ``failure_count``.
    - **open**: after ``threshold`` consecutive failures, all requests are
      blocked for ``reset_timeout`` seconds.
    - **half-open**: after the timeout, one request is allowed through.
      Success → closed (reset); failure → open (reset timer).
    """

    def __init__(
        self,
        threshold: int = 5,
        reset_timeout: float = 30.0,
    ) -> None:
        self.threshold = threshold
        self.reset_timeout = reset_timeout
        self.state: Literal["closed", "open", "half-open"] = "closed"
        self.failure_count: int = 0
        self.last_failure_time: float | None = None

    def can_execute(self) -> bool:
        """Return True if the circuit allows a request through."""
        if self.state == "closed":
            return True
        if self.state == "open":
            # Check if enough time has passed to transition to half-open.
            if self.last_failure_time is not None:
                elapsed = time.monotonic() - self.last_failure_time
                if elapsed >= self.reset_timeout:
                    self.state = "half-open"
                    return True
            return False
        # half-open: allow exactly one request
        return True

    def record_success(self) -> None:
        """Record a successful request; resets the breaker to closed."""
        self.failure_count = 0
        self.state = "closed"
        self.last_failure_time = None

    def record_failure(self) -> None:
        """Record a failed request; may transition to open."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.state == "half-open":
            # Single probe failed — re-open immediately.
            self.state = "open"
        elif self.failure_count >= self.threshold:
            self.state = "open"

    def reset(self) -> None:
        """Force-reset to closed state (for testing / admin override)."""
        self.failure_count = 0
        self.state = "closed"
        self.last_failure_time = None
