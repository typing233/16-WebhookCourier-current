import time
from unittest.mock import patch
from webhook_courier.core.circuit_breaker import CircuitBreakerManager, CircuitState


def make_breaker(failure_threshold=3, recovery_timeout=5, half_open_requests=1):
    mgr = CircuitBreakerManager()
    with patch("webhook_courier.core.circuit_breaker.settings") as mock_settings:
        mock_settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD = failure_threshold
        mock_settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = recovery_timeout
        mock_settings.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = half_open_requests
        yield mgr, mock_settings


def test_starts_closed():
    mgr = CircuitBreakerManager()
    assert mgr.get_state("ep1") == CircuitState.CLOSED


def test_opens_after_threshold():
    mgr = CircuitBreakerManager()
    with patch("webhook_courier.core.circuit_breaker.settings") as s:
        s.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3
        s.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 30
        s.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1
        for _ in range(3):
            mgr.record_failure("ep1")
        assert mgr.get_state("ep1") == CircuitState.OPEN


def test_open_blocks_attempts():
    mgr = CircuitBreakerManager()
    with patch("webhook_courier.core.circuit_breaker.settings") as s:
        s.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 2
        s.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 9999
        s.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1
        mgr.record_failure("ep1")
        mgr.record_failure("ep1")
        assert not mgr.can_attempt("ep1")


def test_half_open_after_recovery():
    mgr = CircuitBreakerManager()
    with patch("webhook_courier.core.circuit_breaker.settings") as s:
        s.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 1
        s.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 0
        s.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1
        mgr.record_failure("ep1")
        assert mgr.get_state("ep1") == CircuitState.OPEN
        assert mgr.can_attempt("ep1")  # recovery_timeout=0 triggers half_open
        assert mgr.get_state("ep1") == CircuitState.HALF_OPEN


def test_success_closes_half_open():
    mgr = CircuitBreakerManager()
    with patch("webhook_courier.core.circuit_breaker.settings") as s:
        s.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 1
        s.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 0
        s.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1
        mgr.record_failure("ep1")
        mgr.can_attempt("ep1")  # transitions to half_open
        mgr.record_success("ep1")
        assert mgr.get_state("ep1") == CircuitState.CLOSED


def test_failure_reopens_from_half_open():
    mgr = CircuitBreakerManager()
    with patch("webhook_courier.core.circuit_breaker.settings") as s:
        s.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 1
        s.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 0
        s.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1
        mgr.record_failure("ep1")
        mgr.can_attempt("ep1")  # transitions to half_open
        mgr.record_failure("ep1")
        assert mgr.get_state("ep1") == CircuitState.OPEN


def test_reset():
    mgr = CircuitBreakerManager()
    with patch("webhook_courier.core.circuit_breaker.settings") as s:
        s.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 1
        s.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 9999
        s.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1
        mgr.record_failure("ep1")
        mgr.reset("ep1")
        assert mgr.get_state("ep1") == CircuitState.CLOSED


def test_success_resets_failure_count_in_closed():
    mgr = CircuitBreakerManager()
    with patch("webhook_courier.core.circuit_breaker.settings") as s:
        s.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3
        s.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 30
        s.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1
        mgr.record_failure("ep1")
        mgr.record_failure("ep1")
        mgr.record_success("ep1")
        mgr.record_failure("ep1")
        mgr.record_failure("ep1")
        # Should still be closed: success reset the count
        assert mgr.get_state("ep1") == CircuitState.CLOSED
