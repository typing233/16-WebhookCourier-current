"""Tests for multi-tenant isolation when AUTH_ENABLED=True.

Verifies that one tenant cannot read/write another tenant's:
- Endpoints
- Messages
- Alert configs
- DLQ entries
- Subscriptions
"""
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webhook_courier.database import Base, get_db
from webhook_courier.models import gen_id


@pytest.fixture
def auth_client():
    """Client with AUTH_ENABLED=True, two apps with separate keys."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    from webhook_courier.api.endpoints import router as endpoints_router
    from webhook_courier.api.messages import router as messages_router
    from webhook_courier.api.applications import router as applications_router
    from webhook_courier.api.subscriptions import router as subscriptions_router
    from webhook_courier.api.alerts import router as alerts_router
    from webhook_courier.dlq.dead_letter import router as dlq_router

    test_app = FastAPI()
    test_app.include_router(endpoints_router)
    test_app.include_router(messages_router)
    test_app.include_router(applications_router)
    test_app.include_router(subscriptions_router)
    test_app.include_router(alerts_router)
    test_app.include_router(dlq_router)
    test_app.dependency_overrides[get_db] = override_get_db

    with patch("webhook_courier.auth.dependencies.settings") as mock_settings:
        mock_settings.AUTH_ENABLED = True

        with TestClient(test_app) as c:
            # Create two applications
            resp_a = c.post("/applications", json={"name": "tenant-a"})
            app_a = resp_a.json()
            resp_b = c.post("/applications", json={"name": "tenant-b"})
            app_b = resp_b.json()

            # Create API keys for each
            key_resp_a = c.post(f"/applications/{app_a['id']}/keys", json={"label": "a-key"})
            key_a = key_resp_a.json()["key"]
            key_resp_b = c.post(f"/applications/{app_b['id']}/keys", json={"label": "b-key"})
            key_b = key_resp_b.json()["key"]

            yield {
                "client": c,
                "app_a": app_a,
                "app_b": app_b,
                "key_a": key_a,
                "key_b": key_b,
                "headers_a": {"Authorization": f"Bearer {key_a}"},
                "headers_b": {"Authorization": f"Bearer {key_b}"},
            }

    test_app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_endpoint_isolation(auth_client):
    """Tenant A's endpoints should not be visible to Tenant B."""
    c = auth_client["client"]
    h_a = auth_client["headers_a"]
    h_b = auth_client["headers_b"]

    # Tenant A creates an endpoint
    resp = c.post("/endpoints", json={
        "url": "http://a.test/hook",
        "secret": "a-secret-long-enough",
        "max_retries": 3,
        "retry_base_interval": 1.0,
    }, headers=h_a)
    assert resp.status_code == 201
    ep_a_id = resp.json()["id"]

    # Tenant B lists endpoints — should not see A's endpoint
    resp = c.get("/endpoints", headers=h_b)
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.json()]
    assert ep_a_id not in ids


def test_message_isolation(auth_client):
    """Tenant A's messages should not be visible to Tenant B."""
    c = auth_client["client"]
    h_a = auth_client["headers_a"]
    h_b = auth_client["headers_b"]

    # Tenant A creates endpoint and message
    resp = c.post("/endpoints", json={
        "url": "http://a.test/hook2",
        "secret": "a-secret-long-enough",
        "max_retries": 3,
        "retry_base_interval": 1.0,
    }, headers=h_a)
    ep_id = resp.json()["id"]

    resp = c.post("/messages", json={
        "endpoint_id": ep_id,
        "idempotency_key": "iso-msg-1",
        "payload": json.dumps({"data": "a"}),
    }, headers=h_a)
    assert resp.status_code == 202
    msg_id = resp.json()["id"]

    # Tenant B tries to get the message
    resp = c.get(f"/messages/{msg_id}", headers=h_b)
    assert resp.status_code == 404

    # Tenant B lists messages — should not see A's message
    resp = c.get("/messages", headers=h_b)
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["items"]]
    assert msg_id not in ids


def test_alert_config_isolation(auth_client):
    """Tenant A's alert configs should not be accessible by Tenant B."""
    c = auth_client["client"]
    h_a = auth_client["headers_a"]
    h_b = auth_client["headers_b"]

    # Tenant A creates alert config
    resp = c.post("/alert-configs", json={
        "endpoint_id": "ep-001",
        "channel": "webhook",
        "destination": "http://a.test/alert",
        "failure_threshold": 3,
        "cooldown_seconds": 60,
    }, headers=h_a)
    assert resp.status_code == 201
    config_id = resp.json()["id"]

    # Tenant B tries to get, update, delete it
    resp = c.get(f"/alert-configs/{config_id}", headers=h_b)
    assert resp.status_code == 404

    resp = c.patch(f"/alert-configs/{config_id}", json={
        "channel": "email",
        "destination": "evil@b.test",
        "failure_threshold": 1,
        "cooldown_seconds": 60,
    }, headers=h_b)
    assert resp.status_code == 404

    resp = c.delete(f"/alert-configs/{config_id}", headers=h_b)
    assert resp.status_code == 404

    # Tenant B lists — should not see A's config
    resp = c.get("/alert-configs", headers=h_b)
    assert resp.status_code == 200
    ids = [cfg["id"] for cfg in resp.json()]
    assert config_id not in ids


def test_subscription_isolation(auth_client):
    """Tenant A's subscriptions should not be accessible by Tenant B."""
    c = auth_client["client"]
    h_a = auth_client["headers_a"]
    h_b = auth_client["headers_b"]

    # Tenant A creates endpoint + subscription
    resp = c.post("/endpoints", json={
        "url": "http://a.test/sub",
        "secret": "a-secret-long-enough",
        "max_retries": 3,
        "retry_base_interval": 1.0,
    }, headers=h_a)
    ep_id = resp.json()["id"]

    resp = c.post("/subscriptions", json={
        "endpoint_id": ep_id,
        "event_type": "tenant.a.event",
    }, headers=h_a)
    assert resp.status_code == 201
    sub_id = resp.json()["id"]

    # Tenant B cannot delete A's subscription
    resp = c.delete(f"/subscriptions/{sub_id}", headers=h_b)
    assert resp.status_code == 404

    # Tenant B cannot list A's subscriptions
    resp = c.get("/subscriptions", headers=h_b)
    ids = [s["id"] for s in resp.json()]
    assert sub_id not in ids


def test_dlq_isolation(auth_client):
    """DLQ entries should be scoped by tenant."""
    c = auth_client["client"]
    h_a = auth_client["headers_a"]
    h_b = auth_client["headers_b"]

    # Both tenants should see empty DLQ
    resp = c.get("/dlq", headers=h_a)
    assert resp.status_code == 200

    resp = c.get("/dlq", headers=h_b)
    assert resp.status_code == 200

    # DLQ stats should also be scoped
    resp = c.get("/dlq/stats", headers=h_b)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
