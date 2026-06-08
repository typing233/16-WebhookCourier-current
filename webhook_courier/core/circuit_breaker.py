import logging
import time
import threading
from datetime import datetime, timezone
from enum import Enum

from webhook_courier.config import settings

logger = logging.getLogger("webhook_courier.circuit_breaker")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class EndpointCircuit:
    __slots__ = ("state", "failure_count", "success_count", "last_failure_time",
                 "opened_at", "half_open_successes")

    def __init__(self):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.opened_at = 0.0
        self.half_open_successes = 0


class CircuitBreakerManager:
    """Per-endpoint circuit breaker state machine.

    CLOSED -> OPEN: after failure_threshold consecutive failures
    OPEN -> HALF_OPEN: after recovery_timeout seconds
    HALF_OPEN -> CLOSED: on success
    HALF_OPEN -> OPEN: on failure
    """

    def __init__(self):
        self._circuits: dict[str, EndpointCircuit] = {}
        self._lock = threading.Lock()

    def _get_circuit(self, endpoint_id: str) -> EndpointCircuit:
        if endpoint_id not in self._circuits:
            self._circuits[endpoint_id] = EndpointCircuit()
        return self._circuits[endpoint_id]

    def can_attempt(self, endpoint_id: str) -> bool:
        with self._lock:
            circuit = self._get_circuit(endpoint_id)

            if circuit.state == CircuitState.CLOSED:
                return True

            if circuit.state == CircuitState.OPEN:
                elapsed = time.monotonic() - circuit.opened_at
                if elapsed >= settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT:
                    circuit.state = CircuitState.HALF_OPEN
                    circuit.half_open_successes = 0
                    logger.info(f"Circuit half-open for endpoint {endpoint_id}")
                    return True
                return False

            if circuit.state == CircuitState.HALF_OPEN:
                return True

            return False

    def record_success(self, endpoint_id: str):
        with self._lock:
            circuit = self._get_circuit(endpoint_id)
            circuit.success_count += 1

            if circuit.state == CircuitState.HALF_OPEN:
                circuit.half_open_successes += 1
                if circuit.half_open_successes >= settings.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS:
                    circuit.state = CircuitState.CLOSED
                    circuit.failure_count = 0
                    logger.info(f"Circuit closed for endpoint {endpoint_id}")
            elif circuit.state == CircuitState.CLOSED:
                circuit.failure_count = 0

    def record_failure(self, endpoint_id: str):
        with self._lock:
            circuit = self._get_circuit(endpoint_id)
            circuit.failure_count += 1
            circuit.last_failure_time = time.monotonic()

            if circuit.state == CircuitState.HALF_OPEN:
                circuit.state = CircuitState.OPEN
                circuit.opened_at = time.monotonic()
                logger.warning(f"Circuit re-opened for endpoint {endpoint_id}")
            elif circuit.state == CircuitState.CLOSED:
                if circuit.failure_count >= settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD:
                    circuit.state = CircuitState.OPEN
                    circuit.opened_at = time.monotonic()
                    logger.warning(
                        f"Circuit opened for endpoint {endpoint_id} "
                        f"after {circuit.failure_count} failures"
                    )

    def get_state(self, endpoint_id: str) -> CircuitState:
        with self._lock:
            return self._get_circuit(endpoint_id).state

    def get_info(self, endpoint_id: str) -> dict:
        with self._lock:
            c = self._get_circuit(endpoint_id)
            return {
                "state": c.state.value,
                "failure_count": c.failure_count,
                "success_count": c.success_count,
            }

    def reset(self, endpoint_id: str):
        with self._lock:
            if endpoint_id in self._circuits:
                self._circuits[endpoint_id] = EndpointCircuit()

    def load_from_db(self, endpoint_id: str, state_str: str, failure_count: int, success_count: int):
        with self._lock:
            circuit = self._get_circuit(endpoint_id)
            try:
                circuit.state = CircuitState(state_str)
            except ValueError:
                circuit.state = CircuitState.CLOSED
            circuit.failure_count = failure_count
            circuit.success_count = success_count
            if circuit.state == CircuitState.OPEN:
                circuit.opened_at = time.monotonic()


circuit_breaker = CircuitBreakerManager()
