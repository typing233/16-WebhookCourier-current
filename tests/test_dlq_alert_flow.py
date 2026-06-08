"""Integration test: message fails all retries → DLQ → alert fires → cooldown suppresses."""
import asyncio
import json
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webhook_courier.database import Base
from webhook_courier.models import (
    Message, Endpoint, AlertConfig, AlertLog, DeadLetter,
    MessageStatus, gen_id,
)
from webhook_courier.core.dispatcher import Dispatcher
from webhook_courier.core.alerter import Alerter
from webhook_courier.core.circuit_breaker import CircuitBreakerManager


@pytest.fixture
def flow_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    yield session, Session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def setup_flow(flow_db):
    """Create endpoint, message with max_retries=0 (fail immediately to DLQ), and alert config."""
    session, _ = flow_db
    ep = Endpoint(
        id=gen_id(),
        url="http://fail.test/hook",
        secret="test-secret-long-enough",
        is_active=True,
        max_retries=0,
        rate_limit_per_sec=100,
        retry_base_interval=2.0,
    )
    session.add(ep)

    msg = Message(
        id=gen_id(),
        endpoint_id=ep.id,
        app_id=None,
        event_type="test.event",
        idempotency_key="flow-test-1",
        payload=json.dumps({"data": "test"}),
        max_retries=0,
        status=MessageStatus.PENDING,
        next_attempt_at=datetime.now(timezone.utc),
    )
    session.add(msg)

    alert_cfg = AlertConfig(
        id=gen_id(),
        app_id=None,
        endpoint_id=ep.id,
        channel="webhook",
        destination="http://alert.test/hook",
        failure_threshold=1,
        cooldown_seconds=300,
        is_active=True,
    )
    session.add(alert_cfg)
    session.commit()
    return {"endpoint": ep, "message": msg, "alert_config": alert_cfg}


def test_message_to_dlq_triggers_alert(flow_db, setup_flow):
    """When a message exhausts retries and enters DLQ, alert should fire exactly once."""
    session, SessionFactory = flow_db
    ep = setup_flow["endpoint"]
    msg = setup_flow["message"]

    alerter = Alerter()
    cb = CircuitBreakerManager()
    disp = Dispatcher()

    with patch("webhook_courier.core.dispatcher.SessionLocal", SessionFactory), \
         patch("webhook_courier.core.dispatcher.circuit_breaker", cb), \
         patch("webhook_courier.core.dispatcher.rate_limiter") as mock_rl, \
         patch("webhook_courier.core.dispatcher.alerter", alerter), \
         patch("webhook_courier.core.dispatcher.metrics") as mock_metrics, \
         patch.object(alerter, "_send_webhook", new_callable=AsyncMock) as mock_webhook, \
         patch("webhook_courier.core.alerter.SessionLocal", SessionFactory), \
         patch("webhook_courier.core.circuit_breaker.settings") as cb_settings:

        mock_rl.acquire.return_value = True
        cb_settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 99
        cb_settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 30
        cb_settings.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS = 1

        import httpx

        # Simulate HTTP 500 response
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        msg_dict = {
            "id": msg.id,
            "endpoint_id": ep.id,
            "app_id": None,
            "payload": msg.payload,
            "attempt_count": 0,
            "max_retries": 0,
            "event_type": "test.event",
        }

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(disp._deliver(mock_client, msg_dict))
            # Let pending tasks (alerter.maybe_alert) run
            loop.run_until_complete(asyncio.sleep(0.1))
        finally:
            loop.close()

        # Message should be in DLQ
        session.expire_all()
        dead = session.query(DeadLetter).filter(DeadLetter.message_id == msg.id).first()
        assert dead is not None, "Message should be in dead letter queue"

        refreshed_msg = session.query(Message).filter(Message.id == msg.id).first()
        assert refreshed_msg.status == MessageStatus.DEAD

        # Alert should have fired
        mock_webhook.assert_called_once()
        call_args = mock_webhook.call_args
        assert call_args[0][0] == "http://alert.test/hook"
        assert call_args[0][1] == ep.id

        # AlertLog should be recorded
        alert_log = session.query(AlertLog).first()
        assert alert_log is not None
        assert alert_log.endpoint_id == ep.id


def test_dlq_alert_cooldown_suppresses_duplicate(flow_db, setup_flow):
    """Second DLQ alert within cooldown window should be suppressed."""
    session, SessionFactory = flow_db
    ep = setup_flow["endpoint"]
    alert_cfg = setup_flow["alert_config"]

    alerter = Alerter()

    with patch.object(alerter, "_send_webhook", new_callable=AsyncMock) as mock_webhook, \
         patch("webhook_courier.core.alerter.SessionLocal", SessionFactory):

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # First alert fires
            sent1 = loop.run_until_complete(
                alerter.maybe_alert(ep.id, "HTTP 500", 1, db=session)
            )
            assert sent1 == 1
            assert mock_webhook.call_count == 1

            # Second alert within cooldown (300s) — should be suppressed
            sent2 = loop.run_until_complete(
                alerter.maybe_alert(ep.id, "HTTP 500", 1, db=session)
            )
            assert sent2 == 0
            assert mock_webhook.call_count == 1
        finally:
            loop.close()


def test_dlq_alert_email_channel(flow_db):
    """Alert with email channel should call _send_email on DLQ entry."""
    session, SessionFactory = flow_db

    ep = Endpoint(
        id=gen_id(),
        url="http://email-test.test/hook",
        secret="test-secret-long-enough",
        is_active=True,
        max_retries=0,
        rate_limit_per_sec=100,
        retry_base_interval=2.0,
    )
    session.add(ep)

    alert_cfg = AlertConfig(
        id=gen_id(),
        app_id=None,
        endpoint_id=ep.id,
        channel="email",
        destination="ops@company.test",
        failure_threshold=1,
        cooldown_seconds=60,
        is_active=True,
    )
    session.add(alert_cfg)
    session.commit()

    alerter = Alerter()

    with patch.object(alerter, "_send_email") as mock_email, \
         patch("webhook_courier.core.alerter.SessionLocal", SessionFactory):

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            sent = loop.run_until_complete(
                alerter.maybe_alert(ep.id, "ConnectTimeout", 1, db=session)
            )
            assert sent == 1
            mock_email.assert_called_once_with("ops@company.test", ep.id, "ConnectTimeout", 1)
        finally:
            loop.close()
