"""Tests for health checker auto-disable and auto-recovery cycle."""
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webhook_courier.database import Base
from webhook_courier.models import Endpoint, CircuitState as ModelCircuitState, gen_id
from webhook_courier.core.circuit_breaker import CircuitBreakerManager, CircuitState
from webhook_courier.core.health_checker import HealthChecker


@pytest.fixture
def health_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    yield session, Session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def endpoint_with_health(health_db):
    session, _ = health_db
    ep = Endpoint(
        id=gen_id(),
        url="http://target.test/hook",
        secret="test-secret-long-enough",
        health_check_url="http://target.test/health",
        health_check_method="GET",
        is_active=True,
        max_retries=3,
        rate_limit_per_sec=100,
        retry_base_interval=2.0,
        circuit_state=ModelCircuitState.CLOSED,
        failure_count=0,
        success_count=0,
    )
    session.add(ep)
    session.commit()
    return ep


def test_full_disable_and_recovery_cycle(health_db, endpoint_with_health):
    """
    Full cycle:
    1. Multiple health check failures → circuit OPEN → endpoint disabled
    2. Health check succeeds → circuit CLOSED → endpoint re-enabled
    """
    session, SessionFactory = health_db
    ep = endpoint_with_health
    cb = CircuitBreakerManager()
    checker = HealthChecker()

    with patch("webhook_courier.core.health_checker.circuit_breaker", cb), \
         patch("webhook_courier.core.health_checker.SessionLocal", SessionFactory), \
         patch("webhook_courier.core.circuit_breaker.settings") as cb_settings:

        cb_settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3
        cb_settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 0
        cb_settings.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1

        # --- Phase 1: Failures trip the circuit and disable endpoint ---
        # Simulate 3 failed health probes
        for _ in range(3):
            cb.record_failure(ep.id)
            checker._persist_health(ep.id, success=False, was_active=True)

        session.refresh(ep)
        assert cb.get_state(ep.id) == CircuitState.OPEN
        assert ep.is_active is False
        assert ep.circuit_state == ModelCircuitState.OPEN

        # --- Phase 2: Successful probe → recovery ---
        # The health checker should force_close the circuit on success
        cb.force_close(ep.id)
        checker._persist_health(ep.id, success=True, was_active=False)

        session.refresh(ep)
        assert cb.get_state(ep.id) == CircuitState.CLOSED
        assert ep.is_active is True
        assert ep.circuit_state == ModelCircuitState.CLOSED
        assert ep.failure_count == 0


def test_probe_success_recovers_disabled_endpoint(health_db, endpoint_with_health):
    """Integration test: actual _probe call with mocked HTTP recovers endpoint."""
    session, SessionFactory = health_db
    ep = endpoint_with_health
    cb = CircuitBreakerManager()
    checker = HealthChecker()

    with patch("webhook_courier.core.health_checker.circuit_breaker", cb), \
         patch("webhook_courier.core.health_checker.SessionLocal", SessionFactory), \
         patch("webhook_courier.core.circuit_breaker.settings") as cb_settings:

        cb_settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 2
        cb_settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 0
        cb_settings.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1

        # Trip the circuit and disable
        cb.record_failure(ep.id)
        cb.record_failure(ep.id)
        assert cb.get_state(ep.id) == CircuitState.OPEN

        ep.is_active = False
        ep.circuit_state = ModelCircuitState.OPEN
        session.commit()

        # Now simulate a successful health probe via _probe
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        # Need to reload the endpoint object as _probe uses it
        ep_snapshot = session.query(Endpoint).filter(Endpoint.id == ep.id).first()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(checker._probe(mock_client, ep_snapshot))
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        # Verify recovery
        session.refresh(ep)
        assert cb.get_state(ep.id) == CircuitState.CLOSED
        assert ep.is_active is True
        assert ep.circuit_state == ModelCircuitState.CLOSED


def test_probe_failure_disables_active_endpoint(health_db, endpoint_with_health):
    """A probe failure that trips the circuit should auto-disable the endpoint."""
    session, SessionFactory = health_db
    ep = endpoint_with_health
    cb = CircuitBreakerManager()
    checker = HealthChecker()

    with patch("webhook_courier.core.health_checker.circuit_breaker", cb), \
         patch("webhook_courier.core.health_checker.SessionLocal", SessionFactory), \
         patch("webhook_courier.core.circuit_breaker.settings") as cb_settings:

        cb_settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 2
        cb_settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 9999
        cb_settings.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        ep_snapshot = session.query(Endpoint).filter(Endpoint.id == ep.id).first()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(checker._probe(mock_client, ep_snapshot))
            loop.run_until_complete(checker._probe(mock_client, ep_snapshot))
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        session.refresh(ep)
        assert cb.get_state(ep.id) == CircuitState.OPEN
        assert ep.is_active is False


def test_probe_success_when_already_closed_keeps_active(health_db, endpoint_with_health):
    """Successful probe on a healthy endpoint should not change is_active."""
    session, SessionFactory = health_db
    ep = endpoint_with_health
    cb = CircuitBreakerManager()
    checker = HealthChecker()

    with patch("webhook_courier.core.health_checker.circuit_breaker", cb), \
         patch("webhook_courier.core.health_checker.SessionLocal", SessionFactory), \
         patch("webhook_courier.core.circuit_breaker.settings") as cb_settings:

        cb_settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
        cb_settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 30
        cb_settings.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        ep_snapshot = session.query(Endpoint).filter(Endpoint.id == ep.id).first()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(checker._probe(mock_client, ep_snapshot))
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        session.refresh(ep)
        assert ep.is_active is True
        assert cb.get_state(ep.id) == CircuitState.CLOSED
