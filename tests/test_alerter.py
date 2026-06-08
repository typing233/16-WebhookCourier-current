import json
import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webhook_courier.database import Base
from webhook_courier.models import AlertConfig, AlertLog, gen_id
from webhook_courier.core.alerter import Alerter


@pytest.fixture
def alert_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def alerter_instance():
    return Alerter()


@pytest.fixture
def webhook_config(alert_db):
    config = AlertConfig(
        id=gen_id(),
        app_id=None,
        endpoint_id="ep-001",
        channel="webhook",
        destination="http://alerts.test/hook",
        failure_threshold=3,
        cooldown_seconds=300,
        is_active=True,
    )
    alert_db.add(config)
    alert_db.commit()
    return config


@pytest.fixture
def global_config(alert_db):
    """Alert config with no endpoint_id (applies to all endpoints)."""
    config = AlertConfig(
        id=gen_id(),
        app_id=None,
        endpoint_id=None,
        channel="webhook",
        destination="http://alerts.test/global",
        failure_threshold=2,
        cooldown_seconds=60,
        is_active=True,
    )
    alert_db.add(config)
    alert_db.commit()
    return config


def test_alert_fires_when_threshold_met(alert_db, alerter_instance, webhook_config):
    """Alert should fire when consecutive_failures >= failure_threshold."""
    with patch.object(alerter_instance, "_send_webhook", new_callable=AsyncMock) as mock_send:
        sent = asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("ep-001", "HTTP 500", 3, db=alert_db)
        )
        assert sent == 1
        mock_send.assert_called_once_with(
            "http://alerts.test/hook", "ep-001", "HTTP 500", 3
        )


def test_alert_does_not_fire_below_threshold(alert_db, alerter_instance, webhook_config):
    """Alert should NOT fire when consecutive_failures < failure_threshold."""
    with patch.object(alerter_instance, "_send_webhook", new_callable=AsyncMock) as mock_send:
        sent = asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("ep-001", "HTTP 500", 2, db=alert_db)
        )
        assert sent == 0
        mock_send.assert_not_called()


def test_alert_suppressed_within_cooldown(alert_db, alerter_instance, webhook_config):
    """Second alert for same endpoint+error within cooldown should be suppressed."""
    with patch.object(alerter_instance, "_send_webhook", new_callable=AsyncMock) as mock_send:
        # First alert fires
        sent1 = asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("ep-001", "HTTP 500", 5, db=alert_db)
        )
        assert sent1 == 1

        # Second alert within cooldown should be suppressed
        sent2 = asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("ep-001", "HTTP 500", 5, db=alert_db)
        )
        assert sent2 == 0
        assert mock_send.call_count == 1  # only called once


def test_alert_not_suppressed_for_different_error(alert_db, alerter_instance, webhook_config):
    """Different error type should not be suppressed."""
    with patch.object(alerter_instance, "_send_webhook", new_callable=AsyncMock) as mock_send:
        asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("ep-001", "HTTP 500", 5, db=alert_db)
        )
        sent = asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("ep-001", "ConnectTimeout", 5, db=alert_db)
        )
        assert sent == 1
        assert mock_send.call_count == 2


def test_alert_not_suppressed_after_cooldown_expires(alert_db, alerter_instance, webhook_config):
    """Alert should fire again after cooldown window expires."""
    with patch.object(alerter_instance, "_send_webhook", new_callable=AsyncMock) as mock_send:
        # First alert
        asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("ep-001", "HTTP 500", 5, db=alert_db)
        )

        # Manually backdate the alert_log entry to simulate cooldown expiry
        log = alert_db.query(AlertLog).first()
        log.sent_at = datetime.now(timezone.utc) - timedelta(seconds=400)
        alert_db.commit()

        # Second alert should now fire (cooldown=300s expired)
        sent = asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("ep-001", "HTTP 500", 5, db=alert_db)
        )
        assert sent == 1
        assert mock_send.call_count == 2


def test_global_config_matches_any_endpoint(alert_db, alerter_instance, global_config):
    """Config with endpoint_id=None should match any endpoint."""
    with patch.object(alerter_instance, "_send_webhook", new_callable=AsyncMock) as mock_send:
        sent = asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("any-endpoint-id", "HTTP 503", 2, db=alert_db)
        )
        assert sent == 1
        mock_send.assert_called_once()


def test_inactive_config_does_not_fire(alert_db, alerter_instance):
    """Inactive alert config should not fire."""
    config = AlertConfig(
        id=gen_id(), endpoint_id="ep-001", channel="webhook",
        destination="http://x.test", failure_threshold=1,
        cooldown_seconds=60, is_active=False,
    )
    alert_db.add(config)
    alert_db.commit()

    with patch.object(alerter_instance, "_send_webhook", new_callable=AsyncMock) as mock_send:
        sent = asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("ep-001", "HTTP 500", 5, db=alert_db)
        )
        assert sent == 0
        mock_send.assert_not_called()


def test_email_alert_fires(alert_db, alerter_instance):
    """Email channel should call _send_email."""
    config = AlertConfig(
        id=gen_id(), endpoint_id="ep-002", channel="email",
        destination="ops@test.com", failure_threshold=1,
        cooldown_seconds=60, is_active=True,
    )
    alert_db.add(config)
    alert_db.commit()

    with patch.object(alerter_instance, "_send_email") as mock_email:
        sent = asyncio.get_event_loop().run_until_complete(
            alerter_instance.maybe_alert("ep-002", "timeout", 3, db=alert_db)
        )
        assert sent == 1
        mock_email.assert_called_once_with("ops@test.com", "ep-002", "timeout", 3)
