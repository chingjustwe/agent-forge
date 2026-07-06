"""P3a-P1: Tests for RetryPolicy + CircuitBreaker — fault recovery.

Covers:
- RetryPolicy: exponential backoff with jitter (zero on attempt<1, monotonic
  increase, max_delay cap, jitter randomness), is_retryable for default +
  custom exception lists, default + custom configuration
- CircuitBreaker: 3-state lifecycle (closed → open → half-open → closed),
  threshold-based opening, success/failure transitions from each state,
  reset timeout transition to half-open, force reset, default threshold
"""
import time

import pytest

from src.runtime.harness.retry import CircuitBreaker, RetryPolicy


# ── Test fixture: custom exception for retryable-exceptions test ──


class MyError(Exception):
    """Custom exception type used to verify retryable_exceptions config."""


# ── RetryPolicy ─────────────────────────────────────────────────────────


class TestRetryPolicy:
    def test_backoff_returns_zero_for_attempt_zero(self):
        policy = RetryPolicy()
        assert policy.backoff(0) == 0.0

    def test_backoff_increases_with_attempt(self):
        policy = RetryPolicy()
        # With jitter, individual samples vary; compare averages over
        # multiple calls to smooth out the randomness.
        avg_1 = sum(policy.backoff(1) for _ in range(100)) / 100
        avg_3 = sum(policy.backoff(3) for _ in range(100)) / 100
        assert avg_3 > avg_1 > 0

    def test_backoff_capped_at_max_delay(self):
        policy = RetryPolicy(base_delay=100.0, max_delay=5.0)
        # attempt=10 → raw delay would be 100 * 2^9 = 51200, capped to 5.0
        # Jitter keeps it in [2.5, 5.0], always <= 5.0
        for _ in range(50):
            assert policy.backoff(10) <= 5.0

    def test_backoff_has_jitter(self):
        policy = RetryPolicy()
        values = {policy.backoff(5) for _ in range(20)}
        # Jitter is random in [50%, 100%] of the delay; 20 calls should
        # produce more than one distinct value.
        assert len(values) > 1

    def test_is_retryable_timeout(self):
        policy = RetryPolicy()
        assert policy.is_retryable(TimeoutError()) is True

    def test_is_retryable_connection(self):
        policy = RetryPolicy()
        assert policy.is_retryable(ConnectionError()) is True

    def test_is_retryable_value_error(self):
        policy = RetryPolicy()
        assert policy.is_retryable(ValueError()) is False

    def test_is_retryable_custom_exception(self):
        policy = RetryPolicy(retryable_exceptions=["MyError"])
        assert policy.is_retryable(MyError()) is True

    def test_default_max_retries_is_3(self):
        policy = RetryPolicy()
        assert policy.max_retries == 3

    def test_custom_policy(self):
        policy = RetryPolicy(max_retries=5, base_delay=0.5)
        assert policy.max_retries == 5
        assert policy.base_delay == 0.5


# ── CircuitBreaker ──────────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == "closed"
        assert cb.can_execute() is True

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"  # not yet at threshold
        cb.record_failure()
        assert cb.state == "open"
        assert cb.can_execute() is False

    def test_record_success_resets_to_closed(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2
        assert cb.state == "closed"
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == "closed"

    def test_half_open_after_reset_timeout(self):
        cb = CircuitBreaker(threshold=3, reset_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.15)
        assert cb.can_execute() is True
        assert cb.state == "half-open"

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(threshold=3, reset_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.15)
        cb.can_execute()  # transitions to half-open
        assert cb.state == "half-open"
        cb.record_failure()
        assert cb.state == "open"

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(threshold=3, reset_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.15)
        cb.can_execute()  # transitions to half-open
        assert cb.state == "half-open"
        cb.record_success()
        assert cb.state == "closed"
        assert cb.failure_count == 0

    def test_reset_method(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        cb.reset()
        assert cb.state == "closed"
        assert cb.failure_count == 0
        assert cb.last_failure_time is None

    def test_threshold_default_is_5(self):
        cb = CircuitBreaker()
        assert cb.threshold == 5
